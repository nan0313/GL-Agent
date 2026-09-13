from dataclasses import dataclass, field

from app.schemas.anchor import AnchorDefinition, AnchorResult
from app.schemas.plan import AgentPlan
from app.schemas.request import AgentChatRequest
from app.schemas.response import AgentResponse
from app.schemas.trace import TraceStep
from typing import Any


@dataclass
class AgentState:
    request: AgentChatRequest
    trace_id: str
    trace_steps: list[TraceStep] = field(default_factory=list)
    anchors: list[AnchorDefinition] = field(default_factory=list)
    plan: AgentPlan | None = None
    anchor_results: list[AnchorResult] = field(default_factory=list)
    final_response: AgentResponse | None = None
    status: str = "running"
    error: str | None = None
    missing_params: list[str] = field(default_factory=list)
    original_query: str | None = None
    semantic_turn: Any | None = None
    skill_result: Any | None = None
