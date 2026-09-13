from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException


PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_MANIFEST = PROJECT_ROOT / "knowledge_manifest.json"

router = APIRouter(prefix="/api/knowledge", tags=["customer-knowledge"])


@router.get("/sources")
def list_customer_knowledge_sources() -> dict[str, object]:
    """Return only customer-safe metadata for the packaged knowledge library."""
    try:
        manifest = json.loads(KNOWLEDGE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="知识库暂时不可用，请稍后重试。") from exc

    values: list[dict[str, object]] = []
    for index, source in enumerate(manifest.get("sources") or [], start=1):
        if not isinstance(source, dict):
            continue
        filename = str(source.get("filename") or "").strip()
        relative_path = str(source.get("path") or "").strip()
        if not filename or not relative_path:
            continue
        candidate = (PROJECT_ROOT / relative_path).resolve()
        try:
            candidate.relative_to(PROJECT_ROOT)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        values.append(
            {
                "source_id": f"kb_{index:03d}",
                "filename": filename,
                "file_type": str(source.get("type") or candidate.suffix.lstrip(".")).lower(),
                "size_bytes": int(source.get("size") or candidate.stat().st_size),
                "status": "ready",
            }
        )
    return {"scope": "输变电知识库", "total": len(values), "sources": values}
