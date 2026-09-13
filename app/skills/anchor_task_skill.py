from app.agent.planner import IntentPlanner
from app.anchors import AnchorGateway, AnchorRegistry
from app.rules import PermissionChecker
from app.schemas.anchor import AnchorDefinition
from app.schemas.response import EvidenceItem, UIEvent
from app.skills.base import AgentSkill
from app.skills.context import SkillContext
from app.skills.result import SkillResult
from app.llm.output_parser import AgentPlanOutputError


class AnchorTaskSkill(AgentSkill):
    skill_id = "anchor_task"
    name = "Anchor Task"
    description = "Plan and execute registered mock anchors through AnchorGateway."
    route_type = "anchor_task"

    def __init__(
        self,
        anchor_registry: AnchorRegistry | None = None,
        intent_planner: IntentPlanner | None = None,
        permission_checker: PermissionChecker | None = None,
        anchor_gateway: AnchorGateway | None = None,
    ) -> None:
        self.anchor_registry = anchor_registry or AnchorRegistry()
        self.intent_planner = intent_planner or IntentPlanner()
        self.permission_checker = permission_checker or PermissionChecker()
        self.anchor_gateway = anchor_gateway or AnchorGateway()

    def execute(self, context: SkillContext) -> SkillResult:
        anchors = context.anchors or self.anchor_registry.list_anchors()
        plan = self.intent_planner.plan(context.request, anchors)
        base_metadata = {
            "skill_id": self.skill_id,
            "route_type": self.route_type,
            "planner_provider": self.intent_planner.provider,
            "planner_class": self.intent_planner.llm_planner_class_name,
            "intent": plan.intent,
            "tool_calls_count": len(plan.tool_calls),
        }

        if plan.missing_params:
            return SkillResult(
                status="clarification_required",
                intent=plan.intent,
                scenario=plan.scenario,
                answer=plan.final_answer_draft,
                tool_calls=[],
                ui_events=[
                    UIEvent(
                        type="ask_clarification",
                        message=plan.final_answer_draft,
                        payload={"missing_params": plan.missing_params},
                    )
                ],
                evidence=[],
                missing_params=plan.missing_params,
                risk_level="low",
                manual_check_required=False,
                metadata={**base_metadata, "missing_params": plan.missing_params},
            )

        permission_result = self._check_permissions(context, anchors, plan.tool_calls)
        if permission_result is not None:
            return permission_result.model_copy(
                update={"metadata": {**base_metadata, **permission_result.metadata}}
            )

        anchor_results = [
            self.anchor_gateway.execute(tool_call, self._require_anchor(anchors, tool_call.anchor_id))
            for tool_call in plan.tool_calls
        ]
        evidence: list[EvidenceItem] = []
        ui_events: list[UIEvent] = []
        for result in anchor_results:
            evidence.extend(result.evidence)
            ui_events.extend(result.ui_events)

        has_success = any(result.status == "success" for result in anchor_results)
        status = "success" if anchor_results and has_success else "failed"
        answer = (
            plan.final_answer_draft
            if status == "success"
            else "工具执行失败，请稍后重试或补充更多信息。"
        )

        return SkillResult(
            status=status,
            intent=plan.intent,
            scenario=plan.scenario,
            answer=answer,
            tool_calls=plan.tool_calls,
            ui_events=ui_events,
            evidence=evidence,
            missing_params=[],
            risk_level="low",
            manual_check_required=False,
            metadata={
                **base_metadata,
                "anchors_count": len(anchors),
                "anchor_results_count": len(anchor_results),
                "evidence_count": len(evidence),
                "ui_events_count": len(ui_events),
            },
        )

    def handle_error(self, context: SkillContext, error: Exception) -> SkillResult:
        if isinstance(error, AgentPlanOutputError):
            return SkillResult(
                status="failed",
                intent="agent_plan_failed",
                scenario="agent_plan_recovery",
                answer="本次请求的执行计划生成异常，系统未执行地图操作。请重试或使用更明确的对象名称。",
                tool_calls=[], ui_events=[], evidence=[], missing_params=[],
                risk_level="low", manual_check_required=False,
                metadata={
                    "error_code": error.code,
                    "validation_summary": error.validation_summary,
                    "repair_attempted": True,
                    "deterministic_fallback_checked": True,
                },
            )
        return super().handle_error(context, error)

    def _check_permissions(self, context, anchors, tool_calls) -> SkillResult | None:
        for tool_call in tool_calls:
            anchor = self._require_anchor(anchors, tool_call.anchor_id)
            result = self.permission_checker.check(context.request.role, anchor)
            if not result["allowed"]:
                return SkillResult(
                    status="blocked",
                    intent="permission_denied",
                    scenario=anchor.scenario,
                    answer=result.get("message", "当前角色权限不足。"),
                    tool_calls=tool_calls,
                    ui_events=[],
                    evidence=[],
                    missing_params=[],
                    risk_level="medium",
                    manual_check_required=False,
                    metadata={
                        "skill_id": self.skill_id,
                        "route_type": self.route_type,
                        "permission": "denied",
                        "anchor_id": anchor.anchor_id,
                    },
                )
        return None

    def _require_anchor(
        self,
        anchors: list[AnchorDefinition],
        anchor_id: str,
    ) -> AnchorDefinition:
        anchor = next((item for item in anchors if item.anchor_id == anchor_id), None)
        if anchor is None:
            raise ValueError(f"Anchor not found: {anchor_id}")
        return anchor
