from __future__ import annotations

import os
from pathlib import Path
import re
from threading import RLock
from typing import Any
from uuid import uuid4

from app.conversations.models import Conversation, ConversationMessage
from app.conversations.store import ConversationStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ConversationError(ValueError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def resolve_conversation_id(request: Any) -> str:
    """Resolve new and legacy request identifiers with explicit precedence."""
    explicit = str(getattr(request, "conversation_id", None) or "").strip()
    legacy = str(getattr(request, "session_id", None) or "").strip()
    value = explicit or legacy
    if not value:
        raise ConversationError("CONVERSATION_ID_REQUIRED", 422)
    return _validate_identifier(value, "CONVERSATION_ID_INVALID")


class ConversationService:
    def __init__(self, store: ConversationStore) -> None:
        self.store = store

    def create(self, user_id: str, title: str = "新会话", *, conversation_id: str | None = None) -> Conversation:
        user = _validate_user_id(user_id)
        clean_title = _validate_title(title)
        cid = _validate_identifier(conversation_id, "CONVERSATION_ID_INVALID") if conversation_id else None
        try:
            return self.store.create_conversation(user, clean_title, conversation_id=cid)
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise ConversationError("CONVERSATION_ALREADY_EXISTS", 409) from exc
            raise

    def ensure(self, conversation_id: str, user_id: str) -> Conversation:
        cid = _validate_identifier(conversation_id, "CONVERSATION_ID_INVALID")
        user = _validate_user_id(user_id)
        existing = self.store.get_conversation(cid)
        if existing:
            self._require_owner(existing, user)
            return existing
        return self.create(user, conversation_id=cid)

    def require(self, conversation_id: str, user_id: str) -> Conversation:
        cid = _validate_identifier(conversation_id, "CONVERSATION_ID_INVALID")
        user = _validate_user_id(user_id)
        value = self.store.get_conversation(cid)
        if value is None:
            raise ConversationError("CONVERSATION_NOT_FOUND", 404)
        self._require_owner(value, user)
        return value

    def list(self, user_id: str, *, offset: int = 0, limit: int = 50) -> list[Conversation]:
        return self.store.list_conversations(_validate_user_id(user_id), offset=offset, limit=limit)

    def rename(self, conversation_id: str, user_id: str, title: str) -> Conversation:
        self.require(conversation_id, user_id)
        value = self.store.rename_conversation(conversation_id, _validate_title(title))
        if value is None:
            raise ConversationError("CONVERSATION_NOT_FOUND", 404)
        return value

    def delete(self, conversation_id: str, user_id: str) -> None:
        self.require(conversation_id, user_id)
        self.store.delete_conversation(conversation_id)

    def messages(self, conversation_id: str, user_id: str, *, offset: int = 0, limit: int = 100) -> list[ConversationMessage]:
        self.require(conversation_id, user_id)
        return self.store.list_messages(conversation_id, offset=offset, limit=limit)

    def append_message(self, conversation_id: str, user_id: str, role: str, content: str, *, request_id: str | None = None, trace_id: str | None = None, status: str = "success", metadata: dict[str, Any] | None = None) -> ConversationMessage:
        self.require(conversation_id, user_id)
        if role not in {"user", "assistant", "system"}:
            raise ConversationError("MESSAGE_ROLE_INVALID", 422)
        text = str(content or "").strip()
        if not text or len(text) > 200_000:
            raise ConversationError("MESSAGE_CONTENT_INVALID", 422)
        value = ConversationMessage(
            message_id=f"msg_{uuid4().hex}", conversation_id=conversation_id, user_id=user_id,
            role=role, content=text, status=str(status or "success")[:64],
            request_id=str(request_id)[:128] if request_id else None,
            trace_id=str(trace_id)[:128] if trace_id else None,
            metadata=metadata or {},
        )
        return self.store.append_message(value)

    def confirm_latest_business_result(
        self,
        conversation_id: str,
        user_id: str,
        content: str,
        *,
        succeeded: bool,
    ) -> ConversationMessage:
        self.require(conversation_id, user_id)
        text = str(content or "").strip()
        if not text or len(text) > 200_000:
            raise ConversationError("MESSAGE_CONTENT_INVALID", 422)
        candidate = None
        for message in reversed(self.store.list_messages(conversation_id, offset=0, limit=500)):
            final_response = message.metadata.get("customer_final_response") if isinstance(message.metadata, dict) else None
            if message.role == "assistant" and isinstance(final_response, dict) and final_response.get("business_actions"):
                candidate = message
                break
        if candidate is None:
            raise ConversationError("BUSINESS_RESULT_NOT_FOUND", 404)
        metadata = dict(candidate.metadata)
        final_response = dict(metadata.get("customer_final_response") or {})
        final_response["answer_markdown"] = text
        final_response["error"] = None if succeeded else {
            "category": "business_action_failed",
            "message": text,
            "retryable": False,
        }
        if not succeeded:
            final_response["business_actions"] = []
        metadata["customer_final_response"] = final_response
        updated = self.store.update_message_result(
            conversation_id,
            user_id,
            candidate.message_id,
            content=text,
            status="success" if succeeded else "failed",
            metadata=metadata,
        )
        if updated is None:
            raise ConversationError("BUSINESS_RESULT_NOT_FOUND", 404)
        return updated

    @staticmethod
    def _require_owner(conversation: Conversation, user_id: str) -> None:
        if conversation.user_id != user_id:
            raise ConversationError("CONVERSATION_FORBIDDEN", 403)


_default_lock = RLock()
_default_store: ConversationStore | None = None
_default_service: ConversationService | None = None


def conversation_database_path() -> Path:
    configured = os.getenv("CONVERSATION_SQLITE_PATH", "").strip()
    return Path(configured).resolve() if configured else (PROJECT_ROOT / "runtime" / "conversations" / "conversations.sqlite3")


def get_default_conversation_store() -> ConversationStore:
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = ConversationStore(conversation_database_path())
        return _default_store


def get_default_conversation_service() -> ConversationService:
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = ConversationService(get_default_conversation_store())
        return _default_service


def close_default_conversation_store() -> None:
    global _default_store, _default_service
    with _default_lock:
        if _default_store is not None:
            _default_store.close()
        _default_store = None
        _default_service = None


def _validate_identifier(value: str | None, code: str) -> str:
    text = str(value or "").strip()
    if not _ID_PATTERN.fullmatch(text):
        raise ConversationError(code, 422)
    return text


def _validate_user_id(value: str) -> str:
    return _validate_identifier(value, "USER_ID_INVALID")


def _validate_title(value: str) -> str:
    title = " ".join(str(value or "").split()).strip()
    if not title or len(title) > 200 or any(char in title for char in "\r\n\x00"):
        raise ConversationError("CONVERSATION_TITLE_INVALID", 422)
    return title
