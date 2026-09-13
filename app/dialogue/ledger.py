from datetime import datetime, timezone
from threading import RLock
from typing import Any

from pydantic import BaseModel, Field


class ModelParticipation(BaseModel):
    request_id: str
    route_source: str
    model_invoked: bool = False
    model_name: str | None = None
    model_stages: list[str] = Field(default_factory=list)
    latency_ms: dict[str, int] = Field(default_factory=dict)
    rule_candidates: list[str] = Field(default_factory=list)
    normalized_text: str
    resolved_references: list[dict[str, Any]] = Field(default_factory=list)
    planner_status: str = "not_invoked"
    planner_repaired: bool = False
    fallback_reason: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ModelParticipationLedger:
    def __init__(self) -> None:
        self._items: dict[str, list[ModelParticipation]] = {}
        self._lock = RLock()

    def add(self, conversation_id: str, item: ModelParticipation) -> None:
        with self._lock:
            self._items.setdefault(conversation_id, []).append(item)
            self._items[conversation_id] = self._items[conversation_id][-100:]

    def list(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [x.model_dump(exclude_none=True) for x in self._items.get(conversation_id, [])]


default_model_ledger = ModelParticipationLedger()
