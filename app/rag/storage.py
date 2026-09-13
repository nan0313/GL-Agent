"""Transactional SQLite storage and FTS5 lexical index for RAG."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any, Iterator

from app.rag.models import ChunkRecord, KnowledgeSource, ParsedDocument
from app.rag.query import index_tokens, matches_metadata_filter, normalize_query, query_phrases, reference_definition_bonus, tokenize_query, validate_metadata_filter
from app.rag.schema import RAGChunk, RAGDocument


SCHEMA_VERSION = 2


class SQLiteRAGStore:
    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._chunk_map_cache: dict[str, RAGChunk] | None = None
        self.fts_available = False
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL") if self.database_path != ":memory:" else None
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS rag_schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_sources (
                    source_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    relative_location TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    modified_at TEXT NOT NULL,
                    ingestion_status TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    chunker_version TEXT NOT NULL,
                    index_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_code TEXT,
                    duplicate_of_source_id TEXT,
                    language TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    description TEXT NOT NULL DEFAULT '',
                    source_status TEXT NOT NULL DEFAULT 'active',
                    parser_status TEXT NOT NULL DEFAULT 'pending',
                    index_status TEXT NOT NULL DEFAULT 'not_indexed',
                    document_count INTEGER NOT NULL DEFAULT 0,
                    section_count INTEGER NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    last_validated_at TEXT,
                    last_indexed_at TEXT,
                    provenance TEXT NOT NULL DEFAULT 'configured_local_root'
                );
                CREATE INDEX IF NOT EXISTS idx_rag_sources_hash ON rag_sources(content_hash);
                CREATE TABLE IF NOT EXISTS rag_documents (
                    document_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES rag_sources(source_id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    language TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    parser_name TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS rag_sections (
                    section_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES rag_documents(document_id) ON DELETE CASCADE,
                    heading TEXT NOT NULL,
                    heading_level INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    page_number INTEGER,
                    paragraph_start INTEGER,
                    paragraph_end INTEGER,
                    section_path_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES rag_documents(document_id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL REFERENCES rag_sources(source_id) ON DELETE CASCADE,
                    section_id TEXT NOT NULL REFERENCES rag_sections(section_id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    section_path_json TEXT NOT NULL DEFAULT '[]',
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    estimated_tokens INTEGER NOT NULL,
                    page_start INTEGER,
                    page_end INTEGER,
                    ordinal INTEGER NOT NULL,
                    previous_chunk_id TEXT,
                    next_chunk_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    chunker_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rag_chunks_document ON rag_chunks(document_id);
                CREATE INDEX IF NOT EXISTS idx_rag_chunks_hash ON rag_chunks(content_hash);
                CREATE TABLE IF NOT EXISTS rag_embeddings (
                    content_hash TEXT NOT NULL,
                    provider_key TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (content_hash, provider_key)
                );
                CREATE TABLE IF NOT EXISTS rag_index_versions (
                    version TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    document_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    embedding_count INTEGER NOT NULL,
                    backend TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS rag_jobs (
                    job_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_source_columns()
            self._connection.execute(
                "INSERT OR IGNORE INTO rag_schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            try:
                self._connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
                        chunk_id UNINDEXED,
                        title_tokens,
                        section_tokens,
                        content_tokens,
                        document_id UNINDEXED,
                        source_id UNINDEXED,
                        source_type UNINDEXED,
                        language UNINDEXED
                    )
                    """
                )
                self.fts_available = True
            except sqlite3.OperationalError:
                self.fts_available = False
            self._connection.commit()

    def _ensure_source_columns(self) -> None:
        """Add lifecycle metadata to databases created by earlier releases."""
        columns = {
            str(row[1]): row
            for row in self._connection.execute("PRAGMA table_info(rag_sources)").fetchall()
        }
        definitions = {
            "description": "TEXT NOT NULL DEFAULT ''",
            "source_status": "TEXT NOT NULL DEFAULT 'active'",
            "parser_status": "TEXT NOT NULL DEFAULT 'pending'",
            "index_status": "TEXT NOT NULL DEFAULT 'not_indexed'",
            "document_count": "INTEGER NOT NULL DEFAULT 0",
            "section_count": "INTEGER NOT NULL DEFAULT 0",
            "chunk_count": "INTEGER NOT NULL DEFAULT 0",
            "last_validated_at": "TEXT",
            "last_indexed_at": "TEXT",
            "provenance": "TEXT NOT NULL DEFAULT 'configured_local_root'",
        }
        for name, definition in definitions.items():
            if name not in columns:
                self._connection.execute(f"ALTER TABLE rag_sources ADD COLUMN {name} {definition}")
        self._connection.execute(
            "INSERT OR REPLACE INTO rag_schema_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    @contextmanager
    def transaction(self) -> Iterator["SQLiteRAGStore"]:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                yield self
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._chunk_map_cache = None
            self._connection.close()

    def list_sources(self, source_statuses: set[str] | None = None) -> list[KnowledgeSource]:
        if source_statuses:
            placeholders = ",".join("?" for _ in source_statuses)
            rows = self._connection.execute(
                f"SELECT * FROM rag_sources WHERE source_status IN ({placeholders}) ORDER BY relative_location, source_id",
                tuple(sorted(source_statuses)),
            ).fetchall()
        else:
            rows = self._connection.execute("SELECT * FROM rag_sources ORDER BY relative_location, source_id").fetchall()
        return [_source_from_row(row) for row in rows]

    def get_source(self, source_id: str) -> KnowledgeSource | None:
        row = self._connection.execute("SELECT * FROM rag_sources WHERE source_id = ?", (source_id,)).fetchone()
        return _source_from_row(row) if row else None

    def upsert_source(self, source: KnowledgeSource) -> None:
        """Persist safe source metadata without touching document content."""
        existing = self.get_source(source.source_id)
        now = datetime.now(timezone.utc).isoformat()
        created_at = existing.created_at if existing else source.created_at
        self._connection.execute(
            """
            INSERT INTO rag_sources(
                source_id,source_type,display_name,relative_location,content_hash,file_size,modified_at,
                ingestion_status,parser_version,chunker_version,index_version,created_at,updated_at,error_code,
                duplicate_of_source_id,language,tags_json,description,source_status,parser_status,index_status,
                document_count,section_count,chunk_count,last_validated_at,last_indexed_at,provenance
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_id) DO UPDATE SET
                source_type=excluded.source_type,
                display_name=excluded.display_name,
                relative_location=excluded.relative_location,
                content_hash=excluded.content_hash,
                file_size=excluded.file_size,
                modified_at=excluded.modified_at,
                ingestion_status=excluded.ingestion_status,
                parser_version=excluded.parser_version,
                chunker_version=excluded.chunker_version,
                index_version=excluded.index_version,
                updated_at=excluded.updated_at,
                error_code=excluded.error_code,
                duplicate_of_source_id=excluded.duplicate_of_source_id,
                language=excluded.language,
                tags_json=excluded.tags_json,
                description=excluded.description,
                source_status=excluded.source_status,
                parser_status=excluded.parser_status,
                index_status=excluded.index_status,
                document_count=excluded.document_count,
                section_count=excluded.section_count,
                chunk_count=excluded.chunk_count,
                last_validated_at=excluded.last_validated_at,
                last_indexed_at=excluded.last_indexed_at,
                provenance=excluded.provenance
            """,
            (
                source.source_id,
                source.source_type,
                source.display_name,
                source.relative_location,
                source.content_hash,
                source.file_size,
                source.modified_at,
                source.ingestion_status,
                source.parser_version,
                source.chunker_version,
                source.index_version,
                created_at,
                now,
                source.error_code,
                source.duplicate_of_source_id,
                source.language,
                _json(source.tags),
                source.description,
                source.source_status,
                source.parser_status,
                source.index_status,
                source.document_count,
                source.section_count,
                source.chunk_count,
                source.last_validated_at,
                source.last_indexed_at,
                source.provenance,
            ),
        )

    def replace_document(self, source: KnowledgeSource, parsed: ParsedDocument, chunks: list[ChunkRecord]) -> None:
        self._chunk_map_cache = None
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_source(source.source_id)
        if existing:
            source = source.model_copy(update={
                "created_at": existing.created_at,
                "description": source.description or existing.description,
                "tags": source.tags or existing.tags,
                "provenance": source.provenance or existing.provenance,
            })
        self._delete_fts_for_source(source.source_id)
        self._connection.execute("DELETE FROM rag_sources WHERE source_id = ?", (source.source_id,))
        self._connection.execute(
            """
            INSERT INTO rag_sources(source_id,source_type,display_name,relative_location,content_hash,file_size,modified_at,ingestion_status,parser_version,chunker_version,index_version,created_at,updated_at,error_code,duplicate_of_source_id,language,tags_json,description,source_status,parser_status,index_status,document_count,section_count,chunk_count,last_validated_at,last_indexed_at,provenance)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (source.source_id, source.source_type, source.display_name, source.relative_location, source.content_hash, source.file_size, source.modified_at, parsed.status, parsed.parser_version, chunks[0].chunker_version if chunks else "structured_v1", source.index_version, existing.created_at if existing else source.created_at, now, parsed.error_code, source.duplicate_of_source_id, parsed.language, json.dumps(source.tags, ensure_ascii=False), source.description, source.source_status, "success", "pending", 1, len(parsed.sections), len(chunks), source.last_validated_at or now, source.last_indexed_at, source.provenance),
        )
        if parsed.status != "parsed":
            return
        self._connection.execute(
            "INSERT INTO rag_documents(document_id,source_id,title,language,content_hash,parser_name,parser_version,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
            (parsed.document_id, parsed.source_id, parsed.title, parsed.language, parsed.content_hash, parsed.parser_name, parsed.parser_version, _json(parsed.metadata)),
        )
        for section in parsed.sections:
            self._connection.execute(
                "INSERT INTO rag_sections(section_id,document_id,heading,heading_level,text,page_number,paragraph_start,paragraph_end,section_path_json,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (section.section_id, parsed.document_id, section.heading, section.heading_level, section.text, section.page_number, section.paragraph_start, section.paragraph_end, _json(section.section_path), _json(section.metadata)),
            )
        for chunk in chunks:
            self._insert_chunk(chunk)

    def update_source_status(self, source: KnowledgeSource, *, status: str, error_code: str | None = None, parser_version: str = "unknown", source_status: str | None = None, parser_status: str | None = None, index_status: str | None = None) -> None:
        self._chunk_map_cache = None
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_source(source.source_id)
        if existing is None:
            self.upsert_source(source.model_copy(update={"ingestion_status": status, "parser_version": parser_version, "error_code": error_code, "source_status": source_status or source.source_status, "parser_status": parser_status or status, "index_status": index_status or source.index_status}))
        else:
            self._connection.execute(
                "UPDATE rag_sources SET content_hash=?,file_size=?,modified_at=?,ingestion_status=?,parser_version=?,updated_at=?,error_code=?,source_status=COALESCE(?,source_status),parser_status=COALESCE(?,parser_status),index_status=COALESCE(?,index_status) WHERE source_id=?",
                (source.content_hash, source.file_size, source.modified_at, status, parser_version, now, error_code, source_status, parser_status, index_status, source.source_id),
            )

    def delete_source(self, source_id: str) -> None:
        self._chunk_map_cache = None
        self._delete_fts_for_source(source_id)
        self._connection.execute("DELETE FROM rag_sources WHERE source_id = ?", (source_id,))

    def remove_source_content(self, source_id: str, *, source_status: str, ingestion_status: str = "deleted", error_code: str | None = None) -> None:
        """Remove searchable content while retaining a safe lifecycle record."""
        self._chunk_map_cache = None
        self._delete_fts_for_source(source_id)
        self._connection.execute("DELETE FROM rag_documents WHERE source_id = ?", (source_id,))
        self._connection.execute(
            "UPDATE rag_sources SET ingestion_status=?,source_status=?,parser_status=?,index_status=?,document_count=0,section_count=0,chunk_count=0,index_version='unbuilt',updated_at=?,error_code=? WHERE source_id=?",
            (ingestion_status, source_status, ingestion_status, "pending", datetime.now(timezone.utc).isoformat(), error_code, source_id),
        )

    def mark_sources_indexed(self, version: str) -> None:
        self._connection.execute(
            "UPDATE rag_sources SET index_status='active',index_version=?,last_indexed_at=?,updated_at=? WHERE source_status='active' AND ingestion_status='parsed'",
            (version, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )

    def list_documents(self) -> list[RAGDocument]:
        rows = self._connection.execute("SELECT d.*, s.relative_location, s.source_type, s.source_id FROM rag_documents d JOIN rag_sources s ON s.source_id=d.source_id ORDER BY d.document_id").fetchall()
        return [_document_from_row(row) for row in rows]

    def list_chunks(self) -> list[RAGChunk]:
        rows = self._connection.execute("SELECT c.*, s.relative_location, s.source_type, s.language, s.modified_at, s.tags_json FROM rag_chunks c JOIN rag_sources s ON s.source_id=c.source_id ORDER BY c.document_id, c.ordinal, c.chunk_id").fetchall()
        return [_chunk_from_row(row) for row in rows]

    def get_chunk_map(self) -> dict[str, RAGChunk]:
        if self._chunk_map_cache is None:
            self._chunk_map_cache = {chunk.chunk_id: chunk for chunk in self.list_chunks()}
        return self._chunk_map_cache

    def lexical_candidates(self, query: str, candidate_limit: int = 24, metadata_filter: dict[str, Any] | None = None) -> list[tuple[RAGChunk, float]]:
        filters = validate_metadata_filter(metadata_filter)
        normalized = normalize_query(query)
        tokens = tokenize_query(normalized)
        if not tokens:
            return []
        rows: list[sqlite3.Row] = []
        if self.fts_available:
            safe_tokens = [token.replace(chr(34), "") for token in tokens[:64]]
            phrase_tokens = [token for token in safe_tokens if len(token) > 1]
            phrase = f'"{" ".join(phrase_tokens)}" OR ' if len(phrase_tokens) > 1 else ""
            fts_query = phrase + " OR ".join(f'"{token}"' for token in safe_tokens)
            try:
                rows = self._connection.execute("SELECT f.chunk_id, f.rank FROM rag_chunks_fts f WHERE rag_chunks_fts MATCH ? ORDER BY f.rank LIMIT ?", (fts_query, max(candidate_limit * 8, 64))).fetchall()
            except sqlite3.OperationalError:
                rows = []
        chunk_map = self.get_chunk_map()
        candidates: list[tuple[RAGChunk, float]] = []
        bm25_ranks = {str(row[0]): float(row[1]) for row in rows if len(row) > 1}
        selected_ids = [str(row[0]) for row in rows]
        scan = [chunk_map[item] for item in selected_ids if item in chunk_map] if rows else list(chunk_map.values())
        for chunk in scan:
            if not _chunk_matches_filter(chunk, filters):
                continue
            if chunk.chunk_id in bm25_ranks:
                chunk.metadata = {**chunk.metadata, "lexical_backend": "fts5_bm25", "fts5_bm25_rank": bm25_ranks[chunk.chunk_id]}
            score = _lexical_score(tokens, normalized, chunk)
            if score > 0:
                candidates.append((chunk, score))
        candidates.sort(key=lambda item: (-item[1], item[0].chunk_id))
        return candidates[:max(0, int(candidate_limit))]

    def save_embedding(self, content_hash: str, provider_key: str, vector: list[float]) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO rag_embeddings(content_hash,provider_key,dimension,vector_json,created_at) VALUES(?,?,?,?,?)",
            (content_hash, provider_key, len(vector), _json(vector), datetime.now(timezone.utc).isoformat()),
        )

    def get_embedding(self, content_hash: str, provider_key: str) -> list[float] | None:
        row = self._connection.execute("SELECT vector_json FROM rag_embeddings WHERE content_hash=? AND provider_key=?", (content_hash, provider_key)).fetchone()
        return list(json.loads(row[0])) if row else None

    def save_index_version(self, version: str, *, status: str, backend: str, document_count: int, chunk_count: int, embedding_count: int, error_code: str | None = None) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO rag_index_versions(version,status,document_count,chunk_count,embedding_count,backend,created_at,error_code) VALUES(?,?,?,?,?,?,?,?)",
            (version, status, document_count, chunk_count, embedding_count, backend, datetime.now(timezone.utc).isoformat(), error_code),
        )

    def active_index_version(self) -> str | None:
        row = self._connection.execute("SELECT version FROM rag_index_versions WHERE status='active' ORDER BY created_at DESC LIMIT 1").fetchone()
        return str(row[0]) if row else None

    def active_index_backend(self) -> str | None:
        row = self._connection.execute("SELECT backend FROM rag_index_versions WHERE status='active' ORDER BY created_at DESC LIMIT 1").fetchone()
        return str(row[0]) if row and row[0] else None

    def activate_index_version(self, version: str) -> None:
        self._connection.execute("UPDATE rag_index_versions SET status='retired' WHERE status='active'")
        self._connection.execute("UPDATE rag_index_versions SET status='active' WHERE version=?", (version,))

    def record_job(self, job_id: str, mode: str, status: str, summary: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._connection.execute("INSERT OR REPLACE INTO rag_jobs(job_id,mode,status,summary_json,created_at,updated_at) VALUES(?,?,?,?,COALESCE((SELECT created_at FROM rag_jobs WHERE job_id=?),?),?)", (job_id, mode, status, _json(summary), job_id, now, now))

    def last_job(self) -> dict[str, Any] | None:
        row = self._connection.execute("SELECT * FROM rag_jobs ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        value = json.loads(row["summary_json"])
        value.update({"job_id": row["job_id"], "mode": row["mode"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"]})
        return value

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._connection.execute("SELECT * FROM rag_jobs ORDER BY updated_at DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
        return [_job_from_row(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._connection.execute("SELECT * FROM rag_jobs WHERE job_id=?", (str(job_id),)).fetchone()
        return _job_from_row(row) if row else None

    def stats(self) -> dict[str, Any]:
        source_count = int(self._connection.execute("SELECT COUNT(*) FROM rag_sources").fetchone()[0])
        document_count = int(self._connection.execute("SELECT COUNT(*) FROM rag_documents").fetchone()[0])
        chunk_count = int(self._connection.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0])
        failed_count = int(self._connection.execute("SELECT COUNT(*) FROM rag_sources WHERE ingestion_status='failed'").fetchone()[0])
        source_status_counts = {str(row[0]): int(row[1]) for row in self._connection.execute("SELECT source_status, COUNT(*) FROM rag_sources GROUP BY source_status").fetchall()}
        active = self.active_index_version()
        return {"schema_version": SCHEMA_VERSION, "source_count": source_count, "source_status_counts": source_status_counts, "document_count": document_count, "chunk_count": chunk_count, "failed_source_count": failed_count, "active_index_version": active, "fts5_available": self.fts_available, "last_job": self.last_job()}

    def _insert_chunk(self, chunk: ChunkRecord) -> None:
        self._connection.execute(
            "INSERT INTO rag_chunks(chunk_id,document_id,source_id,section_id,title,section_path_json,content,content_hash,char_count,estimated_tokens,page_start,page_end,ordinal,previous_chunk_id,next_chunk_id,metadata_json,chunker_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chunk.chunk_id, chunk.document_id, chunk.source_id, chunk.section_id, chunk.title, _json(chunk.section_path), chunk.content, chunk.content_hash, chunk.char_count, chunk.estimated_tokens, chunk.page_start, chunk.page_end, chunk.ordinal, chunk.previous_chunk_id, chunk.next_chunk_id, _json(chunk.metadata), chunk.chunker_version),
        )
        if self.fts_available:
            source = self.get_source(chunk.source_id)
            self._connection.execute("INSERT INTO rag_chunks_fts(chunk_id,title_tokens,section_tokens,content_tokens,document_id,source_id,source_type,language) VALUES(?,?,?,?,?,?,?,?)", (chunk.chunk_id, " ".join(index_tokens(chunk.title)), " ".join(index_tokens(" ".join(chunk.section_path))), " ".join(index_tokens(chunk.content)), chunk.document_id, chunk.source_id, source.source_type if source else "unknown", source.language if source else "unknown"))

    def _delete_fts_for_source(self, source_id: str) -> None:
        if self.fts_available:
            rows = self._connection.execute("SELECT chunk_id FROM rag_chunks WHERE source_id=?", (source_id,)).fetchall()
            for row in rows:
                self._connection.execute("DELETE FROM rag_chunks_fts WHERE chunk_id=?", (row[0],))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _source_from_row(row: sqlite3.Row) -> KnowledgeSource:
    keys = set(row.keys())
    return KnowledgeSource(
        source_id=row["source_id"],
        source_type=row["source_type"],
        display_name=row["display_name"],
        relative_location=row["relative_location"],
        content_hash=row["content_hash"],
        file_size=row["file_size"],
        modified_at=row["modified_at"],
        ingestion_status=row["ingestion_status"],
        parser_version=row["parser_version"],
        chunker_version=row["chunker_version"],
        index_version=row["index_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error_code=row["error_code"],
        duplicate_of_source_id=row["duplicate_of_source_id"],
        language=row["language"],
        tags=json.loads(row["tags_json"] or "[]"),
        description=row["description"] if "description" in keys else "",
        source_status=row["source_status"] if "source_status" in keys else "active",
        parser_status=row["parser_status"] if "parser_status" in keys else row["ingestion_status"],
        index_status=row["index_status"] if "index_status" in keys else "not_indexed",
        document_count=int(row["document_count"] or 0) if "document_count" in keys else 0,
        section_count=int(row["section_count"] or 0) if "section_count" in keys else 0,
        chunk_count=int(row["chunk_count"] or 0) if "chunk_count" in keys else 0,
        last_validated_at=row["last_validated_at"] if "last_validated_at" in keys else None,
        last_indexed_at=row["last_indexed_at"] if "last_indexed_at" in keys else None,
        provenance=row["provenance"] if "provenance" in keys else "configured_local_root",
    )


def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    value = json.loads(row["summary_json"] or "{}")
    value.update({"job_id": row["job_id"], "mode": row["mode"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"]})
    return value


def _document_from_row(row: sqlite3.Row) -> RAGDocument:
    return RAGDocument(doc_id=row["document_id"], document_id=row["document_id"], source_id=row["source_id"], source_type=row["source_type"], source_path=row["relative_location"], relative_location=row["relative_location"], title=row["title"], content="", content_hash=row["content_hash"], parser_name=row["parser_name"], parser_version=row["parser_version"], metadata=json.loads(row["metadata_json"] or "{}"), status="parsed")


def _chunk_from_row(row: sqlite3.Row) -> RAGChunk:
    metadata = json.loads(row["metadata_json"] or "{}")
    metadata.update({"source_id": row["source_id"], "source_type": row["source_type"], "language": row["language"], "modified_at": row["modified_at"], "tags": json.loads(row["tags_json"] or "[]"), "section_path": json.loads(row["section_path_json"] or "[]")})
    return RAGChunk(chunk_id=row["chunk_id"], doc_id=row["document_id"], document_id=row["document_id"], source_id=row["source_id"], source_type=row["source_type"], source_path=row["relative_location"], relative_location=row["relative_location"], title=row["title"], section=metadata.get("section"), section_id=row["section_id"], section_path=json.loads(row["section_path_json"] or "[]"), content=row["content"], content_hash=row["content_hash"], char_count=row["char_count"], estimated_tokens=row["estimated_tokens"], page_start=row["page_start"], page_end=row["page_end"], ordinal=row["ordinal"], previous_chunk_id=row["previous_chunk_id"], next_chunk_id=row["next_chunk_id"], chunker_version=row["chunker_version"], metadata=metadata)


def _chunk_matches_filter(chunk: RAGChunk, filters) -> bool:
    return matches_metadata_filter({**chunk.metadata, "document_id": getattr(chunk, "document_id", chunk.doc_id), "source_id": getattr(chunk, "source_id", None), "source_type": getattr(chunk, "source_type", None), "section": chunk.section}, filters)


def _lexical_score(tokens: list[str], normalized: str, chunk: RAGChunk) -> float:
    title = chunk.title.lower()
    section = (chunk.section or "").lower()
    section_text = " / ".join(chunk.section_path or []).lower()
    content = chunk.content.lower()
    haystack = f"{title}\n{section}\n{section_text}\n{content}"
    common = {"根据", "知识", "识库", "解释", "什么", "当前", "系统", "说明", "是否", "有没", "没有", "哪些", "通常", "可以", "请问", "帮我", "如何", "为什么", "工程", "模型", "文件", "规范", "标准"}
    terms = [token.lower() for token in tokens if (len(token) > 1 or token.isascii()) and token.lower() not in common]
    matched_terms = {term for term in terms if term in haystack}
    phrases = query_phrases(normalized)
    compact_title = re.sub(r"\s+", "", title)
    compact_section = re.sub(r"\s+", "", section_text)
    compact_content = re.sub(r"\s+", "", content)
    phrase_matches = [
        phrase for phrase in phrases
        if re.sub(r"\s+", "", phrase.lower()) in f"{compact_title}\n{compact_section}\n{compact_content}"
    ]
    ascii_terms = [term for term in terms if term.isascii() and term in haystack]
    if not matched_terms and not phrase_matches and not ascii_terms:
        return 0.0
    coverage = len(matched_terms) / max(1, len(set(terms)))
    token_score = sum(0.35 for term in matched_terms if term in content)
    token_score += sum(0.65 for term in matched_terms if term in section_text)
    token_score += sum(0.8 for term in matched_terms if term in title)
    phrase_score = 0.0
    for phrase in phrase_matches:
        value = re.sub(r"\s+", "", phrase.lower())
        length_bonus = min(4.0, len(phrase) * 0.35)
        if value in compact_content:
            phrase_score += 7.0 + length_bonus
        if value in compact_section:
            phrase_score += 10.0 + length_bonus
        if value in compact_title:
            phrase_score += 12.0 + length_bonus
    phrase_score += reference_definition_bonus(phrases, content)
    compact = normalized.replace(" ", "")
    exact_query = 3.0 if len(compact) >= 4 and compact in haystack.replace(" ", "") else 0.0
    return round(token_score + coverage * 2.0 + phrase_score + exact_query, 6)
