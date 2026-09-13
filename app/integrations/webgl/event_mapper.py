from __future__ import annotations

from typing import Any


SUPPORTED_WEBGL_EVENT_TYPES = {
    "fly_to",
    "gis_highlight",
    "open_panel",
    "show_message",
    "set_layer_visibility",
    "clear_highlight",
    "search_business_objects",
    "query_nearby_objects",
    "create_buffer",
    "get_buffer_state",
    "get_buffer_result",
    "fly_to_buffer",
    "clear_buffer",
    "clear_all_buffers",
    "query_objects_in_buffer",
    "highlight_buffer_query_results",
    "clear_buffer_query_highlight",
}


def map_ui_events_to_webgl_events(ui_events: list[Any] | None) -> list[dict[str, Any]]:
    return [map_ui_event_to_webgl_event(event) for event in (ui_events or [])]


def map_ui_event_to_webgl_event(ui_event: Any) -> dict[str, Any]:
    event = _to_dict(ui_event)
    event_type = event.get("type") or event.get("event_type") or "unknown"
    params = _payload(event)

    if event_type == "fly_to":
        return _webgl_event(
            "fly_to",
            {
                "target_type": params.get("target_type") or params.get("object_type"),
                "object_id": params.get("object_id") or event.get("target"),
                "coordinates": params.get("coordinates") or params.get("center"),
                "zoom": params.get("zoom"),
                "duration_ms": params.get("duration_ms"),
            },
            event,
        )
    if event_type == "gis_highlight":
        return _webgl_event(
            "gis_highlight",
            {
                "object_id": params.get("object_id") or event.get("target"),
                "object_type": params.get("object_type"),
                "layer_id": params.get("layer_id"),
                "style": params.get("style") or {},
            },
            event,
        )
    if event_type == "open_panel":
        return _webgl_event(
            "open_panel",
            {
                "panel_type": params.get("panel_type") or event.get("target"),
                "object_id": params.get("object_id"),
                "title": params.get("title"),
                "payload": params.get("payload") or params,
            },
            event,
        )
    if event_type == "show_message":
        return _webgl_event(
            "show_message",
            {
                "level": params.get("level") or "info",
                "message": params.get("message") or event.get("message") or "",
            },
            event,
        )
    if event_type == "set_layer_visibility":
        return _webgl_event(
            "set_layer_visibility",
            {
                "layer_id": params.get("layer_id") or event.get("target"),
                "visible": params.get("visible", True),
            },
            event,
        )
    if event_type == "clear_highlight":
        return _webgl_event(
            "clear_highlight",
            {
                "object_id": params.get("object_id"),
                "layer_id": params.get("layer_id"),
            },
            event,
        )
    if event_type in {
        "search_business_objects", "query_nearby_objects", "create_buffer",
        "get_buffer_state", "get_buffer_result", "fly_to_buffer", "clear_buffer",
        "clear_all_buffers", "query_objects_in_buffer",
        "highlight_buffer_query_results", "clear_buffer_query_highlight",
    }:
        return _webgl_event(event_type, params, event)
    if event_type == "safety_block":
        return _webgl_event(
            "show_message",
            {
                "level": "warning",
                "message": event.get("message")
                or params.get("message")
                or "该请求涉及生产控制操作，Agent 已拦截。",
            },
            event,
        )
    if event_type == "ask_clarification":
        return _webgl_event(
            "show_message",
            {
                "level": "info",
                "message": event.get("message") or params.get("message") or "请补充必要参数。",
            },
            event,
        )

    return _webgl_event(
        "unsupported_event",
        {
            "original_type": event_type,
            "message": f"Unsupported Agent UIEvent type: {event_type}",
            "original_event": event,
        },
        event,
    )


def _webgl_event(
    event_type: str, payload: dict[str, Any], source_event: dict[str, Any]
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "payload": {key: value for key, value in payload.items() if value is not None},
        "source_event": source_event,
    }


def _to_dict(ui_event: Any) -> dict[str, Any]:
    if isinstance(ui_event, dict):
        return dict(ui_event)
    if hasattr(ui_event, "model_dump"):
        return ui_event.model_dump()
    if hasattr(ui_event, "dict"):
        return ui_event.dict()
    return {"type": "unknown", "value": str(ui_event)}


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    params = event.get("params")
    merged: dict[str, Any] = {}
    if isinstance(params, dict):
        merged.update(params)
    if isinstance(payload, dict):
        merged.update(payload)
    return merged
