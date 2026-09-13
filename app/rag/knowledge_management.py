"""Controlled source lifecycle and safe knowledge-quality summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from app.rag.chunking import StructuredChunker
from app.rag.index_manager import RAGIndexManager, RebuildInProgressError
from app.rag.models import KnowledgeSource, SourceLifecycle, utc_now
from app.rag.parsers import DocumentParserRegistry
from app.rag.source_registry import KnowledgeSourceRegistry, SourceRegistryError


class KnowledgeManagementError(ValueError):
    """Safe operator-facing error with a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_TRANSITIONS: dict[str, set[str]] = {
    "staging": {"validating", "deleted"},
    "validating": {"validated", "rejected", "error"},
    "validated": {"active", "rejected", "deleted", "validating"},
    "active": {"disabled", "deleted", "validating"},
    "disabled": {"active", "deleted", "validating"},
    "rejected": {"validating", "deleted"},
    "error": {"staging", "deleted", "validating"},
    "deleted": {"staging", "validating"},
}


class KnowledgeManagementService:
    def __init__(self, manager: RAGIndexManager) -> None:
        self.manager = manager
        self.config = manager.config
        self.store = manager.store
        self.registry: KnowledgeSourceRegistry = manager.source_registry
        self.parsers = DocumentParserRegistry()
        self.chunker: StructuredChunker = manager.chunker

    def list_sources(self, area: str | None = None) -> list[dict[str, Any]]:
        if area:
            area = _validate_area(area)
            areas = (area,)
        else:
            areas = ("active", "staging", "rejected")
        stored = {item.source_id: item for item in self.store.list_sources()}
        discovered: dict[str, KnowledgeSource] = {}
        for current_area in areas:
            discovered.update({item.source_id: item for item in self.registry.scan(area=current_area)})
        if area:
            sources = [item for item in stored.values() if item.source_id in discovered or _source_area(item.relative_location) == area]
        else:
            sources = list(stored.values())
        for source_id, source in discovered.items():
            if source_id not in stored:
                sources.append(source)
        return [_source_summary(source) for source in sorted(sources, key=lambda item: (item.relative_location, item.source_id))]

    def get_source(self, source_id: str) -> dict[str, Any]:
        source = self._find_source(source_id)
        return _source_summary(source)

    def validate(self, *, source_id: str | None = None, area: str = "staging", description: str | None = None, tags: Iterable[str] | None = None) -> dict[str, Any]:
        area = _validate_area(area)
        candidates = self.registry.scan(area=area)
        if source_id:
            candidates = [item for item in candidates if item.source_id == source_id]
        if not candidates:
            raise KnowledgeManagementError("RAG_SOURCE_NOT_FOUND")
        results = [self._validate_one(source, description=description, tags=tags) for source in candidates]
        status = "success" if all(item["status"] in {"validated", "active"} for item in results) else "failed"
        return {"status": status, "area": area, "sources": results}

    def activate(self, source_id: str) -> dict[str, Any]:
        source = self._find_source(source_id)
        if source.source_status == "active":
            return {"status": "success", "source": _source_summary(source), "job": None}
        if source.source_status not in {"validated", "disabled"}:
            raise KnowledgeManagementError("RAG_SOURCE_NOT_VALIDATED")
        path = self._source_path(source)
        root = self._root_for(path)
        logical = _logical_relative(source.relative_location)
        target = _safe_join(root, "active", logical)
        if path != target:
            if target.exists() or target.is_symlink():
                raise KnowledgeManagementError("RAG_SOURCE_DESTINATION_EXISTS")
            target.parent.mkdir(parents=True, exist_ok=True)
            if any(parent.is_symlink() for parent in _parents_between(target.parent, root)):
                raise KnowledgeManagementError("RAG_SYMLINK_ESCAPE")
            path.replace(target)
        updated = source.model_copy(update={"relative_location": target.relative_to(root).as_posix(), "source_status": "active", "index_status": "pending", "updated_at": utc_now()})
        with self.store.transaction():
            self.store.upsert_source(updated)
        return {"status": "success", "source": _source_summary(updated), "job": self._schedule_rebuild()}

    def disable(self, source_id: str) -> dict[str, Any]:
        source = self._find_source(source_id)
        if source.source_status == "disabled":
            return {"status": "success", "source": _source_summary(source), "job": None}
        if source.source_status != "active":
            raise KnowledgeManagementError("RAG_SOURCE_NOT_ACTIVE")
        with self.store.transaction():
            self.store.remove_source_content(source.source_id, source_status="disabled", ingestion_status="registered")
        updated = self.store.get_source(source.source_id)
        return {"status": "success", "source": _source_summary(updated or source), "job": self._schedule_rebuild()}

    def delete(self, source_id: str) -> dict[str, Any]:
        source = self._find_source(source_id)
        if source.source_status == "deleted":
            return {"status": "success", "source": _source_summary(source), "job": None}
        # Rejected/error sources are safe to remove from staging/rejected as
        # well; the browser management view exposes this as an explicit
        # destructive action.  The source file is never copied elsewhere.
        if source.source_status not in {"active", "disabled", "validated", "staging", "rejected", "error"}:
            raise KnowledgeManagementError("RAG_SOURCE_DELETE_NOT_ALLOWED")
        path = self._source_path(source, must_exist=False)
        if path.exists() or path.is_symlink():
            if path.is_dir() or path.is_symlink():
                raise KnowledgeManagementError("RAG_SOURCE_NOT_REGULAR_FILE")
            path.unlink()
        with self.store.transaction():
            self.store.remove_source_content(source.source_id, source_status="deleted", ingestion_status="deleted")
        updated = self.store.get_source(source.source_id) or source
        return {"status": "success", "source": _source_summary(updated), "job": self._schedule_rebuild()}

    def reindex(self, source_id: str) -> dict[str, Any]:
        source = self._find_source(source_id)
        if source.source_status != "active":
            raise KnowledgeManagementError("RAG_SOURCE_NOT_ACTIVE")
        return {"status": "accepted", "source_id": source_id, "job": self._schedule_rebuild()}

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_jobs(limit)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise KnowledgeManagementError("RAG_JOB_NOT_FOUND")
        return _safe_job(job)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise KnowledgeManagementError("RAG_JOB_NOT_FOUND")
        if job.get("status") in {"success", "failed", "canceled"}:
            return {"status": "success", "job": _safe_job(job), "already_terminal": True}
        if not self.manager.cancel(job_id):
            raise KnowledgeManagementError("RAG_JOB_NOT_RUNNING")
        return {"status": "accepted", "job": _safe_job(self.store.get_job(job_id) or job)}

    def _validate_one(self, source: KnowledgeSource, *, description: str | None, tags: Iterable[str] | None) -> dict[str, Any]:
        current = self.store.get_source(source.source_id)
        if current:
            _require_transition(current.source_status, "validating")
        validating = source.model_copy(update={
            "source_status": "validating",
            "description": _safe_description(description if description is not None else (current.description if current else "")),
            "tags": _safe_tags(tags if tags is not None else (current.tags if current else [])),
            "parser_status": "running",
            "index_status": "not_indexed",
        })
        with self.store.transaction():
            self.store.upsert_source(validating)
        try:
            parsed = self.parsers.parse(source, self.registry.path_for(source))
        except SourceRegistryError as exc:
            parsed = None
            error_code = exc.code
        else:
            error_code = parsed.error_code if parsed else None
        now = utc_now()
        if parsed is None or parsed.status in {"failed", "empty"}:
            code = error_code or (parsed.error_code if parsed else None) or ("RAG_EMPTY_DOCUMENT" if parsed and parsed.status == "empty" else "RAG_PARSE_FAILED")
            status: SourceLifecycle = "rejected"
            final = validating.model_copy(update={"source_status": status, "parser_status": "failed" if parsed is None or parsed.status == "failed" else "empty", "ingestion_status": "failed" if parsed is None or parsed.status == "failed" else "empty", "error_code": code, "last_validated_at": now})
            with self.store.transaction():
                self.store.upsert_source(final)
            return {**_source_summary(final), "status": "rejected", "quality": {"source_id": final.source_id, "status": "rejected", "document_count": 0, "section_count": 0, "chunk_count": 0, "duplicate_ratio": 0.0, "short_chunk_ratio": 0.0, "parse_warning_count": 0, "ocr_required": code == "RAG_PDF_OCR_REQUIRED"}}

        chunks = self.chunker.chunk(parsed)
        duplicate_ratio = _duplicate_ratio([item.content_hash for item in chunks])
        short_ratio = sum(1 for item in chunks if item.char_count < self.config.chunk_min_chars) / max(1, len(chunks))
        warnings: list[str] = []
        if source.duplicate_of_source_id:
            warnings.append("RAG_DUPLICATE_SOURCE_WARNING")
        if duplicate_ratio > 0.2:
            warnings.append("RAG_DUPLICATE_CHUNK_WARNING")
        if short_ratio > 0.5:
            warnings.append("RAG_SHORT_CHUNK_WARNING")
        if bool(parsed.metadata.get("ocr_required")):
            warnings.append("RAG_PDF_OCR_REQUIRED")
        final_status: SourceLifecycle = "active" if (current and current.source_status == "active") or source.source_status == "active" else "validated"
        final = validating.model_copy(update={
            "source_status": final_status,
            "ingestion_status": "parsed",
            "parser_status": "success",
            "index_status": "not_indexed",
            "error_code": warnings[0] if warnings else None,
            "language": parsed.language,
            "parser_version": parsed.parser_version,
            "document_count": 1,
            "section_count": len(parsed.sections),
            "chunk_count": len(chunks),
            "last_validated_at": now,
        })
        with self.store.transaction():
            self.store.upsert_source(final)
        return {**_source_summary(final), "status": final_status, "quality": {"source_id": final.source_id, "status": final_status, "document_count": 1, "section_count": len(parsed.sections), "chunk_count": len(chunks), "duplicate_ratio": round(duplicate_ratio, 6), "short_chunk_ratio": round(short_ratio, 6), "parse_warning_count": len(warnings), "ocr_required": bool(parsed.metadata.get("ocr_required")), "warnings": warnings}}

    def _find_source(self, source_id: str) -> KnowledgeSource:
        value = str(source_id or "").strip()
        if not value or len(value) > 100 or any(char in value for char in "\\/.."):
            raise KnowledgeManagementError("RAG_INVALID_SOURCE_ID")
        source = self.store.get_source(value)
        if source:
            return source
        for area in ("active", "staging", "rejected"):
            for candidate in self.registry.scan(area=area):
                if candidate.source_id == value:
                    return candidate
        raise KnowledgeManagementError("RAG_SOURCE_NOT_FOUND")

    def _source_path(self, source: KnowledgeSource, *, must_exist: bool = True) -> Path:
        try:
            path = self.registry.path_for(source)
        except SourceRegistryError:
            if must_exist:
                raise KnowledgeManagementError("RAG_SOURCE_NOT_FOUND")
            root = self._root_for_location(source.relative_location)
            path = _safe_join(root, source.relative_location)
        return path

    def _root_for(self, path: Path) -> Path:
        for root in self.registry.roots:
            if _within(path.resolve(), root):
                return root
        raise KnowledgeManagementError("RAG_SOURCE_NOT_FOUND")

    def _root_for_location(self, relative: str) -> Path:
        for root in self.registry.roots:
            candidate = _safe_join(root, relative)
            if candidate.exists() or _logical_relative(relative):
                return root
        raise KnowledgeManagementError("RAG_SOURCE_NOT_FOUND")

    def _schedule_rebuild(self) -> dict[str, Any] | None:
        try:
            return self.manager.submit_rebuild("incremental")
        except RebuildInProgressError:
            return {"status": "rejected", "error_code": "RAG_REBUILD_IN_PROGRESS"}


def _validate_area(area: str) -> str:
    value = str(area or "").lower().strip()
    if value not in {"active", "staging", "rejected"}:
        raise KnowledgeManagementError("RAG_INVALID_KNOWLEDGE_AREA")
    return value


def _require_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in _TRANSITIONS.get(current, set()):
        raise KnowledgeManagementError("RAG_INVALID_SOURCE_TRANSITION")


def _safe_description(value: str) -> str:
    value = str(value or "").strip()
    if len(value) > 500 or any(token in value.lower() for token in ("select ", "<script", "../")):
        raise KnowledgeManagementError("RAG_INVALID_SOURCE_METADATA")
    return value


def _safe_tags(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if not value or len(value) > 64 or len(result) >= 20 or any(token in value.lower() for token in ("../", "select ", "<script", "\\")):
            raise KnowledgeManagementError("RAG_INVALID_SOURCE_METADATA")
        if value not in result:
            result.append(value)
    return result


def _duplicate_ratio(values: list[str]) -> float:
    return 1.0 - (len(set(values)) / max(1, len(values)))


def _source_area(relative: str) -> str:
    first = Path(relative).parts[0].lower() if Path(relative).parts else ""
    return first if first in {"active", "staging", "rejected"} else "active"


def _logical_relative(relative: str) -> str:
    parts = Path(relative).parts
    if parts and parts[0].lower() in {"active", "staging", "rejected"}:
        parts = parts[1:]
    if not parts or any(item in {"", ".", ".."} for item in parts):
        raise KnowledgeManagementError("RAG_INVALID_SOURCE_LOCATION")
    return "/".join(parts)


def _safe_join(root: Path, *parts: str) -> Path:
    candidate = (root / Path(*parts)).resolve()
    if not _within(candidate, root) or candidate.is_symlink():
        raise KnowledgeManagementError("RAG_PATH_TRAVERSAL")
    return candidate


def _parents_between(path: Path, root: Path) -> list[Path]:
    result: list[Path] = []
    current = path
    while current != root and root in current.parents:
        result.append(current)
        current = current.parent
    return result


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _source_summary(source: KnowledgeSource | None) -> dict[str, Any]:
    if source is None:
        return {}
    if source.source_status == "validated":
        lifecycle_stage = "ready"
    elif source.source_status == "active" and source.index_status != "active":
        lifecycle_stage = "indexing"
    elif source.source_status == "active":
        lifecycle_stage = "active"
    else:
        lifecycle_stage = source.source_status
    return {
        "source_id": source.source_id,
        "display_name": source.display_name,
        "file_name": Path(source.relative_location).name,
        "relative_location": source.relative_location,
        "source_type": source.source_type,
        "description": source.description,
        "tags": list(source.tags),
        "language": source.language,
        "file_size": source.file_size,
        "content_hash": (source.content_hash or "")[:12] or None,
        "source_status": source.source_status,
        "lifecycle_stage": lifecycle_stage,
        "parser_status": source.parser_status,
        "index_status": source.index_status,
        "document_count": source.document_count,
        "section_count": source.section_count,
        "chunk_count": source.chunk_count,
        "last_validated_at": source.last_validated_at,
        "last_indexed_at": source.last_indexed_at,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
        "active_index_version": source.index_version if source.index_status == "active" else None,
        "error_code": source.error_code,
        "provenance": source.provenance,
    }


def _safe_job(job: dict[str, Any]) -> dict[str, Any]:
    allowed = {"job_id", "mode", "status", "added", "modified", "deleted", "unchanged", "parsed", "failed", "chunk_count", "embedding_count", "cache_hits", "duration_ms", "version", "error_code", "created_at", "updated_at"}
    return {key: value for key, value in job.items() if key in allowed}
