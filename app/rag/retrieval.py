"""Lexical, dense and RRF hybrid retrieval over the persistent store."""

from typing import Any

from app.rag.config import RAGConfig
from app.rag.embeddings import EmbeddingProvider, EmbeddingProviderUnavailable
from app.rag.query import matches_metadata_filter, normalize_query, query_phrases, validate_metadata_filter
from app.rag.schema import RAGHit, RAGQuery
from app.rag.storage import SQLiteRAGStore
from app.rag.vector_index import VectorBackend, VectorIndexError


class HybridRetriever:
    def __init__(self, store: SQLiteRAGStore, embedding_provider: EmbeddingProvider, vector_index: VectorBackend, config: RAGConfig) -> None:
        self.store = store
        self.embedding_provider = embedding_provider
        self.vector_index = vector_index
        self.config = config

    def retrieve(self, query: str, *, top_k: int = 3, candidate_limit: int = 24, metadata_filter: dict[str, Any] | None = None, mode: str | None = None) -> list[RAGHit]:
        normalized = normalize_query(query)
        if not normalized:
            return []
        filters = validate_metadata_filter(metadata_filter)
        lexical = self.store.lexical_candidates(normalized, candidate_limit, filters.model_dump(exclude_none=True)) if self.config.lexical_enabled else []
        lexical_map = {chunk.chunk_id: (chunk, score) for chunk, score in lexical}
        dense_map: dict[str, tuple[Any, float]] = {}
        dense_enabled = bool(self.config.dense_enabled and self.embedding_provider.health_check(load=True).get("available"))
        if dense_enabled:
            try:
                active = self.store.active_index_version()
                if active and self.vector_index.version != active:
                    self.vector_index.load(active, provider_key=self.embedding_provider.provider_key, dimension=self.embedding_provider.dimension)
                query_vector = self.embedding_provider.embed_query(normalized)
                allowed = {chunk_id for chunk_id, chunk in self.store.get_chunk_map().items() if matches_metadata_filter({**chunk.metadata, "document_id": getattr(chunk, "document_id", chunk.doc_id), "source_id": getattr(chunk, "source_id", None), "source_type": getattr(chunk, "source_type", None), "section": chunk.section}, filters)}
                for chunk_id, score in self.vector_index.search(query_vector, candidate_limit, allowed):
                    if score < self.config.dense_min_similarity:
                        continue
                    chunk = self.store.get_chunk_map().get(chunk_id)
                    if chunk is not None:
                        dense_map[chunk_id] = (chunk, score)
            except (EmbeddingProviderUnavailable, VectorIndexError):
                dense_map = {}

        if not lexical_map and not dense_map:
            return []
        lexical_rank = {chunk_id: index for index, chunk_id in enumerate(lexical_map, start=1)}
        dense_rank = {chunk_id: index for index, chunk_id in enumerate(dense_map, start=1)}
        phrases = query_phrases(normalized)
        combined: list[RAGHit] = []
        method = mode or ("hybrid_rrf" if lexical_map and dense_map and self.config.hybrid_enabled else "fts5_bm25" if lexical_map else "dense_cosine")
        for chunk_id in sorted(set(lexical_map) | set(dense_map)):
            chunk, lexical_score = lexical_map.get(chunk_id, dense_map.get(chunk_id))
            dense_score = dense_map.get(chunk_id, (None, None))[1]
            fusion_score = 0.0
            if chunk_id in lexical_rank:
                fusion_score += 1.2 / (self.config.rrf_k + lexical_rank[chunk_id])
            if chunk_id in dense_rank:
                fusion_score += 1.0 / (self.config.rrf_k + dense_rank[chunk_id])
            # ``score`` remains a relative ranking score compatible with the
            # existing threshold. The raw components and RRF score are kept in
            # metadata; none are probabilities.
            section_text = " / ".join(chunk.section_path or []).lower()
            phrase_title_match = any(phrase.lower() in (chunk.title or "").lower() for phrase in phrases)
            phrase_section_match = any(phrase.lower() in section_text for phrase in phrases)
            phrase_content_match = any(phrase.lower() in chunk.content.lower() for phrase in phrases)
            display_score = (
                fusion_score * 100.0
                + min(max(float(lexical_score or 0.0), 0.0), 30.0) * 0.20
                + max(float(dense_score or 0.0), 0.0) * 1.5
                + (1.8 if phrase_title_match else 0.0)
                + (1.4 if phrase_section_match else 0.0)
                + (1.0 if phrase_content_match else 0.0)
            )
            combined.append(
                RAGHit(
                    chunk=chunk,
                    score=round(display_score, 6),
                    match_reason=method,
                    metadata={
                        "retrieval_method": method,
                        "lexical_rank": lexical_rank.get(chunk_id),
                        "lexical_score": lexical_score,
                        "fts5_bm25_rank": chunk.metadata.get("fts5_bm25_rank"),
                        "lexical_backend": chunk.metadata.get("lexical_backend") or ("fts5_bm25" if lexical_score is not None else None),
                        "dense_rank": dense_rank.get(chunk_id),
                        "dense_score": dense_score,
                        "fusion_score": round(fusion_score, 8),
                        "phrase_title_match": phrase_title_match,
                        "phrase_section_match": phrase_section_match,
                        "phrase_content_match": phrase_content_match,
                        "retrieval_sources": [name for name, present in (("lexical", chunk_id in lexical_map), ("dense", chunk_id in dense_map)) if present],
                    },
                )
            )
        combined.sort(key=lambda item: (-item.score, -float(item.metadata.get("fusion_score") or 0.0), item.chunk.chunk_id))
        return [item.model_copy(update={"rank": index}) for index, item in enumerate(combined[: max(0, int(top_k))], start=1)]
