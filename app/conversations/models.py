from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Conversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    user_id: str
    title: str
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    last_message_at: str | None = None
    message_count: int = 0
    attachment_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    conversation_id: str
    user_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    status: str = "success"
    request_id: str | None = None
    trace_id: str | None = None
    created_at: str = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment_id: str
    conversation_id: str
    user_id: str
    filename: str
    stored_name: str
    relative_path: str
    mime_type: str
    size: int = Field(ge=0)
    sha256: str
    status: str = "UPLOADED"
    scope: Literal["conversation"] = "conversation"
    source_id: str | None = None
    index_job_id: str | None = None
    error_code: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
