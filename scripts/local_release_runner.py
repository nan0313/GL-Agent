"""Run the local Agent API and pinned WebGL runtime in one supervised process."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from datetime import datetime, timezone

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        logging.getLogger("ev_agent.webgl").info(format, *args)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return data


def _validate_release(webgl_root: Path) -> tuple[dict[str, object], Path]:
    from app.release_metadata import get_release_metadata, validate_release_descriptor

    valid, issues = validate_release_descriptor()
    if not valid:
        raise RuntimeError(",".join(issues))
    metadata = get_release_metadata()
    manifest_path = webgl_root / "release" / "webgl_runtime_manifest.json"
    manifest = _load_json(manifest_path)
    if manifest.get("release_id") != metadata.get("release_id"):
        raise RuntimeError("AGENT_WEBGL_RELEASE_ID_MISMATCH")
    official_entry = webgl_root / str(manifest.get("official_entry") or "")
    if not official_entry.is_file():
        raise RuntimeError("OFFICIAL_WEBGL_ENTRY_MISSING")
    entry_record = next(
        (item for item in manifest.get("runtime_files", []) if isinstance(item, dict) and item.get("path") == manifest.get("official_entry")),
        None,
    )
    if not entry_record or entry_record.get("sha256") != _sha256(official_entry):
        raise RuntimeError("OFFICIAL_WEBGL_ENTRY_CHECKSUM_MISMATCH")
    return metadata, official_entry


def _log_directory() -> Path:
    configured = os.getenv("EV_AGENT_LOG_DIR", "").strip()
    return Path(configured).resolve() if configured else PROJECT_ROOT / "logs"


def _configure_logging() -> None:
    log_dir = _log_directory()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "local_release.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(logging.INFO)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--webgl-root", required=True)
    args = parser.parse_args()
    webgl_root = Path(args.webgl_root).resolve()
    os.environ.setdefault("EV_AGENT_RUNTIME_PROFILE", "local_release")
    _configure_logging()
    metadata, _ = _validate_release(webgl_root)

    runtime_dir = PROJECT_ROOT / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_file = runtime_dir / "ev_agent_runtime.json"
    runtime_file.write_text(json.dumps({
        "pid": os.getpid(),
        "release_id": metadata["release_id"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "agent_host": "127.0.0.1",
        "agent_port": 8009,
        "webgl_host": "127.0.0.1",
        "webgl_port": 8090,
        "official_entry": metadata["official_webgl_entry"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    handler = functools.partial(QuietStaticHandler, directory=str(webgl_root))
    webgl_server = ThreadingHTTPServer(("127.0.0.1", 8090), handler)
    webgl_thread = threading.Thread(target=webgl_server.serve_forever, name="ev-webgl", daemon=True)
    webgl_thread.start()
    logging.getLogger("ev_agent.release").info("release_started release_id=%s", metadata["release_id"])
    try:
        config = uvicorn.Config(
            "app.main:app", host="127.0.0.1", port=8009, log_config=None,
            access_log=True, server_header=False,
        )
        uvicorn.Server(config).run()
    finally:
        webgl_server.shutdown()
        webgl_server.server_close()
        runtime_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
