from fastapi import APIRouter, HTTPException
from app.agent.workflow_factory import get_agent_workflow
from app.config import get_workflow_provider
from app.conversations.integration import run_agent_with_persistence, safe_error_summary
from app.schemas.request import AgentChatRequest
from app.schemas.response import AgentResponse
from app.schemas.trace import TraceRecord
from app.answering import attach_customer_final_response
from app.product.error_mapper import map_internal_error
from app.release_profile import developer_mode_enabled


router = APIRouter()
developer_router = APIRouter()
workflow = get_agent_workflow(get_workflow_provider())


@router.post("/agent/chat", response_model=None)
def agent_chat(request: AgentChatRequest):
    try:
        selected_object = request.gis_context.selected_object
        print(
            "[agent/chat] request "
            f"query={request.query!r} "
            f"selected_object={selected_object.model_dump(exclude_none=True) if selected_object else None}"
        )
        response, _, _ = run_agent_with_persistence(workflow, request)
        print(
            "[agent/chat] final "
            f"route_type={response.route_type!r} "
            f"ui_events_count={len(response.ui_events)} "
            f"ui_events={[event.model_dump(exclude_none=True) for event in response.ui_events]}"
        )
        response = attach_customer_final_response(response, request.query)
        return response if developer_mode_enabled() else response.final_response
    except Exception as exc:
        # Ownership and malformed conversation errors are stable API errors;
        # workflow failures are converted and persisted by the integration.
        from app.conversations import ConversationError

        if isinstance(exc, ConversationError):
            detail = exc.code if developer_mode_enabled() else map_internal_error(exc.code).message
            raise HTTPException(status_code=exc.status_code, detail=detail)
        error_summary = _safe_error_summary(exc)
        print(f"/agent/chat failed: {error_summary}")
        detail = "AGENT_REQUEST_FAILED" if developer_mode_enabled() else map_internal_error("AGENT_REQUEST_FAILED").message
        raise HTTPException(status_code=500, detail=detail)


@developer_router.get("/trace/{trace_id}", response_model=TraceRecord)
def get_trace(trace_id: str) -> TraceRecord:
    trace = workflow.trace_store.get(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


def _safe_error_summary(exc: Exception) -> str:
    return safe_error_summary(exc)
