"""Incremental ingestion, versioning and atomic active-index switching."""

from hashlib import sha256
from pathlib import Path
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.rag.chunking import StructuredChunker
from app.rag.config import RAGConfig, get_rag_config
from app.rag.embeddings import EmbeddingProvider, EmbeddingProviderUnavailable, build_embedding_provider
from app.rag.models import IndexJobSummary, KnowledgeSource
from app.rag.parsers import DocumentParserRegistry
from app.rag.source_registry import KnowledgeSourceRegistry
from app.rag.storage import SQLiteRAGStore
from app.rag.vector_index import (
    PersistentVectorIndex,
    UnavailableVectorBackend,
    VectorBackend,
    VectorIndexError,
    build_vector_backend,
)


class RebuildInProgressError(RuntimeError):
    pass


class RAGIndexManager:
    def __init__(self, config: RAGConfig | None = None, *, store: SQLiteRAGStore | None = None, source_registry: KnowledgeSourceRegistry | None = None, embedding_provider: EmbeddingProvider | None = None) -> None:
        self.config = config or get_rag_config()
        self.store = store or SQLiteRAGStore(self.config.database_path)
        self.source_registry = source_registry or KnowledgeSourceRegistry(self.config)
        self.parser_registry = DocumentParserRegistry()
        self.chunker = StructuredChunker(self.config.chunk_target_chars, self.config.chunk_max_chars, self.config.chunk_overlap_chars, self.config.chunk_min_chars)
        self.embedding_provider = embedding_provider or build_embedding_provider(self.config)
        self._configured_vector_backend = str(self.config.vector_backend or "python_cosine_fallback").lower()
        self._vector_fallback_used = False
        self._vector_error_code: str | None = None
        self.vector_index = self._create_vector_backend()
        self._align_to_active_backend()
        self._rebuild_lock = Lock()
        self._jobs_lock = Lock()
        self._cancel_events: dict[str, Event] = {}
        self._last_dense_error: str | None = None
        self._closed = False

    def close(self) -> None:
        """Release vector and SQLite resources; repeated close is safe."""
        if self._closed:
            return
        self._closed = True
        try:
            self.vector_index.close()
        finally:
            self.store.close()

    def reload_from_disk(self) -> None:
        """Reopen the configured SQLite/vector resources after a validated restore."""
        # A restore closes the old handles before replacing their files.  Keep
        # this method safe for both open and already-closed managers.
        try:
            self.vector_index.close()
        finally:
            try:
                self.store.close()
            except Exception:
                # sqlite3 raises on a second close; the resource is already
                # released, so reopening below is still safe.
                pass
        self._closed = False
        self._vector_fallback_used = False
        self._vector_error_code = None
        self._last_dense_error = None
        self.store = SQLiteRAGStore(self.config.database_path)
        self.vector_index = self._create_vector_backend()
        self._align_to_active_backend()

    def _create_vector_backend(self) -> VectorBackend:
        try:
            return build_vector_backend(self.config, self.config.index_path)
        except VectorIndexError as exc:
            self._vector_error_code = exc.code
            if self._configured_vector_backend == "hnsw" and self.store.active_index_backend() in {"python_cosine_fallback", "python_cosine"}:
                self._vector_fallback_used = True
                return PersistentVectorIndex(self.config.index_path)
            return UnavailableVectorBackend(exc.code)

    def _align_to_active_backend(self) -> None:
        """Use a persisted active backend on restart without building anything."""
        active_backend = self.store.active_index_backend()
        if active_backend in {None, "none", self.vector_index.backend} or (self._configured_vector_backend == "hnsw" and active_backend != "hnsw"):
            return
        try:
            config = self.config.model_copy(update={"vector_backend": active_backend})
            candidate = build_vector_backend(config, self.config.index_path)
        except VectorIndexError as exc:
            self._vector_error_code = exc.code
            self.vector_index = UnavailableVectorBackend(exc.code)
            return
        self.vector_index.close()
        self.vector_index = candidate

    def _load_active_vector(self, *, provider_key: str, dimension: int) -> None:
        active = self.store.active_index_version()
        active_backend = self.store.active_index_backend()
        if not active or active_backend != self.vector_index.backend:
            return
        if self.vector_index.version == active:
            return
        self.vector_index.load(active, provider_key=provider_key, dimension=dimension)

    def ensure_ready(self) -> None:
        if not self.config.enabled:
            return
        if self.store.active_index_version() is None:
            self.rebuild(mode="incremental")

    def rebuild(self, mode: str = "incremental", *, cancel_event: Event | None = None, job_id: str | None = None) -> dict[str, Any]:
        if mode not in {"full", "incremental"}:
            raise ValueError("RAG_INVALID_REBUILD_MODE")
        if not self._rebuild_lock.acquire(blocking=False):
            raise RebuildInProgressError("RAG_REBUILD_IN_PROGRESS")
        job_id = job_id or f"ragjob_{uuid4().hex}"
        started = perf_counter()
        cancel_event = cancel_event or Event()
        current_sources = self.store.list_sources(source_statuses={"active", "disabled"})
        summary = IndexJobSummary(job_id=job_id, mode=mode, status="running")
        self.store.record_job(job_id, mode, "running", summary.model_dump())
        self.store._connection.commit()
        try:
            discovered = self.source_registry.scan()
            existing = {item.source_id: item for item in current_sources}
            seen_ids = {item.source_id for item in discovered}
            with self.store.transaction():
                for index, source in enumerate(discovered):
                    if cancel_event.is_set():
                        summary.status = "canceled"
                        raise _CanceledRebuild()
                    previous = existing.get(source.source_id)
                    parser_version = self.parser_registry.parser_version(source.source_type)
                    if previous and previous.source_status == "disabled":
                        continue
                    unchanged = bool(previous and previous.source_status == "active" and previous.index_status == "active" and previous.content_hash == source.content_hash and previous.ingestion_status == "parsed" and previous.parser_version == parser_version and mode != "full")
                    if unchanged:
                        summary.unchanged += 1
                        continue
                    if previous is None:
                        summary.added += 1
                    else:
                        summary.modified += 1
                    if source.ingestion_status == "failed":
                        self.store.update_source_status(source, status="failed", error_code=source.error_code or "RAG_SOURCE_INVALID", parser_version=parser_version, source_status="error", parser_status="failed")
                        summary.failed += 1
                        continue
                    parsed = self.parser_registry.parse(source, self.source_registry.path_for(source))
                    if parsed.status == "failed":
                        self.store.update_source_status(source, status="failed", error_code=parsed.error_code or "RAG_PARSE_FAILED", parser_version=parsed.parser_version, source_status="error", parser_status="failed")
                        summary.failed += 1
                        continue
                    if parsed.status == "empty":
                        self.store.update_source_status(source, status="empty", error_code="RAG_EMPTY_DOCUMENT", parser_version=parsed.parser_version, source_status="error", parser_status="empty")
                        summary.failed += 1
                        continue
                    chunks = self.chunker.chunk(parsed)
                    enriched_source = source.model_copy(update={"language": parsed.language, "parser_version": parsed.parser_version, "chunker_version": self.chunker.chunker_version if hasattr(self.chunker, "chunker_version") else "structured_v1"})
                    self.store.replace_document(enriched_source, parsed, chunks)
                    summary.parsed += 1
                    summary.chunk_count += len(chunks)
                for source_id in set(existing) - seen_ids:
                    if existing[source_id].source_status == "disabled":
                        continue
                    self.store.remove_source_content(source_id, source_status="deleted", ingestion_status="deleted")
                    summary.deleted += 1

                chunks = self.store.list_chunks()
                # Resolve the provider identity before deriving the version so
                # an unloaded local provider cannot create a one-time version
                # that changes merely because the model was loaded.
                if self.config.dense_enabled:
                    self.embedding_provider.health_check(load=True)
                version = _index_version(self.store.list_sources(source_statuses={"active"}), chunks, self.chunker, self.embedding_provider.provider_key, self.vector_index.backend)
                if mode == "full" and version == self.store.active_index_version():
                    version = f"{version}_full_{uuid4().hex[:8]}"
                dense_vectors, embedding_count, cache_hits = self._build_dense_index(chunks, version, cancel_event, incremental=(mode == "incremental"))
                summary.embedding_count = embedding_count
                summary.cache_hits = cache_hits
                summary.version = version
                self.store.save_index_version(version, status="active", backend=self.vector_index.backend if dense_vectors else "none", document_count=len(self.store.list_documents()), chunk_count=len(chunks), embedding_count=embedding_count, error_code=self._last_dense_error or self._vector_error_code)
                self.store.activate_index_version(version)
                self.store.mark_sources_indexed(version)
                summary.duration_ms = max(0, int((perf_counter() - started) * 1000))
                summary.status = "success"
                self.store.record_job(job_id, mode, "success", summary.model_dump())
            return summary.model_dump()
        except _CanceledRebuild:
            summary.duration_ms = max(0, int((perf_counter() - started) * 1000))
            summary.status = "canceled"
            self.store.record_job(job_id, mode, "canceled", summary.model_dump())
            self.store._connection.commit()
            return summary.model_dump()
        except Exception:
            summary.duration_ms = max(0, int((perf_counter() - started) * 1000))
            summary.status = "failed"
            summary.error_code = "RAG_INDEX_REBUILD_FAILED"
            self.store.record_job(job_id, mode, "failed", summary.model_dump())
            self.store._connection.commit()
            return summary.model_dump()
        finally:
            with self._jobs_lock:
                self._cancel_events.pop(job_id, None)
            self._rebuild_lock.release()

    def validate(self) -> dict[str, Any]:
        with self._rebuild_lock:
            active = self.store.active_index_version()
            active_backend = self.store.active_index_backend()
            result = {"status": "success", "active_index_version": active, "active_vector_backend": active_backend, "fts5_available": self.store.fts_available, "vector_valid": None, "error_code": None}
            if active and self.embedding_provider.health_check(load=True).get("available"):
                try:
                    if active_backend and active_backend != self.vector_index.backend:
                        config = self.config.model_copy(update={"vector_backend": active_backend})
                        candidate = build_vector_backend(config, self.config.index_path)
                        self.vector_index.close()
                        self.vector_index = candidate
                    self._load_active_vector(provider_key=self.embedding_provider.provider_key, dimension=self.embedding_provider.dimension)
                    result["vector_valid"] = True
                    result["vector_details"] = self.vector_index.validate()
                except VectorIndexError as exc:
                    result.update({"status": "failed", "vector_valid": False, "error_code": exc.code})
            elif active and active_backend not in {None, "none"}:
                result.update({"status": "failed", "vector_valid": False, "error_code": "RAG_EMBEDDING_UNAVAILABLE"})
            return result

    def submit_rebuild(self, mode: str = "incremental") -> dict[str, Any]:
        if not self._rebuild_lock.acquire(blocking=False):
            raise RebuildInProgressError("RAG_REBUILD_IN_PROGRESS")
        self._rebuild_lock.release()
        job_id = f"ragjob_{uuid4().hex}"
        event = Event()
        with self._jobs_lock:
            self._cancel_events[job_id] = event
        summary = IndexJobSummary(job_id=job_id, mode=mode, status="queued")
        self.store.record_job(job_id, mode, "queued", summary.model_dump())
        self.store._connection.commit()
        thread = Thread(target=self.rebuild, kwargs={"mode": mode, "cancel_event": event, "job_id": job_id}, daemon=True)
        thread.start()
        return summary.model_dump()

    def cancel(self, job_id: str) -> bool:
        with self._jobs_lock:
            event = self._cancel_events.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    def search(self, query: str, *, top_k: int | None = None, candidate_limit: int | None = None, metadata_filter: dict[str, Any] | None = None):
        self.ensure_ready()
        with self._rebuild_lock:
            from app.rag.retrieval import HybridRetriever

            return HybridRetriever(self.store, self.embedding_provider, self.vector_index, self.config).retrieve(query, top_k=top_k or self.config.top_k, candidate_limit=candidate_limit or self.config.candidate_limit, metadata_filter=metadata_filter)

    def health(self) -> dict[str, Any]:
        stats = self.store.stats()
        embedding = self.embedding_provider.health_check(load=False)
        last_job = stats.get("last_job") or {}
        active_version = stats.get("active_index_version")
        active_backend = self.store.active_index_backend()
        vector_health = self.vector_index.health()
        vector_loaded = bool(active_version and self.vector_index.version == active_version and self.vector_index.dimension == int(embedding.get("dimension") or 0))
        real_dense_validated = bool(embedding.get("available") and embedding.get("provider_type") == "local_sentence_transformer" and embedding.get("semantic_status") == "real_local" and vector_loaded)
        provider_type = "real" if embedding.get("provider_type") == "local_sentence_transformer" and str(embedding.get("semantic_status") or "").startswith("real_local") else ("fixture" if embedding.get("provider_type") == "fixture" else "disabled")
        return {"configured": bool(self.config.enabled), "available": bool(self.config.enabled and (self.store.fts_available or embedding.get("available"))), "lexical_available": bool(self.config.lexical_enabled and self.store.fts_available), "dense_available": bool(embedding.get("available") and vector_health.get("available", True)), "dense_provider_type": embedding.get("provider_type"), "embedding_provider_type": provider_type, "embedding_dimension": embedding.get("dimension", 0), "real_dense_validated": real_dense_validated, "dense_status": embedding.get("semantic_status") or ("real" if real_dense_validated else "disabled"), "reranker_available": self.config.rerank_enabled, "active_index_version": active_version, "source_count": stats.get("source_count", 0), "source_status_counts": stats.get("source_status_counts", {}), "document_count": stats.get("document_count", 0), "chunk_count": stats.get("chunk_count", 0), "last_build_status": last_job.get("status"), "last_build_at": last_job.get("updated_at"), "embedding_model": _safe_model_name(str(embedding.get("model_name") or getattr(self.embedding_provider, "model_name", "none"))), "vector_backend": active_backend or (self.vector_index.backend if embedding.get("available") else None), "configured_vector_backend": self._configured_vector_backend, "active_vector_backend": active_backend, "vector_backend_available": bool(vector_health.get("available", False)), "vector_item_count": vector_health.get("item_count", 0), "vector_capacity": vector_health.get("capacity", 0), "vector_deleted_count": vector_health.get("deleted_count", 0), "vector_index_version": vector_health.get("index_version"), "vector_fallback_used": self._vector_fallback_used, "vector_error_code": self._vector_error_code or vector_health.get("error_code"), "fts5_available": self.store.fts_available, "benchmark_version": "rag-benchmark-v1", "no_answer_policy_version": "retrieval_decision_v1", "error_code": self._last_dense_error if not embedding.get("available") and self.config.dense_enabled else None, "last_job": last_job}

    def _build_dense_index(self, chunks, version: str, cancel_event: Event, *, incremental: bool = False) -> tuple[dict[str, list[float]], int, int]:
        self._last_dense_error = None
        health = self.embedding_provider.health_check(load=True)
        if not health.get("available"):
            if self.config.dense_enabled and health.get("error_code"):
                self._last_dense_error = str(health["error_code"])
            return {}, 0, 0
        backend_health = self.vector_index.health()
        if not backend_health.get("available"):
            self._vector_error_code = str(backend_health.get("error_code") or "RAG_VECTOR_BACKEND_UNAVAILABLE")
            return {}, 0, 0
        self._vector_error_code = None
        try:
            self._load_active_vector(provider_key=self.embedding_provider.provider_key, dimension=self.embedding_provider.dimension)
        except VectorIndexError as exc:
            self._vector_error_code = exc.code
            if incremental and self.vector_index.backend == "hnsw":
                incremental = False
        vectors: dict[str, list[float]] = {}
        missing: list[Any] = []
        cache_hits = 0
        for chunk in chunks:
            cache_key = _embedding_cache_key(chunk)
            cached = self.store.get_embedding(cache_key, self.embedding_provider.provider_key)
            if cached is not None:
                vectors[chunk.chunk_id] = cached
                cache_hits += 1
            else:
                missing.append(chunk)
        try:
            batch_size = max(1, int(self.config.embedding_batch_size))
            for start in range(0, len(missing), batch_size):
                if cancel_event.is_set():
                    raise _CanceledRebuild()
                batch = missing[start : start + batch_size]
                values = self.embedding_provider.embed_documents([_embedding_text(item) for item in batch])
                for chunk, vector in zip(batch, values):
                    self.store.save_embedding(_embedding_cache_key(chunk), self.embedding_provider.provider_key, vector)
                    vectors[chunk.chunk_id] = vector
            if incremental and self.vector_index.version == version and self.vector_index.provider_key == self.embedding_provider.provider_key and self.vector_index.dimension == self.embedding_provider.dimension and self.vector_index.item_count == len(vectors):
                return vectors, len(missing), cache_hits
            self.vector_index.build(vectors, version=version, provider_key=self.embedding_provider.provider_key, dimension=self.embedding_provider.dimension, incremental=incremental and self.vector_index.supports_incremental_update)
            return vectors, len(missing), cache_hits
        except EmbeddingProviderUnavailable as exc:
            self._last_dense_error = exc.code
            return {}, 0, cache_hits

    def migrate_vector_backend(self, target: str) -> dict[str, Any]:
        if not self._rebuild_lock.acquire(blocking=False):
            return {"status": "failed", "error_code": "RAG_REBUILD_IN_PROGRESS"}
        try:
            return self._migrate_vector_backend(target)
        finally:
            self._rebuild_lock.release()

    def _migrate_vector_backend(self, target: str) -> dict[str, Any]:
        """Build a new vector backend from active SQLite chunks and cached vectors."""
        target = str(target or "").lower()
        if target not in {"hnsw", "python_cosine_fallback", "python_cosine"}:
            return {"status": "failed", "error_code": "RAG_VECTOR_BACKEND_INVALID"}
        if target == self.vector_index.backend and self.store.active_index_backend() == target:
            return {"status": "success", "status_detail": "already_active", **self.health()}
        try:
            health = self.embedding_provider.health_check(load=True)
            if not health.get("available"):
                return {"status": "failed", "error_code": str(health.get("error_code") or "RAG_EMBEDDING_UNAVAILABLE")}
            chunks = self.store.list_chunks()
            vectors: dict[str, list[float]] = {}
            missing = []
            cache_hits = 0
            for chunk in chunks:
                cached = self.store.get_embedding(_embedding_cache_key(chunk), self.embedding_provider.provider_key)
                if cached is None:
                    missing.append(chunk)
                else:
                    vectors[chunk.chunk_id] = cached
                    cache_hits += 1
            if missing:
                values = self.embedding_provider.embed_documents([_embedding_text(item) for item in missing])
                for chunk, vector in zip(missing, values):
                    self.store.save_embedding(_embedding_cache_key(chunk), self.embedding_provider.provider_key, vector)
                    vectors[chunk.chunk_id] = vector
            config = self.config.model_copy(update={"vector_backend": target})
            backend = build_vector_backend(config, self.config.index_path)
            version = _index_version(self.store.list_sources(source_statuses={"active"}), chunks, self.chunker, self.embedding_provider.provider_key, target)
            backend.build(vectors, version=version, provider_key=self.embedding_provider.provider_key, dimension=self.embedding_provider.dimension)
            backend.load(version, provider_key=self.embedding_provider.provider_key, dimension=self.embedding_provider.dimension)
            backend.validate()
            with self.store.transaction():
                self.store.save_index_version(version, status="active", backend=backend.backend, document_count=len(self.store.list_documents()), chunk_count=len(chunks), embedding_count=len(missing))
                self.store.activate_index_version(version)
            self.vector_index.close()
            self.vector_index = backend
            return {"status": "success", "target_backend": backend.backend, "index_version": version, "chunk_count": len(chunks), "embedding_count": len(missing), "cache_hits": cache_hits, "health": self.health()}
        except (VectorIndexError, EmbeddingProviderUnavailable) as exc:
            return {"status": "failed", "error_code": getattr(exc, "code", None) or "RAG_VECTOR_MIGRATION_FAILED"}


class _CanceledRebuild(Exception):
    pass


def _index_version(sources: list[KnowledgeSource], chunks, chunker: StructuredChunker, provider_key: str = "", backend: str = "python_cosine_fallback") -> str:
    material = "|".join([f"{item.source_id}:{item.content_hash}:{item.ingestion_status}:{item.parser_version}" for item in sorted(sources, key=lambda value: value.source_id)] + [f"{item.chunk_id}:{item.content_hash}:{item.chunker_version}" for item in chunks] + [chunker.chunker_version, str(chunker.target_chars), str(chunker.max_chars), str(chunker.overlap_chars), provider_key, backend])
    return "ragidx_" + sha256(material.encode("utf-8")).hexdigest()[:20]


def _embedding_text(chunk: Any) -> str:
    section = " / ".join(getattr(chunk, "section_path", []) or [])
    return f"document_title: {chunk.title}\nsection_path: {section}\nchunk_text: {chunk.content}".strip()


def _embedding_cache_key(chunk: Any) -> str:
    return sha256(_embedding_text(chunk).encode("utf-8")).hexdigest()


def _safe_model_name(value: str) -> str:
    if not value:
        return "none"
    return Path(value).name if ("\\" in value or "/" in value) else value
