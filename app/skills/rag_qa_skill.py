from app.rag import LocalRAGAnswerer
from app.skills.base import AgentSkill
from app.skills.context import SkillContext
from app.skills.result import SkillResult


class RagQASkill(AgentSkill):
    skill_id = "rag_qa"
    name = "RAG QA"
    description = "Answer questions with the local Markdown knowledge base."
    route_type = "rag_qa"

    def __init__(self, rag_answerer: LocalRAGAnswerer | None = None) -> None:
        self.rag_answerer = rag_answerer or LocalRAGAnswerer()

    def execute(self, context: SkillContext) -> SkillResult:
        try:
            try:
                answer_result = self.rag_answerer.answer(
                    context.request.query,
                    request_id=context.trace_id,
                )
            except TypeError:
                # Keep existing lightweight adapters compatible with the skill contract.
                answer_result = self.rag_answerer.answer(context.request.query)
        except Exception:
            return SkillResult(
                status="failed",
                intent="rag_qa",
                scenario="local_rag_retrieval_failed",
                answer="当前知识库检索暂时不可用，暂未生成回答。",
                tool_calls=[],
                ui_events=[],
                evidence=[],
                missing_params=[],
                risk_level="low",
                manual_check_required=False,
                metadata={
                    "skill_id": self.skill_id,
                    "route_type": self.route_type,
                    "rag_provider": "local_rag_v2",
                    "retrieval_status": "failed",
                    "error_code": "RAG_SKILL_FAILED",
                },
            )

        if len(answer_result) == 3:
            answer, evidence, scenario = answer_result
            rag_metadata = {}
        else:
            answer, evidence, scenario, rag_metadata = answer_result

        retrieval_status = rag_metadata.get("retrieval_status")
        result_status = "failed" if retrieval_status == "failed" else "success"
        return SkillResult(
            status=result_status,
            intent="rag_qa",
            scenario=scenario,
            answer=answer,
            tool_calls=[],
            ui_events=[],
            evidence=evidence,
            missing_params=[],
            risk_level="low",
            manual_check_required=False,
            metadata={
                "skill_id": self.skill_id,
                "route_type": self.route_type,
                "scenario": scenario,
                "rag_provider": rag_metadata.get("rag_provider", "local_rag_v2"),
                "evidence_count": len(evidence),
                "docs_count": rag_metadata.get("docs_count"),
                "chunks_count": rag_metadata.get("chunks_count"),
                "retrieved_count": rag_metadata.get("retrieved_count"),
                "retrieval_status": retrieval_status,
                "retrieval_method": rag_metadata.get("retrieval_method"),
                "candidate_count": rag_metadata.get("candidate_count"),
                "returned_evidence_count": rag_metadata.get("returned_evidence_count"),
                "top_score": rag_metadata.get("top_score"),
                "retrieval_latency_ms": rag_metadata.get("retrieval_latency_ms"),
                "generation_invoked": rag_metadata.get("generation_invoked", False),
                "generation_status": rag_metadata.get("generation_status"),
                "fallback_used": rag_metadata.get("fallback_used", False),
                "citation_validation": rag_metadata.get("citation_validation", "none"),
                "index_version": rag_metadata.get("index_version"),
                "pipeline_steps": rag_metadata.get("pipeline_steps", []),
                "rag_pipeline_steps": rag_metadata.get("rag_pipeline_steps", []),
                **rag_metadata,
            },
        )
