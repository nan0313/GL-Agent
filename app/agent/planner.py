from app.config import get_llm_planner_provider
from app.llm.factory import get_llm_planner
from app.schemas.anchor import AnchorDefinition
from app.schemas.plan import AgentPlan
from app.schemas.request import AgentChatRequest


class IntentPlanner:
    def __init__(self, provider: str | None = None) -> None:
        selected_provider = provider or get_llm_planner_provider()
        self.provider = selected_provider
        self.llm_planner = get_llm_planner(provider=selected_provider)
        self.llm_planner_class_name = self.llm_planner.__class__.__name__

    def plan(
        self,
        request: AgentChatRequest,
        anchors: list[AnchorDefinition],
    ) -> AgentPlan:
        return self.llm_planner.plan(request=request, anchors=anchors)
