from __future__ import annotations

from typing import Any


BRIDGE_VERSION = "0.1.0"

CUSTOMER_WEBGL_OPERATIONS: tuple[dict[str, Any], ...] = (
    {"key": "find_object", "label": "查找场景对象", "event_types": ("search_business_objects",)},
    {"key": "locate_object", "label": "定位场景对象", "event_types": ("fly_to", "fly_to_object", "fly_to_coordinates")},
    {"key": "highlight_object", "label": "高亮场景对象", "event_types": ("gis_highlight",)},
    {"key": "read_properties", "label": "查看对象属性", "event_types": ("get_object_properties",)},
    {"key": "layer_visibility", "label": "显示或隐藏图层", "event_types": ("set_layer_visibility",)},
    {"key": "reset_view", "label": "恢复场景视角", "event_types": ("reset_view",)},
    {"key": "screenshot", "label": "生成场景截图", "event_types": ("screenshot",)},
)


SUPPORTED_WEBGL_UI_EVENTS: list[dict[str, Any]] = [
    {
        "event_type": "fly_to",
        "description": "Move the WebGL camera to an object or coordinates.",
        "payload_schema": {
            "target_type": "string|null",
            "object_id": "string|null",
            "coordinates": "number[2]|number[3]|null",
            "zoom": "number|null",
            "duration_ms": "integer|null",
        },
        "example": {
            "event_type": "fly_to",
            "payload": {
                "target_type": "feeder",
                "object_id": "F001",
                "coordinates": [114.05, 22.55, 800],
                "zoom": 14,
                "duration_ms": 1200,
            },
        },
    },
    {
        "event_type": "gis_highlight",
        "description": "Highlight an object in a Cesium layer or business layer.",
        "payload_schema": {
            "object_id": "string",
            "object_type": "string|null",
            "layer_id": "string|null",
            "style": "object|null",
        },
        "example": {
            "event_type": "gis_highlight",
            "payload": {
                "object_id": "F001",
                "object_type": "feeder",
                "layer_id": "feeder_layer",
                "style": {"color": "#ffcc00"},
            },
        },
    },
    {
        "event_type": "open_panel",
        "description": "Open a WebGL business panel with Agent-provided payload.",
        "payload_schema": {
            "panel_type": "string",
            "object_id": "string|null",
            "title": "string|null",
            "payload": "object|null",
        },
        "example": {
            "event_type": "open_panel",
            "payload": {
                "panel_type": "feeder_realtime_data",
                "object_id": "F001",
                "title": "馈线实时数据",
                "payload": {"load_rate": 0.76},
            },
        },
    },
    {
        "event_type": "set_layer_visibility",
        "description": "Show or hide a WebGL layer.",
        "payload_schema": {"layer_id": "string", "visible": "boolean"},
        "example": {
            "event_type": "set_layer_visibility",
            "payload": {"layer_id": "feeder_layer", "visible": True},
        },
    },
    {
        "event_type": "show_message",
        "description": "Show a non-blocking message in the WebGL shell.",
        "payload_schema": {"level": "info|warning|error|success", "message": "string"},
        "example": {
            "event_type": "show_message",
            "payload": {"level": "warning", "message": "该请求已被安全规则拦截。"},
        },
    },
    {
        "event_type": "clear_highlight",
        "description": "Clear the current highlight or a specific highlighted object.",
        "payload_schema": {"object_id": "string|null", "layer_id": "string|null"},
        "example": {
            "event_type": "clear_highlight",
            "payload": {"object_id": "F001", "layer_id": "feeder_layer"},
        },
    },
]


def get_webgl_contract() -> dict[str, Any]:
    return {
        "ok": True,
        "provider": "webgl_agent_bridge",
        "bridge_version": BRIDGE_VERSION,
        "agent_endpoint": "/agent/chat",
        "debug_endpoint": "/debug/webgl-contract",
        "context_schema": {
            "selected_object": {
                "object_id": "string|null",
                "object_type": "string|null",
                "object_name": "string|null",
                "layer_id": "string|null",
                "properties": "object",
            },
            "map_state": {
                "center": "number[2]|number[3]|null",
                "zoom": "number|null",
                "camera": "object",
                "bounds": "object|array|null",
            },
            "active_layers": "string[]",
            "user_action": "string|null",
            "page_state": {"current_panel": "string|null", "selected_tool": "string|null"},
            "frontend_metadata": {
                "webgl_version": "string|null",
                "project_id": "string|null",
                "scene_id": "string|null",
            },
        },
        "supported_events": SUPPORTED_WEBGL_UI_EVENTS,
        "notes": [
            "The bridge does not execute production control operations.",
            "The bridge does not require API keys in the browser.",
            "WebGL-side handlers must be registered by the host application.",
            "Hybrid routing is used: deterministic router handles explicit WebGL anchors; QwenPlanner handles complex semantic planning.",
        ],
    }


def get_customer_webgl_operations() -> list[dict[str, Any]]:
    return [dict(item) for item in CUSTOMER_WEBGL_OPERATIONS]
