from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.response import ToolCall


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="allow")

    intent: str
    scenario: str
    target_objects: list[dict[str, Any]] = Field(default_factory=list)
    missing_params: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    final_answer_draft: str
