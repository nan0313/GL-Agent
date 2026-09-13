from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.response import EvidenceItem, UIEvent


AnchorCallType = Literal["mock", "http_api", "frontend_event", "websocket", "external_jump"]
AnchorAdapterType = Literal["mock", "http_api", "frontend_event", "websocket", "external_jump"]
AnchorStatus = Literal["available", "mock", "developing", "offline"]
AnchorMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
AnchorResultStatus = Literal["success", "failed", "blocked", "skipped"]


class AnchorDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    anchor_id: str
    anchor_name: str
    description: str
    scenario: str
    object_types: list[str] = Field(default_factory=list)
    required_params: list[str] = Field(default_factory=list)
    optional_params: list[str] = Field(default_factory=list)
    permission_roles: list[str] = Field(default_factory=list)
    call_type: AnchorCallType
    adapter_type: AnchorAdapterType
    endpoint: str | None = None
    method: AnchorMethod | None = None
    ui_event_type: str | None = None
    output_type: list[str] = Field(default_factory=list)
    status: AnchorStatus
    fallback: list[str] = Field(default_factory=list)


class AnchorResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    anchor_call_id: str
    anchor_id: str
    status: AnchorResultStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    ui_events: list[UIEvent] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
