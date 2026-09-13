from __future__ import annotations

from typing import Any


BASE_ACTIONS: dict[str, list[str]] = {
    "object": ["fly_to_object", "gis_highlight", "get_object_properties"],
    "admin_region": [
        "locate_admin_region",
        "highlight_admin_boundary",
        "get_admin_region_properties",
        "get_object_properties",
        "create_buffer",
    ],
    "buffer": ["get_buffer_state", "get_buffer_result", "fly_to_buffer", "query_objects_in_buffer", "clear_buffer"],
    "object_set": ["highlight_buffer_query_results", "clear_buffer_query_highlight", "select_by_rank"],
    "buffer_query": ["highlight_buffer_query_results", "clear_buffer_query_highlight", "select_by_rank"],
    "coordinate": ["create_buffer"],
    "layer": [],
    "attachment": ["summarize", "question_answering", "compare"],
}


def available_actions(kind: str, snapshot: dict[str, Any] | None = None) -> list[str]:
    snapshot = snapshot or {}
    actions = list(BASE_ACTIONS.get(kind, []))
    if kind == "object":
        has_geometry = bool(
            snapshot.get("geometry")
            or snapshot.get("center")
            or snapshot.get("position")
            or snapshot.get("longitude")
            or snapshot.get("spatial_query_available") is True
        )
        if has_geometry:
            actions.extend(["create_buffer", "query_nearby_objects"])
    return actions


INTENT_FOCUS_KINDS: dict[str, set[str]] = {
    "create_buffer": {"object", "admin_region", "coordinate"},
    "fly_to_object": {"object", "admin_region", "coordinate"},
    "gis_highlight": {"object", "admin_region"},
    "get_object_properties": {"object", "admin_region"},
    "query_objects_in_buffer": {"buffer"},
    "get_buffer_state": {"buffer"},
    "get_buffer_result": {"buffer"},
    "fly_to_buffer": {"buffer"},
    "clear_buffer": {"buffer"},
    "highlight_buffer_query_results": {"object_set", "buffer_query"},
    "clear_buffer_query_highlight": {"object_set", "buffer_query"},
}
