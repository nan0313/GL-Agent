import json
from typing import Any

from .models import PromptBudgetResult


class PromptBudget:
    """Compress structured context before serialisation; never sends browser internals."""

    def __init__(self, *, max_chars: int = 12000, max_recent_turns: int = 6, max_candidates: int = 8, max_tools: int = 24, max_chunks: int = 6, max_properties: int = 12) -> None:
        self.max_chars = max_chars
        self.max_recent_turns = max_recent_turns
        self.max_candidates = max_candidates
        self.max_tools = max_tools
        self.max_chunks = max_chunks
        self.max_properties = max_properties

    def messages(self, messages: list[dict[str, str]], *, purpose: str = "unknown") -> PromptBudgetResult:
        original_chars = sum(len(str(item.get("content", ""))) for item in messages)
        result = [dict(item) for item in messages]
        removed: list[str] = []
        for item in result:
            content = item.get("content", "")
            try:
                value = json.loads(content)
                original_value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                value = self._compact_value(value, removed)
                compacted = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if compacted != original_value or len(compacted) > self.max_chars:
                    # Keep structured context valid JSON; never slice serialized JSON.
                    if len(compacted) > self.max_chars:
                        item["content"] = json.dumps({"prompt_budget_truncated": True}, ensure_ascii=False)
                        removed.append("oversize_structured_context")
                    else:
                        item["content"] = compacted
                    removed.append("structured_context")
            except (TypeError, ValueError):
                if len(content) > self.max_chars:
                    item["content"] = content[: self.max_chars]
                    removed.append("message_content")
            if len(item.get("content", "")) > self.max_chars:
                item["content"] = json.dumps({"prompt_budget_truncated": True}, ensure_ascii=False)
                if "message_content" not in removed:
                    removed.append("message_content")
        while sum(len(str(item.get("content", ""))) for item in result) > self.max_chars and len(result) > 1:
            result.pop(1)
            removed.append("old_message")
        final_chars = sum(len(str(item.get("content", ""))) for item in result)
        return PromptBudgetResult(result, original_chars, final_chars, removed, f"{purpose}_budget" if removed else None)

    def _compact_value(self, value: Any, removed: list[str], depth: int = 0) -> Any:
        forbidden = {"geometry", "coordinates", "properties", "ui_events", "raw_events", "raw_messages", "endpoint", "base_url", "authorization", "cookie"}
        if isinstance(value, dict):
            output = {}
            for key, item in value.items():
                if str(key).lower() in forbidden:
                    removed.append(str(key))
                    continue
                key_name = str(key).lower()
                if isinstance(item, list):
                    if key_name in {"recent_turns", "turns", "conversation"}:
                        limit = self.max_recent_turns
                    elif key_name in {"tools", "allowed_tools", "tool_capabilities"}:
                        limit = self.max_tools
                    elif key_name in {"candidates", "rule_candidates", "object_candidates", "references"}:
                        limit = self.max_candidates
                    elif key_name in {"chunks", "evidence", "rag_chunks"}:
                        limit = self.max_chunks
                    else:
                        limit = None
                    if limit is not None and len(item) > limit:
                        removed.append(f"{key_name}_items")
                        item = item[:limit]
                output[key] = self._compact_value(item, removed, depth + 1)
            return output
        if isinstance(value, list):
            limit = self.max_candidates if depth <= 2 else self.max_chunks
            if len(value) > limit:
                removed.append("list_items")
            return [self._compact_value(item, removed, depth + 1) for item in value[:limit]]
        return value

    def compact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"original_text", "normalized_text", "quantities", "reference_expressions", "rule_candidates", "dialogue_summary", "allowed_intents", "evidence", "query"}
        output = {key: payload[key] for key in allowed if key in payload}
        for key in ("quantities", "rule_candidates"):
            if isinstance(output.get(key), list):
                output[key] = output[key][: self.max_candidates]
        if isinstance(output.get("evidence"), list):
            output["evidence"] = output["evidence"][: self.max_chunks]
        return output

    def audit(self, result: PromptBudgetResult) -> dict[str, Any]:
        return {"original_chars": result.original_chars, "final_chars": result.final_chars, "removed_sections": result.removed_sections, "budget_reason": result.budget_reason}
