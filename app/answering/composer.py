from __future__ import annotations

import re
from typing import Any

from app.answering.planner import AnswerPlanner
from app.product.error_mapper import map_internal_error
from app.schemas.response import AgentResponse, BusinessAction, Citation, FinalResponse, UIEvent


_ACTION_LABELS: dict[str, tuple[str, str]] = {
    "fly_to": ("view_location", "查看位置"),
    "fly_to_object": ("view_location", "查看位置"),
    "fly_to_coordinates": ("view_location", "查看位置"),
    "gis_highlight": ("highlight_object", "高亮对象"),
    "clear_highlight": ("clear_highlight", "清除高亮"),
    "get_object_properties": ("view_properties", "查看属性"),
    "open_panel": ("open_context", "查看详情"),
    "show_table": ("show_table", "查看表格"),
    "reset_view": ("reset_view", "复位视角"),
    "locate_admin_region": ("view_admin_region", "查看位置"),
    "highlight_admin_boundary": ("highlight_boundary", "高亮边界"),
    "get_admin_region_properties": ("view_admin_properties", "查看属性"),
    "screenshot": ("capture_scene", "下载截图"),
    "search_business_objects": ("find_object", "查找对象"),
    "query_nearby_objects": ("nearby_objects", "查看附近对象"),
    "create_buffer": ("create_range", "查看分析范围"),
    "query_objects_in_buffer": ("range_results", "查看范围结果"),
}
_PRIVATE_PAYLOAD_KEYS = {
    "source_entry", "source_event", "trace_id", "request_id", "event_id", "sequence",
    "tool", "tool_name", "tool_args", "debug", "stack", "error_code",
}


def attach_customer_final_response(response: AgentResponse, query: str) -> AgentResponse:
    response.final_response = compose_final_response(query, response)
    return response


def compose_final_response(query: str, response: AgentResponse) -> FinalResponse:
    plan = AnswerPlanner().plan(query, response)
    citations = _citations(response.evidence_list)
    answer = _customer_answer(response, plan.response_type)
    actions = [_business_action(event, index) for index, event in enumerate(response.ui_events, start=1)]
    actions = [action for action in actions if action is not None]
    error = None
    if response.status in {"failed", "blocked"}:
        extra = getattr(response, "model_extra", {}) or {}
        code = (
            (response.metadata or {}).get("error_code")
            or extra.get("error_code")
            or extra.get("code")
            or extra.get("reason")
            or response.status
        )
        error = map_internal_error(str(code), response.answer)
        answer = error.message
    return FinalResponse(
        answer_markdown=answer,
        citations=citations,
        attachments=_safe_attachments(response.metadata or {}),
        business_actions=actions,
        warnings=[],
        error=error,
    )


def _customer_answer(response: AgentResponse, response_type: str) -> str:
    text = str(response.answer or "").strip()
    text = re.sub(r"\[E(\d+)\]", r"[\1]", text)
    if response_type == "business_result" and response.ui_events:
        event_types = {event.type for event in response.ui_events}
        if ({"fly_to_object", "fly_to"} & event_types) and "gis_highlight" in event_types:
            return "已定位并高亮目标对象。"
        if "fly_to_object" in event_types or "fly_to" in event_types or "fly_to_coordinates" in event_types or "locate_admin_region" in event_types:
            return "已定位目标位置。"
        if "gis_highlight" in event_types or "highlight_admin_boundary" in event_types:
            return "已高亮目标对象。"
        if "get_object_properties" in event_types or "get_admin_region_properties" in event_types:
            return "已读取目标对象属性。"
        if "search_business_objects" in event_types:
            return "已完成对象查找。"
        if "reset_view" in event_types:
            return "视角已复位。"
        if "screenshot" in event_types:
            return "已生成当前场景截图。"
        return "已完成请求。"
    text = re.sub(r"已生成[^。]*(?:WebGL|Tool)[^。]*事件。?", "已完成请求。", text, flags=re.IGNORECASE)
    text = re.sub(r"正在(?:调用|执行)[^。]*(?:Tool|工具)[^。]*。?", "", text, flags=re.IGNORECASE)
    return text or "已完成请求。"


def _citations(evidence: list[Any]) -> list[Citation]:
    result: list[Citation] = []
    for index, item in enumerate(evidence or [], start=1):
        data = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)
        source = str(data.get("source_name") or data.get("source") or data.get("title") or "知识库")
        section_path = data.get("section_path") or []
        section = " / ".join(str(value) for value in section_path) if section_path else None
        page_start = data.get("page_start")
        page_end = data.get("page_end")
        page = None
        if page_start is not None:
            page = str(page_start) if page_end in (None, page_start) else f"{page_start}-{page_end}"
        excerpt = str(data.get("snippet") or data.get("content") or data.get("summary") or "").strip()
        result.append(Citation(
            index=index,
            label=f"[{index}]",
            source_name=source,
            title=str(data.get("title") or source),
            section=section,
            page=page,
            excerpt=excerpt[:600] or None,
        ))
    return result


def _business_action(event: UIEvent, index: int) -> BusinessAction | None:
    mapping = _ACTION_LABELS.get(event.type)
    if mapping is None:
        return None
    kind, label = mapping
    raw = {**(event.params or {}), **(event.payload or {})}
    if event.target and "target" not in raw:
        raw["target"] = event.target
    payload = {key: _safe_value(value) for key, value in raw.items() if key not in _PRIVATE_PAYLOAD_KEYS}
    return BusinessAction(action_id=f"action_{index}", kind=kind, label=label, payload=payload)


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _safe_value(item) for key, item in value.items() if key not in _PRIVATE_PAYLOAD_KEYS}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_attachments(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    attachments = metadata.get("attachments") or metadata.get("selected_attachments") or []
    result: list[dict[str, Any]] = []
    for item in attachments if isinstance(attachments, list) else []:
        if not isinstance(item, dict):
            continue
        result.append({
            key: item.get(key)
            for key in ("filename", "status", "message")
            if item.get(key) is not None
        })
    return result
