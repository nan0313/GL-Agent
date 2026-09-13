from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from app.conversations.models import Conversation, ConversationAttachment, ConversationMessage, utc_now


CONVERSATION_SCHEMA_VERSION = 1


class ConversationStore:
    """SQLite persistence for user-owned conversations and runtime attachments."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._closed = False
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            if self.database_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_user_recent
                    ON conversations(user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_id TEXT,
                    trace_id TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(conversation_id, request_id, role)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                    ON messages(conversation_id, created_at, message_id);
                CREATE TABLE IF NOT EXISTS conversation_states (
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    state_json TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attachments (
                    attachment_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'conversation',
                    source_id TEXT,
                    index_job_id TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(conversation_id, sha256)
                );
                CREATE INDEX IF NOT EXISTS idx_attachments_conversation_created
                    ON attachments(conversation_id, created_at DESC);
                """
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO conversation_schema_meta(key,value) VALUES('schema_version',?)",
                (str(CONVERSATION_SCHEMA_VERSION),),
            )
            self._connection.commit()

    @contextmanager
    def transaction(self) -> Iterator["ConversationStore"]:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                yield self
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()

    def create_conversation(self, user_id: str, title: str, *, conversation_id: str | None = None, metadata: dict[str, Any] | None = None) -> Conversation:
        now = utc_now()
        value = Conversation(
            conversation_id=conversation_id or f"conv_{uuid4().hex}",
            user_id=user_id,
            title=title,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        with self.transaction():
            self._connection.execute(
                "INSERT INTO conversations(conversation_id,user_id,title,created_at,updated_at,last_message_at,metadata_json) VALUES(?,?,?,?,?,?,?)",
                (value.conversation_id, value.user_id, value.title, now, now, None, _json(value.metadata)),
            )
        return value

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT c.*,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.conversation_id) AS message_count,
                       (SELECT COUNT(*) FROM attachments a WHERE a.conversation_id=c.conversation_id) AS attachment_count
                FROM conversations c WHERE c.conversation_id=?
                """,
                (conversation_id,),
            ).fetchone()
        return _conversation_from_row(row) if row else None

    def list_conversations(self, user_id: str, *, offset: int = 0, limit: int = 50) -> list[Conversation]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT c.*,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.conversation_id) AS message_count,
                       (SELECT COUNT(*) FROM attachments a WHERE a.conversation_id=c.conversation_id) AS attachment_count
                FROM conversations c WHERE c.user_id=?
                ORDER BY COALESCE(c.last_message_at,c.updated_at) DESC, c.conversation_id
                LIMIT ? OFFSET ?
                """,
                (user_id, max(1, min(int(limit), 200)), max(0, int(offset))),
            ).fetchall()
        return [_conversation_from_row(row) for row in rows]

    def rename_conversation(self, conversation_id: str, title: str) -> Conversation | None:
        with self.transaction():
            self._connection.execute(
                "UPDATE conversations SET title=?,updated_at=? WHERE conversation_id=?",
                (title, utc_now(), conversation_id),
            )
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        with self.transaction():
            result = self._connection.execute("DELETE FROM conversations WHERE conversation_id=?", (conversation_id,))
        return bool(result.rowcount)

    def append_message(self, message: ConversationMessage) -> ConversationMessage:
        with self.transaction():
            if message.request_id:
                existing = self._connection.execute(
                    "SELECT * FROM messages WHERE conversation_id=? AND request_id=? AND role=?",
                    (message.conversation_id, message.request_id, message.role),
                ).fetchone()
                if existing:
                    return _message_from_row(existing)
            self._connection.execute(
                "INSERT INTO messages(message_id,conversation_id,user_id,role,content,status,request_id,trace_id,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (message.message_id, message.conversation_id, message.user_id, message.role, message.content, message.status, message.request_id, message.trace_id, message.created_at, _json(message.metadata)),
            )
            current = self._connection.execute(
                "SELECT title,(SELECT COUNT(*) FROM messages WHERE conversation_id=? AND role='user') AS user_count FROM conversations WHERE conversation_id=?",
                (message.conversation_id, message.conversation_id),
            ).fetchone()
            title = current["title"] if current else "新会话"
            if message.role == "user" and current and int(current["user_count"] or 0) == 1 and title in {"新会话", "New conversation"}:
                title = derive_title(message.content)
            self._connection.execute(
                "UPDATE conversations SET title=?,last_message_at=?,updated_at=? WHERE conversation_id=?",
                (title, message.created_at, message.created_at, message.conversation_id),
            )
        return message

    def list_messages(self, conversation_id: str, *, offset: int = 0, limit: int = 100) -> list[ConversationMessage]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,rowid LIMIT ? OFFSET ?",
                (conversation_id, max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
        return [_message_from_row(row) for row in rows]

    def update_message_result(
        self,
        conversation_id: str,
        user_id: str,
        message_id: str,
        *,
        content: str,
        status: str,
        metadata: dict[str, Any],
    ) -> ConversationMessage | None:
        with self.transaction():
            result = self._connection.execute(
                "UPDATE messages SET content=?,status=?,metadata_json=? WHERE conversation_id=? AND user_id=? AND message_id=? AND role='assistant'",
                (content, status, _json(metadata), conversation_id, user_id, message_id),
            )
            if not result.rowcount:
                return None
            row = self._connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND user_id=? AND message_id=?",
                (conversation_id, user_id, message_id),
            ).fetchone()
        return _message_from_row(row) if row else None

    def get_state(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT state_json FROM conversation_states WHERE conversation_id=?", (conversation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_state(self, conversation_id: str, state: dict[str, Any]) -> None:
        with self.transaction():
            self._connection.execute(
                "INSERT OR REPLACE INTO conversation_states(conversation_id,state_json,schema_version,updated_at) VALUES(?,?,?,?)",
                (conversation_id, _json(state), CONVERSATION_SCHEMA_VERSION, utc_now()),
            )

    def add_attachment(self, value: ConversationAttachment) -> ConversationAttachment:
        with self.transaction():
            self._connection.execute(
                """INSERT INTO attachments(attachment_id,conversation_id,user_id,filename,stored_name,relative_path,mime_type,size,sha256,status,scope,source_id,index_job_id,error_code,created_at,updated_at,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (value.attachment_id, value.conversation_id, value.user_id, value.filename, value.stored_name, value.relative_path, value.mime_type, value.size, value.sha256, value.status, value.scope, value.source_id, value.index_job_id, value.error_code, value.created_at, value.updated_at, _json(value.metadata)),
            )
        return value

    def get_attachment(self, attachment_id: str) -> ConversationAttachment | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM attachments WHERE attachment_id=?", (attachment_id,)).fetchone()
        return _attachment_from_row(row) if row else None

    def get_attachment_by_hash(self, conversation_id: str, digest: str) -> ConversationAttachment | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM attachments WHERE conversation_id=? AND sha256=?", (conversation_id, digest)).fetchone()
        return _attachment_from_row(row) if row else None

    def list_attachments(self, conversation_id: str) -> list[ConversationAttachment]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM attachments WHERE conversation_id=? ORDER BY created_at DESC,attachment_id", (conversation_id,)).fetchall()
        return [_attachment_from_row(row) for row in rows]

    def update_attachment(self, attachment_id: str, **changes: Any) -> ConversationAttachment | None:
        allowed = {"status", "source_id", "index_job_id", "error_code", "metadata"}
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return self.get_attachment(attachment_id)
        columns: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            columns.append("metadata_json=?" if key == "metadata" else f"{key}=?")
            values.append(_json(value) if key == "metadata" else value)
        columns.append("updated_at=?")
        values.append(utc_now())
        values.append(attachment_id)
        with self.transaction():
            self._connection.execute(f"UPDATE attachments SET {','.join(columns)} WHERE attachment_id=?", tuple(values))
        return self.get_attachment(attachment_id)

    def delete_attachment(self, attachment_id: str) -> bool:
        with self.transaction():
            result = self._connection.execute("DELETE FROM attachments WHERE attachment_id=?", (attachment_id,))
        return bool(result.rowcount)


def derive_title(content: str, max_chars: int = 28) -> str:
    value = " ".join(str(content or "").split()).strip()
    if not value:
        return "新会话"
    return value if len(value) <= max_chars else value[:max_chars].rstrip() + "…"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _conversation_from_row(row: sqlite3.Row) -> Conversation:
    return Conversation(
        conversation_id=row["conversation_id"], user_id=row["user_id"], title=row["title"],
        created_at=row["created_at"], updated_at=row["updated_at"], last_message_at=row["last_message_at"],
        message_count=int(row["message_count"] or 0) if "message_count" in row.keys() else 0,
        attachment_count=int(row["attachment_count"] or 0) if "attachment_count" in row.keys() else 0,
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def _message_from_row(row: sqlite3.Row) -> ConversationMessage:
    return ConversationMessage(
        message_id=row["message_id"], conversation_id=row["conversation_id"], user_id=row["user_id"], role=row["role"],
        content=row["content"], status=row["status"], request_id=row["request_id"], trace_id=row["trace_id"],
        created_at=row["created_at"], metadata=json.loads(row["metadata_json"] or "{}"),
    )


def _attachment_from_row(row: sqlite3.Row) -> ConversationAttachment:
    return ConversationAttachment(
        attachment_id=row["attachment_id"], conversation_id=row["conversation_id"], user_id=row["user_id"], filename=row["filename"],
        stored_name=row["stored_name"], relative_path=row["relative_path"], mime_type=row["mime_type"], size=int(row["size"]),
        sha256=row["sha256"], status=row["status"], scope="conversation", source_id=row["source_id"], index_job_id=row["index_job_id"],
        error_code=row["error_code"], created_at=row["created_at"], updated_at=row["updated_at"], metadata=json.loads(row["metadata_json"] or "{}"),
    )
