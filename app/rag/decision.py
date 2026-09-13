"""Deterministic answerability decisions for safe RAG refusal."""

import re
from typing import Sequence

from app.rag.config import RAG_GENERIC_QUERY_TERMS, RAG_NO_ANSWER_POLICY_VERSION
from app.rag.query import normalize_query, tokenize_query
from app.rag.schema import RAGHit, RetrievedEvidence, RetrievalDecision


_SOURCE_DEICTIC_RE = re.compile(r"(?:\u672c\u6807\u51c6|\u8be5\u6807\u51c6|\u6b64\u6807\u51c6|\u672c\u89c4\u8303|\u8be5\u89c4\u8303|\u6b64\u89c4\u8303)")
_EXPLICIT_STANDARD_RE = re.compile(
    r"(?:Q\s*/\s*GDW|QGDW|DL\s*/\s*T|NB\s*/\s*T|GB\s*/\s*T|GB|JGJ|CJJ|ISO|IEC)\s*[-\uff0f/A-Z0-9.\u2014\u2013]*\d",
    re.IGNORECASE,
)


_CJK_FUNCTION_CHARS = frozenset("的是有了为与和在这一个其我你他她它从到对将被并及而或也都更很能可会要请把让给于以按中问什么功")


def meaningful_query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in tokenize_query(query):
        value = token.strip().lower()
        if not value or value in RAG_GENERIC_QUERY_TERMS:
            continue
        if len(value) == 1 and "\u4e00" <= value <= "\u9fff":
            continue
        if len(value) == 2 and all("\u4e00" <= char <= "\u9fff" for char in value) and any(char in _CJK_FUNCTION_CHARS for char in value):
            continue
        if value not in terms:
            terms.append(value)
    return terms


def evaluate_retrieval_decision(
    query: str,
    hits: Sequence[RAGHit],
    evidences: Sequence[RetrievedEvidence] = (),
    *,
    minimum_relevance: float,
    dense_min_similarity: float,
) -> RetrievalDecision:
    normalized = normalize_query(query)
    if not normalized:
        return _decision("empty", "EMPTY_QUERY", None, None, 0.0, 0.0, 0.0, 0, 0.0)
    if not hits:
        return _decision("empty", "NO_CANDIDATES", None, None, 0.0, 0.0, 0.0, 0, 0.0)

    lexical_scores = [float(hit.metadata["lexical_score"]) for hit in hits if hit.metadata.get("lexical_score") is not None]
    dense_scores = [float(hit.metadata["dense_score"]) for hit in hits if hit.metadata.get("dense_score") is not None]
    fusion_scores = [float(hit.metadata.get("fusion_score") or 0.0) for hit in hits]
    ordered_scores = [float(hit.score) for hit in hits]
    lexical_top = max(lexical_scores, default=None)
    dense_top = max(dense_scores, default=None)
    fusion_margin = _margin(sorted(fusion_scores, reverse=True))
    top_margin = _margin(sorted(ordered_scores, reverse=True))
    terms = meaningful_query_terms(normalized)
    haystack = " ".join(
        f"{hit.chunk.title} {hit.chunk.section or ''} {hit.chunk.content}"
        for hit in hits[:10]
    ).lower()
    matched_terms = [term for term in terms if term in haystack]
    coverage = len(matched_terms) / max(1, len(terms)) if terms else 0.0
    evidence_count = len(evidences)
    diversity = len({item.document_id for item in evidences}) / max(1, evidence_count)

    # A bare source deictic ("this standard/specification") cannot identify a
    # source in a global, multi-standard index. Keyword overlap must not allow
    # a different standard to supply a plausible-looking answer.
    if _SOURCE_DEICTIC_RE.search(normalized) and not _EXPLICIT_STANDARD_RE.search(normalized):
        return _decision(
            "insufficient_evidence",
            "UNRESOLVED_SOURCE_REFERENCE",
            lexical_top,
            dense_top,
            fusion_margin,
            top_margin,
            coverage,
            evidence_count,
            diversity,
        )

    if not evidences:
        fallback_top = max(ordered_scores, default=0.0)
        reason = "LOW_LEXICAL" if not lexical_scores and not dense_scores and fallback_top < minimum_relevance else "NO_EVIDENCE_AFTER_FILTER"
        return _decision("insufficient_evidence", reason, lexical_top, dense_top, fusion_margin, top_margin, coverage, evidence_count, diversity)

    if not terms:
        return _decision("insufficient_evidence", "GENERIC_TERM_ONLY", lexical_top, dense_top, fusion_margin, top_margin, coverage, evidence_count, diversity)
    if not matched_terms:
        return _decision("insufficient_evidence", "ENTITY_NOT_FOUND", lexical_top, dense_top, fusion_margin, top_margin, coverage, evidence_count, diversity)
    if coverage < 0.5:
        return _decision("insufficient_evidence", "LOW_QUERY_COVERAGE", lexical_top, dense_top, fusion_margin, top_margin, coverage, evidence_count, diversity)

    has_lexical = bool(lexical_scores)
    has_dense = bool(dense_scores)
    lexical_ok = lexical_top is None or lexical_top >= minimum_relevance
    dense_ok = dense_top is None or dense_top >= dense_min_similarity
    if not lexical_ok and not dense_ok:
        return _decision("insufficient_evidence", "LOW_LEXICAL_AND_DENSE", lexical_top, dense_top, fusion_margin, top_margin, coverage, evidence_count, diversity)
    if has_dense and not dense_ok and not has_lexical:
        return _decision("insufficient_evidence", "LOW_DENSE", lexical_top, dense_top, fusion_margin, top_margin, coverage, evidence_count, diversity)
    if has_lexical and not lexical_ok and not has_dense:
        return _decision("insufficient_evidence", "LOW_LEXICAL", lexical_top, dense_top, fusion_margin, top_margin, coverage, evidence_count, diversity)

    # RRF scores from a lexical-only result set are intentionally close to one
    # another. Treat hybrid retrieval as ambiguous only when the two retrieval
    # families have no overlapping candidate in the returned set and the score
    # margin is also weak; close scores alone are not disagreement.
    has_agreement = any(
        hit.metadata.get("lexical_rank") is not None and hit.metadata.get("dense_rank") is not None
        for hit in hits[:5]
    )
    if has_dense and evidence_count > 1 and not has_agreement and fusion_margin <= 0.001 and coverage < 0.75:
        return _decision("ambiguous", "CONFLICTING_RETRIEVAL", lexical_top, dense_top, fusion_margin, top_margin, coverage, evidence_count, diversity)
    return _decision("answerable", "SUPPORTED_TERM_MATCH", lexical_top, dense_top, fusion_margin, top_margin, coverage, evidence_count, diversity)


def _decision(status: str, reason: str, lexical_top: float | None, dense_top: float | None, fusion_margin: float, top_margin: float, coverage: float, evidence_count: int, diversity: float) -> RetrievalDecision:
    return RetrievalDecision(
        answerability_status=status,
        decision_reason=reason,
        lexical_top_score=lexical_top,
        dense_top_similarity=dense_top,
        fusion_margin=fusion_margin,
        top1_top2_margin=top_margin,
        query_coverage=round(coverage, 6),
        evidence_count=evidence_count,
        document_diversity=round(diversity, 6),
        threshold_version=RAG_NO_ANSWER_POLICY_VERSION,
    )


def _margin(values: list[float]) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return round(values[0] - values[1], 8)
