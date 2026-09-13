from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ObjectReference(BaseModel):
    model_config = ConfigDict(extra="allow")
    reference_id: str
    name: str | None = None
    object_type: str | None = None
    rank: int | None = None
    active: bool = True
    data: dict[str, Any] = Field(default_factory=dict)


class ObjectSet(BaseModel):
    set_id: str = Field(default_factory=lambda: f"set_{uuid4().hex}")
    kind: Literal["object_set"] = "object_set"
    source_tool: str
    items: list[ObjectReference] = Field(default_factory=list)
    count: int = 0
    created_at: str = Field(default_factory=utc_now)
    active: bool = True


class FocusItem(BaseModel):
    focus_id: str = Field(default_factory=lambda: f"focus_{uuid4().hex}")
    kind: Literal["object", "object_set", "admin_region", "buffer", "buffer_query", "coordinate", "layer", "attachment"]
    reference_id: str
    label: str | None = None
    source_turn: int
    source_tool: str | None = None
    confidence: float = 1.0
    plural: bool = False
    available_actions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    active: bool = True
    snapshot: dict[str, Any] = Field(default_factory=dict)

    @property
    def focus_key(self) -> str:
        return f"{self.kind}:{self.reference_id}"


class PendingClarification(BaseModel):
    clarification_id: str = Field(default_factory=lambda: f"clarification_{uuid4().hex}")
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid4().hex}")
    original_request_id: str | None = None
    missing_field: str
    missing_fields: list[str] = Field(default_factory=list)
    question: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    original_utterance: str
    normalized_utterance: str
    plan: dict[str, Any] = Field(default_factory=dict)
    created_turn: int = 0
    expires_after_turns: int = 3
    expires_turn: int | None = None
    status: Literal["awaiting_user", "resolved", "canceled", "expired"] = "awaiting_user"
    resolved_at: str | None = None


class DocumentContext(BaseModel):
    """Lightweight attachment state; document bodies remain in the RAG store."""

    last_attachment_id: str | None = None
    attachment_count: int = 0
    active_attachment_ids: list[str] = Field(default_factory=list)
    indexing_attachment_ids: list[str] = Field(default_factory=list)
    failed_attachment_ids: list[str] = Field(default_factory=list)
    ocr_required_attachment_ids: list[str] = Field(default_factory=list)
    selected_attachment_ids: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    last_document_intent: str | None = None
    last_document_query: str | None = None
    last_evidence_chunk_ids: list[str] = Field(default_factory=list)


class DialogueState(BaseModel):
    conversation_id: str
    turn_index: int = 0
    active_topic: str | None = None
    focus_stack: list[FocusItem] = Field(default_factory=list)
    last_single_object: dict[str, Any] | None = None
    last_object_set: ObjectSet | None = None
    last_admin_region: dict[str, Any] | None = None
    last_buffer: dict[str, Any] | None = None
    last_buffer_query: dict[str, Any] | None = None
    last_tool_result: dict[str, Any] | None = None
    last_successful_plan: dict[str, Any] | None = None
    pending_clarification: PendingClarification | None = None
    recent_turns: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)
    legacy_context_imported: bool = False
    clarification_history: list[dict[str, Any]] = Field(default_factory=list)
    state_events: list[dict[str, Any]] = Field(default_factory=list)
    document_context: DocumentContext = Field(default_factory=DocumentContext)

    def add_focus(self, focus: FocusItem) -> None:
        for item in self.focus_stack:
            if item.kind == focus.kind and item.reference_id == focus.reference_id:
                item.label = focus.label or item.label
                item.source_turn = focus.source_turn
                item.source_tool = focus.source_tool or item.source_tool
                item.confidence = focus.confidence
                item.plural = focus.plural
                item.available_actions = list(focus.available_actions or item.available_actions)
                item.active = focus.active
                item.snapshot = dict(focus.snapshot or item.snapshot)
                return
        self.focus_stack.append(focus)
        self.focus_stack = self.focus_stack[-50:]

    def active_focuses(self, *kinds: str) -> list[FocusItem]:
        return [f for f in reversed(self.focus_stack) if f.active and (not kinds or f.kind in kinds)]

    def invalidate(self, *kinds: str) -> None:
        for focus in self.focus_stack:
            if focus.kind in kinds:
                focus.active = False

    def invalidate_reference(self, kind: str, reference_id: str) -> None:
        for focus in self.focus_stack:
            if focus.kind == kind and focus.reference_id == reference_id:
                focus.active = False
