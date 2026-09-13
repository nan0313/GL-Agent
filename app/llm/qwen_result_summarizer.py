import json
from typing import Any, Callable

from app.config import get_qwen_api_key, get_qwen_enabled
from app.llm.qwen_http import qwen_chat_completion


class QwenResultSummarizer:
    """Natural-language summary constrained to already completed structured results."""

    def __init__(self, caller: Callable[[list[dict[str, str]]], str] | None = None) -> None:
        self.caller = caller

    @property
    def available(self) -> bool:
        return self.caller is not None or (get_qwen_enabled() and bool(get_qwen_api_key()))

    def summarize(self, *, original_text: str, normalized_text: str, executed_tools: list[str], results: list[dict[str, Any]], status: str, limitations: list[str] | None = None, next_steps: list[str] | None = None, request_id: str = "") -> str:
        if not self.available:
            raise RuntimeError("Qwen 结果总结不可用")
        facts = {
            "original_text": original_text,
            "normalized_text": normalized_text,
            "executed_tools": executed_tools,
            "results": results,
            "status": status,
            "limitations": limitations or [],
            "next_steps": next_steps or [],
        }
        messages = [
            {"role": "system", "content": "只根据给定结构化事实生成简洁中文总结。不得添加未执行动作、对象、坐标、数量或成功状态；失败不得改写为成功。只输出总结文字。"},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
        ]
        text = self.caller(messages) if self.caller else qwen_chat_completion(messages, caller="QwenResultSummarizer", request_id=request_id)
        return str(text).strip()
