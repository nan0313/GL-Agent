"""Local runtime profiles with environment-first, secret-free defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path


SUPPORTED_RUNTIME_PROFILES = {"development", "local_release"}
SUPPORTED_AGENT_MODES = {"customer", "developer"}
BGE_MODEL_ID = "BAAI/bge-small-zh-v1.5"


def get_runtime_profile() -> str:
    profile = os.getenv("EV_AGENT_RUNTIME_PROFILE", "development").strip().lower()
    if profile not in SUPPORTED_RUNTIME_PROFILES:
        supported = ", ".join(sorted(SUPPORTED_RUNTIME_PROFILES))
        raise ValueError(f"Unsupported EV_AGENT_RUNTIME_PROFILE: {profile}. Supported: {supported}.")
    return profile


def get_agent_mode() -> str:
    """Server-authoritative product mode; production-safe by default."""
    mode = os.getenv("EV_AGENT_MODE", "customer").strip().lower()
    if mode not in SUPPORTED_AGENT_MODES:
        supported = ", ".join(sorted(SUPPORTED_AGENT_MODES))
        raise ValueError(f"Unsupported EV_AGENT_MODE: {mode}. Supported: {supported}.")
    return mode


def developer_mode_enabled() -> bool:
    return get_agent_mode() == "developer"


def debug_routes_enabled() -> bool:
    if not developer_mode_enabled():
        return False
    explicit = os.getenv("EV_AGENT_DEBUG_ROUTES")
    if explicit is not None:
        return _as_bool(explicit)
    if get_runtime_profile() == "local_release":
        return _as_bool(os.getenv("EV_AGENT_LOCAL_ADMIN_MODE", "false"))
    return True


def local_admin_mode_enabled() -> bool:
    """Server-side capability gate for local knowledge management."""
    if not developer_mode_enabled():
        return False
    if get_runtime_profile() == "development":
        return True
    return _as_bool(os.getenv("EV_AGENT_LOCAL_ADMIN_MODE", "false"))


def apply_release_profile() -> dict[str, object]:
    """Apply only missing values so explicit environment variables always win."""
    profile = get_runtime_profile()
    model_path = find_local_bge_model()
    if profile == "local_release":
        os.environ.setdefault("EV_AGENT_MODE", "customer")
        os.environ.setdefault("CORS_ALLOW_ORIGINS", "http://127.0.0.1:8090,http://localhost:8090")
        os.environ.setdefault("TRACE_STORE_PROVIDER", "sqlite")
        os.environ.setdefault("RAG_LEXICAL_ENABLED", "true")
        if model_path is not None:
            os.environ.setdefault("RAG_DENSE_ENABLED", "true")
            os.environ.setdefault("RAG_EMBEDDING_PROVIDER", "sentence_transformers")
            os.environ.setdefault("RAG_EMBEDDING_MODEL", str(model_path))
            os.environ.setdefault("RAG_VECTOR_BACKEND", "hnsw")
        else:
            os.environ.setdefault("RAG_DENSE_ENABLED", "false")
            os.environ.setdefault("RAG_EMBEDDING_PROVIDER", "disabled")
            os.environ.setdefault("RAG_EMBEDDING_MODEL", "")
            os.environ.setdefault("RAG_VECTOR_BACKEND", "python_cosine_fallback")
    return {
        "runtime_profile": profile,
        "agent_mode": get_agent_mode(),
        "debug_routes_enabled": debug_routes_enabled(),
        "rag_model_status": "LOCAL_AVAILABLE" if model_path is not None else "RAG_MODEL_NOT_LOCAL",
        "dense_default_enabled": bool(profile == "local_release" and model_path is not None),
    }


def find_local_bge_model() -> Path | None:
    """Return a complete local model directory without network access."""
    candidates: list[Path] = []
    explicit = os.getenv("RAG_LOCAL_MODEL_PATH", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    cache_root = Path(os.getenv("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
    model_root = cache_root / "hub" / "models--BAAI--bge-small-zh-v1.5"
    reference = model_root / "refs" / "main"
    if reference.is_file():
        try:
            revision = reference.read_text(encoding="utf-8").strip()
        except OSError:
            revision = ""
        if revision:
            candidates.append(model_root / "snapshots" / revision)
    snapshots = model_root / "snapshots"
    if snapshots.is_dir():
        candidates.extend(sorted((item for item in snapshots.iterdir() if item.is_dir()), reverse=True))
    candidates.append(Path(__file__).resolve().parents[1] / "models" / "bge-small-zh-v1.5")
    for candidate in candidates:
        if _is_complete_sentence_transformer(candidate):
            return candidate.resolve()
    return None


def public_profile_status() -> dict[str, object]:
    model_path = find_local_bge_model()
    return {
        "runtime_profile": get_runtime_profile(),
        "agent_mode": get_agent_mode(),
        "debug_routes_enabled": debug_routes_enabled(),
        "rag_embedding_model": BGE_MODEL_ID,
        "rag_model_status": "LOCAL_AVAILABLE" if model_path is not None else "RAG_MODEL_NOT_LOCAL",
        "local_model_complete": model_path is not None,
    }


def _is_complete_sentence_transformer(path: Path) -> bool:
    if not path.is_dir() or not (path / "config.json").is_file():
        return False
    if not ((path / "model.safetensors").is_file() or (path / "pytorch_model.bin").is_file()):
        return False
    try:
        config = json.loads((path / "config.json").read_text(encoding="utf-8"))
        return int(config.get("hidden_size") or config.get("projection_dim") or 0) == 512
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
