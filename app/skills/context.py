from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.anchor import AnchorDefinition
from app.schemas.request import AgentChatRequest
from app.schemas.route import RouteDecision


class SkillContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    request: AgentChatRequest
    route_decision: RouteDecision
    trace_id: str
    anchors: list[AnchorDefinition] | None = None
    user_roles: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
