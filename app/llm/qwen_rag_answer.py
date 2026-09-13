from pathlib import Path
from typing import Any

from app.config import get_qwen_api_key, get_qwen_enabled
from app.llm.qwen_http import qwen_chat_completion


class QwenRAGAnswer:
    def answer(
        self,
        query: str,
        evidence: list[Any],
        prompt_context: dict[str, Any] | None = None,
        request_id: str = "",
    ) -> str:
        if not evidence:
            return "当前知识库未包含足够证据。"
        if not get_qwen_enabled():
            raise RuntimeError("QwenRAGAnswer is disabled.")
        if not get_qwen_api_key():
            raise RuntimeError("Qwen API key is missing.")

        context = prompt_context or self._fallback_prompt_context(query, evidence)
        messages = [
            {"role": "system", "content": str(context["system_prompt"])},
            {"role": "user", "content": str(context["user_prompt"])},
        ]
        try:
            return qwen_chat_completion(messages, caller="QwenRAGAnswer", request_id=request_id)
        except Exception as exc:
            raise RuntimeError(f"Qwen RAG answer request failed: {self._safe_error(exc)}") from exc

    def _fallback_prompt_context(self, query: str, evidence: list[Any]) -> dict[str, str]:
        blocks = []
        for index, item in enumerate(evidence, start=1):
            if isinstance(item, dict):
                evidence_id = item.get("evidence_id") or f"ev_{index}"
                title = item.get("title") or "-"
                source = item.get("source_name") or item.get("source") or "-"
                content = item.get("content") or item.get("snippet") or ""
                metadata = item.get("metadata") or {}
                section = metadata.get("section") or item.get("section") or "-"
            else:
                evidence_id = getattr(item, "evidence_id", None) or f"ev_{index}"
                title = getattr(item, "title", "-")
                source = getattr(item, "source_name", "-")
                content = getattr(item, "content", "")
                metadata = getattr(item, "metadata", {}) or {}
                section = metadata.get("section") or "-"
            blocks.append(
                f"[E{index}] evidence_id: {evidence_id}\n"
                f"title: {title}\n"
                f"source_name: {Path(str(source)).name}\n"
                f"section: {section}\n"
                f"content_excerpt: {str(content)[:1200]}"
            )
        return {
            "system_prompt": (
                "你是电网 Agent 的 RAG 问答助手。只能依据 evidence 回答，"
                "引用只能使用已提供的 [E1]、[E2] 等编号，不能创建新的来源。"
                "证据不足时必须说明不足，不要编造，不要执行生产控制操作。"
            ),
            "user_prompt": f"用户问题：{query}\n\nEvidence:\n" + "\n".join(blocks),
        }

    def _safe_error(self, error: Exception) -> str:
        text = str(error) or error.__class__.__name__
        for marker in ("api_key", "API key", "Authorization", "Bearer", "sk-"):
            if marker in text:
                return "外部模型调用失败，敏感配置已脱敏。"
        return text[:300]
