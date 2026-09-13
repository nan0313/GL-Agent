"""Compatibility facade over the persistent RAG index manager."""

from pathlib import Path
import tempfile
from uuid import uuid4
from typing import Any

from app.rag.config import RAG_CHUNK_MAX_CHARS, RAG_CHUNK_OVERLAP_CHARS, RAGConfig, get_rag_config
from app.rag.document_loader import DEFAULT_KNOWLEDGE_DIR
from app.rag.index_manager import RAGIndexManager
from app.rag.schema import RAGChunk, RAGDocument


class RAGIndexStore:
    """Keep the previous public API while using SQLite/FTS5 underneath."""

    def __init__(self, knowledge_dir: Path | None = None, max_chars: int = RAG_CHUNK_MAX_CHARS, overlap_chars: int = RAG_CHUNK_OVERLAP_CHARS, *, manager: RAGIndexManager | None = None) -> None:
        self.knowledge_dir = knowledge_dir or DEFAULT_KNOWLEDGE_DIR
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        if manager is not None:
            self.manager = manager
        else:
            config = get_rag_config()
            if knowledge_dir is not None:
                config = config.model_copy(update={"knowledge_roots": [str(knowledge_dir)], "database_path": ":memory:", "index_path": str(Path(tempfile.gettempdir()) / f"mvp-rag-index-{uuid4().hex}")})
            config = config.model_copy(update={"chunk_max_chars": max_chars, "chunk_overlap_chars": overlap_chars})
            self.manager = RAGIndexManager(config)

    @property
    def store(self):
        return self.manager.store

    def ensure_built(self) -> None:
        self.manager.ensure_ready()

    def rebuild(self) -> dict[str, Any]:
        self.manager.rebuild(mode="incremental")
        return self.get_stats()

    def list_documents(self) -> list[RAGDocument]:
        self.ensure_built()
        return self.manager.store.list_documents()

    def list_chunks(self) -> list[RAGChunk]:
        self.ensure_built()
        return self.manager.store.list_chunks()

    def get_stats(self) -> dict[str, Any]:
        stats = self.manager.health()
        last_job = stats.get("last_job") or {}
        return {
            "provider": "local_rag_v3",
            "docs_count": stats.get("document_count", 0),
            "chunks_count": stats.get("chunk_count", 0),
            "sources_count": stats.get("source_count", 0),
            "source_status_counts": stats.get("source_status_counts", {}),
            "index_status": "ready" if stats.get("active_index_version") else (last_job.get("status") or "not_built"),
            "error": stats.get("error_code"),
            "index_version": stats.get("active_index_version") or "unbuilt",
            "knowledge_source": "configured_local_roots",
            "last_build_latency_ms": int((last_job or {}).get("duration_ms") or 0),
            "embedding_count": int((last_job or {}).get("embedding_count") or 0),
            "cache_hits": int((last_job or {}).get("cache_hits") or 0),
            "failed_docs_count": stats.get("failed_source_count", 0),
            "empty_docs_count": 0,
            "fts5_available": stats.get("fts5_available", False),
            "dense_available": stats.get("dense_available", False),
            "embedding_model": stats.get("embedding_model"),
            "last_job": stats.get("last_job"),
        }


_default_index_store: RAGIndexStore | None = None


def get_default_index_store() -> RAGIndexStore:
    global _default_index_store
    if _default_index_store is None:
        _default_index_store = RAGIndexStore()
    return _default_index_store


def get_default_index_manager() -> RAGIndexManager:
    return get_default_index_store().manager


def close_default_index_manager() -> None:
    """Release the process-wide RAG manager during application shutdown."""
    global _default_index_store
    if _default_index_store is not None:
        _default_index_store.manager.close()
        _default_index_store = None
