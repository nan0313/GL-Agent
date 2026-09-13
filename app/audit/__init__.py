"""Trace and audit exports."""

from app.audit.trace_store import TraceStore
from app.audit.sqlite_trace_store import SQLiteTraceStore


def create_trace_store(provider: str | None = None):
    from app.config import get_trace_store_provider

    selected = provider or get_trace_store_provider()
    if selected == "memory":
        return TraceStore()
    if selected == "sqlite":
        return SQLiteTraceStore()
    raise NotImplementedError(f"Unsupported trace store provider: {selected}")


__all__ = ["TraceStore", "SQLiteTraceStore", "create_trace_store"]
