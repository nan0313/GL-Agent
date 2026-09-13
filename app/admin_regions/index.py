from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Iterable


_SUFFIXES = (
    "特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区",
    "自治州", "地区", "省", "市", "区", "县", "盟", "旗",
)


def normalize_region_name(value: str | None) -> str:
    text = re.sub(r"[\s·•,，的]", "", value or "")
    text = text.replace("市市辖区", "市")
    changed = True
    while changed and text:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text


@dataclass(slots=True)
class AdminRegionRecord:
    region_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    adcode: str | None = None
    level: str = "unknown"
    parent_name: str | None = None
    parent_adcode: str | None = None
    center: dict[str, float] | None = None
    center_source: str | None = None
    boundary_object_id: str | None = None
    boundary_available: bool = False
    source: str = "unknown"
    source_confidence: str = "unknown"
    properties: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdminRegionIndex:
    """Idempotent, provider-neutral province/city/district/county index."""

    def __init__(self, records: Iterable[AdminRegionRecord] | None = None) -> None:
        self._records: dict[str, AdminRegionRecord] = {}
        if records:
            self.rebuild(records)

    def rebuild(self, records: Iterable[AdminRegionRecord]) -> None:
        self._records = {record.region_id: record for record in records}

    def add(self, record: AdminRegionRecord) -> None:
        self._records[record.region_id] = record

    def all(self) -> list[AdminRegionRecord]:
        return list(self._records.values())

    def find(
        self,
        *,
        name: str | None = None,
        adcode: str | None = None,
        level: str | None = None,
        parent: str | None = None,
    ) -> dict[str, Any]:
        candidates = self.all()
        matched_by = None
        if adcode:
            candidates = [item for item in candidates if item.adcode == str(adcode)]
            matched_by = "adcode"
        elif name:
            query = name.strip()
            exact = [item for item in candidates if item.name == query]
            if exact:
                candidates, matched_by = exact, "formal_name"
            else:
                aliases = [item for item in candidates if query in item.aliases]
                if aliases:
                    candidates, matched_by = aliases, "alias"
                else:
                    normalized = normalize_region_name(query)
                    candidates = [
                        item for item in candidates
                        if normalize_region_name(item.name) == normalized
                        or normalized in {normalize_region_name(alias) for alias in item.aliases}
                    ]
                    matched_by = "normalized_name"
        if level:
            candidates = [item for item in candidates if item.level == level]
        if parent:
            parent_key = normalize_region_name(parent)
            candidates = [item for item in candidates if normalize_region_name(item.parent_name) == parent_key]
        if not candidates:
            return {"success": False, "code": "ADMIN_REGION_NOT_FOUND", "candidates": []}
        if len(candidates) > 1:
            return {
                "success": False,
                "code": "ADMIN_REGION_AMBIGUOUS",
                "candidates": [self._candidate(item) for item in candidates],
            }
        return {"success": True, "code": "SUCCESS", "matched_by": matched_by, "region": candidates[0].public_dict()}

    @staticmethod
    def _candidate(item: AdminRegionRecord) -> dict[str, Any]:
        return {
            "name": item.name,
            "adcode": item.adcode,
            "level": item.level,
            "parent": item.parent_name,
            "source": item.source,
        }
