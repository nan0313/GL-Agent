from app.agent.planner import IntentPlanner
from app.anchors import AnchorGateway, AnchorRegistry
from app.llm.qwen_general_chat import QwenGeneralChat
from app.rag import LocalRAGAnswerer
from app.rules import PermissionChecker
from app.skills.anchor_task_skill import AnchorTaskSkill
from app.skills.base import AgentSkill
from app.skills.deterministic_webgl_anchor_skill import DeterministicWebGLAnchorSkill
from app.skills.document_understanding_skill import DocumentUnderstandingSkill
from app.skills.general_chat_skill import GeneralChatSkill
from app.skills.rag_qa_skill import RagQASkill
from app.skills.unknown_skill import UnknownSkill


class SkillRegistry:
    def __init__(self, skills: list[AgentSkill] | None = None) -> None:
        self._unknown_skill = UnknownSkill()
        self._skills: dict[str, AgentSkill] = {}
        for skill in skills or []:
            self.register(skill)

    @classmethod
    def default(
        cls,
        anchor_registry: AnchorRegistry | None = None,
        intent_planner: IntentPlanner | None = None,
        permission_checker: PermissionChecker | None = None,
        general_chat: QwenGeneralChat | None = None,
        rag_answerer: LocalRAGAnswerer | None = None,
        anchor_gateway: AnchorGateway | None = None,
    ) -> "SkillRegistry":
        return cls(
            skills=[
                GeneralChatSkill(general_chat=general_chat),
                RagQASkill(rag_answerer=rag_answerer),
                DocumentUnderstandingSkill(),
                AnchorTaskSkill(
                    anchor_registry=anchor_registry,
                    intent_planner=intent_planner,
                    permission_checker=permission_checker,
                    anchor_gateway=anchor_gateway,
                ),
                DeterministicWebGLAnchorSkill(),
                UnknownSkill(),
            ]
        )

    def register(self, skill: AgentSkill) -> None:
        self._skills[skill.skill_id] = skill
        if skill.route_type == "unknown":
            self._unknown_skill = skill

    def list_skills(self) -> list[AgentSkill]:
        return list(self._skills.values())

    def customer_capabilities(self) -> dict[str, object]:
        from app.conversations.attachments import supported_attachment_extensions
        from app.integrations.webgl.contract import get_customer_webgl_operations

        enabled = {skill.skill_id for skill in self._skills.values() if skill.enabled}
        webgl_enabled = "deterministic_webgl_anchor" in enabled
        knowledge_enabled = "rag_qa" in enabled
        document_enabled = "document_understanding" in enabled
        return {
            "knowledge_qa": knowledge_enabled,
            "document_qa": document_enabled,
            "document_formats": supported_attachment_extensions() if document_enabled else [],
            "citations": knowledge_enabled or document_enabled,
            "webgl": {
                "enabled": webgl_enabled,
                "operations": get_customer_webgl_operations() if webgl_enabled else [],
            },
            "conversation_persistence": True,
            "answer_exports": ["docx", "markdown"],
            "physical_control": False,
            "realtime_production_data": False,
        }

    def get_skill(self, skill_id: str) -> AgentSkill | None:
        return self._skills.get(skill_id)

    def get_for_decision(self, decision) -> AgentSkill:
        for key in (
            getattr(decision, "skill_name", None),
            getattr(decision, "route_name", None),
        ):
            if not key:
                continue
            skill = self.get_skill(str(key))
            if skill and skill.enabled:
                return skill

        return self.get_by_route_type(decision.route_type)

    def get_by_route_type(self, route_type: str) -> AgentSkill:
        for skill in self._skills.values():
            if skill.route_type == route_type and skill.enabled:
                return skill
        return self._unknown_skill
