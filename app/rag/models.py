"""Persistent RAG ingestion models.

The models in this module are deliberately independent from the existing
response models.  They describe the ingestion/index boundary and never carry
absolute paths across that boundary.
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SourceType = Literal["markdown", "txt", "pdf", "docx", "html", "csv"]
IngestionStatus = Literal["registered", "parsed", "empty", "failed", "deleted"]
SourceLifecycle = Literal["staging", "validating", "validated", "active", "disabled", "rejected", "deleted", "error"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_type: SourceType
    display_name: str
    relative_location: str
    content_hash: str
    file_size: int = Field(ge=0)
    modified_at: str
    ingestion_status: IngestionStatus = "registered"
    parser_version: str = "unknown"
    chunker_version: str = "unknown"
    index_version: str = "unbuilt"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    error_code: str | None = None
    duplicate_of_source_id: str | None = None
    language: str = "unknown"
    description: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_status: SourceLifecycle = "active"
    parser_status: str = "pending"
    index_status: str = "not_indexed"
    document_count: int = Field(default=0, ge=0)
    section_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    last_validated_at: str | None = None
    last_indexed_at: str | None = None
    provenance: str = Field(default="configured_local_root", max_length=100)


class ParsedSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    section_id: str
    heading: str
    heading_level: int = 0
    text: str
    page_number: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    section_path: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    document_id: str
    source_id: str
    title: str
    language: str = "unknown"
    sections: list[ParsedSection] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    parser_name: str
    parser_version: str
    status: Literal["parsed", "empty", "failed"] = "parsed"
    error_code: str | None = None


class ChunkRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    chunk_id: str
    document_id: str
    source_id: str
    section_id: str
    title: str
    section_path: list[str] = Field(default_factory=list)
    content: str
    content_hash: str
    char_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    page_start: int | None = None
    page_end: int | None = None
    ordinal: int = Field(ge=0)
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunker_version: str = "structured_v1"


class MetadataFilter(BaseModel):
    """Allow-listed filters used by both lexical and dense retrieval."""

    model_config = ConfigDict(extra="forbid")

    source_type: str | None = None
    document_id: str | None = None
    source_id: str | None = None
    language: str | None = None
    section: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    tags: list[str] = Field(default_factory=list)
    version: str | None = None


class IndexJobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    mode: Literal["full", "incremental", "validate"]
    status: Literal["queued", "running", "success", "failed", "canceled"]
    added: int = 0
    modified: int = 0
    deleted: int = 0
    unchanged: int = 0
    parsed: int = 0
    failed: int = 0
    chunk_count: int = 0
    embedding_count: int = 0
    cache_hits: int = 0
    duration_ms: int = 0
    version: str | None = None
    error_code: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
