from app.schemas.trace import TraceRecord


class TraceStore:
    def __init__(self) -> None:
        self._records: dict[str, TraceRecord] = {}

    def save(self, record: TraceRecord) -> None:
        self._records[record.trace_id] = record

    def get(self, trace_id: str) -> TraceRecord | None:
        return self._records.get(trace_id)

    def list_all(self, limit: int = 100) -> list[TraceRecord]:
        return list(self._records.values())[-limit:]

    def clear(self) -> None:
        self._records.clear()
