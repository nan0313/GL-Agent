from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.response import EvidenceItem, ToolCall, UIEvent


class SkillResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    intent: str
    scenario: str
    answer: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    ui_events: list[UIEvent] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_params: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    manual_check_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
