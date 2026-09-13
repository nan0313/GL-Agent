"""Bind the current Agent source hash to the pinned WebGL artifact."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILE = PROJECT_ROOT / "release" / "local_release.json"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--webgl-root", default=str(PROJECT_ROOT.parent / "EV-Globe-WebGL"))
    parser.add_argument("--build-time")
    args = parser.parse_args()
    webgl_root = Path(args.webgl_root).resolve()

    from app.release_metadata import (
        API_SCHEMA_VERSION,
        CONVERSATION_SCHEMA_VERSION,
        RAG_SCHEMA_VERSION,
        compute_agent_source_hash,
        load_release_descriptor,
    )

    source_hash = compute_agent_source_hash()
    existing = load_release_descriptor()
    build_time = args.build_time or str(existing.get("build_time") or datetime.now(timezone.utc).isoformat())
    build_script = webgl_root / "scripts" / "build_agent_overlay.py"
    subprocess.run([
        sys.executable, str(build_script), "--agent-source-hash", source_hash,
        "--agent-base-url", "http://127.0.0.1:8009", "--generated-at", build_time,
    ], cwd=webgl_root, check=True)
    manifest = json.loads((webgl_root / "release" / "webgl_runtime_manifest.json").read_text(encoding="utf-8"))
    expected_id = f"ev-agent-{source_hash[:12]}-{str(manifest['webgl_artifact_hash'])[:12]}"
    if manifest.get("release_id") != expected_id:
        raise RuntimeError("WEBGL_RELEASE_ID_INVALID")
    descriptor = {
        "release_id": expected_id,
        "build_time": build_time,
        "runtime_profile": "local_release",
        "agent_source_hash": source_hash,
        "webgl_artifact_hash": manifest["webgl_artifact_hash"],
        "official_webgl_entry": "ApplicationVue/dist/index_agent.html",
        "api_schema_version": API_SCHEMA_VERSION,
        "rag_schema_version": RAG_SCHEMA_VERSION,
        "conversation_schema_version": CONVERSATION_SCHEMA_VERSION,
    }
    for key in ("agent_commit", "webgl_commit"):
        if existing.get(key):
            descriptor[key] = existing[key]
    RELEASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RELEASE_FILE.write_text(json.dumps(descriptor, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"release_id": expected_id, "agent_source_hash": source_hash}, ensure_ascii=False))


if __name__ == "__main__":
    main()
