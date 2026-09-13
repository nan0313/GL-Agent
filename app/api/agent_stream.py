from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.agent_api import _safe_error_summary, workflow
from app.answering import attach_customer_final_response
from app.conversations import ConversationError, resolve_conversation_id
from app.conversations.integration import run_agent_with_persistence
from app.product.error_mapper import map_internal_error
from app.product.answer_stream import answer_delta_stream
from app.release_profile import developer_mode_enabled
from app.schemas.request import AgentChatRequest


router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("/api/agent/chat/stream")
async def agent_chat_stream_api(request: Request, payload: AgentChatRequest) -> StreamingResponse:
    return _stream_response(request, payload, internal=False)


@router.post("/agent/chat/stream")
async def agent_chat_stream_compat(request: Request, payload: AgentChatRequest) -> StreamingResponse:
    return _stream_response(request, payload, internal=developer_mode_enabled())


def _stream_response(request: Request, payload: AgentChatRequest, *, internal: bool) -> StreamingResponse:
    try:
        resolve_conversation_id(payload)
    except ConversationError as exc:
        from fastapi import HTTPException

        detail = exc.code if internal else map_internal_error(exc.code).message
        raise HTTPException(status_code=exc.status_code, detail=detail)
    return StreamingResponse(
        _internal_event_generator(request, payload) if internal else _customer_event_generator(request, payload),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _customer_event_generator(request: Request, payload: AgentChatRequest) -> AsyncIterator[str]:
    """Stable public stream; internal execution events never enter this path."""
    request_id = _request_id_from_payload(payload) or f"req_{uuid4().hex}"
    # SSE comment keeps intermediaries and the browser connection alive without
    # introducing a customer event outside the public contract.
    yield ": connected\n\n"
    if await request.is_disconnected():
        return
    loop = asyncio.get_running_loop()
    deltas: asyncio.Queue[str] = asyncio.Queue()

    def publish_delta(delta: str) -> None:
        loop.call_soon_threadsafe(deltas.put_nowait, delta)

    def run_with_stream():
        with answer_delta_stream(publish_delta):
            return run_agent_with_persistence(workflow, payload, request_id=request_id)

    task = asyncio.create_task(asyncio.to_thread(run_with_stream))
    streamed_delta_count = 0
    try:
        while not task.done() or not deltas.empty():
            if await request.is_disconnected():
                return
            try:
                delta = await asyncio.wait_for(deltas.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if not _buffer_final_answer():
                streamed_delta_count += 1
                yield _sse("answer_delta", {"delta": delta})
        response, _, _ = await task
        response = attach_customer_final_response(response, payload.query)
        final = response.final_response
        if final is None:
            raise RuntimeError("FINAL_RESPONSE_UNAVAILABLE")
    except asyncio.CancelledError:
        raise
    except Exception:
        error = map_internal_error("REQUEST_FAILED")
        yield _sse("user_facing_error", error.model_dump())
        return

    if await request.is_disconnected():
        return
    if final.error is not None:
        yield _sse("user_facing_error", final.error.model_dump())
    # Business actions execute in the WebGL runtime.  Keep their user-facing
    # sentence buffered so the UI never claims success before the operation
    # has actually completed.
    elif not _buffer_final_answer() and not final.business_actions and streamed_delta_count == 0:
        yield _sse("answer_delta", {"delta": final.answer_markdown})
    if final.citations:
        yield _sse("citation_metadata", {"citations": [item.model_dump() for item in final.citations]})
    yield _sse("answer_completed", {"answer_markdown": final.answer_markdown})
    yield _sse("final_result", final.model_dump(exclude_none=True))


async def _internal_event_generator(request: Request, payload: AgentChatRequest) -> AsyncIterator[str]:
    request_id = _request_id_from_payload(payload) or f"req_{uuid4().hex}"
    conversation_id = resolve_conversation_id(payload)
    answer_parts: list[str] = []
    ui_event_count = 0

    def emit(event_name: str, data: dict[str, Any]) -> str:
        return _sse(event_name, {**data, "request_id": request_id, "conversation_id": conversation_id})

    yield emit(
        "connected",
        {
            "request_id": request_id,
            "conversation_id": conversation_id,
            "timestamp": _now_iso(),
            "transport_mode": "sse_simulated_model_tokens",
            "model_token_streaming": False,
        },
    )
    yield emit("status", {"stage": "understanding", "message": "\u6b63\u5728\u7406\u89e3\u4f60\u7684\u8bf7\u6c42..."})
    document_request = _is_document_request(payload, conversation_id)
    if document_request:
        yield emit("status", {"stage": "reading_document", "message": "正在读取文件..."})
    yield emit("heartbeat", {"timestamp": _now_iso()})

    if await request.is_disconnected():
        return

    try:
        response, conversation_id, request_id = await asyncio.to_thread(
            run_agent_with_persistence,
            workflow,
            payload,
            request_id=request_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield emit(
            "error",
            {
                "error_code": "REQUEST_FAILED",
                "message": "\u8bf7\u6c42\u6267\u884c\u5931\u8d25\uff1a" + _safe_error_summary(exc),
                "recoverable": True,
            },
        )
        return

    if await request.is_disconnected():
        return

    has_map_events = bool(response.ui_events)
    if document_request:
        yield emit("status", {"stage": "analyzing_sections", "message": "正在分析章节..."})
    elif has_map_events:
        yield emit("status", {"stage": "resolving_object", "message": "\u6b63\u5728\u89e3\u6790\u7a7a\u95f4\u5bf9\u8c61..."})
    if has_map_events:
        for item in _object_resolution_events(payload, response):
            yield emit("object_resolution", item)

    semantic = (response.metadata or {}).get("semantic_orchestration")
    if semantic:
        yield emit("semantic_trace", {
            "request_id": request_id,
            "semantic_orchestration": semantic,
            "dialogue_state": response.metadata.get("dialogue_state"),
            "model_participation": response.metadata.get("model_participation"),
            "qwen_invocations": response.metadata.get("qwen_invocations", []),
            "qwen_health": response.metadata.get("qwen_health"),
            "qwen_fallbacks": response.metadata.get("qwen_fallbacks", []),
            "rag": response.metadata.get("rag", {}),
            "rag_retrievals": response.metadata.get("rag_retrievals", []),
            "rag_evidences": response.metadata.get("rag_evidences", []),
            "rag_citations": response.metadata.get("rag_citations", []),
            "rag_fallbacks": response.metadata.get("rag_fallbacks", []),
            "rag_source_operations": response.metadata.get("rag_source_operations", []),
            "rag_index_jobs": response.metadata.get("rag_index_jobs", []),
            "rag_backup_operations": response.metadata.get("rag_backup_operations", []),
            "rag_index_status": response.metadata.get("rag_index_status", {}),
            "clarifications": response.metadata.get("clarifications", []),
        })

    if has_map_events:
        yield emit("status", {"stage": "planning", "message": "\u6b63\u5728\u751f\u6210\u6267\u884c\u8ba1\u5212..."})
    if response.status in {"blocked", "failed"}:
        yield emit(
            "warning" if response.status == "blocked" else "error",
            {
                "code": response.status.upper(),
                "message": response.answer,
                "recoverable": response.status != "blocked",
            },
        )

    yield emit("status", {"stage": "generating_summary" if document_request else "generating", "message": "正在生成摘要..." if document_request and "summary" in str((response.metadata or {}).get("document_intent") or "") else "\u6b63\u5728\u751f\u6210\u56de\u7b54..."})
    for index, chunk in enumerate(_answer_chunks(response.answer), start=1):
        if await request.is_disconnected():
            return
        answer_parts.append(chunk)
        yield emit("token", {"delta": chunk, "index": index})

    if has_map_events:
        yield emit("status", {"stage": "executing", "message": "\u6b63\u5728\u51c6\u5907\u5730\u56fe\u64cd\u4f5c..."})
    for sequence, event in enumerate(response.ui_events or [], start=1):
        if await request.is_disconnected():
            return
        ui_event_count += 1
        yield emit(
            "ui_event",
            {
                "type": event.type,
                "payload": _event_payload(event),
                "sequence": sequence,
                "request_id": request_id,
                "trace_id": response.trace_id,
                "event_id": f"{request_id}:{response.trace_id}:ui_event:{sequence}",
                "source_event": event.model_dump(exclude_none=True),
            },
        )

    yield emit("status", {"stage": "completed", "message": "\u5df2\u5b8c\u6210\u3002"})
    yield emit(
        "done",
        {
            "request_id": request_id,
            "trace_id": response.trace_id,
            "answer": response.answer,
            "finish_reason": "stop",
            "usage": {},
            "ui_event_count": ui_event_count,
            "transport_mode": "sse_simulated_model_tokens",
            "model_token_streaming": False,
        },
    )


def _sse(event_name: str, data: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_id_from_payload(payload: AgentChatRequest) -> str | None:
    extra = getattr(payload, "model_extra", {}) or {}
    value = extra.get("request_id")
    if value:
        return str(value)
    return None


def _buffer_final_answer() -> bool:
    return os.getenv("EV_AGENT_BUFFER_FINAL_ANSWER", "false").strip().lower() in {"1", "true", "yes", "on"}


def _is_document_request(payload: AgentChatRequest, conversation_id: str) -> bool:
    if any(term in payload.query.casefold() for term in ("文件", "附件", "文档", "pdf", "word", "上传")):
        return True
    try:
        from app.dialogue.store import default_dialogue_store

        return bool(default_dialogue_store.get(conversation_id).document_context.selected_attachment_ids)
    except Exception:
        return False


def _answer_chunks(answer: str) -> list[str]:
    text = answer or ""
    if not text:
        return [""]
    if len(text) <= 56:
        midpoint = max(1, len(text) // 2)
        return [text[:midpoint], text[midpoint:]]
    chunks: list[str] = []
    current = ""
    for char in text:
        current += char
        if char in "\u3002\uff01\uff1f\uff1b\n" or len(current) >= 28:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def _event_payload(event: Any) -> dict[str, Any]:
    data = event.model_dump(exclude_none=True) if hasattr(event, "model_dump") else dict(event)
    payload: dict[str, Any] = {}
    params = data.get("params")
    if isinstance(params, dict):
        payload.update(params)
    event_payload = data.get("payload")
    if isinstance(event_payload, dict):
        payload.update(event_payload)
    if data.get("target") and "object_id" not in payload and "target" not in payload:
        payload["target"] = data["target"]
    return payload


def _object_resolution_events(payload: AgentChatRequest, response: Any) -> list[dict[str, Any]]:
    metadata = getattr(response, "metadata", {}) or {}
    candidates = metadata.get("object_resolution_results") or metadata.get("object_resolution") or []
    if isinstance(candidates, dict):
        candidates = [candidates]
    events = [_normalize_object_resolution(item, payload.query) for item in candidates if isinstance(item, dict)]
    selected = payload.gis_context.selected_object
    if not events and selected:
        selected_dict = selected.model_dump(exclude_none=True)
        events.append(
            {
                "query": payload.query,
                "status": "resolved",
                "object_id": selected_dict.get("object_id"),
                "name": selected_dict.get("object_name") or selected_dict.get("name"),
                "business_type": selected_dict.get("object_type"),
                "layer_name": selected_dict.get("layer_name") or selected_dict.get("layer_id"),
                "business_id": selected_dict.get("business_id"),
                "business_id_source": selected_dict.get("business_id_source"),
                "stable_id": bool(selected_dict.get("stable_id", selected_dict.get("object_id"))),
                "id_scope": selected_dict.get("id_scope") or "business",
                "properties": selected_dict.get("properties") or {},
            }
        )
    return events


def _normalize_object_resolution(item: dict[str, Any], query: str) -> dict[str, Any]:
    return {
        "query": item.get("query") or query,
        "status": item.get("status") or "resolved",
        "object_id": item.get("object_id") or item.get("id"),
        "name": item.get("name") or item.get("object_name"),
        "business_type": item.get("business_type") or item.get("object_type") or item.get("type"),
        "layer_name": item.get("layer_name") or item.get("data_source_name") or item.get("layer_id"),
        "data_source_name": item.get("data_source_name"),
        "business_id": item.get("business_id"),
        "business_id_source": item.get("business_id_source"),
        "stable_id": bool(item.get("stable_id", item.get("object_id") or item.get("id"))),
        "id_scope": item.get("id_scope") or "business",
        "properties": item.get("properties") or {},
    }
