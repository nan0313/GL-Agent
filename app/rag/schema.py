import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.rag.config import RAG_EVIDENCE_MAX_CHARS


DocumentStatus = Literal["parsed", "empty", "failed"]
RetrievalStatus = Literal["success", "empty", "failed"]


def stable_evidence_id(document_id: str, chunk_id: str) -> str:
    digest = hashlib.sha1(f"{document_id}:{chunk_id}".encode("utf-8")).hexdigest()[:12]
    return f"ev_{digest}"


class RAGDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    doc_id: str
    source_path: str
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: DocumentStatus = "parsed"
    document_id: str | None = None
    source_id: str | None = None
    source_type: str = "markdown"
    relative_location: str | None = None
    content_hash: str | None = None
    parser_name: str | None = None
    parser_version: str | None = None


class RAGChunk(BaseModel):
    model_config = ConfigDict(extra="allow")

    chunk_id: str
    doc_id: str
    source_path: str
    title: str
    section: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    char_count: int = 0
    token_count: int | None = None
    document_id: str | None = None
    source_id: str | None = None
    source_type: str = "markdown"
    relative_location: str | None = None
    section_id: str | None = None
    section_path: list[str] = Field(default_factory=list)
    content_hash: str | None = None
    estimated_tokens: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    ordinal: int = 0
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None
    chunker_version: str | None = None


class RAGQuery(BaseModel):
    model_config = ConfigDict(extra="allow")

    query: str
    top_k: int = 3
    filters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGHit(BaseModel):
    model_config = ConfigDict(extra="allow")

    chunk: RAGChunk
    score: float
    match_reason: str
    rank: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedEvidence(BaseModel):
    """Safe, serialisable evidence returned by the retrieval boundary."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    document_id: str
    chunk_id: str
    title: str
    source_type: str = "markdown"
    source_name: str
    content: str = Field(max_length=RAG_EVIDENCE_MAX_CHARS)
    score: float = Field(description="Relative retrieval/rerank score used for ordering; not a probability.")
    rank: int
    section_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    retrieval_method: str = "lexical"
    score_summary: dict[str, float | int | str | None] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def source(self) -> str:
        """Compatibility name used by the existing response adapter."""
        return self.source_name

    @property
    def snippet(self) -> str:
        """Compatibility name used by the existing response adapter."""
        return self.content


class RetrievalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answerability_status: Literal["answerable", "insufficient_evidence", "ambiguous", "empty", "index_unavailable"]
    decision_reason: str
    lexical_top_score: float | None = None
    dense_top_similarity: float | None = None
    fusion_margin: float = 0.0
    top1_top2_margin: float = 0.0
    query_coverage: float = 0.0
    evidence_count: int = 0
    document_diversity: float = 0.0
    threshold_version: str


class RAGEvidence(RetrievedEvidence):
    """Backward-compatible name for the public RAG evidence model."""


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    normalized_query: str
    evidences: list[RetrievedEvidence] = Field(default_factory=list)
    retrieval_method: str
    total_candidates: int = 0
    returned_count: int = 0
    top_score: float | None = None
    latency_ms: int = 0
    index_version: str
    status: RetrievalStatus
    error_code: str | None = None
    decision: RetrievalDecision | None = None


class RAGPipelineResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    answer: str
    evidence: list[RAGEvidence] = Field(default_factory=list)
    retrieved_hits: list[RAGHit] = Field(default_factory=list)
    provider: str = "local_rag_v2"
    retrieval: RetrievalResult | None = None
    citations: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
