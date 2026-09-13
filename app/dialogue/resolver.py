import re
from typing import Any

from pydantic import BaseModel, Field

from app.dialogue.capabilities import INTENT_FOCUS_KINDS
from app.dialogue.models import DialogueState, FocusItem


class ReferenceResolution(BaseModel):
    expression: str
    resolved: bool = False
    focus_id: str | None = None
    kind: str | None = None
    reference_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str | None = None
    needs_clarification: bool = False
    candidates: list[dict[str, Any]] = Field(default_factory=list)


_PLURAL = ["这些对象", "它们", "他们", "她们", "刚才查询到的对象", "查询结果", "所有这些对象"]
_BUFFER = ["这个缓冲区", "刚才的缓冲区", "当前缓冲区", "缓冲区内部"]
_ADMIN = ["这个城市", "这个省", "刚才那个行政区", "刚才那个城市", "它的边界", "他的边界", "她的边界"]
_SINGLE = ["这个对象", "该对象", "它", "他", "她", "那个对象", "刚才那个", "前面那个", "刚才找到的对象", "刚才定位的对象", "刚才高亮的对象"]
_SPATIAL = ["这里", "当前位置", "当前区域", "刚才那个位置"]


class ReferenceResolver:
    def resolve_all(self, text: str, state: DialogueState, intent: str | None = None) -> list[ReferenceResolution]:
        results: list[ReferenceResolution] = []
        matched: set[str] = set()
        groups = [(_PLURAL, "object_set"), (_BUFFER, "buffer"), (_ADMIN, "admin_region"), (_SINGLE, "object"), (_SPATIAL, "coordinate")]
        for expressions, kind in groups:
            for expression in sorted(expressions, key=len, reverse=True):
                if expression in text and expression not in matched:
                    matched.add(expression)
                    results.append(self._resolve(expression, kind, state, intent))
        ordinal = re.search(r"(第一个对象|第二个对象|第一个|第二个|最后一个|前两个)", text)
        if ordinal:
            results.append(self._resolve_ordinal(ordinal.group(1), state))
        return results

    def _resolve(self, expression: str, kind: str, state: DialogueState, intent: str | None) -> ReferenceResolution:
        if kind == "object_set":
            obj_set = state.last_object_set
            if obj_set and obj_set.active:
                if not obj_set.items:
                    return ReferenceResolution(expression=expression, kind=kind, needs_clarification=True, reason="上一轮查询结果为空")
                return ReferenceResolution(expression=expression, resolved=True, kind=kind, reference_ids=[x.reference_id for x in obj_set.items], confidence=.99, reason="latest active object set")
            return ReferenceResolution(expression=expression, kind=kind, needs_clarification=True, reason="当前没有可用的对象集合上下文")

        allowed = INTENT_FOCUS_KINDS.get(intent or "", set())
        if expression in {"里面", "其中"}:
            preferred = {"buffer"} if intent == "query_objects_in_buffer" else {"object_set", "buffer_query", "buffer"}
        elif expression in {"它", "他", "她", "这个对象", "该对象", "那个对象", "刚才那个", "前面那个"}:
            preferred = allowed or {"object", "admin_region", "coordinate"}
        else:
            preferred = {kind}

        candidates = [f for f in state.active_focuses() if f.kind in preferred]
        if intent and intent != "multi_step":
            candidates = [f for f in candidates if intent in f.available_actions or (intent == "create_buffer" and f.kind in {"admin_region", "coordinate"})]
        # The same real feature can be represented by a selected object focus and
        # an administrative-region focus.  That is one target, not ambiguity.
        deduped: dict[str, FocusItem] = {}
        priority = {"admin_region": 3, "object": 2, "coordinate": 1}
        for candidate in candidates:
            label_key = re.sub(r"\s+", "", candidate.label or "").casefold()
            identity_key = label_key or candidate.reference_id
            current = deduped.get(identity_key)
            if current is None or priority.get(candidate.kind, 0) > priority.get(current.kind, 0):
                deduped[identity_key] = candidate
        candidates = sorted(deduped.values(), key=lambda item: (item.source_turn, item.confidence), reverse=True)
        if not candidates:
            return ReferenceResolution(expression=expression, kind=kind, needs_clarification=True, reason=f"没有可用于{intent or '当前动作'}的有效焦点")

        top_turn = candidates[0].source_turn
        close = [c for c in candidates if c.source_turn == top_turn and abs(c.confidence - candidates[0].confidence) < .05]
        if len(close) > 1:
            return ReferenceResolution(expression=expression, kind=kind, needs_clarification=True, reason="存在多个动作兼容且同等可信的候选", candidates=[self._candidate(c) for c in close])
        focus = candidates[0]
        return ReferenceResolution(expression=expression, resolved=True, focus_id=focus.focus_id, kind=focus.kind, reference_ids=[focus.reference_id], confidence=focus.confidence, reason=f"latest action-compatible {focus.kind} focus")

    def _resolve_ordinal(self, expression: str, state: DialogueState) -> ReferenceResolution:
        obj_set = state.last_object_set
        if not obj_set or not obj_set.active:
            return ReferenceResolution(expression=expression, kind="object", needs_clarification=True, reason="当前没有对象集合上下文")
        if not obj_set.items:
            return ReferenceResolution(expression=expression, kind="object", needs_clarification=True, reason="上一轮查询结果为空")
        if expression == "前两个":
            return ReferenceResolution(expression=expression, resolved=True, kind="object_set", reference_ids=[x.reference_id for x in obj_set.items[:2]], confidence=1.0, reason="ordered object set")
        index = -1 if expression == "最后一个" else (1 if "第二" in expression else 0)
        if index >= len(obj_set.items):
            return ReferenceResolution(expression=expression, kind="object", needs_clarification=True, reason=f"查询结果只有{len(obj_set.items)}项")
        item = obj_set.items[index]
        return ReferenceResolution(expression=expression, resolved=True, kind="object", reference_ids=[item.reference_id], confidence=1.0, reason="ordered object set")

    @staticmethod
    def _candidate(focus: FocusItem) -> dict[str, Any]:
        type_labels = {"object": "对象", "admin_region": "行政区", "coordinate": "位置", "buffer": "缓冲区", "object_set": "对象集合"}
        return {"focus_id": focus.focus_id, "kind": focus.kind, "reference_id": focus.reference_id, "label": focus.label, "type_label": type_labels.get(focus.kind, focus.kind), "confidence": focus.confidence}
