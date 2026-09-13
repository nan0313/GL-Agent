"""Package-local runtime probes used by install and release validation."""

from __future__ import annotations

import argparse
import importlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import site
import sqlite3
import sys
from typing import Any


CORE_IMPORTS = (
    "fastapi", "pydantic", "yaml", "httpx", "uvicorn", "langgraph",
    "langchain_core", "torch", "sentence_transformers", "transformers",
    "huggingface_hub", "hnswlib", "pypdf", "docx", "numpy",
)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _version(module_name: str) -> str:
    distribution = {"yaml": "PyYAML", "docx": "python-docx"}.get(module_name, module_name)
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        module = importlib.import_module(module_name)
        return str(getattr(module, "__version__", "installed"))


def python_runtime(root: Path) -> dict[str, Any]:
    expected = root / "runtime" / "python"
    executable = Path(sys.executable).resolve()
    prefix = Path(sys.prefix).resolve()
    if not _inside(executable, expected) or not _inside(prefix, expected):
        raise RuntimeError("PYTHON_RUNTIME_OUTSIDE_PACKAGE")
    sites = [Path(value).resolve() for value in site.getsitepackages()]
    if not sites or any(not _inside(value, expected) for value in sites):
        raise RuntimeError("PYTHON_SITE_PACKAGES_OUTSIDE_PACKAGE")
    imports: dict[str, str] = {}
    import_paths: dict[str, str] = {}
    for name in CORE_IMPORTS:
        module = importlib.import_module(name)
        imports[name] = _version(name)
        module_file = getattr(module, "__file__", None)
        if module_file:
            resolved = Path(module_file).resolve()
            if not _inside(resolved, expected):
                raise RuntimeError(f"IMPORT_OUTSIDE_PACKAGE:{name}")
            import_paths[name] = _relative(resolved, root)
    import torch
    return {
        "status": "success",
        "python_version": sys.version.split()[0],
        "sys_executable": _relative(executable, root),
        "sys_prefix": _relative(prefix, root),
        "site_packages": [_relative(value, root) for value in sites],
        "user_site_enabled": bool(site.ENABLE_USER_SITE),
        "architecture_bits": 64 if sys.maxsize > 2**32 else 32,
        "imports": imports,
        "import_paths": import_paths,
        "torch_cuda_available": bool(torch.cuda.is_available()),
        "torch_cuda_build": getattr(torch.version, "cuda", None),
    }


def model_probe(root: Path) -> dict[str, Any]:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    model_path = (root / "models" / "bge-small-zh-v1.5").resolve()
    if not _inside(model_path, root) or not model_path.is_dir():
        raise RuntimeError("BGE_MODEL_NOT_PACKAGE_LOCAL")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(str(model_path), device="cpu", local_files_only=True)
    vectors = model.encode(
        ["飞到黑龙江省并高亮", "查询当前区域内的设备", "这是一条知识库安全规则"],
        normalize_embeddings=True,
    )
    shape = list(vectors.shape)
    if shape != [3, 512]:
        raise RuntimeError(f"BGE_DIMENSION_INVALID:{shape}")
    norms = [math.sqrt(sum(float(value) ** 2 for value in vector)) for vector in vectors]
    if any(not math.isfinite(value) or abs(value - 1.0) > 0.01 for value in norms):
        raise RuntimeError("BGE_NORMALIZATION_INVALID")
    if any(not math.isfinite(float(value)) for vector in vectors for value in vector):
        raise RuntimeError("BGE_NON_FINITE_VECTOR")
    return {
        "status": "success",
        "model_name": "BAAI/bge-small-zh-v1.5",
        "resolved_path": _relative(model_path, root),
        "shape": shape,
        "dimension": 512,
        "dtype": str(vectors.dtype),
        "norms": [round(value, 6) for value in norms],
        "device": "cpu",
        "offline": True,
        "local_files_only": True,
    }


def _database_counts(database: Path) -> dict[str, int]:
    if not database.is_file():
        raise RuntimeError("RAG_DATABASE_MISSING")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        tables = {
            "source": "rag_sources",
            "document": "rag_documents",
            "section": "rag_sections",
            "chunk": "rag_chunks",
            "embedding": "rag_embeddings",
        }
        return {name: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for name, table in tables.items()}
    finally:
        connection.close()


def _validate_rag_integrity(
    health: dict[str, Any],
    validation: dict[str, Any],
    counts: dict[str, int],
) -> dict[str, Any]:
    """Validate the active index, not the historical SQLite embedding cache."""
    if health.get("active_vector_backend") != "hnsw":
        raise RuntimeError("RAG_HNSW_NOT_ACTIVE")
    if bool(health.get("vector_fallback_used")):
        raise RuntimeError("RAG_VECTOR_FALLBACK_USED")
    if int(health.get("embedding_dimension") or 0) != 512:
        raise RuntimeError("RAG_EMBEDDING_DIMENSION_INVALID")
    if counts["chunk"] <= 0:
        raise RuntimeError("RAG_CHUNK_COUNT_INVALID")
    if validation.get("status") != "success" or not validation.get("vector_valid"):
        raise RuntimeError("RAG_VECTOR_INVALID")
    details = validation.get("vector_details") or {}
    if int(details.get("item_count") or 0) != counts["chunk"]:
        raise RuntimeError("RAG_VECTOR_ITEM_COUNT_INVALID")
    if str(details.get("version") or "") != str(health.get("active_index_version") or ""):
        raise RuntimeError("RAG_VECTOR_VERSION_INVALID")
    return {
        "active_chunk_count": counts["chunk"],
        "embedding_cache_entry_count": counts["embedding"],
        "vector_item_count": int(details["item_count"]),
        "vector_index_version": str(details["version"]),
    }


def rag_status(root: Path) -> dict[str, Any]:
    from app.rag.config import get_rag_config
    from app.rag.index_manager import RAGIndexManager
    get_rag_config.cache_clear()
    config = get_rag_config()
    for configured in (Path(config.database_path), Path(config.index_path), Path(config.embedding_model)):
        if not _inside(configured, root):
            raise RuntimeError("RAG_PATH_OUTSIDE_PACKAGE")
    manager = RAGIndexManager(config)
    try:
        # validate() loads the persisted active HNSW index.  Read health only
        # afterwards so a fresh process does not report an unloaded index as
        # item_count=0/index_version=null.
        validation = manager.validate()
        health = manager.health()
    finally:
        manager.close()
    counts = _database_counts(Path(config.database_path))
    integrity = _validate_rag_integrity(health, validation, counts)
    return {
        "status": "success",
        "counts": counts,
        "backend": health.get("active_vector_backend"),
        "dimension": health.get("embedding_dimension"),
        "fallback_used": bool(health.get("vector_fallback_used")),
        "vector_valid": bool(validation.get("vector_valid")),
        "active_index_version": health.get("active_index_version"),
        "vector_index_version": health.get("vector_index_version"),
        "database_path": _relative(Path(config.database_path), root),
        "index_path": _relative(Path(config.index_path), root),
        "embedding_model_path": _relative(Path(config.embedding_model), root),
        "integrity": integrity,
    }


def admin_region(root: Path) -> dict[str, Any]:
    from app.admin_regions.provider import LocalShapefileAdminRegionProvider
    shape = Path(os.environ.get("ADMIN_REGION_SHP_PATH", "")).resolve()
    if not _inside(shape, root):
        raise RuntimeError("ADMIN_REGION_PATH_OUTSIDE_PACKAGE")
    provider = LocalShapefileAdminRegionProvider(shape)
    if not provider.available:
        raise RuntimeError("ADMIN_REGION_SOURCE_UNAVAILABLE")
    records = provider.index().all()
    if not records:
        raise RuntimeError("ADMIN_REGION_SOURCE_EMPTY")
    return {
        "status": "success",
        "source_path": _relative(shape, root),
        "record_count": len(records),
        "boundary_record_count": sum(1 for item in records if item.boundary_available),
    }


def installed_manifest(root: Path) -> dict[str, Any]:
    package = json.loads((root / "package_manifest.json").read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "status": "installed",
        "release_id": package["release_id"],
        "package_type": package["package_type"],
        "python": python_runtime(root),
        "model": model_probe(root),
        "rag": rag_status(root),
        "admin_region": admin_region(root),
        "agent_runtime_path": ".",
        "webgl_runtime_path": "vendor/webgl",
        "runtime_paths_are_package_relative": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("python-runtime", "model", "rag-status", "admin-region", "installed-manifest"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--package-root", required=True)
        sub.add_argument("--output")
    args = parser.parse_args()
    root = Path(args.package_root).resolve()
    try:
        handlers = {
            "python-runtime": python_runtime,
            "model": model_probe,
            "rag-status": rag_status,
            "admin-region": admin_region,
            "installed-manifest": installed_manifest,
        }
        result = handlers[args.command](root)
        text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            output = Path(args.output).resolve()
            if not _inside(output, root):
                raise RuntimeError("PROBE_OUTPUT_OUTSIDE_PACKAGE")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8", newline="\n")
        print(text, end="")
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_code": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
