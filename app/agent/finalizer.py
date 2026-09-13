from app.agent.state import AgentState
from app.answering import attach_customer_final_response
from app.schemas.response import AgentResponse, EvidenceItem, UIEvent


def build_final_response(state: AgentState) -> AgentResponse:
    skill_result = getattr(state, "skill_result", None)
    if skill_result is not None:
        route_type = skill_result.metadata.get(
            "route_type",
            getattr(getattr(state, "route_decision", None), "route_type", None),
        )
        extra_fields = dict(getattr(skill_result, "model_extra", {}) or {})
        return attach_customer_final_response(AgentResponse(
            trace_id=state.trace_id,
            status=skill_result.status,
            answer=skill_result.answer,
            intent=skill_result.intent,
            scenario=skill_result.scenario,
            route_type=route_type,
            missing_params=skill_result.missing_params,
            tool_calls=skill_result.tool_calls,
            evidence_list=skill_result.evidence,
            ui_events=skill_result.ui_events,
            risk_level=skill_result.risk_level,
            manual_check_required=skill_result.manual_check_required,
            metadata=skill_result.metadata,
            **extra_fields,
        ), state.original_query or state.request.query)

    plan = state.plan
    tool_calls = plan.tool_calls if plan else []
    evidence_list: list[EvidenceItem] = []
    ui_events: list[UIEvent] = []

    for result in state.anchor_results:
        evidence_list.extend(result.evidence)
        ui_events.extend(result.ui_events)

    has_success = any(result.status == "success" for result in state.anchor_results)

    if plan and not plan.tool_calls and plan.intent == "unknown":
        status = "failed"
        answer = "当前暂未识别可执行意图，请补充更明确的需求。"
    elif has_success:
        status = "success"
        answer = plan.final_answer_draft if plan else "已完成请求的模拟执行。"
    else:
        status = "failed"
        answer = "工具执行失败，请稍后重试或补充更多信息。"

    return attach_customer_final_response(AgentResponse(
        trace_id=state.trace_id,
        status=status,
        answer=answer,
        intent=plan.intent if plan else None,
        scenario=plan.scenario if plan else None,
        route_type=getattr(getattr(state, "route_decision", None), "route_type", None),
        missing_params=[],
        tool_calls=tool_calls,
        evidence_list=evidence_list,
        ui_events=ui_events,
        risk_level="low",
        manual_check_required=False,
    ), state.original_query or state.request.query)
