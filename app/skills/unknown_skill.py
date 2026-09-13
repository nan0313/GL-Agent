from app.skills.base import AgentSkill
from app.skills.context import SkillContext
from app.skills.result import SkillResult


class UnknownSkill(AgentSkill):
    skill_id = "unknown"
    name = "Unknown"
    description = "Return clarification when no executable route is recognized."
    route_type = "unknown"

    def execute(self, context: SkillContext) -> SkillResult:
        return SkillResult(
            status="clarification_required",
            intent="unknown",
            scenario="unknown",
            answer="当前暂未识别可执行意图，请补充更明确的需求。",
            tool_calls=[],
            ui_events=[],
            evidence=[],
            missing_params=[],
            risk_level="low",
            manual_check_required=False,
            metadata={
                "skill_id": self.skill_id,
                "route_type": self.route_type,
            },
        )
