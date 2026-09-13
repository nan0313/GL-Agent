from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request

from app.api.conversation_api import _user_identity
from app.conversations import ConversationError
from app.conversations.attachments import get_default_attachment_service
from app.rag.config import get_rag_config
from app.product.error_mapper import map_internal_error
from app.release_profile import developer_mode_enabled
from app.uploads import UploadBoundaryError, read_single_file_multipart


router = APIRouter(prefix="/api/conversations", tags=["conversation-attachments"])


@router.post("/{conversation_id}/attachments")
async def upload_attachment(conversation_id: str, request: Request, user_id: str = Query(min_length=1, max_length=128), x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    identity = _user_identity(user_id, x_user_id)
    try:
        upload = await read_single_file_multipart(request, max_file_bytes=get_rag_config().max_file_size)
        value = get_default_attachment_service().upload(conversation_id, identity, upload.filename, upload.content_type, upload.content)
        return {"attachment": value.model_dump() if developer_mode_enabled() else _public_attachment(value)}
    except UploadBoundaryError as exc:
        detail = exc.code if developer_mode_enabled() else map_internal_error(exc.code).message
        raise HTTPException(status_code=exc.status_code, detail=detail)
    except ConversationError as exc:
        detail = exc.code if developer_mode_enabled() else map_internal_error(exc.code).message
        raise HTTPException(status_code=exc.status_code, detail=detail)


@router.get("/{conversation_id}/attachments")
def list_attachments(conversation_id: str, user_id: str = Query(min_length=1, max_length=128), x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    try:
        values = get_default_attachment_service().list(conversation_id, _user_identity(user_id, x_user_id))
        items = [item.model_dump() if developer_mode_enabled() else _public_attachment(item) for item in values]
        return {"attachments": items}
    except ConversationError as exc:
        detail = exc.code if developer_mode_enabled() else map_internal_error(exc.code).message
        raise HTTPException(status_code=exc.status_code, detail=detail)


@router.delete("/{conversation_id}/attachments/{attachment_id}")
def delete_attachment(conversation_id: str, attachment_id: str, user_id: str = Query(min_length=1, max_length=128), x_user_id: str | None = Header(default=None)) -> dict[str, object]:
    try:
        return get_default_attachment_service().delete(conversation_id, attachment_id, _user_identity(user_id, x_user_id))
    except ConversationError as exc:
        detail = exc.code if developer_mode_enabled() else map_internal_error(exc.code).message
        raise HTTPException(status_code=exc.status_code, detail=detail)


def _public_attachment(item) -> dict[str, object]:
    return {
        "attachment_id": item.attachment_id,
        "conversation_id": item.conversation_id,
        "filename": item.filename,
        "mime_type": item.mime_type,
        "size": item.size,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
