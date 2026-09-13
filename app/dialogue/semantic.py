from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ALLOWED_INTENTS = {
    "search_business_objects", "fly_to_object", "gis_highlight", "clear_highlight",
    "get_object_properties", "locate_admin_region", "highlight_admin_boundary",
    "get_admin_region_properties", "create_buffer", "get_buffer_state", "get_buffer_result",
    "fly_to_buffer", "clear_buffer", "clear_all_buffers", "query_objects_in_buffer",
    "highlight_buffer_query_results", "clear_buffer_query_highlight", "query_nearby_objects",
    "get_camera_state", "reset_view", "get_layer_list", "get_layer_tree",
    "set_layer_visibility", "multi_step", "modify_buffer", "unknown",
}


class SemanticTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_expression: str | None = None
    reference_kind: str | None = None
    resolved_reference_id: str | None = None
    explicit_name: str | None = None


class SemanticStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str
    target: SemanticTarget | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class SemanticPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str
    target: SemanticTarget | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    steps: list[SemanticStep] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    needs_clarification: bool = False
    clarification: str | None = None
    response_intent: Literal["execute", "clarify", "cancel"] = "execute"


class GroundingResult(BaseModel):
    valid: bool
    grounded_plan: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_clarification: bool = False


class HybridRouteAudit(BaseModel):
    route_source: Literal["rule", "qwen", "hybrid"] = "rule"
    rule_candidates: list[str] = Field(default_factory=list)
    semantic_complexity: list[str] = Field(default_factory=list)
    model_required: bool = False
    final_intent: str | None = None
    confidence: float = 0.0
    fallback_reason: str | None = None

