from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
from threading import RLock
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

from app.conversations.models import ConversationAttachment
from app.conversations.service import ConversationError, ConversationService, PROJECT_ROOT, get_default_conversation_service
from app.rag.config import RAGConfig, get_rag_config
from app.rag.index_manager import RAGIndexManager, RebuildInProgressError
from app.rag.index_store import get_default_index_manager
from app.rag.knowledge_management import KnowledgeManagementError, KnowledgeManagementService
from app.rag.schema import RAGHit
from app.uploads import UploadBoundaryError, safe_upload_filename


_ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def supported_attachment_extensions() -> list[str]:
    return sorted(_ALLOWED_TYPES)


class AttachmentService:
    """Owns per-conversation files and isolated RAG index managers."""

    def __init__(self, conversation_service: ConversationService, runtime_root: str | Path, *, base_config: RAGConfig | None = None, embedding_provider=None) -> None:
        self.conversations = conversation_service
        self.store = conversation_service.store
        self.runtime_root = Path(runtime_root).resolve()
        self.base_config = base_config or get_rag_config()
        self.embedding_provider = embedding_provider
        self._managers: dict[str, RAGIndexManager] = {}
        self._lock = RLock()

    def upload(self, conversation_id: str, user_id: str, filename: str, content_type: str, content: bytes) -> ConversationAttachment:
        self.conversations.require(conversation_id, user_id)
        clean_name = safe_upload_filename(filename)
        suffix = Path(clean_name).suffix.lower()
        if suffix not in _ALLOWED_TYPES:
            raise ConversationError("ATTACHMENT_UNSUPPORTED_FILE_TYPE", 415)
        if not content:
            raise ConversationError("ATTACHMENT_EMPTY_FILE", 422)
        if len(content) > self.base_config.max_file_size:
            raise ConversationError("ATTACHMENT_TOO_LARGE", 413)
        digest = hashlib.sha256(content).hexdigest()
        duplicate = self.store.get_attachment_by_hash(conversation_id, digest)
        if duplicate is not None:
            raise ConversationError("ATTACHMENT_DUPLICATE", 409)
        attachment_id = f"att_{uuid4().hex}"
        stored_name = f"{attachment_id}{suffix}"
        conversation_root = self._conversation_root(conversation_id)
        relative = Path("attachments") / stored_name
        attachment_path = self._safe_join(conversation_root, relative)
        staging_path = self._safe_join(conversation_root, "rag", "knowledge", "staging", stored_name)
        attachment_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(attachment_path, content)
        try:
            _atomic_write(staging_path, content)
            value = ConversationAttachment(
                attachment_id=attachment_id, conversation_id=conversation_id, user_id=user_id,
                filename=clean_name, stored_name=stored_name, relative_path=relative.as_posix(),
                mime_type=_ALLOWED_TYPES[suffix], size=len(content), sha256=digest,
                status="UPLOADED", metadata={"upload_content_type": str(content_type or "")[:200]},
            )
            self.store.add_attachment(value)
            self.store.update_attachment(attachment_id, status="PARSING")
            manager = self._manager(conversation_id)
            source = next((item for item in manager.source_registry.scan(area="staging") if item.display_name == stored_name), None)
            if source is None:
                raise ConversationError("ATTACHMENT_STAGING_FAILED", 500)
            validation = KnowledgeManagementService(manager).validate(source_id=source.source_id, area="staging", description=f"conversation attachment {attachment_id}", tags=["conversation_attachment"])
            validated = (validation.get("sources") or [{}])[0]
            if validation.get("status") != "success":
                error_code = validated.get("error_code") or "ATTACHMENT_PARSE_FAILED"
                status = "OCR_REQUIRED" if error_code == "RAG_PDF_OCR_REQUIRED" else "FAILED"
                return self.store.update_attachment(attachment_id, status=status, source_id=source.source_id, error_code=error_code) or value
            self.store.update_attachment(attachment_id, status="INDEXING", source_id=source.source_id)
            activated = KnowledgeManagementService(manager).activate(source.source_id)
            job = activated.get("job") or {}
            return self.store.update_attachment(
                attachment_id,
                status="INDEXING" if job.get("job_id") else "ACTIVE",
                source_id=source.source_id,
                index_job_id=job.get("job_id"),
                error_code=job.get("error_code"),
            ) or value
        except Exception:
            if self.store.get_attachment(attachment_id) is None:
                attachment_path.unlink(missing_ok=True)
            staging_path.unlink(missing_ok=True)
            raise

    def list(self, conversation_id: str, user_id: str) -> list[ConversationAttachment]:
        self.conversations.require(conversation_id, user_id)
        return [self._refresh(item) for item in self.store.list_attachments(conversation_id)]

    def delete(self, conversation_id: str, attachment_id: str, user_id: str) -> dict[str, Any]:
        self.conversations.require(conversation_id, user_id)
        attachment = self.store.get_attachment(attachment_id)
        if attachment is None or attachment.conversation_id != conversation_id:
            raise ConversationError("ATTACHMENT_NOT_FOUND", 404)
        manager = self._manager(conversation_id)
        job: dict[str, Any] | None = None
        terminal: dict[str, Any] | None = None
        if attachment.source_id:
            try:
                result = KnowledgeManagementService(manager).delete(attachment.source_id)
                job = result.get("job")
            except KnowledgeManagementError as exc:
                if exc.code not in {"RAG_SOURCE_NOT_FOUND", "RAG_SOURCE_DELETE_NOT_ALLOWED"}:
                    raise ConversationError("ATTACHMENT_DELETE_FAILED", 400) from exc
            job = self._schedule_cleanup_rebuild(manager, job)
        if job and job.get("job_id"):
            self.store.update_attachment(attachment_id, status="DELETING", index_job_id=job["job_id"])
            terminal = self._wait_for_job(manager, job["job_id"])
            if terminal.get("status") != "success":
                self.store.update_attachment(attachment_id, status="DELETE_FAILED", error_code=terminal.get("error_code") or "ATTACHMENT_INDEX_DELETE_FAILED")
                raise ConversationError("ATTACHMENT_DELETE_FAILED", 409)
        elif attachment.source_id:
            self.store.update_attachment(attachment_id, status="DELETE_FAILED", error_code=(job or {}).get("error_code") or "ATTACHMENT_INDEX_DELETE_FAILED")
            raise ConversationError("ATTACHMENT_DELETE_FAILED", 409)
        root = self._conversation_root(conversation_id)
        self._safe_join(root, attachment.relative_path).unlink(missing_ok=True)
        # Validation failures can leave a staging copy; activation leaves an
        # active copy. Remove only this UUID-named attachment inside its root.
        for area in ("staging", "active", "rejected"):
            self._safe_join(root, "rag", "knowledge", area, attachment.stored_name).unlink(missing_ok=True)
        self.store.update_attachment(attachment_id, status="DELETED", error_code=None)
        self.store.delete_attachment(attachment_id)
        return {"status": "deleted", "attachment_id": attachment_id, "job": terminal}

    def state_snapshot(self, conversation_id: str, user_id: str) -> dict[str, Any]:
        """Return the authoritative, lightweight attachment state for one conversation."""
        self.conversations.require(conversation_id, user_id)
        attachments = [self._refresh(item) for item in self.store.list_attachments(conversation_id)]
        attachments.sort(key=lambda item: (item.created_at, item.attachment_id))
        active = [item for item in attachments if item.status == "ACTIVE"]
        indexing_states = {"UPLOADED", "PARSING", "INDEXING"}
        failed_states = {"FAILED"}
        return {
            "conversation_id": conversation_id,
            "attachment_count": len(attachments),
            "active_attachment_count": len(active),
            "indexing_attachment_count": sum(item.status in indexing_states for item in attachments),
            "failed_attachment_count": sum(item.status in failed_states for item in attachments),
            "ocr_required_attachment_count": sum(item.status == "OCR_REQUIRED" for item in attachments),
            "last_attachment_id": attachments[-1].attachment_id if attachments else None,
            "last_active_attachment": active[-1].model_dump() if active else None,
            "active_attachment_ids": [item.attachment_id for item in active],
            "attachments": [
                {
                    "attachment_id": item.attachment_id,
                    "filename": item.filename,
                    "status": item.status,
                    "error_code": item.error_code,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in attachments
            ],
        }

    def get_active(self, conversation_id: str, user_id: str, attachment_ids: list[str] | None = None) -> list[ConversationAttachment]:
        self.conversations.require(conversation_id, user_id)
        requested = {str(item) for item in attachment_ids or []}
        values = [self._refresh(item) for item in self.store.list_attachments(conversation_id)]
        return [
            item for item in values
            if item.status == "ACTIVE" and (not requested or item.attachment_id in requested)
        ]

    def load_chunks(self, conversation_id: str, user_id: str, attachment_ids: list[str]) -> list[tuple[ConversationAttachment, list[Any]]]:
        """Load parsed chunks in source order without reparsing or re-embedding."""
        attachments = self.get_active(conversation_id, user_id, attachment_ids)
        requested = {str(item) for item in attachment_ids}
        if requested != {item.attachment_id for item in attachments}:
            raise ConversationError("ATTACHMENT_NOT_ACTIVE", 409)
        manager = self._manager(conversation_id)
        chunks = manager.store.list_chunks()
        result: list[tuple[ConversationAttachment, list[Any]]] = []
        for attachment in attachments:
            source_chunks = [chunk for chunk in chunks if chunk.source_id == attachment.source_id]
            source_chunks.sort(key=lambda chunk: (chunk.ordinal, chunk.chunk_id))
            result.append((attachment, source_chunks))
        return result

    def retrieve(self, conversation_id: str, user_id: str, query: str, *, candidate_limit: int = 24, attachment_ids: list[str] | None = None) -> list[RAGHit]:
        self.conversations.require(conversation_id, user_id)
        attachments = [self._refresh(item) for item in self.store.list_attachments(conversation_id)]
        requested = {str(item) for item in attachment_ids or []}
        active_by_source = {
            item.source_id: item for item in attachments
            if item.status == "ACTIVE" and item.source_id and (not requested or item.attachment_id in requested)
        }
        if not active_by_source:
            return []
        manager = self._manager(conversation_id)
        result: list[RAGHit] = []
        per_source_limit = max(1, int(candidate_limit))
        # Filtering is enforced inside both lexical and dense retrieval.  We do
        # not search the whole conversation and discard another attachment later.
        for source_id, attachment in active_by_source.items():
            hits = manager.search(
                query,
                top_k=per_source_limit,
                candidate_limit=per_source_limit,
                metadata_filter={"source_id": source_id},
            )
            result.extend(self._scope_hit(hit, attachment, conversation_id) for hit in hits)
        result.sort(key=lambda hit: (-float(hit.score), hit.chunk.chunk_id))
        return [hit.model_copy(update={"rank": index + 1}) for index, hit in enumerate(result[:per_source_limit])]

    @staticmethod
    def _scope_hit(hit: RAGHit, attachment: ConversationAttachment, conversation_id: str) -> RAGHit:
        scoped_doc_id = f"{conversation_id}:{hit.chunk.doc_id}"
        scoped_chunk_id = f"{conversation_id}:{hit.chunk.chunk_id}"
        scope_metadata = {
            "scope": "conversation", "source_kind": "conversation_attachment",
            "conversation_id": conversation_id, "attachment_id": attachment.attachment_id,
            "original_filename": attachment.filename,
            "original_document_id": hit.chunk.doc_id,
            "original_chunk_id": hit.chunk.chunk_id,
        }
        chunk = hit.chunk.model_copy(deep=True, update={
            "doc_id": scoped_doc_id, "document_id": scoped_doc_id, "chunk_id": scoped_chunk_id,
            "source_path": attachment.filename, "relative_location": attachment.filename,
            "title": attachment.filename, "metadata": {**hit.chunk.metadata, **scope_metadata},
        })
        return hit.model_copy(deep=True, update={"chunk": chunk, "metadata": {**hit.metadata, **scope_metadata}})

    def delete_conversation_data(self, conversation_id: str, user_id: str) -> None:
        self.conversations.require(conversation_id, user_id)
        for attachment in list(self.store.list_attachments(conversation_id)):
            try:
                self.delete(conversation_id, attachment.attachment_id, user_id)
            except ConversationError:
                # The entire isolated index directory is removed below, which
                # is the authoritative cleanup boundary for conversation data.
                pass
        self.close_conversation(conversation_id)
        root = self._conversation_root(conversation_id)
        if root.exists():
            shutil.rmtree(root)

    def close_conversation(self, conversation_id: str) -> None:
        with self._lock:
            manager = self._managers.pop(conversation_id, None)
        if manager is not None:
            manager.close()

    def close(self) -> None:
        with self._lock:
            values = list(self._managers.values())
            self._managers.clear()
        for manager in values:
            manager.close()

    def _manager(self, conversation_id: str) -> RAGIndexManager:
        with self._lock:
            current = self._managers.get(conversation_id)
            if current is not None:
                return current
            root = self._conversation_root(conversation_id) / "rag"
            knowledge = root / "knowledge"
            for area in ("active", "staging", "rejected"):
                (knowledge / area).mkdir(parents=True, exist_ok=True)
            config = self.base_config.model_copy(update={
                "knowledge_roots": [str(knowledge)],
                "database_path": str(root / "rag.sqlite3"),
                "index_path": str(root / "index"),
                "backup_path": str(root / "backups"),
                "allowed_file_types": sorted(_ALLOWED_TYPES),
            })
            provider = self.embedding_provider
            if provider is None:
                provider = get_default_index_manager().embedding_provider
            current = RAGIndexManager(config, embedding_provider=provider)
            self._managers[conversation_id] = current
            return current

    def _refresh(self, attachment: ConversationAttachment) -> ConversationAttachment:
        if attachment.status not in {"INDEXING", "DELETING"} or not attachment.index_job_id:
            return attachment
        manager = self._manager(attachment.conversation_id)
        job = manager.store.get_job(attachment.index_job_id)
        if not job or job.get("status") in {"queued", "running"}:
            return attachment
        if attachment.status == "INDEXING" and job.get("status") == "success":
            validation = manager.validate()
            status = "ACTIVE" if validation.get("status") == "success" else "FAILED"
            return self.store.update_attachment(attachment.attachment_id, status=status, error_code=validation.get("error_code")) or attachment
        if job.get("status") != "success":
            return self.store.update_attachment(attachment.attachment_id, status="FAILED", error_code=job.get("error_code") or "ATTACHMENT_INDEX_FAILED") or attachment
        return attachment

    def _wait_for_job(self, manager: RAGIndexManager, job_id: str, timeout_seconds: float = 30.0) -> dict[str, Any]:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            job = manager.store.get_job(job_id) or {}
            if job.get("status") in {"success", "failed", "canceled"}:
                return job
            sleep(0.05)
        return {"status": "failed", "error_code": "ATTACHMENT_INDEX_JOB_TIMEOUT"}

    @staticmethod
    def _schedule_cleanup_rebuild(manager: RAGIndexManager, initial_job: dict[str, Any] | None, timeout_seconds: float = 30.0) -> dict[str, Any]:
        if initial_job and initial_job.get("job_id"):
            return initial_job
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            try:
                return manager.submit_rebuild("incremental")
            except RebuildInProgressError:
                sleep(0.05)
        return {"status": "failed", "error_code": "ATTACHMENT_INDEX_JOB_TIMEOUT"}

    def _conversation_root(self, conversation_id: str) -> Path:
        if not _CONVERSATION_ID.fullmatch(str(conversation_id or "")):
            raise ConversationError("CONVERSATION_ID_INVALID", 422)
        return self._safe_join(self.runtime_root, conversation_id)

    @staticmethod
    def _safe_join(root: Path, *parts: str | Path) -> Path:
        root = root.resolve()
        candidate = root.joinpath(*(Path(item) for item in parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ConversationError("ATTACHMENT_PATH_INVALID", 422) from exc
        return candidate


def attachment_runtime_root() -> Path:
    configured = os.getenv("CONVERSATION_RUNTIME_ROOT", "").strip()
    return Path(configured).resolve() if configured else (PROJECT_ROOT / "runtime" / "conversations")


_default_lock = RLock()
_default_service: AttachmentService | None = None


def get_default_attachment_service() -> AttachmentService:
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = AttachmentService(get_default_conversation_service(), attachment_runtime_root())
        return _default_service


def close_default_attachment_service() -> None:
    global _default_service
    with _default_lock:
        if _default_service is not None:
            _default_service.close()
        _default_service = None


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
