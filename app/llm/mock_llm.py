from app.llm.base import BaseLLMPlanner
from app.schemas.anchor import AnchorDefinition
from app.schemas.plan import AgentPlan
from app.schemas.request import AgentChatRequest, SelectedObject
from app.schemas.response import ToolCall


class MockLLMPlanner(BaseLLMPlanner):
    def plan(
        self,
        request: AgentChatRequest,
        anchors: list[AnchorDefinition],
    ) -> AgentPlan:
        anchor = self._select_anchor(request.query, anchors)
        target_objects = self._target_objects(request.gis_context.selected_object)

        if anchor is None:
            return AgentPlan(
                intent="unknown",
                scenario="unknown",
                target_objects=target_objects,
                missing_params=[],
                tool_calls=[],
                final_answer_draft="暂时无法识别该请求，请补充更明确的操作意图。",
            )

        if anchor.anchor_id == "placeholder_open_panel" and not request.gis_context.selected_object:
            return AgentPlan(
                intent="open_panel",
                scenario=anchor.scenario,
                target_objects=[],
                missing_params=["selected_object"],
                tool_calls=[],
                final_answer_draft="请先在 GIS 中选择对象，或补充对象信息后再查看。",
            )

        arguments = self._build_arguments(request, anchor)
        return AgentPlan(
            intent=self._intent_for_anchor(anchor.anchor_id),
            scenario=anchor.scenario,
            target_objects=target_objects,
            missing_params=[],
            tool_calls=[
                ToolCall(
                    anchor_id=anchor.anchor_id,
                    anchor_name=anchor.anchor_name,
                    arguments=arguments,
                )
            ],
            final_answer_draft=f"已生成开发期计划：{anchor.anchor_name}。",
        )

    def _select_anchor(
        self,
        query: str,
        anchors: list[AnchorDefinition],
    ) -> AnchorDefinition | None:
        if "搜索" in query or "定位" in query:
            return self._find_anchor("placeholder_search_object", anchors)

        open_panel_keywords = ["查看", "打开", "面板", "实时数据", "模型", "GIM"]
        if any(keyword in query for keyword in open_panel_keywords):
            return self._find_anchor("placeholder_open_panel", anchors)

        return None

    def _find_anchor(
        self,
        anchor_id: str,
        anchors: list[AnchorDefinition],
    ) -> AnchorDefinition | None:
        return next((anchor for anchor in anchors if anchor.anchor_id == anchor_id), None)

    def _build_arguments(
        self,
        request: AgentChatRequest,
        anchor: AnchorDefinition,
    ) -> dict[str, str]:
        arguments: dict[str, str] = {}

        if "keyword" in anchor.required_params:
            arguments["keyword"] = request.query

        if "panel_id" in anchor.required_params:
            arguments["panel_id"] = anchor.anchor_id

        selected_object = request.gis_context.selected_object
        if selected_object:
            arguments.update(
                {
                    "object_id": selected_object.object_id,
                    "object_type": selected_object.object_type,
                    "object_name": selected_object.object_name,
                }
            )

        return arguments

    def _target_objects(
        self,
        selected_object: SelectedObject | None,
    ) -> list[dict[str, str]]:
        if not selected_object:
            return []

        return [
            {
                "object_id": selected_object.object_id,
                "object_type": selected_object.object_type,
                "object_name": selected_object.object_name,
            }
        ]

    def _intent_for_anchor(self, anchor_id: str) -> str:
        if anchor_id == "placeholder_search_object":
            return "search_object"
        if anchor_id == "placeholder_open_panel":
            return "open_panel"
        return "unknown"
