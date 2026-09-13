import json
from typing import Any, Callable
from time import monotonic

from app.config import get_qwen_api_key, get_qwen_enabled, get_qwen_model
from app.dialogue.semantic import ALLOWED_INTENTS, SemanticPlan
from app.llm.qwen_http import qwen_chat_completion
from app.qwen_gateway import get_qwen_gateway


class QwenSemanticPlanner:
    """Constrained semantic planner. It never executes tools or invents reference ids."""

    def __init__(self, caller: Callable[[list[dict[str, str]]], str] | None = None) -> None:
        self.caller = caller
        self.last_debug_info: dict[str, Any] = {}
        self._cooldown_until = 0.0

    @property
    def available(self) -> bool:
        return monotonic() >= self._cooldown_until and (self.caller is not None or (get_qwen_enabled() and bool(get_qwen_api_key())))

    def plan(self, payload: dict[str, Any]) -> SemanticPlan:
        if not self.available:
            raise RuntimeError("Qwen 语义规划不可用")
        safe_payload = {
            "original_text": payload.get("original_text"),
            "normalized_text": payload.get("normalized_text"),
            "quantities": payload.get("quantities", []),
            "reference_expressions": payload.get("reference_expressions", []),
            "allowed_intents": sorted(ALLOWED_INTENTS),
            "rule_candidates": payload.get("rule_candidates", []),
            "dialogue_summary": payload.get("dialogue_summary", {}),
        }
        messages = [
            {"role": "system", "content": "你是 GIS 语义规划器。只输出严格 JSON。不得生成 object_id/reference_id，不得宣布执行成功，不得输出 Cesium/viewer 参数。intent 只能来自 allowed_intents。"},
            {"role": "user", "content": json.dumps(safe_payload, ensure_ascii=False)},
        ]
        self.last_debug_info = {"model": get_qwen_model(), "repair_attempted": False, "repair_success": False}
        try:
            raw = self.caller(messages) if self.caller else qwen_chat_completion(messages, caller="QwenSemanticPlanner", request_id=str(payload.get("request_id", "")))
        except Exception:
            self._cooldown_until = monotonic() + 300
            raise
        try:
            return self._parse(raw)
        except Exception as first:
            self.last_debug_info["repair_attempted"] = True
            get_qwen_gateway().record_schema_failure(messages, purpose="semantic_planner", request_id=str(payload.get("request_id", "")), invocation_source="mock" if self.caller else "real")
            repair = messages + [{"role": "assistant", "content": raw[:4000]}, {"role": "user", "content": f"JSON Schema 校验失败：{str(first)[:300]}。仅修复一次，只输出合法 JSON。"}]
            repaired = self.caller(repair) if self.caller else qwen_chat_completion(repair, caller="QwenSemanticPlannerRepair", request_id=str(payload.get("request_id", "")))
            plan = self._parse(repaired)
            self.last_debug_info["repair_success"] = True
            return plan

    @staticmethod
    def _parse(raw: str) -> SemanticPlan:
        data = json.loads(raw)
        plan = SemanticPlan.model_validate(data)
        intents = [x.intent for x in plan.steps] if plan.intent == "multi_step" else [plan.intent]
        invalid = [x for x in intents if x not in ALLOWED_INTENTS]
        if invalid:
            raise ValueError(f"unsupported intents: {invalid}")
        if plan.target and plan.target.resolved_reference_id:
            raise ValueError("model must not generate resolved_reference_id")
        return plan
