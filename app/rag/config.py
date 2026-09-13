"""Small, process-local configuration for the local RAG pipeline."""

RAG_INDEX_VERSION_PREFIX = "local_md"
RAG_TOP_K = 3
RAG_MAX_CANDIDATES = 24
RAG_MAX_PER_DOCUMENT = 2
RAG_MIN_RELEVANCE_SCORE = 2.0
RAG_CHUNK_MAX_CHARS = 800
RAG_CHUNK_OVERLAP_CHARS = 100
RAG_EVIDENCE_MAX_CHARS = 1200
RAG_PROMPT_MAX_EVIDENCES = 5
RAG_PROMPT_MAX_CHARS = 6000
RAG_RETRIEVAL_METHOD = "keyword_weighted_rerank"
RAG_NO_ANSWER_POLICY_VERSION = "retrieval_decision_v1"
RAG_GENERIC_QUERY_TERMS = frozenset({
    "什么", "哪些", "是否", "当前", "系统", "知识库", "查询", "数据", "对象", "模型", "功能", "信息", "操作", "内容", "规则", "通常", "可以", "请问", "帮我", "告诉我", "有没有", "有没", "为什么", "如何", "说明", "查看", "分析", "介绍",
})
RAG_GENERIC_QUERY_TERMS = RAG_GENERIC_QUERY_TERMS | frozenset({"根据", "解释", "含义"})


from functools import lru_cache
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RAGConfig(BaseModel):
    """RAG-only configuration; it never reads the LLM secret config."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    knowledge_roots: list[str] = Field(default_factory=lambda: [str(PROJECT_ROOT / "docs" / "knowledge")])
    database_path: str = str(PROJECT_ROOT / "data" / "rag" / "rag.sqlite3")
    index_path: str = str(PROJECT_ROOT / "data" / "rag_index")
    backup_path: str = str(PROJECT_ROOT / "data" / "rag_backups")
    allowed_file_types: list[str] = Field(default_factory=lambda: [".md", ".markdown", ".txt", ".pdf", ".docx", ".html", ".htm", ".csv"])
    max_file_size: int = 10 * 1024 * 1024
    chunk_target_chars: int = 1000
    chunk_max_chars: int = 1600
    chunk_overlap_chars: int = 140
    chunk_min_chars: int = 120
    lexical_enabled: bool = True
    dense_enabled: bool = False
    embedding_provider: str = "disabled"
    embedding_model: str = ""
    embedding_device: str = "cpu"
    embedding_batch_size: int = 16
    vector_backend: str = "python_cosine_fallback"
    hnsw_space: str = "cosine"
    hnsw_m: int = 16
    hnsw_ef_construction: int = 200
    hnsw_ef_search: int = 64
    hnsw_max_elements: int = 0
    hnsw_resize_factor: float = 2.0
    hybrid_enabled: bool = True
    rrf_k: int = 60
    dense_min_similarity: float = 0.05
    candidate_limit: int = 24
    rerank_enabled: bool = True
    rerank_provider: str = "lightweight"
    rerank_model: str = ""
    rerank_candidate_limit: int = 20
    top_k: int = RAG_TOP_K
    per_document_limit: int = RAG_MAX_PER_DOCUMENT
    minimum_relevance: float = RAG_MIN_RELEVANCE_SCORE
    max_evidence_chars: int = RAG_EVIDENCE_MAX_CHARS
    max_total_evidence_chars: int = RAG_PROMPT_MAX_CHARS


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {name}; expected an integer") from exc


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {name}; expected a number") from exc


@lru_cache(maxsize=1)
def get_rag_config() -> RAGConfig:
    roots = _env_text("RAG_KNOWLEDGE_ROOTS", "")
    knowledge_roots = [item.strip() for item in roots.split(os.pathsep) if item.strip()] if roots else [str(PROJECT_ROOT / "docs" / "knowledge")]
    allowed = _env_text("RAG_ALLOWED_FILE_TYPES", ".md,.markdown,.txt,.pdf,.docx,.html,.htm,.csv")
    allowed_types = []
    for item in allowed.split(","):
        item = item.strip().lower()
        if item:
            allowed_types.append(item if item.startswith(".") else f".{item}")
    return RAGConfig(
        enabled=_env_bool("RAG_ENABLED", True),
        knowledge_roots=knowledge_roots,
        database_path=_env_text("RAG_DATABASE_PATH", str(PROJECT_ROOT / "data" / "rag" / "rag.sqlite3")),
        index_path=_env_text("RAG_INDEX_PATH", str(PROJECT_ROOT / "data" / "rag_index")),
        backup_path=_env_text("RAG_BACKUP_PATH", str(PROJECT_ROOT / "data" / "rag_backups")),
        allowed_file_types=allowed_types,
        max_file_size=_env_int("RAG_MAX_FILE_SIZE", 10 * 1024 * 1024),
        chunk_target_chars=_env_int("RAG_CHUNK_TARGET_CHARS", 1000),
        chunk_max_chars=_env_int("RAG_CHUNK_MAX_CHARS", 1600),
        chunk_overlap_chars=_env_int("RAG_CHUNK_OVERLAP_CHARS", 140),
        chunk_min_chars=_env_int("RAG_CHUNK_MIN_CHARS", 120),
        lexical_enabled=_env_bool("RAG_LEXICAL_ENABLED", True),
        dense_enabled=_env_bool("RAG_DENSE_ENABLED", False),
        embedding_provider=_env_text("RAG_EMBEDDING_PROVIDER", "disabled").lower(),
        embedding_model=_env_text("RAG_EMBEDDING_MODEL", ""),
        embedding_device=_env_text("RAG_EMBEDDING_DEVICE", "cpu"),
        embedding_batch_size=_env_int("RAG_EMBEDDING_BATCH_SIZE", 16),
        vector_backend=_env_text("RAG_VECTOR_BACKEND", "python_cosine_fallback").lower(),
        hnsw_space=_env_text("RAG_HNSW_SPACE", "cosine").lower(),
        hnsw_m=_env_int("RAG_HNSW_M", 16),
        hnsw_ef_construction=_env_int("RAG_HNSW_EF_CONSTRUCTION", 200),
        hnsw_ef_search=_env_int("RAG_HNSW_EF_SEARCH", 64),
        hnsw_max_elements=_env_int("RAG_HNSW_MAX_ELEMENTS", 0),
        hnsw_resize_factor=_env_float("RAG_HNSW_RESIZE_FACTOR", 2.0),
        hybrid_enabled=_env_bool("RAG_HYBRID_ENABLED", True),
        rrf_k=_env_int("RAG_RRF_K", 60),
        dense_min_similarity=_env_float("RAG_DENSE_MIN_SIMILARITY", 0.05),
        candidate_limit=_env_int("RAG_CANDIDATE_LIMIT", 24),
        rerank_enabled=_env_bool("RAG_RERANK_ENABLED", True),
        rerank_provider=_env_text("RAG_RERANK_PROVIDER", "lightweight").lower(),
        rerank_model=_env_text("RAG_RERANK_MODEL", ""),
        rerank_candidate_limit=_env_int("RAG_RERANK_CANDIDATE_LIMIT", 20),
        top_k=_env_int("RAG_TOP_K", RAG_TOP_K),
        per_document_limit=_env_int("RAG_PER_DOCUMENT_LIMIT", RAG_MAX_PER_DOCUMENT),
        minimum_relevance=_env_float("RAG_MINIMUM_RELEVANCE", RAG_MIN_RELEVANCE_SCORE),
        max_evidence_chars=_env_int("RAG_MAX_EVIDENCE_CHARS", RAG_EVIDENCE_MAX_CHARS),
        max_total_evidence_chars=_env_int("RAG_MAX_TOTAL_EVIDENCE_CHARS", RAG_PROMPT_MAX_CHARS),
    )


def rag_config_public_dict(config: RAGConfig | None = None) -> dict[str, Any]:
    value = config or get_rag_config()
    data = value.model_dump()
    data["knowledge_roots"] = [Path(item).name for item in value.knowledge_roots]
    data["database_path"] = "configured"
    data["index_path"] = "configured"
    data["backup_path"] = "configured"
    return data
