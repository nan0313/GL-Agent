from threading import RLock
from typing import Any

from .models import QwenInvocation


class QwenInvocationLedger:
    def __init__(self) -> None:
        self._items: list[QwenInvocation] = []
        self._lock = RLock()

    def add(self, item: QwenInvocation) -> None:
        with self._lock:
            self._items.append(item)
            self._items = self._items[-500:]

    def list(self, request_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = self._items if not request_id else [item for item in self._items if item.request_id == request_id]
            return [item.model_dump(exclude_none=False) for item in items[-100:]]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


default_qwen_ledger = QwenInvocationLedger()
