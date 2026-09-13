"""Consistency snapshots for the local RAG SQLite/index state.

Backups contain the RAG database snapshot and active vector artifacts, but do
not copy source files or model caches.  Restore therefore recovers a searchable
RAG snapshot only; missing original source files remain missing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from app.rag.index_manager import RAGIndexManager
from app.rag.storage import SQLiteRAGStore
from app.rag.vector_index import VectorIndexError, build_vector_backend


class RAGBackupError(ValueError):
    """Safe operator-facing backup error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_BACKUP_ID = re.compile(r"^ragbak_[0-9a-f]{16,32}$")


class RAGBackupService:
    def __init__(self, manager: RAGIndexManager) -> None:
        self.manager = manager
        self.config = manager.config
        self.root = Path(self.config.backup_path).resolve()

    def create(self) -> dict[str, Any]:
        return self._with_job("backup_create", self._create_snapshot)

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        result: list[dict[str, Any]] = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name):
            if not path.is_dir() or not _BACKUP_ID.fullmatch(path.name):
                continue
            try:
                manifest = _read_manifest(path)
                result.append(_backup_summary(manifest))
            except RAGBackupError:
                result.append({"backup_id": path.name, "status": "invalid", "error_code": "RAG_BACKUP_MANIFEST_INVALID"})
        return result

    def validate(self, backup_id: str) -> dict[str, Any]:
        path = self._path(backup_id)
        manifest = _read_manifest(path)
        details = _validate_snapshot(path, manifest, self.config)
        return {"status": "success", **_backup_summary(manifest), "validation": details}

    def restore(self, backup_id: str) -> dict[str, Any]:
        path = self._path(backup_id)
        manifest = _read_manifest(path)
        _validate_snapshot(path, manifest, self.config)
        return self._with_job("backup_restore", lambda: self._restore_snapshot(path, manifest))

    def delete(self, backup_id: str) -> dict[str, Any]:
        path = self._path(backup_id)
        shutil.rmtree(path)
        return {"status": "success", "backup_id": backup_id, "deleted": True}

    def _create_snapshot(self) -> dict[str, Any]:
        if self.manager.store.database_path == ":memory:":
            raise RAGBackupError("RAG_BACKUP_DATABASE_NOT_PERSISTENT")
        active_version = self.manager.store.active_index_version()
        if not active_version:
            raise RAGBackupError("RAG_BACKUP_NO_ACTIVE_INDEX")
        backend = self.manager.store.active_index_backend() or "none"
        backup_id = f"ragbak_{uuid4().hex[:20]}"
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f".{backup_id}.tmp"
        target = self.root / backup_id
        if temporary.exists() or target.exists():
            raise RAGBackupError("RAG_BACKUP_ALREADY_EXISTS")
        temporary.mkdir(parents=True)
        try:
            vector_dir = temporary / "vector"
            vector_dir.mkdir()
            database_path = temporary / "rag.sqlite3"
            _sqlite_snapshot(self.manager.store, database_path)
            vector_files = _copy_active_vector(self.manager, active_version, backend, vector_dir)
            source_metadata = [_safe_source(item) for item in self.manager.store.list_sources()]
            (temporary / "source_metadata.json").write_text(_json(source_metadata), encoding="utf-8")
            files = [_file_entry(temporary, relative) for relative in ["rag.sqlite3", "source_metadata.json", *vector_files]]
            stats = self.manager.store.stats()
            payload = {
                "format": 1,
                "backup_id": backup_id,
                "schema_version": stats.get("schema_version"),
                "active_index_version": active_version,
                "active_vector_backend": backend,
                "provider_key": _active_provider_key(self.manager, backend, active_version),
                "dimension": _active_dimension(self.manager, backend, active_version),
                "source_count": stats.get("source_count", 0),
                "document_count": stats.get("document_count", 0),
                "chunk_count": stats.get("chunk_count", 0),
                "source_files_restored": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "files": files,
            }
            payload["checksum"] = _checksum(payload)
            (temporary / "manifest.json").write_text(_json(payload), encoding="utf-8")
            temporary.replace(target)
            return {"status": "success", **_backup_summary(payload), "file_count": len(files), "size_bytes": sum(int(item["size"]) for item in files)}
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _restore_snapshot(self, path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        if self.manager.store.database_path == ":memory:":
            raise RAGBackupError("RAG_BACKUP_DATABASE_NOT_PERSISTENT")
        rollback = self.root / f".restore-{uuid4().hex}"
        database = Path(self.manager.config.database_path).resolve()
        index_path = Path(self.manager.config.index_path).resolve()
        try:
            rollback.mkdir(parents=True)
            if database.exists():
                shutil.copy2(database, rollback / "rag.sqlite3")
            if index_path.exists():
                shutil.copytree(index_path, rollback / "index")
            self.manager.vector_index.close()
            self.manager.store.close()
            database.parent.mkdir(parents=True, exist_ok=True)
            staged_database = database.with_name(f".{database.name}.restore-{uuid4().hex}.tmp")
            shutil.copy2(path / "rag.sqlite3", staged_database)
            staged_database.replace(database)
            index_path.mkdir(parents=True, exist_ok=True)
            for item in (path / "vector").iterdir():
                if item.is_file():
                    staged = index_path / f".{item.name}.restore-{uuid4().hex}.tmp"
                    shutil.copy2(item, staged)
                    staged.replace(index_path / item.name)
            self.manager.reload_from_disk()
            restored_version = self.manager.store.active_index_version()
            if restored_version != manifest.get("active_index_version"):
                raise RAGBackupError("RAG_BACKUP_RESTORE_VERSION_MISMATCH")
            return {"status": "success", "backup_id": manifest["backup_id"], "active_index_version": restored_version, "active_vector_backend": self.manager.store.active_index_backend(), "source_files_restored": False, "source_count": self.manager.store.stats().get("source_count", 0)}
        except Exception as exc:
            try:
                self.manager.store.close()
            except Exception:
                pass
            if (rollback / "rag.sqlite3").exists():
                database.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rollback / "rag.sqlite3", database)
            if index_path.exists():
                shutil.rmtree(index_path)
            if (rollback / "index").exists():
                shutil.copytree(rollback / "index", index_path)
            try:
                self.manager.reload_from_disk()
            except Exception:
                pass
            if isinstance(exc, RAGBackupError):
                raise
            raise RAGBackupError("RAG_BACKUP_RESTORE_FAILED") from exc
        finally:
            shutil.rmtree(rollback, ignore_errors=True)

    def _with_job(self, mode: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if not self.manager._rebuild_lock.acquire(blocking=False):
            raise RAGBackupError("RAG_BACKUP_RESTORE_REBUILD_IN_PROGRESS")
        job_id = f"ragjob_{uuid4().hex}"
        try:
            self.manager.store.record_job(job_id, mode, "running", {"job_id": job_id, "mode": mode})
            self.manager.store._connection.commit()
            result = callback()
            self.manager.store.record_job(job_id, mode, "success", {**result, "job_id": job_id, "mode": mode})
            self.manager.store._connection.commit()
            return {**result, "job_id": job_id}
        except Exception as exc:
            code = getattr(exc, "code", None) or "RAG_BACKUP_OPERATION_FAILED"
            self.manager.store.record_job(job_id, mode, "failed", {"job_id": job_id, "mode": mode, "error_code": code})
            self.manager.store._connection.commit()
            if isinstance(exc, RAGBackupError):
                raise
            raise RAGBackupError(code) from exc
        finally:
            self.manager._rebuild_lock.release()

    def _path(self, backup_id: str) -> Path:
        value = str(backup_id or "").strip()
        if not _BACKUP_ID.fullmatch(value):
            raise RAGBackupError("RAG_BACKUP_ID_INVALID")
        path = (self.root / value).resolve()
        if path.parent != self.root or not path.is_dir():
            raise RAGBackupError("RAG_BACKUP_NOT_FOUND")
        return path


def _sqlite_snapshot(store: SQLiteRAGStore, target: Path) -> None:
    destination = sqlite3.connect(str(target))
    try:
        with store._lock:
            store._connection.backup(destination)
    except Exception as exc:
        raise RAGBackupError("RAG_BACKUP_SQLITE_FAILED") from exc
    finally:
        destination.close()


def _copy_active_vector(manager: RAGIndexManager, version: str, backend: str, target: Path) -> list[str]:
    if backend == "hnsw":
        names = [f"{_safe_version(version)}.hnsw", f"{_safe_version(version)}.hnsw.manifest.json"]
    elif backend in {"python_cosine", "python_cosine_fallback"}:
        names = [f"{_safe_version(version)}.ragindex"]
    else:
        return []
    result: list[str] = []
    for name in names:
        source = Path(manager.config.index_path).resolve() / name
        if not source.is_file():
            raise RAGBackupError("RAG_BACKUP_VECTOR_ARTIFACT_MISSING")
        shutil.copy2(source, target / name)
        result.append(f"vector/{name}")
    if backend == "hnsw":
        manifest = json.loads((target / names[1]).read_text(encoding="utf-8"))
        (target / "label_mapping.json").write_text(_json({"version": version, "labels": manifest.get("labels", {})}), encoding="utf-8")
        result.append("vector/label_mapping.json")
    return result


def _validate_snapshot(path: Path, manifest: dict[str, Any], config: Any) -> dict[str, Any]:
    for item in manifest.get("files", []):
        relative = Path(str(item.get("name", "")))
        file_path = (path / relative).resolve()
        if path not in file_path.parents or not file_path.is_file():
            raise RAGBackupError("RAG_BACKUP_FILE_MISSING")
        if int(item.get("size", -1)) != file_path.stat().st_size or item.get("sha256") != _sha256_file(file_path):
            raise RAGBackupError("RAG_BACKUP_CHECKSUM_MISMATCH")
    database = path / "rag.sqlite3"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RAGBackupError("RAG_BACKUP_SQLITE_CORRUPT")
        version = connection.execute("SELECT version FROM rag_index_versions WHERE status='active' ORDER BY created_at DESC LIMIT 1").fetchone()
        backend = connection.execute("SELECT backend FROM rag_index_versions WHERE status='active' ORDER BY created_at DESC LIMIT 1").fetchone()
        if not version or str(version[0]) != str(manifest.get("active_index_version")) or str((backend or [None])[0] or "none") != str(manifest.get("active_vector_backend")):
            raise RAGBackupError("RAG_BACKUP_VERSION_MISMATCH")
        counts = {
            "source_count": int(connection.execute("SELECT COUNT(*) FROM rag_sources").fetchone()[0]),
            "document_count": int(connection.execute("SELECT COUNT(*) FROM rag_documents").fetchone()[0]),
            "chunk_count": int(connection.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]),
        }
        source_metadata = json.loads((path / "source_metadata.json").read_text(encoding="utf-8"))
        if not isinstance(source_metadata, list) or len(source_metadata) != counts["source_count"] or any(not isinstance(item, dict) or not item.get("source_id") for item in source_metadata):
            raise RAGBackupError("RAG_BACKUP_SOURCE_METADATA_MISMATCH")
    except sqlite3.Error as exc:
        raise RAGBackupError("RAG_BACKUP_SQLITE_CORRUPT") from exc
    finally:
        connection.close()
    if manifest.get("active_vector_backend") not in {None, "none"}:
        vector_dir = path / "vector"
        vector_config = config.model_copy(update={"vector_backend": manifest["active_vector_backend"], "index_path": str(vector_dir)})
        try:
            backend = build_vector_backend(vector_config, vector_dir)
            backend.load(str(manifest["active_index_version"]), provider_key=manifest.get("provider_key"), dimension=int(manifest.get("dimension") or 0))
            vector_details = backend.validate()
            backend.close()
        except VectorIndexError as exc:
            raise RAGBackupError(getattr(exc, "code", None) or "RAG_BACKUP_VECTOR_INVALID") from exc
    else:
        vector_details = {"status": "not_applicable", "backend": "none"}
    return {**counts, "vector": vector_details, "source_files_restored": False}


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        checksum = manifest.pop("checksum")
        if checksum != _checksum(manifest) or manifest.get("backup_id") != path.name:
            raise RAGBackupError("RAG_BACKUP_MANIFEST_INVALID")
        manifest["checksum"] = checksum
        return manifest
    except RAGBackupError:
        raise
    except Exception as exc:
        raise RAGBackupError("RAG_BACKUP_MANIFEST_INVALID") from exc


def _backup_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: manifest.get(key) for key in ("backup_id", "schema_version", "active_index_version", "active_vector_backend", "dimension", "source_count", "document_count", "chunk_count", "source_files_restored", "created_at")}


def _safe_source(source: Any) -> dict[str, Any]:
    return {key: getattr(source, key, None) for key in ("source_id", "display_name", "source_type", "relative_location", "content_hash", "file_size", "modified_at", "ingestion_status", "parser_version", "chunker_version", "index_version", "created_at", "updated_at", "error_code", "duplicate_of_source_id", "language", "description", "tags", "source_status", "parser_status", "index_status", "document_count", "section_count", "chunk_count", "last_validated_at", "last_indexed_at", "provenance")}


def _file_entry(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    return {"name": relative.replace("\\", "/"), "size": path.stat().st_size, "sha256": _sha256_file(path)}


def _active_provider_key(manager: RAGIndexManager, backend: str, version: str) -> str | None:
    if backend == getattr(manager.vector_index, "backend", None) and manager.vector_index.index_version == version:
        return manager.vector_index.provider_key
    if backend == "hnsw":
        path = Path(manager.config.index_path) / f"{_safe_version(version)}.hnsw.manifest.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")).get("provider_key")
    if backend in {"python_cosine", "python_cosine_fallback"}:
        path = Path(manager.config.index_path) / f"{_safe_version(version)}.ragindex"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")).get("provider_key")
    return None


def _active_dimension(manager: RAGIndexManager, backend: str, version: str) -> int:
    if backend == getattr(manager.vector_index, "backend", None) and manager.vector_index.index_version == version:
        return int(manager.vector_index.dimension)
    if backend == "hnsw":
        path = Path(manager.config.index_path) / f"{_safe_version(version)}.hnsw.manifest.json"
    else:
        path = Path(manager.config.index_path) / f"{_safe_version(version)}.ragindex"
    if path.exists():
        return int(json.loads(path.read_text(encoding="utf-8")).get("dimension") or 0)
    return 0


def _checksum(payload: dict[str, Any]) -> str:
    value = {key: item for key, item in payload.items() if key != "checksum"}
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_version(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))
