from app.llm.qwen_general_chat import QwenGeneralChat
from app.skills.base import AgentSkill
from app.skills.context import SkillContext
from app.skills.result import SkillResult


class GeneralChatSkill(AgentSkill):
    skill_id = "general_chat"
    name = "General Chat"
    description = "Use QwenGeneralChat for non-tool general questions."
    route_type = "general_chat"

    def __init__(self, general_chat: QwenGeneralChat | None = None) -> None:
        self.general_chat = general_chat or QwenGeneralChat()

    def execute(self, context: SkillContext) -> SkillResult:
        answer = self.general_chat.answer(
            query=context.request.query,
            gis_context=context.request.gis_context,
            request_id=context.trace_id,
        )
        return SkillResult(
            status="success",
            intent="general_chat",
            scenario="qwen_general_chat",
            answer=answer,
            tool_calls=[],
            ui_events=[],
            evidence=[],
            missing_params=[],
            risk_level="low",
            manual_check_required=False,
            metadata={
                "skill_id": self.skill_id,
                "route_type": self.route_type,
                "provider": "qwen",
                "class": self.general_chat.__class__.__name__,
                "answer_length": len(answer),
            },
        )

    def handle_error(self, context: SkillContext, error: Exception) -> SkillResult:
        gateway_error = getattr(error, "gateway_error", None)
        error_code = getattr(gateway_error, "code", None) or self._extract_error_code(error)
        return SkillResult(
            status="success",
            intent="general_chat",
            scenario="general_chat_degraded",
            answer=(
                "当前通用语言模型暂未连接，所以我不能可靠回答这类开放问题。"
                "你仍可以让我查询本地输变电知识库、总结已上传文件，或操作当前 WebGL/GIS 场景。"
            ),
            tool_calls=[],
            ui_events=[],
            evidence=[],
            missing_params=[],
            risk_level="low",
            manual_check_required=False,
            metadata={
                "skill_id": self.skill_id,
                "route_type": self.route_type,
                "provider": "qwen",
                "fallback_used": True,
                "degraded": True,
                "error_code": error_code,
                "error_type": error.__class__.__name__,
                "error_summary": "模型服务暂时不可用。",
            },
        )

    @staticmethod
    def _extract_error_code(error: Exception) -> str:
        text = str(error)
        for code in (
            "QWEN_CONNECT_TIMEOUT", "QWEN_READ_TIMEOUT", "QWEN_SCHEMA_FAILED",
            "QWEN_AUTH_FAILED", "QWEN_RATE_LIMITED", "QWEN_CIRCUIT_OPEN",
            "QWEN_DEADLINE_EXCEEDED", "QWEN_DISABLED",
        ):
            if code in text:
                return code
        return "QWEN_UNAVAILABLE"
