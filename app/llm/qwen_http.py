"""Compatibility adapter for the shared Qwen gateway.

Legacy LLM classes keep their public interfaces, but no longer own HTTP
clients or timeout policy.
"""

from app.qwen_gateway import QwenGatewayError, get_qwen_gateway


PREVIEW_LIMIT = 300


def qwen_chat_completion(messages: list[dict[str, str]], caller: str, *, request_id: str = "") -> str:
    purpose = _purpose_for(caller)
    try:
        return get_qwen_gateway().invoke_structured(messages, purpose=purpose, request_id=request_id)
    except QwenGatewayError as exc:
        # Preserve the legacy exception wording for callers while exposing the
        # stable code through ``gateway_error`` for new integrations.
        error = RuntimeError(f"{caller} HTTP request failed: {exc.code}: {str(exc)}")
        setattr(error, "gateway_error", exc)
        raise error from exc


def preview_text(text: str) -> str:
    return (text or "")[:PREVIEW_LIMIT]


def _purpose_for(caller: str) -> str:
    value = (caller or "").lower()
    if "router" in value:
        return "router"
    if "semantic" in value:
        return "schema_repair" if "repair" in value else "semantic_planner"
    if "rag" in value:
        return "rag_answer"
    if "summar" in value:
        return "summarizer"
    if "repair" in value:
        return "schema_repair"
    if "planner" in value:
        return "complex_planner"
    return "general_chat"
