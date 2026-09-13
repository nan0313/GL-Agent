import json
from typing import Any

from langchain_core.runnables import RunnableLambda

from app.llm.base import BaseLLMPlanner
from app.llm.output_parser import parse_agent_plan
from app.prompts import create_planner_prompt
from app.schemas.anchor import AnchorDefinition
from app.schemas.plan import AgentPlan
from app.schemas.request import AgentChatRequest, SelectedObject


class LCELMockPlanner(BaseLLMPlanner):
    def __init__(self) -> None:
        self.prompt = create_planner_prompt()

    def plan(
        self,
        request: AgentChatRequest,
        anchors: list[AnchorDefinition],
    ) -> AgentPlan:
        prompt_input = self._build_prompt_input(request=request, anchors=anchors)
        mock_model = RunnableLambda(lambda _: self._mock_model_output(request, anchors))
        chain = self.prompt | mock_model
        raw_output = chain.invoke(prompt_input)
        return parse_agent_plan(raw_output)

    def _build_prompt_input(
        self,
        request: AgentChatRequest,
        anchors: list[AnchorDefinition],
    ) -> dict[str, str]:
        return {
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

    def _mock_model_output(
        self,
        request: AgentChatRequest,
        anchors: list[AnchorDefinition],
    ) -> str:
        anchor = self._select_anchor(request.query, anchors)
        target_objects = self._target_objects(request.gis_context.selected_object)

        if anchor is None:
            return self._json_output(
                {
                    "intent": "unknown",
                    "scenario": "unknown",
                    "target_objects": target_objects,
                    "missing_params": [],
                    "tool_calls": [],
                    "final_answer_draft": "暂时无法识别该请求，请补充更明确的操作意图。",
                }
            )

        if anchor.anchor_id == "placeholder_open_panel" and not request.gis_context.selected_object:
            return self._json_output(
                {
                    "intent": "open_panel",
                    "scenario": anchor.scenario,
                    "target_objects": [],
                    "missing_params": ["selected_object"],
                    "tool_calls": [],
                    "final_answer_draft": "请先在 GIS 中选择对象，或补充对象信息后再查看。",
                }
            )

        return self._json_output(
            {
                "intent": self._intent_for_anchor(anchor.anchor_id),
                "scenario": anchor.scenario,
                "target_objects": target_objects,
                "missing_params": [],
                "tool_calls": [
                    {
                        "anchor_id": anchor.anchor_id,
                        "anchor_name": anchor.anchor_name,
                        "arguments": self._build_arguments(request, anchor),
                    }
                ],
                "final_answer_draft": f"已生成 LCEL mock 规划：{anchor.anchor_name}。",
            }
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

    def _json_output(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)
