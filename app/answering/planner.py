from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.response import AgentResponse


class AnswerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_type: Literal["answer", "comparison", "document_summary", "business_result", "clarification", "error"]
    title: str | None = None
    render_markdown: bool = True
    include_citations: bool = False
    include_business_actions: bool = False
    buffer_final_answer: bool = False
    sections: list[str] = Field(default_factory=list)


class AnswerPlanner:
    """Plan the customer answer shape without exposing orchestration."""

    def plan(self, query: str, response: AgentResponse) -> AnswerPlan:
        normalized = str(query or "").strip()
        if response.status in {"failed", "blocked"}:
            return AnswerPlan(response_type="error", buffer_final_answer=True)
        if response.status == "clarification_required":
            return AnswerPlan(response_type="clarification", buffer_final_answer=True)
        if response.ui_events:
            return AnswerPlan(
                response_type="business_result",
                include_business_actions=True,
                buffer_final_answer=True,
                sections=["result", "properties"],
            )
        is_document = bool(
            (response.scenario and "document" in response.scenario)
            or (response.intent and str(response.intent).startswith("document_"))
        )
        if is_document:
            return AnswerPlan(
                response_type="document_summary" if response.intent == "document_summary" else "answer",
                include_citations=bool(response.evidence_list),
                sections=["summary", "details", "sources"],
            )
        if any(term in normalized for term in ("比较", "对比", "分别", "区别", "差异")):
            return AnswerPlan(
                response_type="comparison",
                title="对比结果",
                include_citations=bool(response.evidence_list),
                sections=["comparison", "differences", "sources"],
            )
        return AnswerPlan(
            response_type="answer",
            include_citations=bool(response.evidence_list),
            sections=["answer", "sources"],
        )
