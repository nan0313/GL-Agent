from app.rag.rag_pipeline import RAGPipeline
from app.rag.retriever import LocalRetriever
from app.schemas.response import EvidenceItem


class LocalRAGAnswerer:
    def __init__(
        self,
        retriever: LocalRetriever | None = None,
        qwen_rag_answer=None,
        pipeline: RAGPipeline | None = None,
    ) -> None:
        self.pipeline = pipeline or RAGPipeline(
            retriever=retriever,
            qwen_rag_answer=qwen_rag_answer,
        )

    def answer(self, query: str, request_id: str = "", *, conversation_id: str | None = None, user_id: str | None = None) -> tuple[str, list[EvidenceItem], str, dict]:
        result = self.pipeline.answer(query, request_id=request_id, conversation_id=conversation_id, user_id=user_id)
        retrieval_status = result.retrieval.status if result.retrieval else result.metadata.get("retrieval_status")
        if retrieval_status == "failed":
            scenario = "local_rag_retrieval_failed"
        elif result.metadata.get("fallback_used"):
            scenario = "local_rag_generation_fallback"
        else:
            scenario = "local_rag" if result.evidence else "local_rag_no_evidence"

        evidence = [
            EvidenceItem(
                anchor_id="local_rag",
                anchor_name="Local Markdown Knowledge Base",
                status="success",
                summary=item.snippet,
                source=item.source_name,
                title=item.title,
                snippet=item.snippet,
                evidence_id=item.evidence_id,
                document_id=item.document_id,
                chunk_id=item.chunk_id,
                source_type=item.source_type,
                source_name=item.source_name,
                content=item.content,
                score=item.score,
                rank=item.rank,
                match_reason=item.metadata.get("match_reason"),
                metadata=item.metadata,
                scope=item.metadata.get("scope", "global"),
                source_kind=item.metadata.get("source_kind", "global_knowledge"),
                conversation_id=item.metadata.get("conversation_id"),
                attachment_id=item.metadata.get("attachment_id"),
            )
            for item in result.evidence
        ]
        metadata = dict(result.metadata)
        metadata["provider"] = result.provider
        metadata["rag_provider"] = result.provider
        metadata["evidence_ids"] = [item.evidence_id for item in result.evidence]
        metadata["citations"] = result.citations
        return result.answer, evidence, scenario, metadata
