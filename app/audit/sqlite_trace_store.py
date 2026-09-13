import sqlite3
from pathlib import Path

from app.config import get_trace_sqlite_path
from app.schemas.trace import TraceRecord


class SQLiteTraceStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or get_trace_sqlite_path())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def save(self, record: TraceRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO traces (trace_id, payload)
                VALUES (?, ?)
                """,
                (record.trace_id, record.model_dump_json()),
            )

    def get(self, trace_id: str) -> TraceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        if row is None:
            return None
        return TraceRecord.model_validate_json(row[0])

    def list_all(self, limit: int = 100) -> list[TraceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM traces ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [TraceRecord.model_validate_json(row[0]) for row in rows]

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM traces")

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )

    def _connect(self):
        return sqlite3.connect(self.db_path)
