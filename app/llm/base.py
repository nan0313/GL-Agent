from abc import ABC, abstractmethod

from app.schemas.anchor import AnchorDefinition
from app.schemas.plan import AgentPlan
from app.schemas.request import AgentChatRequest


class BaseLLMPlanner(ABC):
    @abstractmethod
    def plan(
        self,
        request: AgentChatRequest,
        anchors: list[AnchorDefinition],
    ) -> AgentPlan:
        """Generate a structured plan without executing any tools."""
