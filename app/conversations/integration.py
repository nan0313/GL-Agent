from __future__ import annotations

import re
import traceback
from uuid import uuid4

from app.conversations.service import get_default_conversation_service, resolve_conversation_id
from app.schemas.request import AgentChatRequest
from app.schemas.response import AgentResponse
from app.answering import attach_customer_final_response


def run_agent_with_persistence(workflow, request: AgentChatRequest, *, request_id: str | None = None) -> tuple[AgentResponse, str, str]:
    service = get_default_conversation_service()
    conversation_id = resolve_conversation_id(request)
    resolved_request_id = str(request_id or request.request_id or f"req_{uuid4().hex}")[:128]
    service.ensure(conversation_id, request.user_id)
    normalized = request.model_copy(
        deep=True,
        update={
            "session_id": conversation_id,
            "conversation_id": conversation_id,
            "request_id": resolved_request_id,
        },
    )
    service.append_message(
        conversation_id,
        request.user_id,
        "user",
        request.query,
        request_id=resolved_request_id,
        metadata={"source": "agent_request"},
    )
    try:
        response = workflow.run(normalized)
    except Exception as exc:
        response = failed_agent_response(exc)
    response = attach_customer_final_response(response, request.query)
    response.metadata = {
        **(response.metadata or {}),
        "conversation_id": conversation_id,
        "request_id": resolved_request_id,
    }
    service.append_message(
        conversation_id,
        request.user_id,
        "assistant",
        response.final_response.answer_markdown if response.final_response else response.answer,
        request_id=resolved_request_id,
        trace_id=response.trace_id,
        status=response.status,
        metadata={
            "intent": response.intent,
            "scenario": response.scenario,
            "route_type": response.route_type,
            "evidence_count": len(response.evidence_list),
            "customer_final_response": response.final_response.model_dump(mode="json") if response.final_response else None,
        },
    )
    return response, conversation_id, resolved_request_id


def failed_agent_response(exc: Exception) -> AgentResponse:
    return AgentResponse(
        trace_id=f"trace_error_{uuid4().hex}",
        status="failed",
        answer="请求暂时未完成，请重试。",
        intent="unknown",
        scenario="system_error",
        route_type=None,
        missing_params=[],
        tool_calls=[],
        evidence_list=[],
        ui_events=[],
        risk_level="low",
        manual_check_required=False,
        metadata={
            "error_code": "AGENT_REQUEST_FAILED",
            "internal_error_type": exc.__class__.__name__,
            "internal_error_summary": safe_error_summary(exc),
            "internal_error_stack": "".join(traceback.format_exception(exc))[-12000:],
        },
    )


def safe_error_summary(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    sensitive_markers = ["sk-", "Bearer ", "api_key", "API_KEY", "Authorization", "http://", "https://"]
    if any(marker.lower() in message.lower() for marker in sensitive_markers) or re.search(r"(?:[A-Za-z]:[\\/]|/[^ ]+/[^ ]+)", message):
        return f"{exc.__class__.__name__}: sensitive details redacted"
    return message[:240]
