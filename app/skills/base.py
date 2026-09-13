from abc import ABC, abstractmethod
from time import perf_counter

from app.skills.context import SkillContext
from app.skills.result import SkillResult


class SkillValidationError(ValueError):
    pass


class SkillDisabledError(RuntimeError):
    pass


class SkillTimeoutError(RuntimeError):
    pass


class SkillExecutionError(RuntimeError):
    pass


class AgentSkill(ABC):
    skill_id = "base"
    name = "Base Skill"
    description = "Base Agent skill"
    route_type = "unknown"
    risk_level = "low"
    enabled = True
    timeout_seconds = 30.0
    required_roles: list[str] = []

    def can_handle(self, context: SkillContext) -> bool:
        return self.enabled and context.route_decision.route_type == self.route_type

    def validate_input(self, context: SkillContext) -> None:
        if not self.enabled:
            raise SkillDisabledError(f"Skill is disabled: {self.skill_id}")
        if context is None:
            raise SkillValidationError("SkillContext is required.")
        if context.request is None:
            raise SkillValidationError("SkillContext.request is required.")
        if context.route_decision is None:
            raise SkillValidationError("SkillContext.route_decision is required.")
        if not context.trace_id:
            raise SkillValidationError("SkillContext.trace_id is required.")
        if self.route_type != "unknown" and context.route_decision.route_type != self.route_type:
            raise SkillValidationError(
                f"Skill route_type mismatch: expected={self.route_type}, "
                f"actual={context.route_decision.route_type}"
            )

    def run(self, context: SkillContext) -> SkillResult:
        start = perf_counter()
        try:
            self.validate_input(context)
            result = self.execute(context)
        except Exception as exc:
            result = self.handle_error(context, exc)

        self._enrich_metadata(result=result, start=start)
        return result

    @abstractmethod
    def execute(self, context: SkillContext) -> SkillResult:
        raise NotImplementedError

    def handle_error(self, context: SkillContext, error: Exception) -> SkillResult:
        route_type = getattr(getattr(context, "route_decision", None), "route_type", self.route_type)
        error_summary = self._safe_error_summary(error)
        return SkillResult(
            status="failed",
            intent=route_type or "unknown",
            scenario=f"{self.skill_id}_error",
            answer=f"请求失败：{error_summary}",
            tool_calls=[],
            ui_events=[],
            evidence=[],
            missing_params=[],
            risk_level=self.risk_level,
            manual_check_required=False,
            metadata={
                "skill_id": self.skill_id,
                "skill_name": self.name,
                "route_type": self.route_type,
                "error_type": error.__class__.__name__,
                "error_summary": error_summary,
            },
        )

    def _safe_error_summary(self, error: Exception) -> str:
        text = str(error) or error.__class__.__name__
        for marker in ("api_key", "API key", "Authorization", "Bearer", "sk-"):
            if marker in text:
                return "外部模型或服务调用失败，敏感配置已脱敏。"
        return text[:300]

    def _enrich_metadata(self, result: SkillResult, start: float) -> None:
        latency_ms = self._latency_ms(start)
        timeout_exceeded = latency_ms > int(self.timeout_seconds * 1000)
        result.metadata.setdefault("skill_id", self.skill_id)
        result.metadata.setdefault("skill_name", self.name)
        result.metadata.setdefault("route_type", self.route_type)
        result.metadata.setdefault("timeout_seconds", self.timeout_seconds)
        result.metadata.setdefault("latency_ms", latency_ms)
        result.metadata.setdefault("timeout_exceeded", timeout_exceeded)
        result.metadata.setdefault("error_type", None)
        result.metadata.setdefault("error_summary", None)
        result.metadata.setdefault("evidence_count", len(result.evidence))
        result.metadata.setdefault("tool_calls_count", len(result.tool_calls))
        result.metadata.setdefault("ui_events_count", len(result.ui_events))

    def _latency_ms(self, start: float) -> int:
        return max(0, int((perf_counter() - start) * 1000))
