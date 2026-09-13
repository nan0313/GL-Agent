from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WebGLSelectedObject(BaseModel):
    model_config = ConfigDict(extra="allow")

    object_id: str | None = None
    object_type: str | None = None
    object_name: str | None = None
    layer_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class WebGLMapState(BaseModel):
    model_config = ConfigDict(extra="allow")

    center: list[float] | None = None
    zoom: float | int | None = None
    camera: dict[str, Any] = Field(default_factory=dict)
    bounds: dict[str, Any] | list[Any] | None = None


class WebGLPageState(BaseModel):
    model_config = ConfigDict(extra="allow")

    current_panel: str | None = None
    selected_tool: str | None = None


class WebGLFrontendMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    webgl_version: str | None = None
    project_id: str | None = None
    scene_id: str | None = None


class WebGLContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    selected_object: WebGLSelectedObject | None = None
    map_state: WebGLMapState = Field(default_factory=WebGLMapState)
    active_layers: list[str] = Field(default_factory=list)
    user_action: str | None = None
    page_state: WebGLPageState = Field(default_factory=WebGLPageState)
    frontend_metadata: WebGLFrontendMetadata = Field(default_factory=WebGLFrontendMetadata)
    metadata: dict[str, Any] = Field(default_factory=dict)


def normalize_webgl_context(raw_context: dict[str, Any] | WebGLContext | None) -> dict[str, Any]:
    """Convert a WebGL-side context snapshot into AgentChatRequest.gis_context."""
    if raw_context is None:
        context = WebGLContext()
    elif isinstance(raw_context, WebGLContext):
        context = raw_context
    elif isinstance(raw_context, dict):
        context = _parse_context(raw_context)
    else:
        context = WebGLContext(metadata={"context_error": "unsupported_context_type"})

    selected_object = (
        context.selected_object.model_dump(exclude_none=True) if context.selected_object else None
    )
    map_state = context.map_state.model_dump(exclude_none=True)
    page_state = context.page_state.model_dump(exclude_none=True)
    frontend_metadata = context.frontend_metadata.model_dump(exclude_none=True)

    return {
        "selected_object": selected_object,
        "active_layers": context.active_layers,
        "map_center": map_state.get("center"),
        "zoom": map_state.get("zoom"),
        "map_state": map_state,
        "user_action": context.user_action,
        "page_state": page_state,
        "frontend_metadata": frontend_metadata,
        "metadata": context.metadata,
    }


def _parse_context(raw: dict[str, Any]) -> WebGLContext:
    normalized = dict(raw)

    map_state = dict(normalized.get("map_state") or {})
    if "center" not in map_state and normalized.get("map_center") is not None:
        map_state["center"] = normalized.get("map_center")
    if "zoom" not in map_state and normalized.get("zoom") is not None:
        map_state["zoom"] = normalized.get("zoom")
    normalized["map_state"] = map_state

    selected = normalized.get("selected_object")
    if selected is not None and not isinstance(selected, dict):
        normalized["selected_object"] = None
        metadata = dict(normalized.get("metadata") or {})
        metadata["selected_object_error"] = "selected_object_must_be_object"
        normalized["metadata"] = metadata

    active_layers = normalized.get("active_layers")
    if active_layers is None:
        normalized["active_layers"] = []
    elif not isinstance(active_layers, list):
        normalized["active_layers"] = [str(active_layers)]

    try:
        return WebGLContext.model_validate(normalized)
    except Exception as exc:
        return WebGLContext(metadata={"context_error": str(exc)[:200]})
