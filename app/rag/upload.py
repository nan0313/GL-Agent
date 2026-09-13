from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from app.rag.index_manager import RAGIndexManager
from app.rag.knowledge_management import KnowledgeManagementService
from app.uploads import safe_upload_filename


class KnowledgeUploadError(ValueError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class KnowledgeUploadService:
    def __init__(self, manager: RAGIndexManager) -> None:
        self.manager = manager
        self.config = manager.config
        self.registry = manager.source_registry

    def stage(self, filename: str, content_type: str, content: bytes, *, auto_activate: bool = False) -> dict[str, object]:
        if auto_activate:
            raise KnowledgeUploadError("RAG_AUTO_ACTIVATE_NOT_ALLOWED", 422)
        try:
            clean_name = safe_upload_filename(filename)
        except Exception as exc:
            raise KnowledgeUploadError("RAG_UPLOAD_FILENAME_INVALID", 422) from exc
        suffix = Path(clean_name).suffix.lower()
        if suffix not in self.config.allowed_file_types:
            raise KnowledgeUploadError("RAG_UNSUPPORTED_FILE_TYPE", 415)
        if not content:
            raise KnowledgeUploadError("RAG_EMPTY_DOCUMENT", 422)
        if len(content) > self.config.max_file_size:
            raise KnowledgeUploadError("RAG_SOURCE_TOO_LARGE", 413)
        digest = hashlib.sha256(content).hexdigest()
        existing = []
        for area in ("active", "staging", "rejected"):
            existing.extend(self.registry.scan(area=area))
        duplicate = next((item for item in existing if item.content_hash == digest), None)
        if duplicate is not None:
            raise KnowledgeUploadError("RAG_DUPLICATE_SOURCE", 409)
        root = self.registry.roots[0] if self.registry.roots else None
        if root is None:
            raise KnowledgeUploadError("RAG_ROOT_NOT_CONFIGURED", 500)
        staging = _safe_join(root, "staging")
        staging.mkdir(parents=True, exist_ok=True)
        target = _safe_join(staging, clean_name)
        if target.exists():
            target = _safe_join(staging, f"{target.stem}_{digest[:10]}{suffix}")
        _atomic_write(target, content)
        source = next((item for item in self.registry.scan(area="staging") if item.relative_location == target.relative_to(root).as_posix()), None)
        if source is None:
            target.unlink(missing_ok=True)
            raise KnowledgeUploadError("RAG_UPLOAD_STAGING_FAILED", 500)
        return {
            "status": "staging",
            "auto_activate": False,
            "content_type": str(content_type or "")[:200],
            "source": KnowledgeManagementService(self.manager).get_source(source.source_id),
        }


def _safe_join(root: Path, *parts: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise KnowledgeUploadError("RAG_PATH_TRAVERSAL", 422) from exc
    return candidate


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
