import json
from pydantic import ValidationError

from app.config import (
    get_qwen_api_key,
    get_qwen_enabled,
)
from app.llm.qwen_http import qwen_chat_completion
from app.schemas.request import AgentChatRequest
from app.schemas.route import RouteDecision


class QwenRouter:
    def decide(self, request: AgentChatRequest) -> RouteDecision:
        if not get_qwen_enabled():
            raise RuntimeError("QwenRouter is disabled.")
        if not get_qwen_api_key():
            raise RuntimeError("Qwen API key is missing.")

        raw_output = self._call_chat_completions(self._build_prompt(request), request_id=request.session_id)
        try:
            data = json.loads(raw_output)
            decision = RouteDecision.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError("Invalid RouteDecision JSON output") from exc

        decision.router = "qwen"
        return decision

    def _build_prompt(self, request: AgentChatRequest) -> str:
        return (
            "你只做电网 Agent 的路由判断，不执行工具，不回答问题。\n"
            "只能输出 JSON，不要输出解释文字。\n"
            "route_type 只能是 anchor_task/general_chat/rag_qa/unknown。\n"
            "涉及 WebGL 操作、设备定位、打开面板、当前对象、地图交互、图层控制时输出 anchor_task。\n"
            "用户说飞到/定位/高亮/查看属性加对象名称时，属于 anchor_task；优先由前端业务对象解析为 canonical object_id，不要要求经纬度。\n"
            "普通知识解释、系统能力询问、闲聊、非工具问题输出 general_chat。\n"
            "规程、制度、手册、规范、知识库、文档依据相关问题输出 rag_qa。\n"
            "无法判断输出 unknown。\n"
            f"用户角色: {request.role}\n"
            f"用户问题: {request.query}\n"
            f"GIS 上下文: {request.gis_context.model_dump()}\n"
            "JSON 字段: route_type, reason, confidence, need_tool, need_rag, rewritten_query。"
        )

    def _call_chat_completions(self, prompt_text: str, request_id: str = "") -> str:
        return qwen_chat_completion(
            [
                {
                    "role": "system",
                    "content": "你是电网 Agent 路由器，只输出 RouteDecision JSON。",
                },
                {"role": "user", "content": prompt_text},
            ],
            caller="QwenRouter",
            request_id=request_id,
        )
