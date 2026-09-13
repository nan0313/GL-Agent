"""Safe release metadata shared by health, scripts, and the WebGL page."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.release_profile import get_runtime_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILE = PROJECT_ROOT / "release" / "local_release.json"
OFFICIAL_WEBGL_ENTRY = "ApplicationVue/dist/index_agent.html"
API_SCHEMA_VERSION = "1.2"
RAG_SCHEMA_VERSION = "3"
CONVERSATION_SCHEMA_VERSION = "1"
SOURCE_ROOTS = ("app", "frontend", "scripts", "tools")
SOURCE_FILES = (
    "requirements.txt",
    "config/anchors.yaml",
    "config/rules.yaml",
    "config/rag_config.example.yaml",
    "install.ps1",
    "portable_common.ps1",
    "rebuild_rag.ps1",
    "verify_installation.ps1",
    "verify_package.ps1",
)
SOURCE_SUFFIXES = {".py", ".js", ".html", ".ps1", ".yaml", ".yml", ".json"}


def compute_agent_source_hash(root: Path = PROJECT_ROOT) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for name in SOURCE_ROOTS:
        base = root / name
        if base.is_dir():
            files.extend(path for path in base.rglob("*") if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES)
    files.extend(root / name for name in SOURCE_FILES if (root / name).is_file())
    filtered = [
        path for path in files
        if "__pycache__" not in path.parts
        and path.name != "local_llm_config.yaml"
        and path.name != "verification_report.json"
    ]
    for path in sorted(set(filtered), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def get_agent_commit(root: Path = PROJECT_ROOT) -> str:
    if not (root / ".git").exists():
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def load_release_descriptor(path: Path = RELEASE_FILE) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def get_release_metadata() -> dict[str, Any]:
    descriptor = load_release_descriptor()
    source_hash = compute_agent_source_hash()
    webgl_hash = str(descriptor.get("webgl_artifact_hash") or "unbound")
    calculated_id = f"ev-agent-{source_hash[:12]}-{webgl_hash[:12]}"
    configured_id = str(descriptor.get("release_id") or calculated_id)
    repository_commit = get_agent_commit()
    packaged_commit = str(descriptor.get("agent_commit") or "unknown")
    return {
        "release_id": configured_id,
        "agent_commit": repository_commit if repository_commit != "unknown" else packaged_commit,
        "agent_source_hash": source_hash,
        "build_time": descriptor.get("build_time"),
        "runtime_profile": get_runtime_profile(),
        "official_webgl_entry": OFFICIAL_WEBGL_ENTRY,
        "webgl_artifact_version": webgl_hash,
        "api_schema_version": API_SCHEMA_VERSION,
        "rag_schema_version": RAG_SCHEMA_VERSION,
        "conversation_schema_version": CONVERSATION_SCHEMA_VERSION,
        "release_consistent": bool(descriptor and configured_id == calculated_id and descriptor.get("agent_source_hash") == source_hash),
    }


def validate_release_descriptor() -> tuple[bool, list[str]]:
    metadata = get_release_metadata()
    issues: list[str] = []
    if not load_release_descriptor():
        issues.append("RELEASE_DESCRIPTOR_MISSING")
    if not metadata["release_consistent"]:
        issues.append("AGENT_RELEASE_CONTENT_MISMATCH")
    return not issues, issues
