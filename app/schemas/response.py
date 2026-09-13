from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentStatus = Literal["success", "clarification_required", "blocked", "failed", "canceled"]
RiskLevel = Literal["low", "medium", "high"]
UIEventType = Literal[
    "gis_highlight",
    "fly_to",
    "open_panel",
    "open_model",
    "open_video_panel",
    "draw_path",
    "show_table",
    "show_chart",
    "show_message",
    "set_layer_visibility",
    "get_layer_list",
    "get_layer_tree",
    "get_camera_state",
    "reset_view",
    "fly_to_coordinates",
    "fly_to_object",
    "get_selected_object",
    "get_selection_state",
    "get_object_properties",
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
    "locate_admin_region",
    "highlight_admin_boundary",
    "get_admin_region_properties",
    "screenshot",
    "clear_highlight",
    "ask_clarification",
    "safety_block",
]


class UIEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: UIEventType
    target: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    anchor_id: str
    anchor_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    anchor_id: str
    anchor_name: str
    status: str
    data_time: str | None = None
    summary: str


class Citation(BaseModel):
    """Customer-safe source metadata without retrieval identifiers."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    label: str
    source_name: str
    title: str | None = None
    section: str | None = None
    page: str | None = None
    excerpt: str | None = None


class BusinessAction(BaseModel):
    """Stable domain action consumed by the customer UI, not a tool call."""

    model_config = ConfigDict(extra="forbid")

    action_id: str
    kind: str
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)
    auto_execute: bool = True


class UserFacingError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    message: str
    retryable: bool = True


class FinalResponse(BaseModel):
    """The only response model consumed by Customer Mode."""

    model_config = ConfigDict(extra="forbid")

    answer_markdown: str
    citations: list[Citation] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    business_actions: list[BusinessAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: UserFacingError | None = None


class InternalAgentResult(BaseModel):
    """Observable internal result retained for trace/developer surfaces only."""

    model_config = ConfigDict(extra="allow")

    route: dict[str, Any] = Field(default_factory=dict)
    planner: dict[str, Any] = Field(default_factory=dict)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    traces: list[dict[str, Any]] = Field(default_factory=list)
    model_calls: list[dict[str, Any]] = Field(default_factory=list)
    latency: dict[str, Any] = Field(default_factory=dict)
    final_response: FinalResponse


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    trace_id: str
    status: AgentStatus
    answer: str
    intent: str | None = None
    scenario: str | None = None
    route_type: str | None = None
    missing_params: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    evidence_list: list[EvidenceItem] = Field(default_factory=list)
    ui_events: list[UIEvent] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    manual_check_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    final_response: FinalResponse | None = None
