from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("AGENT_WORKFLOW_PROVIDER", "langgraph")
os.environ.setdefault("LLM_PLANNER_PROVIDER", "lcel_mock")
os.environ["EV_AGENT_MODE"] = "developer"

from app.main import app  # noqa: E402


client = TestClient(app)


def _payload(query: str, selected_object: dict | None = None) -> dict:
    return {
        "session_id": "smoke-webgl",
        "user_id": "smoke-user",
        "role": "developer",
        "query": query,
        "gis_context": {
            "selected_object": selected_object,
            "active_layers": [],
            "map_center": [114.05, 22.55],
            "zoom": 14,
        },
    }


def _chat(query: str, selected_object: dict | None = None) -> dict:
    response = client.post("/agent/chat", json=_payload(query, selected_object))
    assert response.status_code == 200, response.text
    return response.json()


def _assert_event(query: str, expected_type: str, selected_object: dict | None = None) -> dict:
    data = _chat(query, selected_object)
    assert data["route_type"] == "anchor_task", data
    assert data["metadata"]["skill_id"] == "deterministic_webgl_anchor", data
    assert data["ui_events"], data
    assert data["ui_events"][0]["type"] == expected_type, data
    return data


def main() -> int:
    selected = {
        "object_id": "test-object-001",
        "object_type": "mock",
        "object_name": "测试对象",
        "properties": {"source": "smoke"},
    }

    cases = [
        ("获取图层列表", "get_layer_list"),
        ("隐藏影像图层", "set_layer_visibility"),
        ("显示地形图层", "set_layer_visibility"),
        ("当前相机状态", "get_camera_state"),
        ("复位视角", "reset_view"),
        ("飞到东经116.39北纬39.90高度1000", "fly_to_coordinates"),
        ("截图", "screenshot"),
    ]
    for query, expected_type in cases:
        data = _assert_event(query, expected_type)
        event = data["ui_events"][0]
        assert isinstance(event.get("payload"), dict), event

    search = _assert_event("查找龙王站", "search_business_objects")
    assert search["ui_events"][0]["payload"]["query"] == "龙王站"

    object_flow = _chat("飞到黑龙江省并高亮")
    assert [event["type"] for event in object_flow["ui_events"]] == [
        "fly_to_object", "gis_highlight", "get_object_properties",
    ]
    assert [event["sequence"] for event in object_flow["ui_events"]] == [1, 2, 3]

    cleared = _assert_event("取消黑龙江省高亮", "clear_highlight")
    assert cleared["ui_events"][0]["payload"]["object_name"] == "黑龙江省"

    buffered = _assert_event("以黑龙江省生成500米缓冲区", "create_buffer")
    assert buffered["ui_events"][0]["payload"]["distance_m"] == 500

    admin = _chat("飞到哈尔滨并高亮")
    assert [event["type"] for event in admin["ui_events"]] == [
        "locate_admin_region", "highlight_admin_boundary",
    ]
    assert [event["sequence"] for event in admin["ui_events"]] == [1, 2]

    selected_data = _assert_event("查看当前选中对象", "get_object_properties", selected)
    selected_event = selected_data["ui_events"][0]
    assert selected_event["target"] == "selected_object"
    assert selected_event["payload"]["selection_source"] == "current_webgl_selection"
    assert selected_event["payload"]["matched_by"] == "selection_state"

    missing = _assert_event("查看当前选中对象", "get_object_properties")
    missing_event = missing["ui_events"][0]
    assert missing_event["target"] == "selected_object"
    assert missing_event["payload"]["selection_source"] == "current_webgl_selection"
    assert missing_event["payload"]["matched_by"] == "selection_state"

    general = _chat("你能做什么")
    assert general["route_type"] == "general_chat", general
    assert general["ui_events"] == [], general

    print("webgl agent smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
