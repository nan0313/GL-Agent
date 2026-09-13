from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class RAGLedgerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = ""
    retrieval_id: str = Field(default_factory=lambda: f"retrieval_{uuid4().hex}")
    query_chars: int = 0
    query_language: str = "unknown"
    retrieval_mode: str = "lexical"
    retrieval_method: str = "keyword_weighted_rerank"
    lexical_invoked: bool = False
    dense_invoked: bool = False
    reranker_invoked: bool = False
    candidate_count: int = 0
    fused_count: int = 0
    returned_count: int = 0
    top_score: float | None = None
    latency_ms: int = 0
    index_version: str = "unknown"
    embedding_model: str | None = None
    reranker_model: str | None = None
    status: str = "failed"
    generation_invoked: bool = False
    generation_status: str | None = None
    citation_status: str | None = None
    answerability_status: str | None = None
    decision_reason: str | None = None
    threshold_version: str | None = None
    fallback_used: bool = False
    error_code: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RAGLedger:
    """Small in-memory ledger; response metadata remains the export boundary."""

    def __init__(self) -> None:
        self._items: list[RAGLedgerRecord] = []
        self._lock = RLock()

    def add(self, item: RAGLedgerRecord) -> None:
        with self._lock:
            self._items.append(item)
            self._items = self._items[-500:]

    def list(self, request_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = self._items if not request_id else [item for item in self._items if item.request_id == request_id]
            return [item.model_dump(exclude_none=False) for item in items[-100:]]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


default_rag_ledger = RAGLedger()
