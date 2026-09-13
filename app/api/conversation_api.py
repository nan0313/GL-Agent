from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.conversations import ConversationError, get_default_conversation_service
from app.product.error_mapper import map_internal_error
from app.release_profile import developer_mode_enabled


router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="新会话", min_length=1, max_length=200)


class ConversationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)


class BusinessResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=128)
    answer_markdown: str = Field(min_length=1, max_length=200_000)
    succeeded: bool


def _user_identity(explicit: str | None, header: str | None) -> str:
    if explicit and header and explicit != header:
        raise HTTPException(status_code=403, detail="USER_ID_MISMATCH")
    value = str(header or explicit or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="USER_ID_REQUIRED")
    return value


def _error(exc: ConversationError) -> HTTPException:
    detail = exc.code if developer_mode_enabled() else map_internal_error(exc.code).message
    return HTTPException(status_code=exc.status_code, detail=detail)


@router.post("")
def create_conversation(payload: ConversationCreateRequest, x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    try:
        value = get_default_conversation_service().create(_user_identity(payload.user_id, x_user_id), payload.title)
        return {"conversation": value.model_dump()}
    except ConversationError as exc:
        raise _error(exc)


@router.get("")
def list_conversations(user_id: str = Query(min_length=1, max_length=128), offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200), x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    try:
        values = get_default_conversation_service().list(_user_identity(user_id, x_user_id), offset=offset, limit=limit)
        return {"conversations": [item.model_dump() for item in values], "offset": offset, "limit": limit}
    except ConversationError as exc:
        raise _error(exc)


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, user_id: str = Query(min_length=1, max_length=128), x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    try:
        value = get_default_conversation_service().require(conversation_id, _user_identity(user_id, x_user_id))
        return {"conversation": value.model_dump()}
    except ConversationError as exc:
        raise _error(exc)


@router.patch("/{conversation_id}")
def update_conversation(conversation_id: str, payload: ConversationUpdateRequest, x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    try:
        value = get_default_conversation_service().rename(conversation_id, _user_identity(payload.user_id, x_user_id), payload.title)
        return {"conversation": value.model_dump()}
    except ConversationError as exc:
        raise _error(exc)


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, user_id: str = Query(min_length=1, max_length=128), x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    identity = _user_identity(user_id, x_user_id)
    try:
        service = get_default_conversation_service()
        service.require(conversation_id, identity)
        from app.conversations.attachments import get_default_attachment_service

        get_default_attachment_service().delete_conversation_data(conversation_id, identity)
        service.delete(conversation_id, identity)
        return {"status": "deleted", "conversation_id": conversation_id}
    except ConversationError as exc:
        raise _error(exc)


@router.get("/{conversation_id}/messages")
def list_messages(conversation_id: str, user_id: str = Query(min_length=1, max_length=128), offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500), x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    try:
        values = get_default_conversation_service().messages(conversation_id, _user_identity(user_id, x_user_id), offset=offset, limit=limit)
        messages = [item.model_dump() if developer_mode_enabled() else _public_message(item) for item in values]
        return {"messages": messages, "offset": offset, "limit": limit}
    except ConversationError as exc:
        raise _error(exc)


@router.patch("/{conversation_id}/business-result")
def confirm_business_result(conversation_id: str, payload: BusinessResultRequest, x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    try:
        identity = _user_identity(payload.user_id, x_user_id)
        value = get_default_conversation_service().confirm_latest_business_result(
            conversation_id,
            identity,
            payload.answer_markdown,
            succeeded=payload.succeeded,
        )
        return {"message": _public_message(value)}
    except ConversationError as exc:
        raise _error(exc)


def _public_message(item) -> dict[str, object]:
    value = {
        "message_id": item.message_id,
        "conversation_id": item.conversation_id,
        "role": item.role,
        "content": item.content,
        "status": item.status,
        "created_at": item.created_at,
    }
    final_response = item.metadata.get("customer_final_response") if isinstance(item.metadata, dict) else None
    if item.role == "assistant" and isinstance(final_response, dict):
        value["final_response"] = final_response
    return value
