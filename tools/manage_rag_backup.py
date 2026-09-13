"""Safe local RAG backup operations; paths are always configuration-scoped."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.backup import RAGBackupError, RAGBackupService
from app.rag.index_manager import RAGIndexManager
from app.rag.config import get_rag_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage configured RAG consistency backups")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create")
    for name in ("list",):
        subparsers.add_parser(name)
    validate = subparsers.add_parser("validate")
    validate.add_argument("backup_id")
    restore = subparsers.add_parser("restore")
    restore.add_argument("backup_id")
    restore.add_argument("--confirm", action="store_true")
    delete = subparsers.add_parser("delete")
    delete.add_argument("backup_id")
    delete.add_argument("--confirm", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    manager = RAGIndexManager(get_rag_config())
    service = RAGBackupService(manager)
    try:
        if args.command == "list":
            return {"command": "list", "backups": service.list()}
        if args.command == "create":
            return {"command": "create", **service.create()}
        if args.command == "validate":
            return {"command": "validate", **service.validate(args.backup_id)}
        if args.command == "restore":
            if not args.confirm:
                return {"command": "restore", "status": "failed", "error_code": "RAG_BACKUP_CONFIRM_REQUIRED"}
            return {"command": "restore", **service.restore(args.backup_id)}
        if args.command == "delete":
            if not args.confirm:
                return {"command": "delete", "status": "failed", "error_code": "RAG_BACKUP_CONFIRM_REQUIRED"}
            return {"command": "delete", **service.delete(args.backup_id)}
        raise RAGBackupError("RAG_BACKUP_COMMAND_INVALID")
    finally:
        manager.close()


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(_parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") != "failed" else 2
    except RAGBackupError as exc:
        print(json.dumps({"status": "failed", "error_code": exc.code}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
