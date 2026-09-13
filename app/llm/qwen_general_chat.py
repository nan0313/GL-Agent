from collections.abc import Callable
from typing import Any

from app.config import get_qwen_api_key, get_qwen_enabled
from app.llm.qwen_http import qwen_chat_completion
from app.product.answer_stream import emit_answer_delta
from app.schemas.request import GISContext


class QwenGeneralChat:
    def __init__(self, capability_provider: Callable[[], dict[str, Any]] | None = None) -> None:
        self._capability_provider = capability_provider

    def set_capability_provider(self, provider: Callable[[], dict[str, Any]]) -> None:
        self._capability_provider = provider

    def answer(
        self,
        query: str,
        gis_context: GISContext | None = None,
        system_context: str | None = None,
        request_id: str = "",
    ) -> str:
        guarded_answer = self._guarded_answer(query)
        if guarded_answer is not None:
            return guarded_answer

        if not get_qwen_enabled():
            raise RuntimeError("QwenGeneralChat is disabled.")
        if not get_qwen_api_key():
            raise RuntimeError("Qwen API key is missing.")

        return self._call_chat_completions(
            query=query,
            gis_context=gis_context,
            system_context=system_context,
            request_id=request_id,
        )

    def _guarded_answer(self, query: str) -> str | None:
        normalized = "".join(str(query or "").split()).casefold().strip("，。！？?!,.～~")

        if normalized in {"你好", "您好", "嗨", "哈喽", "hello", "hi", "hey", "早上好", "下午好", "晚上好"}:
            return "你好！我是 **EV Agent**。你可以让我查询输变电规范、总结上传的文件，或处理当前 WebGL/GIS 场景中的对象。今天想先做什么？"

        if normalized in {"谢谢", "多谢", "感谢", "thankyou", "thanks"}:
            return "不客气！如果还要继续查规范、总结文件或处理当前地图场景，直接告诉我就可以。"

        if normalized in {"再见", "拜拜", "bye", "goodbye"}:
            return "再见！当前对话会保留，之后可以继续从这里处理。"

        if any(keyword in query for keyword in ["拉开开关", "合闸", "分闸", "远程控制", "送电", "停电"]):
            return "该请求涉及生产控制操作，我不能执行。请按电网生产规程走人工确认和具备权限的操作流程。"

        if "天气" in query:
            return "当前系统没有接入实时天气工具，我无法查询实时天气；如果后续接入天气 API 或 RAG，可以提供实时天气查询。"

        if _is_capability_query(query):
            return self._capability_answer()

        return None

    def _call_chat_completions(
        self,
        query: str,
        gis_context: GISContext | None,
        system_context: str | None,
        request_id: str = "",
    ) -> str:
        capability_context = self._capability_prompt_context()
        user_content = (
            "你是 EV Agent 的客户问答界面。当前轮若未经过业务动作路由，不要声称操作已经执行；"
            "但不得否认能力清单中已经接入的 WebGL 软件场景操作。"
            "WebGL 场景操作不等于真实现场设备控制，未接入的实时生产数据不得假装可用。\n"
            f"用户问题: {query}\n"
            f"GIS 上下文: {gis_context.model_dump() if gis_context else {}}\n"
            f"已接入客户能力: {capability_context}\n"
            f"系统上下文: {system_context or ''}"
        )
        return qwen_chat_completion(
            [
                {
                    "role": "system",
                    "content": "你是 EV Agent。准确说明已接入能力，不泄露内部工具、规划、检索或调试信息。",
                },
                {"role": "user", "content": user_content},
            ],
            caller="QwenGeneralChat",
            request_id=request_id,
        )

    def _capability_snapshot(self) -> dict[str, Any]:
        if self._capability_provider is None:
            return {}
        try:
            return dict(self._capability_provider() or {})
        except Exception:
            return {}

    def _capability_answer(self) -> str:
        capabilities = self._capability_snapshot()
        parts = ["我是 **EV Agent**，可以帮助你："]
        if capabilities.get("knowledge_qa"):
            citation = "，并给出来源引用" if capabilities.get("citations") else ""
            parts.append(f"\n\n- 基于输变电工程知识库回答规范、设备和三维设计问题{citation}；")
        if capabilities.get("document_qa"):
            format_labels = {".docx": "DOCX", ".pdf": "PDF", ".txt": "TXT", ".md": "Markdown", ".markdown": "Markdown"}
            formats = "、".join(dict.fromkeys(format_labels.get(str(item).lower(), str(item).lstrip(".").upper()) for item in capabilities.get("document_formats") or []))
            suffix = f"（支持 {formats}）" if formats else ""
            parts.append(f"\n- 阅读、总结和问答你上传的工程文件{suffix}；")
        webgl = capabilities.get("webgl") or {}
        operations = [str(item.get("label")) for item in webgl.get("operations") or [] if item.get("label")]
        if webgl.get("enabled") and operations:
            parts.append("\n- 在当前 WebGL/GIS 软件场景中" + "、".join(operations) + "；")
        if capabilities.get("conversation_persistence"):
            parts.append("\n- 保留多轮会话，并用标题、列表和表格整理工程信息。")
        exports = [str(item).strip().lower() for item in capabilities.get("answer_exports", []) if str(item).strip()]
        export_names = {"docx": "Word", "markdown": "Markdown", "md": "Markdown"}
        export_labels = list(dict.fromkeys(export_names.get(item, item.upper()) for item in exports))
        if export_labels:
            parts.append(f"\n- 将回答生成{'、'.join(export_labels)} 文件。")
        if webgl.get("enabled") and operations:
            parts.append("\n\n具体场景操作取决于当前场景是否已加载并公开对应对象。")
        parts.append("\n\n我操作的是当前软件场景中的对象，不是现实变电站设备。当前也未接入实时 SCADA、远程开关控制或其他生产控制系统。")
        for part in parts:
            emit_answer_delta(part)
        return "".join(parts)

    def _capability_prompt_context(self) -> str:
        capabilities = self._capability_snapshot()
        webgl = capabilities.get("webgl") or {}
        operation_labels = [str(item.get("label")) for item in webgl.get("operations") or [] if item.get("label")]
        return (
            f"知识库问答={bool(capabilities.get('knowledge_qa'))}; "
            f"文档问答={bool(capabilities.get('document_qa'))}; "
            f"引用={bool(capabilities.get('citations'))}; "
            f"WebGL软件场景操作={operation_labels}; "
            "真实设备控制=False; 实时生产数据=False"
        )


def _is_capability_query(query: str) -> bool:
    normalized = "".join(str(query or "").split()).casefold()
    return any(
        marker in normalized
        for marker in (
            "你是谁", "你能做什么", "你的能力", "能帮我干嘛", "可以做什么",
            "你能操作gis", "你能操作地图", "你能操作webgl", "你可以操作gis",
            "你可以操作地图", "你可以帮我", "你能控制真实", "你可以控制真实",
        )
    )
