import re
from pathlib import Path

from app.rag.config import RAG_MAX_CANDIDATES, RAG_TOP_K, RAG_RETRIEVAL_METHOD
from app.rag.index_store import RAGIndexStore
from app.rag.index_store import get_default_index_store
from app.rag.query import normalize_query, structural_query_terms
from app.rag.schema import RAGHit, RAGQuery, stable_evidence_id
from app.rag.text_splitter import KnowledgeChunk


class RetrievedChunk(RAGHit):
    @property
    def snippet(self) -> str:
        return str(self.metadata.get("snippet", ""))

    def to_evidence(self) -> dict:
        safe_metadata = {
            key: self.metadata.get(key)
            for key in ("retriever", "rerank_applied", "match_reason")
            if key in self.metadata
        }
        document_id = str(getattr(self.chunk, "document_id", None) or self.chunk.doc_id)
        return {
            "evidence_id": stable_evidence_id(document_id, self.chunk.chunk_id),
            "document_id": document_id,
            "chunk_id": self.chunk.chunk_id,
            "source_type": str(getattr(self.chunk, "source_type", None) or "markdown"),
            "source_name": Path(self.chunk.source_path).name,
            "source": Path(self.chunk.source_path).name,
            "title": self.chunk.title,
            "content": self.snippet,
            "snippet": self.snippet,
            "score": self.score,
            "rank": self.rank,
            "match_reason": self.match_reason,
            "section_path": list(getattr(self.chunk, "section_path", []) or []),
            "page_start": getattr(self.chunk, "page_start", None),
            "page_end": getattr(self.chunk, "page_end", None),
            "retrieval_method": self.metadata.get("retrieval_method", self.metadata.get("retriever", "lexical")),
            "score_summary": {
                key: self.metadata.get(key)
                for key in ("lexical_score", "fts5_bm25_rank", "dense_score", "fusion_score", "lexical_rank", "dense_rank")
                if self.metadata.get(key) is not None
            },
            "metadata": safe_metadata,
        }


class LocalRetriever:
    def __init__(
        self,
        knowledge_dir: Path | None = None,
        index_store: RAGIndexStore | None = None,
    ) -> None:
        self.index_store = index_store or (
            RAGIndexStore(knowledge_dir=knowledge_dir)
            if knowledge_dir is not None
            else get_default_index_store()
        )

    def retrieve(
        self,
        query: str,
        top_k: int = RAG_TOP_K,
        *,
        candidate_k: int | None = None,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[RetrievedChunk]:
        normalized_query = normalize_query(query)
        rag_query = RAGQuery(query=normalized_query, top_k=max(0, int(top_k or 0)), filters=metadata_filter or {})
        manager = getattr(self.index_store, "manager", None)
        if manager is not None:
            hits = manager.search(
                normalized_query,
                top_k=max(0, int(candidate_k if candidate_k is not None else rag_query.top_k)),
                candidate_limit=max(1, int(candidate_k if candidate_k is not None else RAG_MAX_CANDIDATES)),
                metadata_filter=rag_query.filters,
            )
            retrieved = [
                RetrievedChunk(
                    chunk=hit.chunk,
                    score=hit.score,
                    match_reason=hit.match_reason,
                    rank=hit.rank,
                    metadata={**hit.metadata, "snippet": _snippet(hit.chunk.content, _tokenize(normalized_query)), "retriever": hit.metadata.get("retrieval_method", RAG_RETRIEVAL_METHOD)},
                )
                for hit in hits
            ]
            structural = _structural_recall(
                normalized_query,
                self.index_store.list_chunks(),
                limit=max(0, int(candidate_k if candidate_k is not None else rag_query.top_k)),
                metadata_filter=rag_query.filters,
            )
            merged: dict[str, RetrievedChunk] = {hit.chunk.chunk_id: hit for hit in retrieved}
            for hit in structural:
                previous = merged.get(hit.chunk.chunk_id)
                if previous is None or hit.score > previous.score:
                    merged[hit.chunk.chunk_id] = hit
                elif previous is not None:
                    merged[hit.chunk.chunk_id] = previous.model_copy(
                        update={"metadata": {**hit.metadata, **previous.metadata, "structural_recall": True}}
                    )
            ordered = sorted(
                merged.values(),
                key=lambda item: (-round(item.score, 6), item.chunk.doc_id, item.chunk.ordinal, item.chunk.chunk_id),
            )
            limit = max(0, int(candidate_k if candidate_k is not None else rag_query.top_k))
            return [hit.model_copy(update={"rank": index + 1}) for index, hit in enumerate(ordered[:limit])]
        tokens = _tokenize(rag_query.query)
        if not tokens:
            return []

        scored: list[RetrievedChunk] = []
        for chunk in self.index_store.list_chunks():
            if not _matches_filter(chunk.metadata, rag_query.filters):
                continue
            score, reasons = _score(tokens, rag_query.query, chunk)
            if score <= 0:
                continue
            scored.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=score,
                    match_reason="; ".join(reasons),
                    metadata={
                        "snippet": _snippet(chunk.content, tokens),
                        "query_terms": tokens,
                        "retriever": RAG_RETRIEVAL_METHOD,
                        "source_type": "markdown",
                    },
                )
            )

        scored.sort(key=lambda item: (-round(item.score, 6), item.chunk.doc_id, item.chunk.chunk_id))
        limit = max(0, int(candidate_k if candidate_k is not None else rag_query.top_k))
        return [
            hit.model_copy(update={"rank": index + 1})
            for index, hit in enumerate(scored[:limit])
        ]

    @property
    def chunks(self) -> list[KnowledgeChunk]:
        return self.index_store.list_chunks()

    @property
    def documents_count(self) -> int:
        return int(self.index_store.get_stats().get("docs_count", 0))


def _tokenize(text: str) -> list[str]:
    normalized = _normalize_text(text)
    words = re.findall(r"[a-zA-Z0-9_]+", normalized)
    chinese_terms = [
        "保护定值",
        "馈线",
        "变电站",
        "开关",
        "实时数据",
        "gim",
        "模型",
        "安全",
        "规程",
        "远程控制",
        "分闸",
        "合闸",
        "停电",
        "送电",
        "倒闸",
        "知识库",
        "trace",
        "锚点",
        "供电路径",
        "风险评价",
    ]
    return list(dict.fromkeys(words + [term for term in chinese_terms if term in normalized]))


def _matches_filter(metadata: dict[str, object], filters: dict[str, object]) -> bool:
    if not filters:
        return True
    return all(metadata.get(key) == value for key, value in filters.items())


def _structural_recall(
    query: str,
    chunks: list[KnowledgeChunk],
    *,
    limit: int,
    metadata_filter: dict[str, object],
) -> list[RetrievedChunk]:
    terms = structural_query_terms(query)
    if not terms or limit <= 0:
        return []
    recalled: list[RetrievedChunk] = []
    for chunk in chunks:
        if not _matches_filter(chunk.metadata, metadata_filter):
            continue
        title = re.sub(r"\s+", "", str(chunk.title or "")).lower()
        section = re.sub(r"\s+", "", " / ".join(chunk.section_path or [chunk.section or ""])).lower()
        content = re.sub(r"\s+", "", str(chunk.content or "")).lower()
        matched = [term for term in terms if term in content or term in section or term in title]
        if not matched:
            continue
        reference_bonus = sum(
            4.0 if re.search(r"(?i)(?:[a-d]\.|\d+\.)", term) else 0.75
            for term in matched
            if term.startswith(("表", "附录", "§"))
        )
        technical = min(3, sum(bool(re.search(r"[a-z0-9_*.<]", term)) for term in matched))
        coverage = len(matched) / max(1, len(terms))
        score = 4.0 + reference_bonus + technical * 0.8 + coverage * 4.0 + _source_priority(chunk.source_path)
        recalled.append(
            RetrievedChunk(
                chunk=chunk,
                score=round(score, 6),
                match_reason="structural_exact_recall",
                metadata={
                    "snippet": _snippet(chunk.content, matched, max_length=360),
                    "retriever": "structural_exact_recall",
                    "retrieval_method": "structural_exact_recall",
                    "structural_recall": True,
                    "structural_terms": matched,
                    "structural_coverage": round(coverage, 6),
                },
            )
        )
    recalled.sort(key=lambda item: (-item.score, item.chunk.doc_id, item.chunk.ordinal, item.chunk.chunk_id))
    # Preserve several exact blocks for multi-table comparison questions while
    # keeping the pass bounded for large knowledge bases.
    return recalled[: min(limit, max(8, len(terms) * 2))]


def _source_priority(source_path: str) -> float:
    name = Path(str(source_path)).name.lower()
    if "gdw 11809—2023" in name or "q/gdw 11809—2023" in name:
        return 4.0
    if "11809—2018" in name:
        return -1.0
    return 0.0


def _score(tokens: list[str], query: str, chunk: KnowledgeChunk) -> tuple[float, list[str]]:
    content = _normalize_text(chunk.content)
    title = _normalize_text(chunk.title or "")
    section = _normalize_text(chunk.section or "")
    normalized_query = _normalize_text(query).strip()
    score = 0.0
    reasons: list[str] = []

    if normalized_query and normalized_query in content:
        score += 5.0
        reasons.append("full_query_in_content")
    if normalized_query and normalized_query in title:
        score += 4.0
        reasons.append("full_query_in_title")
    if normalized_query and normalized_query in section:
        score += 3.0
        reasons.append("full_query_in_section")

    for token in tokens:
        term = token.lower()
        content_count = content.count(term)
        title_count = title.count(term)
        section_count = section.count(term)
        if title_count:
            score += 3.0 * title_count
            reasons.append(f"title:{token}")
        if section_count:
            score += 2.0 * section_count
            reasons.append(f"section:{token}")
        if content_count:
            score += 1.0 + min(content_count - 1, 4) * 0.25
            reasons.append(f"content:{token}")
        if token in {"保护定值", "规程", "安全", "远程控制", "馈线", "变电站"} and (
            content_count or title_count or section_count
        ):
            score += 1.5
            reasons.append(f"domain_term:{token}")
    return score, list(dict.fromkeys(reasons))


def _snippet(content: str, tokens: list[str], max_length: int = 180) -> str:
    normalized = _normalize_text(content)
    start = 0
    for token in tokens:
        index = normalized.find(token.lower())
        if index >= 0:
            start = max(0, index - 40)
            break
    snippet = content[start : start + max_length].strip()
    return snippet.replace("\n", " ")


def _normalize_text(text: str) -> str:
    normalized = (text or "").lower()
    replacements = {
        "淇濇姢瀹氬€?": "保护定值",
        "淇濇姢瀹氬€": "保护定值",
        "棣堢嚎": "馈线",
        "鍙樼數绔?": "变电站",
        "寮€鍏?": "开关",
        "瀹炴椂鏁版嵁": "实时数据",
        "妯″瀷": "模型",
        "瀹夊叏": "安全",
        "瑙勭▼": "规程",
        "杩滅▼鎺у埗": "远程控制",
        "鐭ヨ瘑搴?": "知识库",
        "閿氱偣": "锚点",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old.lower(), new)
    return normalized
