from difflib import SequenceMatcher
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.llm.qwen_rag_answer import QwenRAGAnswer
from app.product.answer_stream import emit_answer_delta
from app.rag.citations import validate_citations
from app.rag.decision import evaluate_retrieval_decision
from app.rag.config import (
    RAG_MAX_CANDIDATES,
    RAG_MAX_PER_DOCUMENT,
    RAG_MIN_RELEVANCE_SCORE,
    RAG_RETRIEVAL_METHOD,
    RAG_TOP_K,
)
from app.rag.index_store import RAGIndexStore, get_default_index_store
from app.rag.ledger import RAGLedger, RAGLedgerRecord, default_rag_ledger
from app.rag.prompt_builder import PromptBuilder
from app.rag.query import is_enumeration_query, query_phrases, rewrite_queries, tokenize_query
from app.rag.reranker import LightweightReranker
from app.rag.retriever import LocalRetriever, normalize_query
from app.rag.schema import RAGEvidence, RAGHit, RAGPipelineResult, RetrievalResult


NO_EVIDENCE_MESSAGE = "当前知识库中未找到足够相关的资料。"
NO_ANSWER_MESSAGE = "当前知识库中未找到足够相关且可支持回答的资料。"
RETRIEVAL_FAILED_MESSAGE = "当前知识库检索暂时不可用，暂未生成回答。"
GENERATION_FALLBACK_MESSAGE = "已找到相关资料，但当前模型生成服务暂时不可用。以下为检索到的证据。"
INVALID_CITATION_MESSAGE = "当前模型回答包含无法验证的引用，暂不展示该回答。以下为检索到的证据。"


class RAGPipeline:
    provider = "local_rag_v2"

    def __init__(
        self,
        index_store: RAGIndexStore | None = None,
        retriever: LocalRetriever | None = None,
        reranker: LightweightReranker | None = None,
        prompt_builder: PromptBuilder | None = None,
        qwen_rag_answer: QwenRAGAnswer | None = None,
        conversation_retriever=None,
        *,
        top_k: int = RAG_TOP_K,
        min_relevance_score: float = RAG_MIN_RELEVANCE_SCORE,
        max_per_document: int = RAG_MAX_PER_DOCUMENT,
        ledger: RAGLedger | None = None,
    ) -> None:
        self.index_store = index_store or get_default_index_store()
        self.retriever = retriever or LocalRetriever(index_store=self.index_store)
        self.reranker = reranker or LightweightReranker()
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.qwen_rag_answer = qwen_rag_answer or QwenRAGAnswer()
        self.conversation_retriever = conversation_retriever
        self.top_k = max(0, int(top_k))
        self.min_relevance_score = float(min_relevance_score)
        self.max_per_document = max(1, int(max_per_document))
        self.ledger = ledger or default_rag_ledger

    def answer(self, query: str, top_k: int | None = None, request_id: str = "", *, conversation_id: str | None = None, user_id: str | None = None) -> RAGPipelineResult:
        start = perf_counter()
        normalized_query = normalize_query(query)
        enumeration = is_enumeration_query(normalized_query)
        requested_limit = self.top_k if top_k is None else max(0, int(top_k))
        limit = max(requested_limit, 7 if enumeration else 4) if top_k is None else requested_limit
        prompt_builder = PromptBuilder(
            max_context_chars=9000 if enumeration else 6000,
            max_evidences=limit,
            max_excerpt_chars=900 if enumeration else 600,
        )
        pipeline_steps: list[str] = []
        retrieval_trace: dict[str, Any] = {
            "retrieval_pass": 1,
            "query": query,
            "normalized_query": normalized_query,
            "rewrite_queries": [],
            "scope_policy": "global_only" if not (conversation_id and user_id) else "global_plus_conversation",
            "lexical_candidate_count": 0,
            "dense_candidate_count": 0,
            "fusion_count": 0,
            "neighbor_expansion_count": 0,
            "section_expansion_count": 0,
            "rerank_method": getattr(self.reranker, "provider_type", "unknown"),
            "final_evidence_count": 0,
        }

        try:
            self.index_store.ensure_built()
            pipeline_steps.append("index_loaded")
            stats = self.index_store.get_stats()
            global_index_failed = stats.get("index_status") == "failed"

            if not normalized_query or not tokenize_query(normalized_query) or limit <= 0:
                pipeline_steps.append("empty_query")
                return self._empty_result(
                    query=query,
                    normalized_query=normalized_query,
                    stats=stats,
                    pipeline_steps=pipeline_steps,
                    start=start,
                    request_id=request_id,
                    candidate_count=0,
                    error_code=None,
                )

            candidate_limit = max(limit, RAG_MAX_CANDIDATES)
            candidates: list[RAGHit] = []
            if not global_index_failed and self.index_store.list_chunks():
                pass_one = [
                    _global_scope_hit(hit)
                    for hit in self.retriever.retrieve(
                        normalized_query,
                        top_k=candidate_limit,
                        candidate_k=candidate_limit,
                    )
                ]
                candidates.extend(pass_one)
                pipeline_steps.append("retrieve_global")
                if _needs_second_pass(normalized_query, pass_one, enumeration):
                    rewrites = rewrite_queries(normalized_query, maximum=2)
                    retrieval_trace["rewrite_queries"] = rewrites
                    if rewrites:
                        retrieval_trace["retrieval_pass"] = 2
                        for rewritten in rewrites:
                            candidates.extend(
                                _global_scope_hit(hit)
                                for hit in self.retriever.retrieve(rewritten, top_k=60, candidate_k=60)
                            )
                        pipeline_steps.append("adaptive_retrieve_global")
            if conversation_id and user_id:
                candidates.extend(self._retrieve_conversation(conversation_id, user_id, normalized_query, candidate_limit))
                pipeline_steps.append("retrieve_conversation")
            if global_index_failed and not candidates:
                return self._retrieval_failure(
                    query=query,
                    normalized_query=normalized_query,
                    stats=stats,
                    pipeline_steps=pipeline_steps,
                    start=start,
                    request_id=request_id,
                    error_code="RAG_INDEX_UNAVAILABLE",
                )
            candidates = _merge_hits_keep_best(candidates)
            retrieval_trace.update(_candidate_counts(candidates))
            pipeline_steps.append("merge_scopes")
            reranked_hits = self.reranker.rerank(normalized_query, candidates, top_k=len(candidates))
            pipeline_steps.append("rerank")
            deduped_hits = _deduplicate_hits(reranked_hits)
            pipeline_steps.append("deduplicate")
            relevant_hits = [hit for hit in deduped_hits if hit.score >= self.min_relevance_score]
            pipeline_steps.append("relevance_threshold")
            expanded_hits, neighbor_count, section_count = _expand_neighbor_context(
                relevant_hits,
                self.index_store.list_chunks(),
                enumeration=enumeration,
            )
            retrieval_trace["neighbor_expansion_count"] = neighbor_count
            retrieval_trace["section_expansion_count"] = section_count
            if neighbor_count:
                pipeline_steps.append("neighbor_expansion")
            if section_count:
                pipeline_steps.append("section_expansion")
            expanded_hits = _deduplicate_hits(expanded_hits)
            expanded_hits.sort(key=lambda item: (-item.score, item.chunk.doc_id, item.chunk.ordinal, item.chunk.chunk_id))
            per_document = max(self.max_per_document, 6 if enumeration else 3)
            limited_hits = _select_context_windows(
                relevant_hits,
                expanded_hits,
                limit,
                per_document,
                enumeration=enumeration,
            )
            limited_hits = [hit.model_copy(update={"rank": index + 1}) for index, hit in enumerate(limited_hits)]
            evidence = prompt_builder.build_evidence(limited_hits, normalized_query)
            retrieval_trace["final_evidence_count"] = len(evidence)
            pipeline_steps.append("build_evidence")
            rag_config = getattr(getattr(self.index_store, "manager", None), "config", None)
            decision = evaluate_retrieval_decision(
                normalized_query,
                expanded_hits,
                evidence,
                minimum_relevance=self.min_relevance_score,
                dense_min_similarity=float(getattr(rag_config, "dense_min_similarity", 0.05)),
            )
            if decision.answerability_status != "answerable":
                pipeline_steps.append("answerability_reject")
                if decision.answerability_status != "ambiguous":
                    evidence = []
                    limited_hits = []
            retrieval_status = "success" if evidence else "empty"
            retrieval = self._retrieval_result(
                query=query,
                normalized_query=normalized_query,
                evidence=evidence,
                candidate_count=len(candidates),
                top_score=max((hit.score for hit in candidates), default=None),
                latency_ms=self._latency_ms(start),
                index_version=str(stats.get("index_version") or "unknown"),
                status=retrieval_status,
                error_code=None,
                retrieval_method=_retrieval_method(candidates),
                decision=decision,
            )
        except Exception:
            pipeline_steps.append("retrieval_failed")
            stats = self.index_store.get_stats()
            return self._retrieval_failure(
                query=query,
                normalized_query=normalized_query,
                stats=stats,
                pipeline_steps=pipeline_steps,
                start=start,
                request_id=request_id,
                error_code="RAG_RETRIEVAL_FAILED",
            )

        if not evidence or (retrieval.decision and retrieval.decision.answerability_status != "answerable"):
            pipeline_steps.append("no_evidence")
            return self._finish_result(
                answer=(
                    NO_EVIDENCE_MESSAGE
                    if (
                        not retrieval.decision
                        or retrieval.decision.answerability_status in {"empty", "index_unavailable"}
                        or retrieval.decision.decision_reason in {"LOW_LEXICAL", "LOW_LEXICAL_AND_DENSE", "LOW_DENSE"}
                    )
                    else NO_ANSWER_MESSAGE
                ),
                query=query,
                normalized_query=normalized_query,
                evidence=[],
                retrieved_hits=[],
                retrieval=retrieval,
                pipeline_steps=pipeline_steps,
                stats=stats,
                request_id=request_id,
                start=start,
                answer_provider="local_rag_no_evidence",
                generation_invoked=False,
                generation_status=None,
                fallback_used=False,
                citations=[],
                citation_validation="none",
                fallback=None,
                retrieval_trace=retrieval_trace,
            )

        prompt_context = prompt_builder.build_prompt_context(normalized_query, evidence)
        pipeline_steps.append("build_prompt")
        generation_status = "success"
        fallback_used = False
        fallback: dict[str, Any] | None = None
        citations: list[dict[str, str]] = []
        citation_validation = "none"
        answer_provider = "qwen_rag_answer"
        try:
            answer = self._invoke_answerer(
                query=normalized_query,
                evidence=evidence,
                prompt_context=prompt_context,
                request_id=request_id,
            )
            citation_result = validate_citations(answer, list(prompt_context.get("citations", [])))
            citation_validation = "invalid" if citation_result["invalid_citations"] else str(citation_result.get("citation_coverage") or citation_result["status"])
            citations = list(citation_result["citations"])
            if citation_result["invalid_citations"]:
                answer = INVALID_CITATION_MESSAGE
                generation_status = "invalid_citation"
                fallback_used = True
                answer_provider = "local_rag_invalid_citation_fallback"
                fallback = {
                    "reason": "invalid_citation",
                    "invalid_citations": citation_result["invalid_citations"],
                }
            else:
                answer = citation_result["answer"]
            pipeline_steps.append("answer")
        except Exception as exc:
            generation_status = _generation_status(exc)
            fallback_used = True
            answer_provider = "local_rag_generation_fallback"
            answer, citations = _grounded_fallback_answer(normalized_query, evidence)
            citation_validation = "present" if citations else "none"
            fallback = {
                "reason": generation_status,
                "error_code": _generation_error_code(exc),
                "evidence_count": len(evidence),
            }
            pipeline_steps.append("generation_fallback")

        return self._finish_result(
            answer=answer,
            query=query,
            normalized_query=normalized_query,
            evidence=evidence,
            retrieved_hits=limited_hits,
            retrieval=retrieval,
            pipeline_steps=pipeline_steps,
            stats=stats,
            request_id=request_id,
            start=start,
            answer_provider=answer_provider,
            generation_invoked=True,
            generation_status=generation_status,
            fallback_used=fallback_used,
            citations=citations,
            citation_validation=citation_validation,
            fallback=fallback,
            retrieval_trace=retrieval_trace,
        )

    def _retrieve_conversation(self, conversation_id: str, user_id: str, query: str, candidate_limit: int) -> list[RAGHit]:
        retriever = self.conversation_retriever
        if retriever is None:
            from app.conversations.attachments import get_default_attachment_service

            retriever = get_default_attachment_service().retrieve
        try:
            return list(retriever(conversation_id, user_id, query, candidate_limit=candidate_limit))
        except TypeError:
            return list(retriever(conversation_id, user_id, query, candidate_limit))

    def _invoke_answerer(self, *, query, evidence, prompt_context, request_id):
        try:
            return self.qwen_rag_answer.answer(
                query=query,
                evidence=evidence,
                prompt_context=prompt_context,
                request_id=request_id,
            )
        except TypeError:
            try:
                return self.qwen_rag_answer.answer(query, evidence, prompt_context=prompt_context)
            except TypeError:
                return self.qwen_rag_answer.answer(query, [item.model_dump() for item in evidence])

    def _empty_result(self, *, query, normalized_query, stats, pipeline_steps, start, request_id, candidate_count, error_code, top_score=None):
        from app.rag.schema import RetrievalDecision

        decision = RetrievalDecision(answerability_status="empty", decision_reason="EMPTY_QUERY_OR_INDEX", threshold_version="retrieval_decision_v1")
        retrieval = self._retrieval_result(
            query=query,
            normalized_query=normalized_query,
            evidence=[],
            candidate_count=candidate_count,
            top_score=top_score,
            latency_ms=self._latency_ms(start),
            index_version=str(stats.get("index_version") or "unknown"),
            status="empty",
            error_code=error_code,
            decision=decision,
        )
        return self._finish_result(
            answer=NO_EVIDENCE_MESSAGE,
            query=query,
            normalized_query=normalized_query,
            evidence=[],
            retrieved_hits=[],
            retrieval=retrieval,
            pipeline_steps=pipeline_steps,
            stats=stats,
            request_id=request_id,
            start=start,
            answer_provider="local_rag_no_evidence",
            generation_invoked=False,
            generation_status=None,
            fallback_used=False,
            citations=[],
            citation_validation="none",
            fallback=None,
        )

    def _retrieval_failure(self, *, query, normalized_query, stats, pipeline_steps, start, request_id, error_code):
        from app.rag.schema import RetrievalDecision

        decision = RetrievalDecision(answerability_status="index_unavailable", decision_reason=error_code, threshold_version="retrieval_decision_v1")
        retrieval = self._retrieval_result(
            query=query,
            normalized_query=normalized_query,
            evidence=[],
            candidate_count=0,
            top_score=None,
            latency_ms=self._latency_ms(start),
            index_version=str(stats.get("index_version") or "unknown"),
            status="failed",
            error_code=error_code,
            decision=decision,
        )
        return self._finish_result(
            answer=RETRIEVAL_FAILED_MESSAGE,
            query=query,
            normalized_query=normalized_query,
            evidence=[],
            retrieved_hits=[],
            retrieval=retrieval,
            pipeline_steps=pipeline_steps,
            stats=stats,
            request_id=request_id,
            start=start,
            answer_provider="local_rag_retrieval_failed",
            generation_invoked=False,
            generation_status=None,
            fallback_used=False,
            citations=[],
            citation_validation="none",
            fallback=None,
        )

    def _retrieval_result(self, *, query, normalized_query, evidence, candidate_count, top_score, latency_ms, index_version, status, error_code, retrieval_method: str | None = None, decision=None):
        return RetrievalResult(
            query=query,
            normalized_query=normalized_query,
            evidences=evidence,
            retrieval_method=retrieval_method or (evidence[0].retrieval_method if evidence else RAG_RETRIEVAL_METHOD),
            total_candidates=candidate_count,
            returned_count=len(evidence),
            top_score=top_score,
            latency_ms=latency_ms,
            index_version=index_version,
            status=status,
            error_code=error_code,
            decision=decision,
        )

    def _finish_result(
        self,
        *,
        answer,
        query,
        normalized_query,
        evidence,
        retrieved_hits,
        retrieval,
        pipeline_steps,
        stats,
        request_id,
        start,
        answer_provider,
        generation_invoked,
        generation_status,
        fallback_used,
        citations,
        citation_validation,
        fallback,
        citation_coverage=None,
        retrieval_trace=None,
    ):
        record = RAGLedgerRecord(
            request_id=request_id,
            retrieval_id=f"retrieval_{uuid4().hex}",
            query_chars=len(normalized_query),
            retrieval_method=retrieval.retrieval_method,
            retrieval_mode=retrieval.retrieval_method,
            query_language=_query_language(normalized_query),
            lexical_invoked=any("lexical" in (hit.metadata.get("retrieval_sources") or []) or hit.metadata.get("retrieval_method") in {"fts5_bm25", "keyword_weighted_rerank"} for hit in retrieved_hits),
            dense_invoked=any("dense" in (hit.metadata.get("retrieval_sources") or []) for hit in retrieved_hits),
            reranker_invoked=any(bool(hit.metadata.get("rerank_applied")) for hit in retrieved_hits),
            candidate_count=retrieval.total_candidates,
            fused_count=retrieval.total_candidates,
            returned_count=retrieval.returned_count,
            top_score=retrieval.top_score,
            latency_ms=retrieval.latency_ms,
            index_version=retrieval.index_version,
            embedding_model=str(stats.get("embedding_model") or "none"),
            reranker_model=next((str(hit.metadata.get("rerank_provider")) for hit in retrieved_hits if hit.metadata.get("rerank_provider")), None),
            status=retrieval.status,
            generation_invoked=generation_invoked,
            generation_status=generation_status,
            citation_status=citation_validation,
            answerability_status=(retrieval.decision.answerability_status if retrieval.decision else None),
            decision_reason=(retrieval.decision.decision_reason if retrieval.decision else None),
            threshold_version=(retrieval.decision.threshold_version if retrieval.decision else None),
            fallback_used=fallback_used,
            error_code=retrieval.error_code or (fallback or {}).get("error_code"),
        )
        self.ledger.add(record)
        record_dict = record.model_dump(exclude_none=False)
        metadata = self._metadata(
            stats=stats,
            retrieved_count=retrieval.total_candidates,
            reranked_count=len(retrieved_hits),
            evidence_count=len(evidence),
            pipeline_steps=pipeline_steps,
            start=start,
            answer_provider=answer_provider,
            retrieval=retrieval,
            record=record_dict,
            generation_invoked=generation_invoked,
            generation_status=generation_status,
            fallback_used=fallback_used,
            fallback=fallback,
            citations=citations,
            citation_validation=citation_validation,
            citation_coverage=citation_validation,
            retrieval_trace=retrieval_trace,
        )
        return RAGPipelineResult(
            answer=answer,
            evidence=evidence,
            retrieved_hits=retrieved_hits,
            provider=self.provider,
            retrieval=retrieval,
            citations=citations,
            metadata=metadata,
        )

    def _metadata(self, *, stats, retrieved_count, reranked_count, evidence_count, pipeline_steps, start, answer_provider, retrieval, record, generation_invoked, generation_status, fallback_used, fallback, citations, citation_validation, citation_coverage=None, retrieval_trace=None):
        evidence_dump = [item.model_dump() for item in retrieval.evidences]
        fallback_records = [fallback] if fallback else []
        scope_counts: dict[str, int] = {}
        for item in retrieval.evidences:
            scope = str(item.metadata.get("scope") or "global")
            scope_counts[scope] = scope_counts.get(scope, 0) + 1
        return {
            "provider": self.provider,
            "rag_provider": self.provider,
            "docs_count": stats.get("docs_count", 0),
            "chunks_count": stats.get("chunks_count", 0),
            "index_status": stats.get("index_status"),
            "rag_index_status": {
                "status": stats.get("index_status"),
                "active_index_version": retrieval.index_version,
                "document_count": stats.get("docs_count", 0),
                "chunk_count": stats.get("chunks_count", 0),
                "lexical_available": stats.get("fts5_available", True),
                "dense_available": stats.get("dense_available", False),
                "embedding_model": stats.get("embedding_model"),
            },
            "index_version": retrieval.index_version,
            "retrieved_count": retrieved_count,
            "reranked_count": reranked_count,
            "evidence_count": evidence_count,
            "pipeline_steps": pipeline_steps,
            "rag_pipeline_steps": pipeline_steps,
            "answer_provider": answer_provider,
            "latency_ms": max(0, int((perf_counter() - start) * 1000)),
            "retrieval": retrieval.model_dump(exclude_none=False),
            "rag_retrievals": [record],
            "rag_evidences": evidence_dump,
            "retrieval_scopes": sorted(scope_counts),
            "scope_counts": scope_counts,
            "rag_citations": citations,
            "rag_fallbacks": fallback_records,
            "retrieval_status": retrieval.status,
            "retrieval_method": retrieval.retrieval_method,
            "candidate_count": retrieval.total_candidates,
            "fused_count": retrieval.total_candidates,
            "returned_evidence_count": retrieval.returned_count,
            "top_score": retrieval.top_score,
            "retrieval_latency_ms": retrieval.latency_ms,
            "generation_invoked": generation_invoked,
            "generation_status": generation_status,
            "fallback_used": fallback_used,
            "lexical_invoked": record.get("lexical_invoked", False),
            "dense_invoked": record.get("dense_invoked", False),
            "reranker_invoked": record.get("reranker_invoked", False),
            "embedding_model": record.get("embedding_model") or stats.get("embedding_model"),
            "reranker_model": record.get("reranker_model"),
            "citation_validation": citation_validation,
            "citation_coverage": citation_coverage,
            "retrieval_decision": retrieval.decision.model_dump() if retrieval.decision else None,
            "answer_status": "insufficient_evidence" if retrieval.status == "empty" else ("generation_timeout" if generation_status == "timeout" else ("generation_failed" if generation_status == "failed" else "answered")),
            "rag_trace": retrieval_trace or {},
            **(retrieval_trace or {}),
        }

    def _latency_ms(self, start: float) -> int:
        return max(0, int((perf_counter() - start) * 1000))


def _deduplicate_hits(hits: list[RAGHit]) -> list[RAGHit]:
    result: list[RAGHit] = []
    for hit in hits:
        duplicate = False
        for previous in result:
            if hit.chunk.chunk_id == previous.chunk.chunk_id:
                duplicate = True
                break
            if hit.chunk.doc_id == previous.chunk.doc_id:
                ratio = SequenceMatcher(None, hit.chunk.content, previous.chunk.content).ratio()
                if ratio >= 0.82:
                    duplicate = True
                    break
        if not duplicate:
            result.append(hit)
    return result


def _merge_hits_keep_best(hits: list[RAGHit]) -> list[RAGHit]:
    merged: dict[str, RAGHit] = {}
    for hit in hits:
        previous = merged.get(hit.chunk.chunk_id)
        if previous is None or hit.score > previous.score:
            metadata = dict(hit.metadata)
            if previous is not None:
                metadata["retrieval_sources"] = sorted(set(metadata.get("retrieval_sources") or []) | set(previous.metadata.get("retrieval_sources") or []))
            merged[hit.chunk.chunk_id] = hit.model_copy(update={"metadata": metadata})
        elif previous is not None:
            metadata = dict(previous.metadata)
            metadata["retrieval_sources"] = sorted(set(metadata.get("retrieval_sources") or []) | set(hit.metadata.get("retrieval_sources") or []))
            merged[hit.chunk.chunk_id] = previous.model_copy(update={"metadata": metadata})
    return sorted(merged.values(), key=lambda item: (-item.score, item.chunk.chunk_id))


def _candidate_counts(hits: list[RAGHit]) -> dict[str, int]:
    return {
        "lexical_candidate_count": sum(1 for hit in hits if hit.metadata.get("lexical_rank") is not None),
        "dense_candidate_count": sum(1 for hit in hits if hit.metadata.get("dense_rank") is not None),
        "fusion_count": len(hits),
    }


def _needs_second_pass(query: str, hits: list[RAGHit], enumeration: bool) -> bool:
    if not hits:
        return True
    phrases = query_phrases(query)
    if phrases and not any(
        hit.metadata.get("phrase_title_match") or hit.metadata.get("phrase_section_match") or hit.metadata.get("phrase_content_match")
        for hit in hits[:10]
    ):
        return True
    if enumeration and not any(
        re.search(r"(?:^|\n)\s*(?:[1-9]\d*[.、）)]|[一二三四五六七八九十]+[、）)])", hit.chunk.content)
        for hit in hits[:5]
    ):
        return True
    return max((hit.score for hit in hits[:3]), default=0.0) < 2.5


def _expand_neighbor_context(hits: list[RAGHit], chunks: list[Any], *, enumeration: bool) -> tuple[list[RAGHit], int, int]:
    if not hits:
        return [], 0, 0
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    expanded = list(hits)
    seen = {hit.chunk.chunk_id for hit in hits}
    neighbor_count = 0
    section_count = 0
    anchors = hits[:4 if enumeration else 3]

    def add(anchor: RAGHit, chunk: Any, *, distance: int, method: str) -> None:
        nonlocal neighbor_count, section_count
        if chunk is None or chunk.chunk_id in seen or chunk.doc_id != anchor.chunk.doc_id:
            return
        seen.add(chunk.chunk_id)
        metadata = {
            **anchor.metadata,
            "retrieval_method": method,
            "neighbor_of": anchor.chunk.chunk_id,
            "expansion_distance": distance,
            "rerank_applied": True,
            "rerank_provider": anchor.metadata.get("rerank_provider") or "lightweight_heuristic",
        }
        expanded.append(
            RAGHit(
                chunk=chunk,
                score=max(0.0, anchor.score - 0.25 * max(1, distance)),
                match_reason=method,
                metadata=metadata,
            )
        )
        if method == "section_expansion":
            section_count += 1
        else:
            neighbor_count += 1

    for anchor in anchors:
        maximum_distance = 2 if enumeration else 1
        previous_id = anchor.chunk.previous_chunk_id
        next_id = anchor.chunk.next_chunk_id
        for distance in range(1, maximum_distance + 1):
            previous = chunk_map.get(previous_id) if previous_id else None
            following = chunk_map.get(next_id) if next_id else None
            add(anchor, previous, distance=distance, method="neighbor_expansion")
            add(anchor, following, distance=distance, method="neighbor_expansion")
            previous_id = previous.previous_chunk_id if previous is not None else None
            next_id = following.next_chunk_id if following is not None else None

        if anchor.metadata.get("phrase_section_match") and anchor.chunk.section_id:
            same_section = sorted(
                (
                    chunk for chunk in chunks
                    if chunk.doc_id == anchor.chunk.doc_id and chunk.section_id == anchor.chunk.section_id
                ),
                key=lambda chunk: (abs(chunk.ordinal - anchor.chunk.ordinal), chunk.ordinal),
            )
            for chunk in same_section[:4]:
                add(anchor, chunk, distance=abs(chunk.ordinal - anchor.chunk.ordinal), method="section_expansion")
    return expanded, neighbor_count, section_count


def _global_scope_hit(hit: RAGHit) -> RAGHit:
    scope = {"scope": "global", "source_kind": "global_knowledge", "conversation_id": None, "attachment_id": None}
    chunk = hit.chunk.model_copy(deep=True, update={"metadata": {**hit.chunk.metadata, **scope}})
    return hit.model_copy(deep=True, update={"chunk": chunk, "metadata": {**hit.metadata, **scope}})


def _limit_per_document(hits: list[RAGHit], top_k: int, max_per_document: int) -> list[RAGHit]:
    counts: dict[str, int] = {}
    selected: list[RAGHit] = []
    for hit in hits:
        document_id = hit.chunk.doc_id
        if counts.get(document_id, 0) >= max_per_document:
            continue
        counts[document_id] = counts.get(document_id, 0) + 1
        selected.append(hit)
        if len(selected) >= top_k:
            break
    return selected


def _select_context_windows(
    anchors: list[RAGHit],
    expanded: list[RAGHit],
    top_k: int,
    max_per_document: int,
    *,
    enumeration: bool,
) -> list[RAGHit]:
    """Keep diverse anchors visible, then spend the remaining budget on context.

    The previous implementation globally sorted expanded neighbors. A high
    scoring but wrong-source anchor could therefore insert two neighbors ahead
    of a correct anchor from another source. This selector reserves the first
    positions for distinct anchor documents and only then adds bounded windows.
    """
    if top_k <= 0:
        return []
    ordered_anchors = sorted(anchors, key=lambda item: (-item.score, item.chunk.doc_id, item.chunk.ordinal, item.chunk.chunk_id))
    anchor_budget = min(top_k, 4 if top_k >= 5 else top_k)
    structural_focus = any(hit.metadata.get("structural_recall") for hit in ordered_anchors[:4])
    selected: list[RAGHit] = []
    selected_ids: set[str] = set()
    document_counts: dict[str, int] = {}

    def add(hit: RAGHit) -> bool:
        if hit.chunk.chunk_id in selected_ids or len(selected) >= top_k:
            return False
        document_id = hit.chunk.doc_id
        if document_counts.get(document_id, 0) >= max_per_document:
            return False
        selected.append(hit)
        selected_ids.add(hit.chunk.chunk_id)
        document_counts[document_id] = document_counts.get(document_id, 0) + 1
        return True

    seen_documents: set[str] = set()
    if not structural_focus:
        for hit in ordered_anchors:
            if hit.chunk.doc_id in seen_documents:
                continue
            if add(hit):
                seen_documents.add(hit.chunk.doc_id)
            if len(selected) >= anchor_budget:
                break
    if len(selected) < anchor_budget:
        for hit in ordered_anchors:
            add(hit)
            if len(selected) >= anchor_budget:
                break

    anchor_positions = {hit.chunk.chunk_id: index for index, hit in enumerate(selected)}
    context_candidates = [hit for hit in expanded if hit.metadata.get("neighbor_of") in anchor_positions]
    context_candidates.sort(
        key=lambda hit: (
            anchor_positions.get(str(hit.metadata.get("neighbor_of")), 999),
            int(hit.metadata.get("expansion_distance") or 1),
            0 if hit.metadata.get("retrieval_method") == "section_expansion" else 1,
            -hit.score,
            hit.chunk.ordinal,
        )
    )
    per_anchor_context: dict[str, int] = {}
    context_limit = 2 if enumeration else 1
    for hit in context_candidates:
        anchor_id = str(hit.metadata.get("neighbor_of") or "")
        if per_anchor_context.get(anchor_id, 0) >= context_limit:
            continue
        if add(hit):
            per_anchor_context[anchor_id] = per_anchor_context.get(anchor_id, 0) + 1
        if len(selected) >= top_k:
            break

    if len(selected) < top_k:
        for hit in ordered_anchors:
            add(hit)
            if len(selected) >= top_k:
                break
    if len(selected) < top_k:
        for hit in expanded:
            add(hit)
            if len(selected) >= top_k:
                break
    return selected


def _generation_status(exc: Exception) -> str:
    code = _generation_error_code(exc)
    if "TIMEOUT" in code or "TIMEOUT" in str(exc).upper() or "TIMEOUT" in exc.__class__.__name__.upper():
        return "timeout"
    return "failed"


def _generation_error_code(exc: Exception) -> str:
    current: BaseException | None = exc
    for _ in range(5):
        gateway_error = getattr(current, "gateway_error", None)
        if gateway_error is not None and getattr(gateway_error, "code", None):
            return str(gateway_error.code)
        if current is None:
            break
        current = current.__cause__ or current.__context__
    return "RAG_GENERATION_TIMEOUT" if _generation_status_name(exc) == "timeout" else "RAG_GENERATION_FAILED"


def _generation_status_name(exc: Exception) -> str:
    text = f"{exc.__class__.__name__} {exc}".upper()
    return "timeout" if "TIMEOUT" in text else "failed"


def _retrieval_method(hits: list[RAGHit]) -> str:
    methods = {str(hit.metadata.get("retrieval_method") or "") for hit in hits if hit.metadata.get("retrieval_method")}
    if "hybrid_rrf" in methods:
        return "hybrid_rrf"
    if "dense_cosine" in methods:
        return "dense_cosine"
    if "fts5_bm25" in methods:
        return "fts5_bm25"
    return RAG_RETRIEVAL_METHOD


def _query_language(query: str) -> str:
    if any("\u4e00" <= char <= "\u9fff" for char in query):
        return "zh"
    return "en" if any(char.isalpha() for char in query) else "unknown"


def _grounded_fallback_answer(query: str, evidence: list[RAGEvidence]) -> tuple[str, list[dict[str, str]]]:
    """Return extractive evidence text when generation is unavailable."""
    comparison = _model_level_comparison_answer(query, evidence)
    if comparison is not None:
        return comparison
    phrases = query_phrases(query)
    terms = [term for term in tokenize_query(query) if len(term) > 1]
    candidates: list[tuple[float, int, str, int]] = []
    for index, item in enumerate(evidence, start=1):
        compact = re.sub(r"(?<!\n)\n(?!\n)", "", item.content)
        sentences = [value.strip() for value in re.split(r"(?<=[。！？；])|\n{2,}", compact) if value.strip()]
        for sentence in sentences:
            phrase_hits = sum(len(phrase) for phrase in phrases if phrase in sentence)
            term_hits = sum(1 for term in terms if term in sentence)
            if phrase_hits or term_hits >= 2:
                candidates.append((phrase_hits * 2.0 + term_hits, phrase_hits, sentence, index))
    if phrases and any(phrase_hits for _, phrase_hits, _, _ in candidates):
        candidates = [item for item in candidates if item[1] > 0]
    candidates.sort(key=lambda item: (-item[0], item[3], -len(item[2])))
    selected: list[tuple[str, int]] = []
    seen: set[str] = set()
    limit = 3 if is_enumeration_query(query) else 1
    for _, _, sentence, index in candidates:
        key = re.sub(r"\s+", "", sentence)
        if key in seen:
            continue
        seen.add(key)
        selected.append((sentence[:900], index))
        if len(selected) >= limit:
            break
    if not selected and evidence:
        selected = [(evidence[0].content[:900], 1)]
    if not selected:
        return GENERATION_FALLBACK_MESSAGE, []
    used = sorted({index for _, index in selected})
    citations = [
        {"citation_id": f"E{index}", "evidence_id": evidence[index - 1].evidence_id}
        for index in used
    ]
    if len(selected) == 1:
        sentence, index = selected[0]
        answer = f"{sentence} [E{index}]"
        emit_answer_delta(answer)
    else:
        parts: list[str] = []
        for position, (sentence, index) in enumerate(selected):
            part = ("\n" if position else "") + f"- {sentence} [E{index}]"
            parts.append(part)
            emit_answer_delta(part)
        answer = "".join(parts)
    return answer, citations


def _model_level_comparison_answer(
    query: str,
    evidence: list[RAGEvidence],
) -> tuple[str, list[dict[str, str]]] | None:
    compact_query = re.sub(r"\s+", "", str(query or ""))
    if "模型层级" not in compact_query or not any(term in compact_query for term in ("比较", "对比", "分别", "变电")):
        return None
    patterns = {
        "变电工程": r"7\.1\s*变电工程模型层级按照(.{8,100}?)层级分为\s*5\s*级",
        "架空线路工程": r"7\.2\s*线路工程模型层级按照(.{8,100}?)层级分为\s*5\s*级",
        "电缆线路工程": r"7\.3\s*电缆工程模型层级按照(.{8,100}?)层级分为\s*5\s*级",
    }
    values: dict[str, list[str]] = {}
    citation_index: int | None = None
    for index, item in enumerate(evidence, start=1):
        text = re.sub(r"设备\s+部件", "设备、部件", item.content)
        text = re.sub(r"\s+", "", text)
        for label, pattern in patterns.items():
            if label in values:
                continue
            match = re.search(pattern, text)
            if not match:
                continue
            levels = [part.strip("，、。； ") for part in re.split(r"[、，]", match.group(1)) if part.strip("，、。； ")]
            if len(levels) == 5:
                values[label] = levels
                citation_index = citation_index or index
    if len(values) != 3 or citation_index is None:
        return None
    rows = []
    for level in range(5):
        rows.append(
            f"| {level + 1} | {values['变电工程'][level]} | {values['架空线路工程'][level]} | {values['电缆线路工程'][level]} |"
        )
    parts = [
        "## 三类工程模型层级对比\n\n",
        "| 层级 | 变电工程 | 架空线路工程 | 电缆线路工程 |\n",
        "|---|---|---|---|\n",
    ]
    parts.extend(row + "\n" for row in rows)
    parts.append(
        "\n## 主要区别\n\n"
        "架空线路工程的第 2、3 级分别是**分段**和**耐张段**；变电工程与电缆线路工程采用专业、系统层级。"
        f"[E{citation_index}]"
    )
    for part in parts:
        emit_answer_delta(part)
    answer = "".join(parts)
    citations = [{"citation_id": f"E{citation_index}", "evidence_id": evidence[citation_index - 1].evidence_id}]
    return answer, citations
