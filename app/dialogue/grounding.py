from typing import Any

from app.dialogue.models import DialogueState
from app.dialogue.semantic import ALLOWED_INTENTS, GroundingResult, SemanticPlan


class GroundingValidator:
    def validate(self, plan: SemanticPlan, state: DialogueState) -> GroundingResult:
        errors: list[str] = []
        warnings: list[str] = []
        steps = plan.steps if plan.intent == "multi_step" else []
        intents = [step.intent for step in steps] or [plan.intent]
        for intent in intents:
            if intent not in ALLOWED_INTENTS:
                errors.append(f"不允许或不存在的 Tool/意图：{intent}")
        if plan.intent in {"create_buffer", "modify_buffer"}:
            distance = plan.parameters.get("distance_m")
            if distance is None and isinstance(plan.parameters.get("distance"), dict):
                distance = plan.parameters["distance"].get("value")
            if distance is not None and (not isinstance(distance, (int, float)) or distance <= 0 or distance > 1_000_000):
                errors.append("缓冲距离必须在 0 到 1000000 米之间")
        target = plan.target
        if target and target.resolved_reference_id:
            active_ids = {f.reference_id for f in state.active_focuses()}
            if target.resolved_reference_id not in active_ids:
                errors.append("目标引用已失效或不在当前 DialogueState 中")
        if target and target.reference_expression and not target.resolved_reference_id and not target.explicit_name:
            errors.append("指代表达尚未绑定真实 reference_id")
        if plan.intent in {"highlight_buffer_query_results"} and (not state.last_object_set or not state.last_object_set.active):
            errors.append("当前没有可用的对象集合")
        if plan.intent in {"get_buffer_state", "get_buffer_result", "fly_to_buffer", "clear_buffer", "query_objects_in_buffer", "modify_buffer"}:
            if not state.last_buffer or state.last_buffer.get("rendered", True) is False or state.last_buffer.get("status") == "cleared":
                warnings.append("当前请求依赖浏览器中的有效缓冲区，执行层仍需再次校验")
        return GroundingResult(valid=not errors, grounded_plan=plan.model_dump(exclude_none=True) if not errors else {}, errors=errors, warnings=warnings, needs_clarification=bool(errors))
