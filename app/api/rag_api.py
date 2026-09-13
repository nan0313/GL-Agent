"""RAG management and safe search APIs."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.rag.index_manager import RebuildInProgressError
from app.rag.index_store import get_default_index_manager
from app.rag.backup import RAGBackupError, RAGBackupService
from app.rag.knowledge_management import KnowledgeManagementError, KnowledgeManagementService
from app.rag.models import MetadataFilter
from app.rag.prompt_builder import PromptBuilder
from app.rag.query import normalize_query
from app.rag.reranker import LightweightReranker
from app.rag.upload import KnowledgeUploadError, KnowledgeUploadService
from app.release_profile import get_runtime_profile, local_admin_mode_enabled
from app.uploads import UploadBoundaryError, read_single_file_multipart


router = APIRouter(tags=["rag"])


class RAGIndexRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["full", "incremental"] = "incremental"
    role: str = "developer"


class RAGSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=0, max_length=4000)
    top_k: int = Field(default=3, ge=0, le=20)
    candidate_limit: int = Field(default=24, ge=1, le=100)
    filters: MetadataFilter = Field(default_factory=MetadataFilter)


class RAGSourceValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = "developer"
    source_id: str | None = Field(default=None, max_length=100)
    area: Literal["active", "staging", "rejected"] = "staging"
    description: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = Field(default=None, max_length=20)


class RAGSourceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = "developer"


class RAGBackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = "developer"
    confirm: bool = False


def _require_management_role(role: str) -> None:
    if str(role or "").strip().lower() not in {"developer", "admin", "operator"}:
        raise HTTPException(status_code=403, detail="RAG_MANAGEMENT_ROLE_REQUIRED")


def _require_management_access(request: Request, role: str) -> None:
    _require_management_role(role)
    if not local_admin_mode_enabled():
        raise HTTPException(status_code=403, detail="RAG_LOCAL_ADMIN_MODE_REQUIRED")
    host = str(getattr(request.client, "host", "") or "").lower()
    allowed = {"127.0.0.1", "::1", "localhost"}
    if get_runtime_profile() == "development":
        allowed.add("testclient")
    if host not in allowed:
        raise HTTPException(status_code=403, detail="RAG_LOCAL_REQUEST_REQUIRED")


def _management_service() -> KnowledgeManagementService:
    return KnowledgeManagementService(get_default_index_manager())


def _management_error(exc: KnowledgeManagementError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.code)


def _backup_service() -> RAGBackupService:
    return RAGBackupService(get_default_index_manager())


def _backup_error(exc: RAGBackupError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.code)


@router.post("/api/rag/sources/upload")
async def rag_source_upload(http_request: Request) -> dict[str, object]:
    manager = get_default_index_manager()
    try:
        upload = await read_single_file_multipart(http_request, max_file_bytes=manager.config.max_file_size)
        role = upload.fields.get("role", "developer")
        _require_management_access(http_request, role)
        auto_activate = upload.fields.get("auto_activate", "false").strip().lower() in {"1", "true", "yes", "on"}
        return KnowledgeUploadService(manager).stage(upload.filename, upload.content_type, upload.content, auto_activate=auto_activate)
    except UploadBoundaryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code)
    except KnowledgeUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code)


@router.get("/health/rag")
def rag_health() -> dict[str, object]:
    return get_default_index_manager().health()


@router.get("/rag/index/status")
def rag_index_status() -> dict[str, object]:
    manager = get_default_index_manager()
    return {"status": "ok", **manager.health()}


@router.post("/rag/index/rebuild")
def rag_index_rebuild(http_request: Request, payload: RAGIndexRebuildRequest | None = None) -> dict[str, object]:
    request = payload or RAGIndexRebuildRequest()
    _require_management_access(http_request, request.role)
    manager = get_default_index_manager()
    try:
        job = manager.submit_rebuild(request.mode)
    except RebuildInProgressError:
        raise HTTPException(status_code=409, detail="RAG_REBUILD_IN_PROGRESS")
    return {"status": "accepted", "job_id": job["job_id"], "job": job}


@router.post("/rag/index/validate")
def rag_index_validate(http_request: Request, payload: RAGIndexRebuildRequest | None = None) -> dict[str, object]:
    request = payload or RAGIndexRebuildRequest()
    _require_management_access(http_request, request.role)
    return get_default_index_manager().validate()


@router.post("/rag/search")
def rag_search(payload: RAGSearchRequest) -> dict[str, object]:
    manager = get_default_index_manager()
    manager.ensure_ready()
    normalized_query = normalize_query(payload.query)
    if not normalized_query:
        return {"query": "", "normalized_query": "", "status": "empty", "retrieval_method": "none", "candidate_count": 0, "returned_count": 0, "evidences": [], "index_version": manager.health().get("active_index_version")}
    hits = manager.search(normalized_query, top_k=payload.candidate_limit, candidate_limit=payload.candidate_limit, metadata_filter=payload.filters.model_dump(exclude_none=True))
    reranked = LightweightReranker().rerank(normalized_query, hits, top_k=payload.candidate_limit)
    evidence = PromptBuilder(max_evidences=payload.top_k).build_evidence(reranked[: payload.top_k])
    return {"query": payload.query[:4000], "normalized_query": normalized_query, "status": "success" if evidence else "empty", "retrieval_method": evidence[0].retrieval_method if evidence else "none", "candidate_count": len(hits), "returned_count": len(evidence), "top_score": evidence[0].score if evidence else None, "index_version": manager.health().get("active_index_version"), "evidences": [item.model_dump() for item in evidence]}


@router.get("/rag/sources")
def rag_sources() -> dict[str, object]:
    service = _management_service()
    return {"sources": service.list_sources()}


@router.get("/rag/sources/{source_id}")
def rag_source(source_id: str) -> dict[str, object]:
    try:
        return {"source": _management_service().get_source(source_id)}
    except KnowledgeManagementError as exc:
        raise _management_error(exc)


@router.post("/rag/sources/validate")
def rag_sources_validate(http_request: Request, payload: RAGSourceValidateRequest | None = None) -> dict[str, object]:
    request = payload or RAGSourceValidateRequest()
    _require_management_access(http_request, request.role)
    try:
        return _management_service().validate(source_id=request.source_id, area=request.area, description=request.description, tags=request.tags)
    except KnowledgeManagementError as exc:
        raise _management_error(exc)


@router.post("/rag/sources/{source_id}/activate")
def rag_source_activate(source_id: str, http_request: Request, payload: RAGSourceActionRequest | None = None) -> dict[str, object]:
    request = payload or RAGSourceActionRequest()
    _require_management_access(http_request, request.role)
    try:
        return _management_service().activate(source_id)
    except KnowledgeManagementError as exc:
        raise _management_error(exc)


@router.post("/rag/sources/{source_id}/disable")
def rag_source_disable(source_id: str, http_request: Request, payload: RAGSourceActionRequest | None = None) -> dict[str, object]:
    request = payload or RAGSourceActionRequest()
    _require_management_access(http_request, request.role)
    try:
        return _management_service().disable(source_id)
    except KnowledgeManagementError as exc:
        raise _management_error(exc)


@router.delete("/rag/sources/{source_id}")
def rag_source_delete(source_id: str, http_request: Request, payload: RAGSourceActionRequest | None = None) -> dict[str, object]:
    request = payload or RAGSourceActionRequest()
    _require_management_access(http_request, request.role)
    try:
        return _management_service().delete(source_id)
    except KnowledgeManagementError as exc:
        raise _management_error(exc)


@router.post("/rag/sources/{source_id}/reindex")
def rag_source_reindex(source_id: str, http_request: Request, payload: RAGSourceActionRequest | None = None) -> dict[str, object]:
    request = payload or RAGSourceActionRequest()
    _require_management_access(http_request, request.role)
    try:
        return _management_service().reindex(source_id)
    except KnowledgeManagementError as exc:
        raise _management_error(exc)


@router.get("/rag/jobs")
def rag_jobs(limit: int = 100) -> dict[str, object]:
    return {"jobs": _management_service().list_jobs(limit)}


@router.get("/rag/jobs/{job_id}")
def rag_job(job_id: str) -> dict[str, object]:
    try:
        return {"job": _management_service().get_job(job_id)}
    except KnowledgeManagementError as exc:
        raise _management_error(exc)


@router.post("/rag/jobs/{job_id}/cancel")
def rag_job_cancel(job_id: str, http_request: Request, payload: RAGSourceActionRequest | None = None) -> dict[str, object]:
    request = payload or RAGSourceActionRequest()
    _require_management_access(http_request, request.role)
    try:
        return _management_service().cancel_job(job_id)
    except KnowledgeManagementError as exc:
        raise _management_error(exc)


@router.get("/rag/backups")
def rag_backups() -> dict[str, object]:
    return {"backups": _backup_service().list()}


@router.post("/rag/backups")
def rag_backup_create(http_request: Request, payload: RAGBackupRequest | None = None) -> dict[str, object]:
    request = payload or RAGBackupRequest()
    _require_management_access(http_request, request.role)
    try:
        return _backup_service().create()
    except RAGBackupError as exc:
        raise _backup_error(exc)


@router.post("/rag/backups/{backup_id}/validate")
def rag_backup_validate(backup_id: str, http_request: Request, payload: RAGBackupRequest | None = None) -> dict[str, object]:
    request = payload or RAGBackupRequest()
    _require_management_access(http_request, request.role)
    try:
        return _backup_service().validate(backup_id)
    except RAGBackupError as exc:
        raise _backup_error(exc)


@router.post("/rag/backups/{backup_id}/restore")
def rag_backup_restore(backup_id: str, http_request: Request, payload: RAGBackupRequest | None = None) -> dict[str, object]:
    request = payload or RAGBackupRequest()
    _require_management_access(http_request, request.role)
    if not request.confirm:
        raise HTTPException(status_code=400, detail="RAG_BACKUP_CONFIRM_REQUIRED")
    try:
        return _backup_service().restore(backup_id)
    except RAGBackupError as exc:
        raise _backup_error(exc)


@router.delete("/rag/backups/{backup_id}")
def rag_backup_delete(backup_id: str, http_request: Request, payload: RAGBackupRequest | None = None) -> dict[str, object]:
    request = payload or RAGBackupRequest()
    _require_management_access(http_request, request.role)
    if not request.confirm:
        raise HTTPException(status_code=400, detail="RAG_BACKUP_CONFIRM_REQUIRED")
    try:
        return _backup_service().delete(backup_id)
    except RAGBackupError as exc:
        raise _backup_error(exc)
