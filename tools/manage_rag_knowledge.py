"""Small, safe operator CLI for the configured local RAG knowledge roots.

The command deliberately delegates discovery, parsing, validation and rebuild
to the existing RAG registries/managers.  It never accepts a filesystem root
from the command line and only prints redacted source summaries.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.config import get_rag_config
from app.rag.index_manager import RAGIndexManager, RebuildInProgressError
from app.rag.knowledge_management import KnowledgeManagementError, KnowledgeManagementService
from app.rag.source_registry import KnowledgeSourceRegistry, SourceRegistryError


_AREAS = ("active", "staging", "rejected")


def _source_summary(source: Any) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "file_name": Path(source.relative_location).name,
        "source_type": source.source_type,
        "file_size": source.file_size,
        "content_hash": (source.content_hash or "")[:12] or None,
        "status": source.ingestion_status,
        "source_status": source.source_status,
        "parser_status": source.parser_status,
        "index_status": source.index_status,
        "document_count": source.document_count,
        "section_count": source.section_count,
        "chunk_count": source.chunk_count,
        "error_code": source.error_code,
        "index_version": source.index_version,
    }


def _safe_health(manager: RAGIndexManager) -> dict[str, Any]:
    health = manager.health()
    return {
        "configured": health.get("configured"),
        "available": health.get("available"),
        "lexical_available": health.get("lexical_available"),
        "dense_available": health.get("dense_available"),
        "embedding_provider_type": health.get("embedding_provider_type"),
        "embedding_model": health.get("embedding_model"),
        "embedding_dimension": health.get("embedding_dimension"),
        "vector_backend": health.get("vector_backend"),
        "configured_vector_backend": health.get("configured_vector_backend"),
        "active_vector_backend": health.get("active_vector_backend"),
        "vector_backend_available": health.get("vector_backend_available"),
        "vector_item_count": health.get("vector_item_count"),
        "vector_capacity": health.get("vector_capacity"),
        "vector_deleted_count": health.get("vector_deleted_count"),
        "vector_index_version": health.get("vector_index_version"),
        "vector_fallback_used": health.get("vector_fallback_used"),
        "vector_error_code": health.get("vector_error_code"),
        "active_index_version": health.get("active_index_version"),
        "source_count": health.get("source_count"),
        "source_status_counts": health.get("source_status_counts", {}),
        "document_count": health.get("document_count"),
        "chunk_count": health.get("chunk_count"),
        "last_build_status": health.get("last_build_status"),
        "last_build_at": health.get("last_build_at"),
        "error_code": health.get("error_code"),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage configured local RAG knowledge safely")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "status", "list-errors"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--area", choices=_AREAS, default="active")
    sources = subparsers.add_parser("sources")
    sources.add_argument("--area", choices=_AREAS, default=None)
    show = subparsers.add_parser("show")
    show.add_argument("source_id")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--area", choices=_AREAS, default="staging")
    validate.add_argument("--source-id", default=None)
    for name in ("activate", "disable", "delete", "reindex"):
        sub = subparsers.add_parser(name)
        sub.add_argument("source_id")
    subparsers.add_parser("jobs")
    cancel_job = subparsers.add_parser("cancel-job")
    cancel_job.add_argument("job_id")
    rebuild = subparsers.add_parser("rebuild")
    rebuild.add_argument("--mode", choices=("full", "incremental"), default="incremental")
    migrate = subparsers.add_parser("migrate-vector")
    migrate.add_argument("--to", choices=("hnsw", "python_cosine_fallback"), required=True)
    subparsers.add_parser("validate-vector")
    subparsers.add_parser("vector-status")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = get_rag_config()
    registry = KnowledgeSourceRegistry(config)

    if args.command == "scan":
        return {"command": "scan", "area": args.area, "sources": [_source_summary(item) for item in registry.scan(area=args.area)]}

    if args.command == "migrate-vector":
        config = config.model_copy(update={"vector_backend": args.to})
    manager = RAGIndexManager(config, source_registry=registry)
    service = KnowledgeManagementService(manager)
    try:
        if args.command == "sources":
            return {"command": "sources", "sources": service.list_sources(getattr(args, "area", None))}
        if args.command == "show":
            return {"command": "show", "source": service.get_source(args.source_id)}
        if args.command == "validate":
            if getattr(args, "source_id", None) or getattr(args, "area", "staging") != "active":
                return {"command": "validate", **service.validate(source_id=getattr(args, "source_id", None), area=getattr(args, "area", "staging"))}
            return {"command": "validate", "area": "active", **manager.validate()}
        if args.command == "status":
            return {"command": "status", **_safe_health(manager)}
        if args.command == "vector-status":
            validation = manager.validate()
            return {
                "command": "vector-status",
                **_safe_health(manager),
                "vector_valid": validation.get("vector_valid"),
                "vector_validation_status": validation.get("status"),
                "vector_validation_error_code": validation.get("error_code"),
                "vector_details": validation.get("vector_details"),
            }
        if args.command == "validate-vector":
            return {"command": "validate-vector", **manager.validate()}
        if args.command == "migrate-vector":
            return {"command": "migrate-vector", **manager.migrate_vector_backend(args.to)}
        if args.command == "list-errors":
            if args.area == "staging":
                return {"command": "list-errors", "area": args.area, "errors": [item for item in service.validate(area="staging")["sources"] if item["status"] == "rejected" or item.get("error_code")]}
            return {"command": "list-errors", "area": args.area, "errors": [item for item in service.list_sources(args.area) if item.get("parser_status") in {"failed", "error"} or item.get("error_code")]}
        if args.command in {"activate", "disable", "delete", "reindex"}:
            operation = getattr(service, args.command)(args.source_id)
            job = operation.get("job")
            if isinstance(job, dict) and job.get("job_id"):
                operation["job"] = _wait_for_job(manager, str(job["job_id"]))
            return {"command": args.command, **operation}
        if args.command == "jobs":
            return {"command": "jobs", "jobs": service.list_jobs()}
        if args.command == "cancel-job":
            return {"command": "cancel-job", **service.cancel_job(args.job_id)}
        if args.command == "rebuild":
            try:
                return {"command": "rebuild", **manager.rebuild(args.mode)}
            except RebuildInProgressError:
                return {"command": "rebuild", "status": "failed", "error_code": "RAG_REBUILD_IN_PROGRESS"}
    finally:
        manager.close()
    raise ValueError("RAG_INVALID_COMMAND")


def _wait_for_job(manager: RAGIndexManager, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.store.get_job(job_id)
        if job and job.get("status") in {"success", "failed", "canceled"}:
            allowed = {"job_id", "mode", "status", "added", "modified", "deleted", "unchanged", "parsed", "failed", "chunk_count", "embedding_count", "cache_hits", "duration_ms", "version", "error_code", "created_at", "updated_at"}
            return {key: value for key, value in job.items() if key in allowed}
        time.sleep(0.02)
    return {"job_id": job_id, "status": "running", "error_code": "RAG_JOB_WAIT_TIMEOUT"}


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        print(json.dumps(run(args), ensure_ascii=False, sort_keys=True))
        return 0
    except (SourceRegistryError, KnowledgeManagementError, ValueError) as exc:
        code = getattr(exc, "code", None) or str(exc) or "RAG_OPERATION_FAILED"
        print(json.dumps({"status": "failed", "error_code": code}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
