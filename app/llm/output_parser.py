import json

from pydantic import ValidationError

from app.schemas.plan import AgentPlan


class AgentPlanOutputError(ValueError):
    def __init__(self, code: str, message: str, validation_summary: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.validation_summary = validation_summary


def parse_agent_plan(raw_output: str) -> AgentPlan:
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise AgentPlanOutputError("AGENT_PLAN_PARSE_FAILED", "Invalid AgentPlan JSON output", str(exc)[:300]) from exc

    try:
        return AgentPlan.model_validate(payload)
    except ValidationError as exc:
        summary = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', []))}:{item.get('type')}"
            for item in exc.errors(include_url=False)[:10]
        )
        raise AgentPlanOutputError("AGENT_PLAN_SCHEMA_INVALID", "Invalid AgentPlan schema output", summary) from exc
