import re
from typing import Any

from app.schemas.request import SelectedObject
from app.schemas.response import UIEvent
from app.skills.base import AgentSkill
from app.skills.context import SkillContext
from app.skills.result import SkillResult
from app.spatial import RealSpatialObjectIndex, SpatialObjectIndex, default_spatial_index
from app.admin_regions import get_admin_region_provider, normalize_region_name


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


_SELECT_OBJECT_MESSAGE = _u("\\u8bf7\\u5148\\u5728 WebGL \\u573a\\u666f\\u4e2d\\u9009\\u62e9\\u4e00\\u4e2a\\u5bf9\\u8c61\\u3002")
_BASIC_PANEL_TITLE = "open_panel_basic / mock only"
_BASIC_PANEL_ANCHOR = "open_panel_basic"
_MOCK_MODE = "mock"
_SOURCE_MODE = "source_driven"
_REALTIME_TITLE = _u("\\u5b9e\\u65f6\\u6570\\u636e\\u9762\\u677f")
_OBJECT_PANEL_TITLE = _u("\\u5bf9\\u8c61\\u4fe1\\u606f\\u9762\\u677f")
_EVENT_CREATED_PREFIX = _u("\\u5df2\\u751f\\u6210 ")
_EVENT_CREATED_SUFFIX = _u(" \\u7684 WebGL \\u6253\\u5f00\\u4e8b\\u4ef6\\u3002")
_REALTIME_KEYWORDS = [
    _u("\\u5b9e\\u65f6\\u6570\\u636e"),
    _u("\\u5be6\\u6642\\u6578\\u64da"),
    _u("\\u8fd0\\u884c\\u6570\\u636e"),
    _u("\\u8fd0\\u884c\\u60c5\\u51b5"),
]
_DEFAULT_VIEW = {
    "destination": [110.684138, 33.268607, 5000000],
    "orientation": [0, -1.5, 0],
}
_LAYER_ALIASES = {
    _u("\\u5f71\\u50cf"): "IMAGE",
    _u("\\u5730\\u5f62"): "TERRAIN",
    _u("\\u77e2\\u91cf"): "GEOMETRY",
    "E3M": "E3M",
    "e3m": "E3M",
    "OSGB": "OSGB",
    "osgb": "OSGB",
    _u("\\u70b9\\u4e91"): "POINT_CLOUD",
    _u("\\u9f99\\u738b\\u7ad9"): _u("\\u9f99\\u738b\\u7ad9"),
}


class DeterministicWebGLAnchorSkill(AgentSkill):
    skill_id = "deterministic_webgl_anchor"
    name = "Deterministic WebGL Anchor"
    description = "Converts explicit WebGL anchor commands into structured UI events."
    route_type = "anchor_task"

    def execute(self, context: SkillContext) -> SkillResult:
        request = context.request
        query = request.query or ""
        selected_object = request.gis_context.selected_object

        command = _classify_command(query)
        if command in {"create_buffer", "create_buffer_and_query", "query_objects_in_buffer", "highlight_buffer_query_results", "clear_buffer_query_highlight", "get_buffer_state", "get_buffer_result", "fly_to_buffer", "clear_buffer", "clear_all_buffers"}:
            return _buffer_result(command, query)
        if command in {
            "locate_admin_region", "locate_and_highlight_admin_region",
            "highlight_admin_boundary", "query_admin_region_properties",
            "query_last_admin_region_properties", "highlight_last_admin_region",
        }:
            return _admin_region_result(command, query, request.gis_context)
        if command in {"nearby_objects", "radius_search", "nearby_objects_panel", "highlight_nearby_objects", "fly_to_nearby_object"}:
            return _spatial_result(command, query, request.gis_context)

        if command in {"search_business_objects", "search_and_locate_business_object"}:
            object_name = _extract_search_object_name(query)
            if not object_name:
                return _failed_result(command, "OBJECT_SEARCH_QUERY_REQUIRED")
            events = [UIEvent(
                type="search_business_objects",
                target="runtime_business_object_index",
                payload={
                    "query": object_name,
                    "object_type": _extract_object_type_filter(query),
                    "layer_name": None,
                    "limit": 20,
                    "exact_first": True,
                    "source_entry": "viewer.entities/viewer.dataSources/scene.primitives -> findBusinessObjectsByName",
                },
                sequence=1,
            )]
            if command == "search_and_locate_business_object":
                events.append(UIEvent(
                    type="fly_to_object",
                    target="business_object",
                    payload={"object_name": object_name, "duration": 1.5},
                    sequence=2,
                ))
            return _events_result(
                intent=command,
                answer=(f"正在查询“{object_name}”，找到唯一对象后将定位。" if len(events) > 1 else f"正在查询“{object_name}”的真实运行时对象。"),
                events=events,
                deterministic_anchor_id=command,
            )

        if command == "get_layer_list":
            return _event_result(
                intent="get_layer_list",
                answer=_u("\\u5df2\\u751f\\u6210\\u83b7\\u53d6 WebGL \\u56fe\\u5c42\\u5217\\u8868\\u7684\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="get_layer_list",
                    target="webgl_source_layers",
                    payload={
                        "panel_type": "webgl_layer_list",
                        "source_entry": "window.LayerManager plus dist layer-tree data loaded from ./datas/data.json",
                        "fallback": "inspect viewer.dataSources and imageryLayers",
                    },
                ),
                deterministic_anchor_id="get_layer_list",
            )

        if command == "get_layer_tree":
            filters = _extract_layer_tree_filters(query)
            return _event_result(
                intent="get_layer_tree",
                answer=_u("\\u6b63\\u5728\\u8bfb\\u53d6 WebGL \\u5f53\\u524d\\u56fe\\u5c42\\u6811\\u3002"),
                event=UIEvent(
                    type="get_layer_tree",
                    target="webgl_source_layers",
                    payload={
                        **filters,
                        "source_entry": "EVWebGLSourceAnchors.getLayerTree -> LayerManager/viewer collections",
                    },
                ),
                deterministic_anchor_id="get_layer_tree",
            )

        if command == "get_selection_state":
            return _event_result(
                intent="get_selection_state",
                answer=_u("\\u6b63\\u5728\\u8bfb\\u53d6 WebGL \\u771f\\u5b9e\\u9009\\u62e9\\u72b6\\u6001\\u3002"),
                event=UIEvent(
                    type="get_selection_state",
                    target="webgl_selection_state",
                    payload={"source_entry": "EVPickTool/viewer.selectedEntity selected object state"},
                ),
                deterministic_anchor_id="get_selection_state",
            )

        if command == "get_selected_object_properties_runtime":
            return _event_result(
                intent="get_selected_object_properties",
                answer=_u("\\u6b63\\u5728\\u8bfb\\u53d6 WebGL \\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61\\u5c5e\\u6027\\u3002"),
                event=UIEvent(
                    type="get_object_properties",
                    target="selected_object",
                    payload={
                        "selection_source": "current_webgl_selection",
                        "matched_by": "selection_state",
                        "source_entry": "getSelectionState -> getSelectedObjectProperties",
                    },
                ),
                deterministic_anchor_id="get_selected_object_properties",
            )

        if command == "get_last_context_object_properties":
            last_object = _last_context_object(request.gis_context)
            object_id = last_object.get("object_id") if last_object else None
            if not object_id:
                return _failed_result("get_last_context_object_properties", "no_last_context_object")
            return _event_result(
                intent="get_object_properties",
                answer=_u("\\u6b63\\u5728\\u8bfb\\u53d6\\u521a\\u624d\\u5b9a\\u4f4d\\u6216\\u9ad8\\u4eae\\u5bf9\\u8c61\\u7684\\u5c5e\\u6027\\u3002"),
                event=UIEvent(
                    type="get_object_properties",
                    target=object_id,
                    payload={
                        "object_id": object_id,
                        "selection_source": "last_context_object",
                        "matched_by": "session_last_object_id",
                        "source_entry": "panelRuntime.currentObject -> resolveSpatialObject",
                    },
                ),
                deterministic_anchor_id="get_last_context_object_properties",
            )

        if command == "set_layer_visibility":
            visible = not any(word in query for word in [_u("\\u9690\\u85cf"), _u("\\u5173\\u95ed")])
            layer_query = _extract_layer_query(query)
            return _event_result(
                intent="set_layer_visibility",
                answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u56fe\\u5c42\\u663e\\u9690\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="set_layer_visibility",
                    target=layer_query,
                    payload={
                        "layer_id": layer_query,
                        "layer_query": layer_query,
                        "visible": visible,
                        "source_entry": "dist layer-tree Ve.setDataVisible(node, checked); adapter fallback uses window.LayerManager/viewer collections",
                    },
                ),
                deterministic_anchor_id="set_layer_visibility",
            )

        if command == "get_camera_state":
            return _event_result(
                intent="get_camera_state",
                answer=_u("\\u5df2\\u751f\\u6210\\u8bfb\\u53d6 WebGL \\u76f8\\u673a\\u72b6\\u6001\\u7684\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="get_camera_state",
                    target="window.viewer.camera",
                    payload={
                        "panel_type": "camera_state",
                        "source_entry": "window.viewer.camera",
                    },
                ),
                deterministic_anchor_id="get_camera_state",
            )

        if command == "reset_view":
            return _event_result(
                intent="reset_view",
                answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u590d\\u4f4d\\u89c6\\u89d2\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="reset_view",
                    target="default_view",
                    payload={
                        "default_view": _DEFAULT_VIEW,
                        "source_entry": "ApplicationVue/dist/datas/data.json defaultView and window.viewer.camera.flyTo",
                    },
                ),
                deterministic_anchor_id="reset_view",
            )

        if command == "fly_to_coordinates":
            coordinates = _extract_coordinates(query)
            if coordinates is None:
                return _message_result(_u("\\u8bf7\\u63d0\\u4f9b\\u53ef\\u89e3\\u6790\\u7684\\u7ecf\\u7eac\\u5ea6\\uff0c\\u4f8b\\u5982\\uff1a\\u98de\\u5230\\u4e1c\\u7ecf116.39\\u5317\\u7eac39.90\\u3002"), "fly_to_coordinates_missing_params")
            return _event_result(
                intent="fly_to_coordinates",
                answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u5750\\u6807\\u5b9a\\u4f4d\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="fly_to_coordinates",
                    target="window.viewer.camera.flyTo",
                    payload={
                        "lon": coordinates[0],
                        "lat": coordinates[1],
                        "height": coordinates[2],
                        "duration_ms": 1200,
                        "source_entry": "window.viewer.camera.flyTo(Cesium.Cartesian3.fromDegrees(...))",
                    },
                ),
                deterministic_anchor_id="fly_to_coordinates",
            )

        if command == "fly_to_named_object":
            object_name = _extract_named_object(query)
            if not object_name:
                return _message_result(_u("\\u8bf7\\u63d0\\u4f9b\\u8981\\u5b9a\\u4f4d\\u7684\\u4e1a\\u52a1\\u5bf9\\u8c61\\u540d\\u79f0\\u3002"), "fly_to_named_object_missing_name")
            return _event_result(
                intent="fly_to_object",
                answer=_u("\\u6b63\\u5728\\u67e5\\u627e\\u201c") + object_name + _u("\\u201d\\uff0c\\u627e\\u5230\\u540e\\u5c06\\u4f7f\\u7528\\u4e1a\\u52a1\\u5bf9\\u8c61\\u5b9a\\u4f4d\\u3002"),
                event=UIEvent(
                    type="fly_to_object",
                    target="business_object",
                    payload={
                        "object_name": object_name,
                        "duration": 1.5,
                        "source_entry": "business_object_name -> findBusinessObjectsByName -> fly_to_object",
                    },
                ),
                deterministic_anchor_id="fly_to_object_by_name",
            )

        if command == "fly_to_and_highlight_named_object":
            object_name = _extract_named_object(query)
            if not object_name:
                return _message_result(_u("\\u8bf7\\u63d0\\u4f9b\\u8981\\u5b9a\\u4f4d\\u548c\\u9ad8\\u4eae\\u7684\\u4e1a\\u52a1\\u5bf9\\u8c61\\u540d\\u79f0\\u3002"), "fly_to_and_highlight_named_object_missing_name")
            return _events_result(
                intent="fly_to_and_highlight_object",
                answer=_u("\\u6b63\\u5728\\u67e5\\u627e\\u201c") + object_name + _u("\\u201d\\uff0c\\u627e\\u5230\\u540e\\u5c06\\u5148\\u5b9a\\u4f4d\\u518d\\u9ad8\\u4eae\\u3002"),
                events=[
                    UIEvent(
                        type="fly_to_object",
                        target="business_object",
                        payload={
                            "object_name": object_name,
                            "duration": 1.5,
                            "source_entry": "business_object_name -> findBusinessObjectsByName -> fly_to_object",
                        },
                        sequence=1,
                    ),
                    UIEvent(
                        type="gis_highlight",
                        target="business_object",
                        payload={
                            "object_name": object_name,
                            "source_entry": "business_object_name -> findBusinessObjectsByName -> gis_highlight",
                        },
                        sequence=2,
                    ),
                    UIEvent(
                        type="get_object_properties",
                        target="business_object",
                        payload={
                            "object_name": object_name,
                            "source_entry": "business_object_name -> findBusinessObjectsByName -> get_object_properties",
                        },
                        sequence=3,
                    ),
                ],
                deterministic_anchor_id="fly_to_and_highlight_object_by_name",
            )

        if command == "highlight_named_object":
            object_name = _extract_named_object(query)
            if not object_name:
                return _message_result(_u("\\u8bf7\\u63d0\\u4f9b\\u8981\\u9ad8\\u4eae\\u7684\\u4e1a\\u52a1\\u5bf9\\u8c61\\u540d\\u79f0\\u3002"), "highlight_named_object_missing_name")
            return _event_result(
                intent="highlight_object",
                answer=_u("\\u6b63\\u5728\\u67e5\\u627e\\u201c") + object_name + _u("\\u201d\\uff0c\\u627e\\u5230\\u540e\\u5c06\\u9ad8\\u4eae\\u8be5\\u4e1a\\u52a1\\u5bf9\\u8c61\\u3002"),
                event=UIEvent(
                    type="gis_highlight",
                    target="business_object",
                    payload={
                        "object_name": object_name,
                        "source_entry": "business_object_name -> findBusinessObjectsByName -> gis_highlight",
                    },
                ),
                deterministic_anchor_id="highlight_object_by_name",
            )

        if command == "get_named_object_properties":
            object_name = _extract_named_object(query)
            if not object_name:
                return _message_result(_u("\\u8bf7\\u63d0\\u4f9b\\u8981\\u67e5\\u770b\\u5c5e\\u6027\\u7684\\u4e1a\\u52a1\\u5bf9\\u8c61\\u540d\\u79f0\\u3002"), "get_named_object_properties_missing_name")
            return _event_result(
                intent="get_object_properties",
                answer=_u("\\u6b63\\u5728\\u67e5\\u627e\\u201c") + object_name + _u("\\u201d\\uff0c\\u627e\\u5230\\u540e\\u5c06\\u8bfb\\u53d6\\u5bf9\\u8c61\\u5c5e\\u6027\\u3002"),
                event=UIEvent(
                    type="get_object_properties",
                    target="business_object",
                    payload={
                        "object_name": object_name,
                        "source_entry": "business_object_name -> findBusinessObjectsByName -> get_object_properties",
                    },
                ),
                deterministic_anchor_id="get_object_properties_by_name",
            )

        if command == "clear_named_object_highlight":
            object_name = _extract_named_object(query)
            if not object_name:
                return _message_result(_u("\\u8bf7\\u63d0\\u4f9b\\u8981\\u53d6\\u6d88\\u9ad8\\u4eae\\u7684\\u4e1a\\u52a1\\u5bf9\\u8c61\\u540d\\u79f0\\u3002"), "clear_named_object_highlight_missing_name")
            return _event_result(
                intent="clear_highlight",
                answer=_u("\\u6b63\\u5728\\u67e5\\u627e\\u201c") + object_name + _u("\\u201d\\uff0c\\u627e\\u5230\\u540e\\u5c06\\u6e05\\u9664\\u8be5\\u5bf9\\u8c61\\u9ad8\\u4eae\\u3002"),
                event=UIEvent(
                    type="clear_highlight",
                    target="business_object",
                    payload={
                        "object_name": object_name,
                        "source_entry": "business_object_name -> findBusinessObjectsByName -> clear_highlight",
                    },
                ),
                deterministic_anchor_id="clear_highlight_by_name",
            )
        if command == "fly_to_selected_object":
            if selected_object is None:
                return _failed_result("fly_to_selected_object", "no_selected_object")
            coordinates = _selected_object_coordinates(selected_object)
            if coordinates is None:
                return _failed_result("fly_to_selected_object", "missing_position")
            return _event_result(
                intent="fly_to_selected_object",
                answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u5bf9\\u8c61\\u5b9a\\u4f4d\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="fly_to_coordinates",
                    target="window.viewer.camera.flyTo",
                    payload={
                        "lon": coordinates[0],
                        "lat": coordinates[1],
                        "height": coordinates[2],
                        "duration_ms": 1500,
                        "source_entry": "selected_object longitude/latitude -> fly_to_coordinates",
                        "object_id": selected_object.object_id,
                    },
                ),
                deterministic_anchor_id="fly_to_object",
            )

        if command == "highlight_selected_object":
            if selected_object is None:
                return _failed_result("highlight_selected_object", "no_selected_object")
            selected_payload = _selected_object_payload(selected_object)
            return _event_result(
                intent="highlight_selected_object",
                answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u5bf9\\u8c61\\u9ad8\\u4eae\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="gis_highlight",
                    target=selected_payload.get("object_id"),
                    payload={
                        "object_id": selected_payload.get("object_id"),
                        "object_type": selected_payload.get("object_type"),
                        "layer_id": selected_payload.get("layer_id"),
                        "source_entry": "selected_object -> gis_highlight",
                    },
                ),
                deterministic_anchor_id="highlight_object",
            )

        if command == "clear_highlight":
            return _event_result(
                intent="clear_highlight",
                answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u53d6\\u6d88\\u9ad8\\u4eae\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="clear_highlight",
                    target="webgl_highlight_state",
                    payload={"source_entry": "adapter clear_highlight handler"},
                ),
                deterministic_anchor_id="clear_highlight",
            )

        if command == "get_selected_object_properties":
            if selected_object is None:
                return _failed_result("get_selected_object_properties", "no_selected_object")
            object_payload = _selected_object_properties_payload(selected_object)
            return SkillResult(
                status="success",
                intent="get_selected_object_properties",
                scenario="webgl_source_driven_anchor",
                answer=_u("\\u5df2\\u8fd4\\u56de\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61\\u7684\\u5c5e\\u6027\\u4fe1\\u606f\\u3002"),
                tool_calls=[],
                ui_events=[],
                evidence=[],
                missing_params=[],
                risk_level="low",
                manual_check_required=False,
                metadata={
                    "routing_mode": "deterministic",
                    "integration_mode": _SOURCE_MODE,
                    "deterministic_anchor_id": "get_object_properties",
                    "route_name": "webgl_deterministic_anchor",
                    "source": "deterministic_router",
                    "selected_object_present": True,
                    "qwen_planner_used": False,
                },
                object=object_payload,
            )

        if command == "screenshot":
            return _event_result(
                intent="screenshot",
                answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u622a\\u56fe\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="screenshot",
                    target="window.globeScene.screenshot",
                    payload={
                        "source_entry": "window.globeScene.screenshot()",
                        "fallback": "viewer.scene.canvas.toDataURL",
                    },
                ),
                deterministic_anchor_id="screenshot",
            )

        if command == "get_selected_object":
            if selected_object is None:
                return _message_result(_SELECT_OBJECT_MESSAGE, "show_message")
            selected_payload = _selected_object_payload(selected_object)
            return _event_result(
                intent="get_selected_object",
                answer=_u("\\u5df2\\u751f\\u6210\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61\\u4fe1\\u606f\\u9762\\u677f\\u4e8b\\u4ef6\\u3002"),
                event=UIEvent(
                    type="open_panel",
                    target="selected_object_info",
                    anchor="selected_object_info",
                    title="selected_object_info / source context",
                    payload={
                        "panel_type": "selected_object_info",
                        "title": "selected_object_info / source context",
                        "mode": "partial",
                        "payload": {
                            "source_entry": "webgl_agent_bootstrap getSelectedObject() from window.currentSelectedObject / __EV_AGENT_SELECTED_OBJECT__",
                            "selected_object": selected_payload,
                        },
                    },
                ),
                deterministic_anchor_id="get_selected_object",
            )

        panel_type = _BASIC_PANEL_ANCHOR

        if selected_object is None:
            return _message_result(_SELECT_OBJECT_MESSAGE, "show_message")

        selected_payload = _selected_object_payload(selected_object)
        title = _BASIC_PANEL_TITLE
        payload = {
            "panel_type": panel_type,
            "object_id": selected_payload["object_id"],
            "object_type": selected_payload["object_type"],
            "object_name": selected_payload["object_name"],
            "title": title,
            "mode": _MOCK_MODE,
            "payload": {
                "source": "deterministic_router",
                "mode": _MOCK_MODE,
                "selected_object": selected_payload,
            },
        }

        return SkillResult(
            status="success",
            intent=panel_type,
            scenario="webgl_explicit_anchor",
            answer=f"{_EVENT_CREATED_PREFIX}{title}{_EVENT_CREATED_SUFFIX}",
            tool_calls=[],
            ui_events=[
                UIEvent(
                    type="open_panel",
                    target=panel_type,
                    anchor=panel_type,
                    title=title,
                    params=payload,
                    payload=payload,
                )
            ],
            evidence=[],
            missing_params=[],
            risk_level="low",
            manual_check_required=False,
            metadata={
                "routing_mode": "deterministic",
                "deterministic_anchor_id": panel_type,
                "route_name": "webgl_deterministic_anchor",
                "source": "deterministic_router",
                "selected_object_present": True,
                "qwen_planner_used": False,
            },
        )


def _panel_type_for_query(query: str) -> str:
    normalized = (query or "").strip().lower()
    if any(keyword in normalized for keyword in _REALTIME_KEYWORDS):
        return "realtime_data_panel"
    return "open_panel_basic"


def _classify_command(query: str) -> str:
    buffer_command = _classify_buffer_command(query)
    if buffer_command:
        return buffer_command
    if _is_buffer_query_result_pronoun_highlight(query):
        return "highlight_buffer_query_results"
    admin_command = _classify_admin_region_command(query)
    if admin_command:
        return admin_command
    if _is_fly_to_nearby_object_query(query):
        return "fly_to_nearby_object"
    if _is_highlight_nearby_objects_query(query):
        return "highlight_nearby_objects"
    if _is_nearby_objects_panel_query(query):
        return "nearby_objects_panel"
    if _is_radius_search_query(query):
        return "radius_search"
    if _is_nearby_objects_query(query):
        return "nearby_objects"
    if _is_fly_to_and_highlight_named_object_query(query):
        return "fly_to_and_highlight_named_object"
    if _is_search_business_object_query(query):
        return "search_and_locate_business_object" if any(word in query for word in ["定位", "飞到"]) else "search_business_objects"
    if _is_layer_tree_query(query):
        return "get_layer_tree"
    if any(word in query for word in [_u("\\u56fe\\u5c42\\u5217\\u8868"), _u("\\u83b7\\u53d6\\u56fe\\u5c42")]):
        return "get_layer_list"
    if any(word in query for word in [_u("\\u9690\\u85cf"), _u("\\u663e\\u793a"), _u("\\u5173\\u95ed")]) and _u("\\u56fe\\u5c42") in query or any(word in query for word in [_u("\\u9690\\u85cf\\u5f71\\u50cf"), _u("\\u663e\\u793a\\u5f71\\u50cf"), _u("\\u9690\\u85cf\\u5730\\u5f62"), _u("\\u663e\\u793a\\u5730\\u5f62")]):
        return "set_layer_visibility"
    if any(word in query for word in [_u("\\u5f53\\u524d\\u76f8\\u673a"), _u("\\u76f8\\u673a\\u72b6\\u6001"), _u("\\u89c6\\u89d2\\u4fe1\\u606f")]):
        return "get_camera_state"
    if any(word in query for word in [_u("\\u590d\\u4f4d\\u89c6\\u89d2"), _u("\\u6062\\u590d\\u89c6\\u89d2"), _u("\\u521d\\u59cb\\u89c6\\u89d2")]):
        return "reset_view"
    if _is_last_context_object_query(query):
        return "get_last_context_object_properties"
    if _is_runtime_selected_object_properties_query(query):
        return "get_selected_object_properties_runtime"
    if _is_selection_state_query(query):
        return "get_selection_state"
    if _is_coordinate_query(query):
        return "fly_to_coordinates"
    if _is_fly_to_named_object_query(query):
        return "fly_to_named_object"
    if _is_clear_named_object_highlight_query(query):
        return "clear_named_object_highlight"
    if _is_highlight_named_object_query(query):
        return "highlight_named_object"
    if _is_get_named_object_properties_query(query):
        return "get_named_object_properties"
    if _is_fly_to_selected_object_query(query):
        return "fly_to_selected_object"
    if _is_highlight_selected_object_query(query):
        return "highlight_selected_object"
    if any(word in query for word in [_u("\\u53d6\\u6d88\\u9ad8\\u4eae"), _u("\\u6e05\\u9664\\u6807\\u8bb0"), _u("\\u6e05\\u9664\\u9ad8\\u4eae")]):
        return "clear_highlight"
    if _is_get_selected_object_properties_query(query):
        return "get_selected_object_properties"
    if _u("\\u622a\\u56fe") in query:
        return "screenshot"
    if any(word in query for word in [_u("\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61"), _u("\\u83b7\\u53d6\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61")]):
        return "get_selected_object"
    return "open_panel_basic"


def _classify_buffer_command(query: str) -> str | None:
    """Route explicit buffer geometry requests before generic object/admin routing."""
    if "缓冲" not in query:
        return None
    if any(phrase in query for phrase in ["查看刚才的缓冲区", "飞到当前缓冲区", "飞到刚才的缓冲区", "定位缓冲区", "显示当前缓冲区"]):
        return "fly_to_buffer"
    if any(phrase in query for phrase in ["清除缓冲区查询结果高亮", "清除缓冲查询高亮", "取消缓冲区查询高亮"]):
        return "clear_buffer_query_highlight"
    is_inside_query = any(phrase in query for phrase in ["缓冲区内", "缓冲区里", "缓冲区中", "缓冲区包含", "缓冲区里面", "缓冲区内部", "缓冲区中的", "缓冲区内的", "刚才缓冲区"])
    has_create = any(word in query for word in ["生成", "创建", "建立", "做一个", "做", "向外缓冲"])
    if is_inside_query and not has_create and "高亮" in query:
        return "highlight_buffer_query_results"
    if is_inside_query and not has_create:
        return "query_objects_in_buffer"
    if any(word in query for word in ["清除所有", "全部清除", "清空所有"]):
        return "clear_all_buffers"
    if any(word in query for word in ["清除", "删除", "清空"]):
        return "clear_buffer"
    if any(word in query for word in ["生成", "创建", "建立", "做一个", "做", "向外缓冲", "缓冲区"]):
        if not any(word in query for word in ["生成", "创建", "建立", "做一个", "做", "向外缓冲"]):
            if any(word in query for word in ["查看", "当前", "几个", "状态", "结果"]):
                return "get_buffer_result" if "结果" in query else "get_buffer_state"
        if any(word in query for word in ["并查找", "并查询", "内部对象", "其中的"]):
            return "create_buffer_and_query"
        return "create_buffer"
    return None


_BUFFER_DISTANCE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(\u516c\u91cc|\u5343\u7c73|\u516c\u5c3a|\u7c73|km|m)", re.I)


def _extract_buffer_distance(query: str) -> tuple[float | None, str | None, str | None]:
    match = _BUFFER_DISTANCE_RE.search(query or "")
    if not match:
        return None, None, "BUFFER_DISTANCE_REQUIRED"
    value = float(match.group(1))
    unit = match.group(2).lower()
    if value <= 0:
        return None, unit, "BUFFER_DISTANCE_INVALID"
    distance_m = value * 1000 if unit in {_u("\\u516c\\u91cc"), _u("\\u5343\\u7c73"), "km"} else value
    if distance_m > 1_000_000:
        return None, unit, "BUFFER_DISTANCE_OUT_OF_RANGE"
    return distance_m, unit, None


def _buffer_result(command: str, query: str) -> SkillResult:
    if command in {"query_objects_in_buffer", "highlight_buffer_query_results", "clear_buffer_query_highlight"}:
        event_type = command
        payload: dict[str, Any] = {}
        if command == "query_objects_in_buffer":
            payload = {
                "buffer_id": None,
                "query": None,
                "object_type": _extract_object_type_filter(query),
                "layer_id": None,
                "layer_name": None,
                "visible_only": True,
                "relation": "intersects",
                "limit": 100,
                "include_properties": True,
            }
            if not payload["object_type"]:
                cleaned = re.sub(r"(?:查询|查找|查看|当前|刚才|这个|缓冲区|里面|内部|其中|内的|中的|包含|哪些|对象|显示|内)", "", query).strip("。？? ")
                payload["query"] = cleaned or None
        answers = {
            "query_objects_in_buffer": "正在从真实运行时空间对象索引中查询缓冲区内对象。",
            "highlight_buffer_query_results": "正在高亮最近一次缓冲区查询的匹配对象。",
            "clear_buffer_query_highlight": "正在清除缓冲区查询结果高亮。",
        }
        return _event_result(command, answers[command], UIEvent(type=event_type, target="latest_buffer_query", payload=payload), command)
    if command in {"get_buffer_state", "get_buffer_result", "fly_to_buffer", "clear_buffer", "clear_all_buffers"}:
        event_type = command
        answers = {
            "get_buffer_state": "正在读取当前缓冲区状态。",
            "get_buffer_result": "正在读取最近一次缓冲区结果。",
            "fly_to_buffer": "正在定位到最近一次缓冲区结果。",
            "clear_buffer": "正在清除当前缓冲区。",
            "clear_all_buffers": "正在清除全部 Agent 缓冲区。",
        }
        return _event_result(command, answers[command], UIEvent(type=event_type, target="ev-agent-buffer", payload={"buffer_id": "latest"} if command == "fly_to_buffer" else {}), command)

    distance_m, input_unit, error = _extract_buffer_distance(query)
    if error:
        return _failed_result("create_buffer", error)
    coordinate_match = re.search(r"(?:经度)?\s*(-?\d+(?:\.\d+)?)\s*[,，、]\s*(?:纬度)?\s*(-?\d+(?:\.\d+)?)", query)
    payload: dict[str, Any] = {
        "distance_m": distance_m,
        "distance": float(distance_m / 1000) if input_unit in {"公里", "千米", "km"} else distance_m,
        "unit": input_unit,
        "display_value": f"{distance_m / 1000:g} km" if distance_m >= 1000 else f"{distance_m:g} m",
        "dissolve": True,
        "target": {},
        "source_entry": "EVWebGLAgentAdapter -> Cesium.EV_DrawBuffer",
    }
    if coordinate_match:
        payload["target"] = {"longitude": float(coordinate_match.group(1)), "latitude": float(coordinate_match.group(2))}
    else:
        payload["target"] = _extract_buffer_target(query)
    if any(phrase in query for phrase in ["生成并查看", "创建并查看", "生成后查看", "创建后查看"]):
        payload["zoom_to_result"] = True

    multi_admin = re.search(r"(?:找到|飞到|定位)([^，,。；;]+?(?:省|市|自治区|区|县))", query)
    events: list[UIEvent] = []
    sequence = 1
    if command == "create_buffer_and_query" and multi_admin:
        admin_name = multi_admin.group(1)
        payload["target"] = {"object_name": admin_name, "admin_region_name": admin_name}
        events.append(UIEvent(type="locate_admin_region", target=admin_name, payload={"name": admin_name}, sequence=sequence))
        sequence += 1
    events.append(UIEvent(type="create_buffer", target="ev-agent-buffer", payload=payload, sequence=sequence))
    if command == "create_buffer_and_query":
        events.append(UIEvent(type="query_objects_in_buffer", target="latest_buffer", payload={"buffer_id": "latest", "query": None, "object_type": _extract_object_type_filter(query), "visible_only": True, "relation": "intersects", "limit": 100}, sequence=sequence + 1))
    return _events_result(
        intent=command,
        answer="正在解析目标几何并生成缓冲区。",
        events=events,
        deterministic_anchor_id=command,
    )


def _extract_buffer_target(query: str) -> dict[str, Any]:
    """Extract an explicit buffer target without feeding distance/action text to object search."""
    if any(phrase in query for phrase in ["当前选择对象", "当前选中对象", "当前对象"]):
        return {"context": "selected_object"}
    if any(phrase in query for phrase in ["刚才定位的对象", "刚才的对象", "刚才定位对象"]):
        return {"context": "last_context_object"}
    if "测试对象" in query:
        return {"context": "test_object"}

    text = _BUFFER_DISTANCE_RE.sub("", query or "")
    text = re.sub(r"(?:请|帮我|给|以|对|将|把)", "", text)
    text = re.sub(r"(?:生成|创建|建立|做一个|做|向外|缓冲区|缓冲|为中心|边界|查看|显示|并查看|定位)", "", text)
    text = re.sub(r"[，,。！？?\s]+", "", text).strip()
    if not text:
        return {}
    if re.search(r"(?:省|自治区|特别行政区|市|自治州|盟|旗|区|县)$", text):
        return {"object_name": text, "admin_region_name": text}
    return {"object_name": text}


def _classify_admin_region_command(query: str) -> str | None:
    if any(word in query for word in ["刚才城市", "刚才定位的城市"]):
        if "高亮" in query:
            return "highlight_last_admin_region"
        if any(word in query for word in ["属性", "信息"]):
            return "query_last_admin_region_properties"
    name, _, _, found = _extract_admin_region_request(query)
    if not found:
        return None
    if "省" in (name or "") and not any(word in query for word in ["城市", "市辖区", "区县"]):
        return None
    if any(word in query for word in ["取消", "清除"]):
        return None
    has_fly = any(word in query for word in ["飞到", "定位"])
    has_highlight = any(word in query for word in ["高亮", "标记"])
    if has_fly and has_highlight:
        return "locate_and_highlight_admin_region"
    if has_fly:
        return "locate_admin_region"
    if has_highlight:
        return "highlight_admin_boundary"
    if name and any(word in query for word in ["属性", "信息"]):
        return "query_admin_region_properties"
    return None


def _extract_admin_region_request(query: str) -> tuple[str | None, str | None, str | None, bool]:
    name = _extract_named_object(query)
    if not name:
        return None, None, None, False
    parent = None
    level = None
    province_match = re.match(r"^(.+?(?:\u7701|\u81ea\u6cbb\u533a|\u7279\u522b\u884c\u653f\u533a))(.+)$", name)
    if province_match:
        parent, name = province_match.group(1), province_match.group(2)
    provider = get_admin_region_provider()
    if not parent and provider.available:
        normalized_input = normalize_region_name(name)
        parent_names = sorted({item.parent_name for item in provider.index().all() if item.parent_name}, key=len, reverse=True)
        for candidate_parent in parent_names:
            normalized_parent = normalize_region_name(candidate_parent)
            if normalized_input.startswith(normalized_parent) and len(normalized_input) > len(normalized_parent):
                parent = candidate_parent
                name = normalized_input[len(normalized_parent):]
                break
    for marker, candidate_level in [("省", "province"), ("市", "city"), ("区", "district"), ("县", "county")]:
        if name.endswith(marker):
            level = candidate_level
            break
    lookup = provider.search(name=name, level=level, parent=parent) if provider.available else {"success": False}
    admin_suffix = any(marker in name for marker in ["省", "市", "区", "县", "自治区", "自治州", "盟", "旗"])
    return name, parent, level, bool(lookup.get("success") or lookup.get("code") == "ADMIN_REGION_AMBIGUOUS" or admin_suffix)


def _last_admin_region(gis_context: Any) -> dict[str, Any] | None:
    extra = getattr(gis_context, "model_extra", None) or {}
    value = extra.get("last_admin_region")
    return value if isinstance(value, dict) else None


def _admin_region_result(command: str, query: str, gis_context: Any) -> SkillResult:
    last_region = _last_admin_region(gis_context)
    if command in {"query_last_admin_region_properties", "highlight_last_admin_region"}:
        if not last_region:
            return _failed_result(command, "no_last_admin_region")
        payload = {
            "region_id": last_region.get("region_id"), "name": last_region.get("name"),
            "adcode": last_region.get("adcode"), "level": last_region.get("level"),
            "parent": last_region.get("parent_name"), "selection_source": "last_admin_region",
        }
    else:
        name, parent, level, _ = _extract_admin_region_request(query)
        payload = {"name": name, "adcode": None, "level": level, "parent": parent}
    payload["source_entry"] = "AdminRegionProvider -> AdminRegionIndex -> EV Cesium Adapter"
    if command in {"locate_admin_region", "locate_and_highlight_admin_region"}:
        payload["duration"] = 2.0
    if command == "locate_and_highlight_admin_region":
        return _events_result(
            intent=command,
            answer="正在解析行政区，将先定位再高亮边界。",
            events=[
                UIEvent(type="locate_admin_region", target=payload.get("name"), payload=payload, sequence=1),
                UIEvent(type="highlight_admin_boundary", target=payload.get("name"), payload=payload, sequence=2),
            ],
            deterministic_anchor_id=command,
        )
    event_type = {
        "locate_admin_region": "locate_admin_region",
        "highlight_admin_boundary": "highlight_admin_boundary",
        "highlight_last_admin_region": "highlight_admin_boundary",
        "query_admin_region_properties": "get_admin_region_properties",
        "query_last_admin_region_properties": "get_admin_region_properties",
    }[command]
    return _event_result(
        intent=command,
        answer="正在读取行政区真实数据并执行。",
        event=UIEvent(type=event_type, target=payload.get("name") or payload.get("region_id"), payload=payload),
        deterministic_anchor_id=command,
    )


def _is_layer_tree_query(query: str) -> bool:
    has_layer = _u("\\u56fe\\u5c42") in query or any(alias in query for alias in ["E3M", "e3m", "OSGB", "osgb", _u("\\u5730\\u5f62")])
    query_words = [
        _u("\\u56fe\\u5c42\\u6811"), _u("\\u5f53\\u524d\\u6709\\u54ea\\u4e9b\\u56fe\\u5c42"),
        _u("\\u5217\\u51fa\\u6240\\u6709\\u56fe\\u5c42"), _u("\\u54ea\\u4e9b\\u56fe\\u5c42"),
        _u("\\u67e5\\u627e\\u540d\\u79f0\\u4e2d\\u5305\\u542b"), _u("\\u67e5\\u627e\\u5305\\u542b"),
        _u("\\u5f53\\u524d\\u6709\\u6ca1\\u6709"), _u("\\u76f8\\u5173\\u56fe\\u5c42"),
    ]
    return has_layer and any(word in query for word in query_words)


def _extract_layer_tree_filters(query: str) -> dict[str, Any]:
    visible_only = any(word in query for word in [_u("\\u6b63\\u5728\\u663e\\u793a"), _u("\\u53ef\\u89c1\\u56fe\\u5c42")])
    hidden_only = any(word in query for word in [_u("\\u88ab\\u9690\\u85cf"), _u("\\u9690\\u85cf\\u7684\\u56fe\\u5c42")])
    keyword = None
    for alias in ["E3M", "e3m", "OSGB", "osgb", _u("\\u5730\\u5f62")]:
        if alias in query:
            keyword = alias.upper() if alias.lower() in {"e3m", "osgb"} else alias
            break
    return {"keyword": keyword, "visible_only": visible_only, "hidden_only": hidden_only, "max_depth": None}


def _is_selection_state_query(query: str) -> bool:
    return any(word in query for word in [
        _u("\\u8fd9\\u4e2a\\u5bf9\\u8c61\\u662f\\u4ec0\\u4e48"),
        _u("\\u6211\\u521a\\u624d\\u9009\\u4e2d\\u4e86\\u4ec0\\u4e48"),
        _u("\\u521a\\u624d\\u9009\\u4e2d\\u4e86\\u4ec0\\u4e48"),
    ])


def _is_runtime_selected_object_properties_query(query: str) -> bool:
    return any(word in query for word in [
        _u("\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61\\u7684\\u5c5e\\u6027"),
        _u("\\u67e5\\u770b\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61"),
    ])


def _is_last_context_object_query(query: str) -> bool:
    return any(word in query for word in [
        _u("\\u521a\\u624d\\u5b9a\\u4f4d\\u5bf9\\u8c61"),
        _u("\\u521a\\u624d\\u5b9a\\u4f4d\\u7684\\u5bf9\\u8c61"),
        _u("\\u521a\\u624d\\u9ad8\\u4eae\\u7684\\u5bf9\\u8c61"),
    ])


def _last_context_object(gis_context: Any) -> dict[str, Any] | None:
    extra = getattr(gis_context, "model_extra", None) or {}
    value = extra.get("last_context_object")
    return value if isinstance(value, dict) else None


def _is_fly_to_selected_object_query(query: str) -> bool:
    fly_words = [_u("\\u98de\\u5230"), _u("\\u5b9a\\u4f4d\\u5230"), _u("\\u5b9a\\u4f4d")]
    object_words = [
        _u("\\u8fd9\\u4e2a\\u5bf9\\u8c61"),
        _u("\\u5f53\\u524d\\u5bf9\\u8c61"),
        _u("\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61"),
        _u("\\u9009\\u4e2d\\u5bf9\\u8c61"),
        _u("\\u8fd9\\u4e2a\\u56fe\\u5f62"),
    ]
    return any(word in query for word in fly_words) and any(word in query for word in object_words)


def _is_fly_to_named_object_query(query: str) -> bool:
    if _is_coordinate_query(query):
        return False
    return any(word in query for word in [_u("\\u98de\\u5230"), _u("\\u5b9a\\u4f4d\\u5230"), _u("\\u5b9a\\u4f4d")]) and bool(
        _extract_named_object(query)
    )


def _is_fly_to_and_highlight_named_object_query(query: str) -> bool:
    has_locate_intent = _is_fly_to_named_object_query(query) or any(
        word in query for word in ["找到", "查找", "搜索"]
    )
    return has_locate_intent and any(word in query for word in [_u("\\u9ad8\\u4eae"), _u("\\u6807\\u8bb0")]) and bool(
        _extract_named_object(query)
    )


def _is_highlight_named_object_query(query: str) -> bool:
    return any(word in query for word in [_u("\\u9ad8\\u4eae"), _u("\\u6807\\u8bb0")]) and bool(_extract_named_object(query))


def _is_buffer_query_result_pronoun_highlight(query: str) -> bool:
    return any(phrase in query for phrase in ["高亮该对象", "高亮它", "高亮刚才查到的对象", "高亮查询结果"])


def _is_clear_named_object_highlight_query(query: str) -> bool:
    return any(word in query for word in [_u("\\u53d6\\u6d88"), _u("\\u6e05\\u9664")]) and _u("\\u9ad8\\u4eae") in query and bool(
        _extract_named_object(query)
    )


def _is_get_named_object_properties_query(query: str) -> bool:
    return any(word in query for word in [_u("\\u5c5e\\u6027"), _u("\\u4fe1\\u606f")]) and any(
        word in query for word in [_u("\\u67e5\\u770b"), _u("\\u83b7\\u53d6"), _u("\\u67e5")]
    ) and bool(_extract_named_object(query))


def _is_coordinate_query(query: str) -> bool:
    return _extract_coordinates(query) is not None and any(
        word in query
        for word in [
            _u("\\u5750\\u6807"),
            _u("\\u7ecf\\u7eac"),
            _u("\\u4e1c\\u7ecf"),
            _u("\\u5317\\u7eac"),
            "lon",
            "lat",
            "lng",
        ]
    )


def _is_get_selected_object_properties_query(query: str) -> bool:
    return any(
        word in query
        for word in [
            _u("\\u67e5\\u770b\\u5f53\\u524d\\u5bf9\\u8c61"),
            _u("\\u67e5\\u770b\\u8fd9\\u4e2a\\u5bf9\\u8c61\\u7684\\u4fe1\\u606f"),
            _u("\\u67e5\\u770b\\u8fd9\\u4e2a\\u5bf9\\u8c61\\u4fe1\\u606f"),
            _u("\\u67e5\\u770b\\u5bf9\\u8c61\\u5c5e\\u6027"),
            _u("\\u5bf9\\u8c61\\u5c5e\\u6027"),
            _u("\\u8fd9\\u4e2a\\u5bf9\\u8c61\\u7684\\u5c5e\\u6027"),
        ]
    )


def _is_highlight_selected_object_query(query: str) -> bool:
    highlight_words = [_u("\\u9ad8\\u4eae"), _u("\\u6807\\u8bb0")]
    object_words = [
        _u("\\u5f53\\u524d\\u5bf9\\u8c61"),
        _u("\\u8fd9\\u4e2a\\u5bf9\\u8c61"),
        _u("\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61"),
        _u("\\u9009\\u4e2d\\u5bf9\\u8c61"),
    ]
    return any(word in query for word in highlight_words) and any(word in query for word in object_words)


def _is_nearby_objects_query(query: str) -> bool:
    return any(word in query for word in [_u("\\u9644\\u8fd1"), _u("\\u5468\\u56f4")])


def _is_nearby_objects_panel_query(query: str) -> bool:
    return _is_nearby_objects_query(query) and any(word in query for word in [_u("\\u67e5\\u770b"), _u("\\u6253\\u5f00\\u9762\\u677f"), _u("\\u5c55\\u793a")])


def _is_highlight_nearby_objects_query(query: str) -> bool:
    return _is_nearby_objects_query(query) and any(word in query for word in [_u("\\u9ad8\\u4eae"), _u("\\u6807\\u8bb0")])


def _is_fly_to_nearby_object_query(query: str) -> bool:
    return _is_nearby_objects_query(query) and any(word in query for word in [_u("\\u98de\\u5230"), _u("\\u5b9a\\u4f4d")])


def _is_radius_search_query(query: str) -> bool:
    return any(word in query for word in [_u("\\u8303\\u56f4\\u5185"), _u("\\u7c73\\u5185"), _u("\\u4ee5\\u5185")]) and any(
        word in query for word in [_u("\\u67e5\\u627e"), _u("\\u67e5\\u8be2"), _u("\\u641c\\u7d22")]
    )


def _is_search_business_object_query(query: str) -> bool:
    normalized = (query or "").strip()
    if not any(normalized.startswith(word) for word in ["查找", "搜索", "找到"]):
        return False
    return not any(word in normalized for word in ["附近", "周围", "范围", "米内", "图层"])


def _extract_search_object_name(query: str) -> str | None:
    text = (query or "").strip()
    text = re.sub(r"^(?:请|帮我|请帮我)?(?:查找|搜索|找到)", "", text)
    text = re.sub(r"(?:并)?(?:定位|飞到)$", "", text)
    text = re.sub(r"[。！？!?，,\\s]+", "", text)
    return text or None


def _extract_layer_query(query: str) -> str:
    for name, layer_id in _LAYER_ALIASES.items():
        if name in query:
            return layer_id
    return _u("\\u56fe\\u5c42")


def _extract_coordinates(query: str) -> tuple[float, float, float] | None:
    numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", query)]
    if len(numbers) < 2:
        return None
    height = numbers[2] if len(numbers) >= 3 else 1000.0
    return numbers[0], numbers[1], height


def _extract_named_object(query: str) -> str | None:
    text = (query or "").strip()
    if not text:
        return None
    if _is_coordinate_like_text(text):
        return None
    for phrase in [
        _u("\\u5f53\\u524d\\u9009\\u4e2d\\u5bf9\\u8c61"),
        _u("\\u5f53\\u524d\\u5bf9\\u8c61"),
        _u("\\u8fd9\\u4e2a\\u5bf9\\u8c61"),
        _u("\\u8fd9\\u4e2a\\u56fe\\u5f62"),
    ]:
        if phrase in text:
            return None
    cleanup_words = [
        _u("\\u98de\\u5230"),
        _u("\\u5b9a\\u4f4d\\u5230"),
        _u("\\u5b9a\\u4f4d"),
        _u("\\u9ad8\\u4eae"),
        _u("\\u6807\\u8bb0"),
        _u("\\u67e5\\u770b"),
        "查询",
        _u("\\u83b7\\u53d6"),
        _u("\\u67e5"),
        "找到",
        "查找",
        "搜索",
        _u("\\u5c5e\\u6027"),
        _u("\\u4fe1\\u606f"),
        _u("\\u53d6\\u6d88"),
        _u("\\u6e05\\u9664"),
        _u("\\u5e76"),
        _u("\\u7136\\u540e"),
        _u("\\u4e00\\u4e0b"),
        _u("\\u8bf7"),
        _u("\\u5e2e\\u6211"),
        _u("\\u628a"),
        _u("\\u5c06"),
        _u("\\u7684"),
    ]
    for word in cleanup_words:
        text = text.replace(word, "")
    text = re.sub(r"[，,。！？?；;\s]+", "", text)
    return text or None


def _is_coordinate_like_text(text: str) -> bool:
    return any(
        word in text
        for word in [
            _u("\\u5750\\u6807"),
            _u("\\u7ecf\\u7eac"),
            _u("\\u4e1c\\u7ecf"),
            _u("\\u5317\\u7eac"),
            "lon",
            "lat",
            "lng",
        ]
    )


def _extract_radius(query: str, default_radius: float = 500.0) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|\u7c73|\u7c73\u8303\u56f4\u5185|\u7c73\u4ee5\u5185|\u7c73\u5185)", query, re.IGNORECASE)
    if not match:
        return default_radius
    try:
        return float(match.group(1))
    except ValueError:
        return default_radius


def _extract_object_type_filter(query: str) -> str | None:
    aliases = {
        _u("\\u6746\\u5854"): "tower",
        _u("\\u94c1\\u5854"): "tower",
        _u("\\u9988\\u7ebf"): "feeder",
        _u("\\u7ebf\\u8def"): "line",
        _u("\\u53d8\\u7535\\u7ad9"): "substation",
    }
    for keyword, object_type in aliases.items():
        if keyword in query:
            return object_type
    return None


def _spatial_result(command: str, query: str, gis_context: Any) -> SkillResult:
    center = resolve_spatial_query_center(gis_context)
    requested_type = _extract_object_type_filter(query)
    # Business categories such as feeders must be read from the live WebGL index.
    # The generic/tower path below remains for existing local spatial fixtures.
    if command in {"nearby_objects", "radius_search"} and requested_type == "feeder":
        radius = _extract_radius(query)
        object_type = requested_type
        return _event_result(
            intent="query_nearby_objects",
            answer="正在解析范围查询中心并读取附近真实对象。",
            event=UIEvent(
                type="query_nearby_objects",
                target="runtime_spatial_index",
                payload={
                    "radius_m": radius,
                    "category": object_type or _extract_category_label(query),
                    "center": center,
                    "anchor_object_id": center.get("anchor_id") if center else None,
                    "anchor_name": center.get("anchor_name") if center else None,
                    "limit": 100,
                    "allow_map_center": False,
                    "source_entry": "resolveSpatialQueryCenter -> runtime business object index",
                },
            ),
            deterministic_anchor_id="query_nearby_objects",
        )
    if center is None:
        return _failed_result(command, "SPATIAL_QUERY_CENTER_REQUIRED")
    radius = _extract_radius(query)
    object_type = _extract_object_type_filter(query) if command == "radius_search" else None
    selected_object = getattr(gis_context, "selected_object", None)
    exclude_object_id = getattr(selected_object, "object_id", None)
    spatial_index, spatial_source, spatial_meta = _spatial_index_from_context(gis_context)
    objects = spatial_index.query_radius(
        center["longitude"],
        center["latitude"],
        radius,
        object_type=object_type,
        exclude_object_id=exclude_object_id,
    )
    center_object = {
        "id": center.get("object_id"),
        "name": center.get("object_name"),
        "type": center.get("object_type"),
        "longitude": center["longitude"],
        "latitude": center["latitude"],
        "height": center.get("height", 0),
        "source": center.get("source"),
        "accuracy": center.get("accuracy"),
    }
    query_type = "radius_search" if command == "radius_search" else "nearby_objects"

    if command == "highlight_nearby_objects":
        object_ids = [str(item["object_id"]) for item in objects if item.get("object_id")]
        return _event_result(
            intent="highlight_nearby_objects",
            answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u9644\\u8fd1\\u5bf9\\u8c61\\u6279\\u91cf\\u9ad8\\u4eae\\u4e8b\\u4ef6\\u3002"),
            event=UIEvent(
                type="gis_highlight",
                target="nearby_objects",
                payload={
                    "object_ids": object_ids,
                    "objects": objects,
                    "radius": radius,
                    "center_object": center_object,
                    "source": spatial_source,
                    "fallback_reason": spatial_meta.get("reason"),
                    "source_entry": "RealSpatialObjectIndex.query_radius -> gis_highlight" if spatial_source == "webgl" else "SpatialObjectIndex.query_radius -> gis_highlight",
                },
            ),
            deterministic_anchor_id="highlight_nearby_objects",
        )

    if command == "fly_to_nearby_object":
        if not objects:
            return _failed_result("fly_to_nearby_object", "no_nearby_objects")
        target_object = objects[0]
        return _event_result(
            intent="fly_to_nearby_object",
            answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u9644\\u8fd1\\u5bf9\\u8c61\\u5b9a\\u4f4d\\u4e8b\\u4ef6\\u3002"),
            event=UIEvent(
                type="fly_to_coordinates",
                target="window.viewer.camera.flyTo",
                payload={
                    "lon": target_object.get("longitude"),
                    "lat": target_object.get("latitude"),
                    "height": 1000,
                    "duration_ms": 1500,
                    "object_id": target_object.get("object_id"),
                    "object_name": target_object.get("object_name"),
                    "radius": radius,
                    "source": spatial_source,
                    "fallback_reason": spatial_meta.get("reason"),
                    "source_entry": "RealSpatialObjectIndex.query_radius -> nearest object -> fly_to_coordinates" if spatial_source == "webgl" else "SpatialObjectIndex.query_radius -> nearest object -> fly_to_coordinates",
                },
            ),
            deterministic_anchor_id="fly_to_nearby_object",
        )

    if command == "nearby_objects_panel":
        panel_payload = {
            "panel_type": "nearby_objects",
            "title": _u("\\u9644\\u8fd1\\u5bf9\\u8c61"),
            "query_type": query_type,
            "center_object": center_object,
            "radius": radius,
            "object_count": len(objects),
            "objects": objects,
            "source": spatial_source,
            "fallback_reason": spatial_meta.get("reason"),
            "source_entry": "RealSpatialObjectIndex.query_radius" if spatial_source == "webgl" else "SpatialObjectIndex.query_radius",
        }
        return _event_result(
            intent="nearby_objects",
            answer=_u("\\u5df2\\u751f\\u6210 WebGL \\u9644\\u8fd1\\u5bf9\\u8c61\\u7ed3\\u679c\\u9762\\u677f\\u4e8b\\u4ef6\\u3002"),
            event=UIEvent(
                type="open_panel",
                target="nearby_objects",
                anchor="open_panel_basic",
                title=_u("\\u9644\\u8fd1\\u5bf9\\u8c61"),
                payload=panel_payload,
            ),
            deterministic_anchor_id="nearby_objects",
        )

    return SkillResult(
        status="success",
        intent=query_type,
        scenario="webgl_spatial_intelligence",
        answer=_u("\\u5df2\\u8fd4\\u56de\\u7a7a\\u95f4\\u67e5\\u8be2\\u7ed3\\u679c\\u3002"),
        tool_calls=[],
        ui_events=[],
        evidence=[],
        missing_params=[],
        risk_level="low",
        manual_check_required=False,
        metadata={
            "routing_mode": "deterministic",
            "integration_mode": _SOURCE_MODE,
            "deterministic_anchor_id": query_type,
            "route_name": "webgl_deterministic_anchor",
            "source": "deterministic_router",
            "spatial_index": spatial_source,
            "spatial_index_meta": spatial_meta,
            "qwen_planner_used": False,
        },
        query_type=query_type,
        source=spatial_source,
        fallback_reason=spatial_meta.get("reason"),
        center_object=center_object,
        radius=radius,
        object_type=object_type,
        objects=objects,
    )


def _spatial_index_from_context(gis_context: Any) -> tuple[SpatialObjectIndex, str, dict[str, Any]]:
    extra = getattr(gis_context, "model_extra", {}) or {}
    payload = extra.get("spatial_objects")
    objects_payload: list[dict[str, Any]] | None = None
    meta: dict[str, Any] = {}

    if isinstance(payload, dict):
        source = payload.get("source")
        reason = payload.get("reason")
        if reason:
            meta["reason"] = str(reason)
        raw_objects = payload.get("objects")
        if isinstance(raw_objects, list):
            objects_payload = [item for item in raw_objects if isinstance(item, dict)]
        elif source == "mock":
            meta.setdefault("reason", "webgl_objects_unavailable")
            return default_spatial_index, "mock", meta
    elif isinstance(payload, list):
        objects_payload = [item for item in payload if isinstance(item, dict)]

    if objects_payload is not None:
        real_index = RealSpatialObjectIndex.build_from_webgl_objects(objects_payload)
        if real_index.objects:
            return real_index, "webgl", {"object_count": len(real_index.objects)}
        meta.setdefault("reason", "webgl_objects_empty_or_invalid")
        return default_spatial_index, "mock", meta

    return default_spatial_index, "mock", {"reason": "webgl_objects_not_provided"}


def resolve_spatial_query_center(gis_context: Any, allow_map_center: bool = False) -> dict[str, Any] | None:
    extra = getattr(gis_context, "model_extra", {}) or {}
    explicit = _center_from_mapping(extra.get("center") or extra.get("spatial_center"), "explicit_coordinates", "exact")
    if explicit:
        return explicit
    selected_object = getattr(gis_context, "selected_object", None)
    if selected_object is not None:
        coordinates = _selected_object_coordinates(selected_object)
        if coordinates is not None:
            payload = selected_object.model_dump(exclude_none=True)
            return {
                "object_id": payload.get("object_id"),
                "object_name": payload.get("object_name"),
                "object_type": payload.get("object_type"),
                "longitude": coordinates[0],
                "latitude": coordinates[1],
                "height": coordinates[2],
                "source": "selected_object",
                "accuracy": "object_position_or_geometry",
            }
    for key in ["last_context_object", "last_admin_region", "test_object"]:
        parsed = _center_from_mapping(extra.get(key), key, "geometry_center")
        if parsed:
            return parsed
    map_center = getattr(gis_context, "map_center", None)
    if allow_map_center and map_center and len(map_center) >= 2:
        return {"object_id": None, "object_name": "当前地图视图中心", "object_type": "map_view", "longitude": float(map_center[0]), "latitude": float(map_center[1]), "height": 0.0, "source": "map_view_center", "accuracy": "view_center"}
    return None


def _extract_category_label(query: str) -> str | None:
    for label in ["馈线", "变电站", "线路", "杆塔", "铁塔"]:
        if label in query:
            return label
    return None


def _center_from_mapping(value: Any, source: str, accuracy: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    nested = value.get("center") if isinstance(value.get("center"), dict) else {}
    position = value.get("position") if isinstance(value.get("position"), dict) else {}
    sphere = value.get("bounding_sphere_center") if isinstance(value.get("bounding_sphere_center"), dict) else {}
    lon = _pick_number(value.get("longitude"), value.get("lon"), value.get("lng"), nested.get("longitude"), position.get("longitude"), sphere.get("longitude"))
    lat = _pick_number(value.get("latitude"), value.get("lat"), nested.get("latitude"), position.get("latitude"), sphere.get("latitude"))
    if lon is None or lat is None:
        properties = value.get("properties") if isinstance(value.get("properties"), dict) else {}
        geometry_center = _geometry_center(value.get("geometry") or properties.get("geometry"))
        if geometry_center:
            lon, lat = geometry_center
            accuracy = "geometry_centroid"
    if lon is None or lat is None:
        return None
    return {
        "object_id": value.get("object_id") or value.get("region_id"),
        "object_name": value.get("object_name") or value.get("name"),
        "object_type": value.get("object_type") or value.get("level"),
        "longitude": lon,
        "latitude": lat,
        "height": _pick_number(value.get("height"), nested.get("height"), position.get("height"), 0) or 0.0,
        "source": value.get("center_source") or source,
        "accuracy": accuracy,
    }


def _selected_object_coordinates(selected_object: SelectedObject) -> tuple[float, float, float] | None:
    payload = selected_object.model_dump(exclude_none=True)
    properties = payload.get("properties") or {}
    lon = _pick_number(
        payload.get("longitude"),
        payload.get("lon"),
        payload.get("lng"),
        properties.get("longitude"),
        properties.get("lon"),
        properties.get("lng"),
    )
    lat = _pick_number(
        payload.get("latitude"),
        payload.get("lat"),
        properties.get("latitude"),
        properties.get("lat"),
    )
    if lon is None or lat is None:
        geometry_center = _geometry_center(payload.get("geometry") or properties.get("geometry"))
        if geometry_center is not None:
            lon, lat = geometry_center
    if lon is None or lat is None:
        return None
    height = _pick_number(
        payload.get("height"),
        payload.get("altitude"),
        payload.get("alt"),
        properties.get("height"),
        properties.get("altitude"),
        properties.get("alt"),
        1000,
    )
    return lon, lat, height if height is not None else 1000.0


def _geometry_center(geometry: Any) -> tuple[float, float] | None:
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if coordinates is None:
        return None
    points: list[tuple[float, float]] = []
    _collect_geometry_points(coordinates, points)
    if not points:
        return None
    lon = sum(point[0] for point in points) / len(points)
    lat = sum(point[1] for point in points) / len(points)
    return lon, lat


def _collect_geometry_points(value: Any, points: list[tuple[float, float]]) -> None:
    if not isinstance(value, list) or not value:
        return
    if len(value) >= 2 and not isinstance(value[0], list) and not isinstance(value[1], list):
        lon = _pick_number(value[0])
        lat = _pick_number(value[1])
        if lon is not None and lat is not None:
            points.append((lon, lat))
        return
    for item in value:
        _collect_geometry_points(item, points)


def _pick_number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        return number
    return None


def _event_result(intent: str, answer: str, event: UIEvent, deterministic_anchor_id: str) -> SkillResult:
    return SkillResult(
        status="success",
        intent=intent,
        scenario="webgl_source_driven_anchor",
        answer=answer,
        tool_calls=[],
        ui_events=[event],
        evidence=[],
        missing_params=[],
        risk_level="low",
        manual_check_required=False,
        metadata={
            "routing_mode": "deterministic",
            "integration_mode": _SOURCE_MODE,
            "deterministic_anchor_id": deterministic_anchor_id,
            "route_name": "webgl_deterministic_anchor",
            "source": "deterministic_router",
            "qwen_planner_used": False,
        },
    )


def _events_result(intent: str, answer: str, events: list[UIEvent], deterministic_anchor_id: str) -> SkillResult:
    return SkillResult(
        status="success",
        intent=intent,
        scenario="webgl_source_driven_anchor",
        answer=answer,
        tool_calls=[],
        ui_events=events,
        evidence=[],
        missing_params=[],
        risk_level="low",
        manual_check_required=False,
        metadata={
            "routing_mode": "deterministic",
            "integration_mode": _SOURCE_MODE,
            "deterministic_anchor_id": deterministic_anchor_id,
            "route_name": "webgl_deterministic_anchor",
            "source": "deterministic_router",
            "qwen_planner_used": False,
        },
    )


def _message_result(message: str, deterministic_anchor_id: str) -> SkillResult:
    return SkillResult(
        status="success",
        intent="webgl_anchor_message",
        scenario="webgl_source_driven_anchor",
        answer=message,
        tool_calls=[],
        ui_events=[
            UIEvent(
                type="show_message",
                message=message,
                payload={"level": "info", "message": message},
            )
        ],
        evidence=[],
        missing_params=[],
        risk_level="low",
        manual_check_required=False,
        metadata={
            "routing_mode": "deterministic",
            "integration_mode": _SOURCE_MODE,
            "deterministic_anchor_id": deterministic_anchor_id,
            "route_name": "webgl_deterministic_anchor",
            "source": "deterministic_router",
            "selected_object_present": False,
            "qwen_planner_used": False,
        },
    )


def _failed_result(intent: str, reason: str) -> SkillResult:
    user_messages = {
        "SPATIAL_QUERY_CENTER_REQUIRED": "无法确定范围查询的中心，请先选择对象、定位对象，或明确指定中心位置。",
        "OBJECT_SEARCH_QUERY_REQUIRED": "请提供需要查找的对象名称。",
        "BUFFER_DISTANCE_REQUIRED": "请提供缓冲距离，例如500米或1公里。",
        "BUFFER_DISTANCE_INVALID": "缓冲距离无效，请输入大于0的距离，例如500米或1公里。",
        "BUFFER_DISTANCE_OUT_OF_RANGE": "缓冲距离超出当前安全范围，请输入不超过1000公里的距离。",
    }
    return SkillResult(
        status="failed",
        intent=intent,
        scenario="webgl_source_driven_anchor",
        answer=user_messages.get(reason, reason),
        tool_calls=[],
        ui_events=[],
        evidence=[],
        missing_params=[],
        risk_level="low",
        manual_check_required=False,
        metadata={
            "routing_mode": "deterministic",
            "integration_mode": _SOURCE_MODE,
            "deterministic_anchor_id": intent,
            "route_name": "webgl_deterministic_anchor",
            "source": "deterministic_router",
            "reason": reason,
            "qwen_planner_used": False,
        },
        reason=reason,
        code=reason,
    )


def _selected_object_payload(selected_object: SelectedObject) -> dict[str, Any]:
    payload = selected_object.model_dump(exclude_none=True)
    return {
        "object_id": payload.get("object_id"),
        "object_type": payload.get("object_type"),
        "object_name": payload.get("object_name"),
        "layer_id": payload.get("layer_id"),
        "longitude": payload.get("longitude") or payload.get("lon") or payload.get("lng"),
        "latitude": payload.get("latitude") or payload.get("lat"),
        "height": payload.get("height") or payload.get("altitude") or payload.get("alt"),
        "properties": payload.get("properties") or {},
    }


def _selected_object_properties_payload(selected_object: SelectedObject) -> dict[str, Any]:
    payload = selected_object.model_dump(exclude_none=True)
    properties = payload.get("properties") or {}
    position: dict[str, Any] = {}

    longitude = _pick_number(payload.get("longitude"), payload.get("lon"), payload.get("lng"))
    latitude = _pick_number(payload.get("latitude"), payload.get("lat"))
    height = _pick_number(payload.get("height"), payload.get("altitude"), payload.get("alt"))

    if longitude is not None:
        position["longitude"] = longitude
    if latitude is not None:
        position["latitude"] = latitude
    if height is not None:
        position["height"] = height

    object_id = payload.get("object_id")
    object_name = payload.get("object_name")
    object_type = payload.get("object_type")
    layer_id = payload.get("layer_id")
    return {
        "id": object_id,
        "name": object_name,
        "type": object_type,
        "layer_id": layer_id,
        "object_id": object_id,
        "object_name": object_name,
        "object_type": object_type,
        "position": position,
        "properties": properties,
    }
