import json
from typing import Any

from app.config import (
    get_qwen_api_key,
    get_qwen_base_url,
    get_qwen_enabled,
    get_qwen_max_tokens,
    get_qwen_model,
    get_qwen_temperature,
    get_qwen_timeout_seconds,
)
from app.llm.base import BaseLLMPlanner
from app.llm.qwen_http import preview_text, qwen_chat_completion
from app.qwen_gateway import get_qwen_gateway
from app.llm.output_parser import AgentPlanOutputError, parse_agent_plan
from app.prompts.planner_prompt import create_planner_prompt
from app.schemas.anchor import AnchorDefinition
from app.schemas.plan import AgentPlan
from app.schemas.request import AgentChatRequest


class QwenPlanner(BaseLLMPlanner):
    """Planner adapter for OpenAI-compatible Qwen chat completion services."""

    def __init__(self) -> None:
        self.prompt = create_planner_prompt()
        self.last_debug_info: dict[str, Any] = {}

    def plan(
        self,
        request: AgentChatRequest,
        anchors: list[AnchorDefinition],
    ) -> AgentPlan:
        if not get_qwen_enabled():
            raise RuntimeError("QwenPlanner is disabled.")

        if not get_qwen_api_key():
            raise RuntimeError("Qwen API key is missing.")

        prompt_text = self._build_prompt(request, anchors)
        raw_output = self._call_chat_completions(prompt_text, request_id=request.session_id)
        try:
            plan = parse_agent_plan(raw_output)
        except AgentPlanOutputError as first_error:
            self.last_debug_info["parse_success"] = False
            self.last_debug_info["parse_error"] = first_error.code
            self.last_debug_info["repair_attempted"] = True
            get_qwen_gateway().record_schema_failure(
                [{"role": "user", "content": raw_output[:4000]}],
                purpose="complex_planner",
                request_id=request.session_id,
            )
            repaired_output = self._repair_agent_plan(raw_output, first_error, request_id=request.session_id)
            try:
                plan = parse_agent_plan(repaired_output)
            except AgentPlanOutputError as repair_error:
                self.last_debug_info["repair_success"] = False
                self.last_debug_info["repair_error"] = repair_error.code
                raise AgentPlanOutputError(
                    "AGENT_PLAN_REPAIR_FAILED",
                    "AgentPlan repair failed",
                    f"initial={first_error.code}; repair={repair_error.code}; {repair_error.validation_summary or ''}"[:300],
                ) from repair_error
            self.last_debug_info["repair_success"] = True

        self.last_debug_info["parse_success"] = True
        return plan

    def _repair_agent_plan(self, raw_output: str, error: AgentPlanOutputError, request_id: str = "") -> str:
        repair_messages = [
            {
                "role": "system",
                "content": (
                    "你是 AgentPlan JSON 修复器。只输出一个符合 AgentPlan Schema 的 JSON；"
                    "不得解释、不得执行工具、不得补造地图执行结果。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "error_code": error.code,
                    "validation_summary": error.validation_summary,
                    "required_fields": ["intent", "scenario", "target_objects", "missing_params", "tool_calls", "final_answer_draft"],
                    "invalid_output": raw_output[:4000],
                }, ensure_ascii=False),
            },
        ]
        return qwen_chat_completion(repair_messages, caller="QwenPlannerRepair", request_id=request_id)

    def _build_prompt(
        self,
        request: AgentChatRequest,
        anchors: list[AnchorDefinition],
    ) -> str:
        prompt_input = {
            "query": request.query,
            "role": request.role,
            "gis_context": json.dumps(
                request.gis_context.model_dump(),
                ensure_ascii=False,
            ),
            "anchors": json.dumps(
                [anchor.model_dump() for anchor in anchors],
                ensure_ascii=False,
            ),
        }
        return self.prompt.format(**prompt_input)

    def _call_chat_completions(self, prompt_text: str, request_id: str = "") -> str:
        api_key = get_qwen_api_key()
        base_url = get_qwen_base_url()
        model = get_qwen_model()
        temperature = get_qwen_temperature()
        timeout_seconds = get_qwen_timeout_seconds()
        self.last_debug_info = {
            "model_endpoint_configured": bool(base_url),
            "model": model,
            "timeout_seconds": timeout_seconds,
            "temperature": temperature,
            "max_tokens": get_qwen_max_tokens(),
            "api_key_configured": bool(api_key),
        }
        messages = [
                {
                    "role": "system",
                    "content": (
                        "你是电网 Agent 的 Planner，只能输出符合 AgentPlan "
                        "Schema 的 JSON，不要输出解释文字。"
                    ),
                },
                {"role": "user", "content": prompt_text},
        ]

        try:
            content = qwen_chat_completion(messages, caller="QwenPlanner", request_id=request_id)
        except Exception as exc:
            self.last_debug_info["http_success"] = False
            self.last_debug_info["http_error_type"] = exc.__class__.__name__
            raise

        self.last_debug_info["http_success"] = True
        self.last_debug_info["response_content_length"] = len(content)
        self.last_debug_info["response_preview"] = preview_text(content)
        return content
