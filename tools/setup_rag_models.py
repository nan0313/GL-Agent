"""Check or explicitly download the single approved local RAG model.

The default action is a read-only check. Model files are runtime artifacts and
must live outside the repository unless an operator explicitly chooses another
location with ``--target``.
"""

import argparse
import os
from pathlib import Path
import sys


MODEL_ID = "BAAI/bge-small-zh-v1.5"
MODEL_DIR_NAME = "bge-small-zh-v1.5"


def default_target() -> Path:
    configured = os.getenv("RAG_MODEL_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".cache" / "rag_models" / MODEL_DIR_NAME


def required_files(target: Path) -> tuple[Path, ...]:
    return (
        target / "config.json",
        target / "modules.json",
        target / "tokenizer.json",
        target / "tokenizer_config.json",
        target / "model.safetensors",
    )


def check_model(target: Path) -> bool:
    missing = [path.name for path in required_files(target) if not path.is_file() or path.stat().st_size == 0]
    if missing:
        print(f"model={MODEL_ID} status=missing missing={','.join(missing)}")
        return False
    print(f"model={MODEL_ID} status=ready")
    return True


def download_model(target: Path) -> int:
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("error=RAG_MODEL_DOWNLOAD_DEPENDENCY_MISSING", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(repo_id=MODEL_ID, local_dir=str(target), local_dir_use_symlinks=False)
    except Exception as exc:
        print(f"error=RAG_MODEL_DOWNLOAD_FAILED type={exc.__class__.__name__}", file=sys.stderr)
        return 3
    return 0 if check_model(target) else 4


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or explicitly download the local RAG embedding model.")
    parser.add_argument("--download", action="store_true", help="download exactly one model; omitted means read-only check")
    parser.add_argument("--target", default=str(default_target()), help="runtime model directory; never commit this directory")
    args = parser.parse_args()
    target = Path(args.target).expanduser()
    if not args.download:
        return 0 if check_model(target) else 1
    return download_model(target)


if __name__ == "__main__":
    raise SystemExit(main())
