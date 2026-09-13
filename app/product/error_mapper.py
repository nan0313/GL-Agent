from __future__ import annotations

from app.schemas.response import UserFacingError


_MESSAGES: dict[str, tuple[str, str, bool]] = {
    "ATTACHMENT_DUPLICATE": ("attachment_duplicate", "这个文件已经上传过了，可以直接开始提问。", False),
    "DOCUMENT_INDEXING": ("document_processing", "文件正在处理中，完成后即可开始问答。", True),
    "ATTACHMENT_NOT_READY": ("document_processing", "文件正在处理中，完成后即可开始问答。", True),
    "RAG_RETRIEVAL_FAILED": ("knowledge_unavailable", "暂时无法从知识库获取相关内容，请稍后重试。", True),
    "RAG_SKILL_FAILED": ("knowledge_unavailable", "暂时无法从知识库获取相关内容，请稍后重试。", True),
    "MAP_TARGET_NOT_FOUND": ("object_not_found", "当前场景中没有找到对应对象。", False),
    "OBJECT_NOT_FOUND": ("object_not_found", "当前场景中没有找到对应对象。", False),
    "UNRESOLVED_SOURCE_REFERENCE": ("source_not_selected", "请明确要查询的文件或规范。", False),
    "NETWORK_ERROR": ("request_incomplete", "请求暂时未完成，请重试。", True),
    "REQUEST_FAILED": ("request_incomplete", "请求暂时未完成，请重试。", True),
    "AGENT_REQUEST_FAILED": ("request_incomplete", "请求暂时未完成，请重试。", True),
}


def map_internal_error(code: str | None, message: str | None = None) -> UserFacingError:
    normalized = str(code or "REQUEST_FAILED").strip().upper()
    category, friendly, retryable = _MESSAGES.get(
        normalized,
        ("request_incomplete", "请求暂时未完成，请重试。", True),
    )
    return UserFacingError(category=category, message=friendly, retryable=retryable)
