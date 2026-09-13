from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "evaluation" / "datasets"
REPORT_PATH = ROOT / "evaluation" / "reports" / "dataset_validation_v1.json"
RAG_DB = ROOT / "runtime" / "rag" / "rag.sqlite3"
ROUTE_LABELS = {"anchor_task", "rag_qa", "document_task", "general_chat", "unknown"}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: each line must be an object")
            rows.append(value)
    return rows


def _digest(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _check_unique(rows: list[dict[str, Any]], key: str, dataset: str, errors: list[str]) -> None:
    counts = Counter(str(row.get(key)) for row in rows)
    duplicates = sorted(value for value, count in counts.items() if count > 1)
    _require(not duplicates, f"{dataset}: duplicate {key}: {duplicates[:8]}", errors)


def _family_split_leaks(rows: list[dict[str, Any]]) -> list[str]:
    splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        splits[str(row.get("family_id"))].add(str(row.get("split")))
    return sorted(family for family, values in splits.items() if len(values) > 1)


def validate_gis(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    _require(len(rows) == 80, f"GIS: expected 80 rows, got {len(rows)}", errors)
    executable = [row for row in rows if row.get("case_type") == "executable"]
    abnormal = [row for row in rows if row.get("case_type") == "abnormal"]
    _require(len(executable) == 60, f"GIS: expected 60 executable rows, got {len(executable)}", errors)
    _require(len(abnormal) == 20, f"GIS: expected 20 abnormal rows, got {len(abnormal)}", errors)
    _check_unique(rows, "case_id", "GIS", errors)
    signatures = Counter(
        json.dumps(
            {
                "queries": [_normalize(turn.get("query")) for turn in row.get("turns", [])],
                "preconditions": row.get("preconditions") or {},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for row in rows
    )
    _require(not [signature for signature, count in signatures.items() if count > 1], "GIS: duplicate case signatures found", errors)
    for row in rows:
        case_id = row.get("case_id")
        gold = row.get("gold") or {}
        turns = row.get("turns") or []
        _require(bool(turns), f"GIS {case_id}: missing turns", errors)
        _require(all(turn.get("query") for turn in turns), f"GIS {case_id}: empty query", errors)
        _require(gold.get("route_type") == "anchor_task", f"GIS {case_id}: invalid route gold", errors)
        _require(bool(gold.get("postconditions")), f"GIS {case_id}: missing postconditions", errors)
        if row.get("case_type") == "executable":
            _require(gold.get("expected_handling") == "execute", f"GIS {case_id}: executable handling mismatch", errors)
            _require(bool(gold.get("business_actions")), f"GIS {case_id}: missing expected action", errors)
        else:
            _require(gold.get("expected_handling") in {"clarify", "reject", "not_found"}, f"GIS {case_id}: invalid abnormal handling", errors)
            _require(not gold.get("business_actions"), f"GIS {case_id}: abnormal case mutates business state", errors)
    leaks = _family_split_leaks(rows)
    _require(not leaks, f"GIS: family split leakage: {leaks[:8]}", errors)
    summary = {
        "case_count": len(rows),
        "executable_count": len(executable),
        "abnormal_count": len(abnormal),
        "handling_counts": dict(sorted(Counter((row.get("gold") or {}).get("expected_handling") for row in rows).items())),
        "category_counts": dict(sorted(Counter(row.get("category") for row in rows).items())),
        "split_counts": dict(sorted(Counter(row.get("split") for row in rows).items())),
        "multi_turn_count": sum(len(row.get("turns") or []) > 1 for row in rows),
        "family_count": len({row.get("family_id") for row in rows}),
        "sha256": _digest(rows),
    }
    return summary, errors


def validate_rag(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    _require(len(rows) == 120, f"RAG: expected 120 rows, got {len(rows)}", errors)
    answerable = [row for row in rows if row.get("answerable") is True]
    unanswerable = [row for row in rows if row.get("answerable") is False]
    _require(len(answerable) == 100, f"RAG: expected 100 answerable rows, got {len(answerable)}", errors)
    _require(len(unanswerable) == 20, f"RAG: expected 20 unanswerable rows, got {len(unanswerable)}", errors)
    _require(sum(len(row.get("gold_units") or []) > 1 for row in rows) == 20, "RAG: expected 20 multi-evidence rows", errors)
    _check_unique(rows, "case_id", "RAG", errors)
    query_counts = Counter(_normalize(row.get("query")) for row in rows)
    duplicate_queries = [query for query, count in query_counts.items() if count > 1]
    _require(not duplicate_queries, f"RAG: duplicate queries: {duplicate_queries[:8]}", errors)
    _require(RAG_DB.exists(), f"RAG: database missing: {RAG_DB}", errors)

    evidence_count = 0
    source_names: set[str] = set()
    connection = sqlite3.connect(RAG_DB)
    connection.row_factory = sqlite3.Row
    chunks = {
        row["chunk_id"]: row
        for row in connection.execute(
            "SELECT c.chunk_id, c.content, c.content_hash AS chunk_hash, c.page_start, c.page_end, "
            "s.display_name AS source_name, s.content_hash AS source_hash "
            "FROM rag_chunks c JOIN rag_sources s ON s.source_id=c.source_id"
        )
    }
    corpus_text = "".join(_normalize(row["content"]) for row in chunks.values())
    for row in rows:
        case_id = row.get("case_id")
        _require(bool(row.get("query")), f"RAG {case_id}: empty query", errors)
        if row.get("answerable"):
            units = row.get("gold_units") or []
            _require(bool(row.get("gold_answer")), f"RAG {case_id}: missing gold answer", errors)
            _require(bool(units), f"RAG {case_id}: missing gold units", errors)
            for unit in units:
                fact = unit.get("fact") or ""
                _require(_normalize(fact) in _normalize(row.get("gold_answer")), f"RAG {case_id}: fact absent from gold answer", errors)
                spans = unit.get("acceptable_spans") or []
                _require(bool(spans), f"RAG {case_id}/{unit.get('unit_id')}: missing spans", errors)
                for span in spans:
                    evidence_count += 1
                    chunk = chunks.get(span.get("chunk_id"))
                    if chunk is None:
                        errors.append(f"RAG {case_id}: missing chunk {span.get('chunk_id')}")
                        continue
                    source_names.add(span.get("source_name"))
                    _require(span.get("source_name") == chunk["source_name"], f"RAG {case_id}: source name mismatch", errors)
                    _require(span.get("source_sha256") == chunk["source_hash"], f"RAG {case_id}: source hash mismatch", errors)
                    _require(span.get("chunk_content_hash") == chunk["chunk_hash"], f"RAG {case_id}: chunk hash mismatch", errors)
                    _require(_normalize(span.get("quote")) in _normalize(chunk["content"]), f"RAG {case_id}: quote not found in chunk", errors)
                    _require(span.get("page_start") == chunk["page_start"], f"RAG {case_id}: page_start mismatch", errors)
                    _require(span.get("page_end") == chunk["page_end"], f"RAG {case_id}: page_end mismatch", errors)
        else:
            _require(row.get("gold_answer") is None, f"RAG {case_id}: unanswerable gold_answer must be null", errors)
            _require(not row.get("gold_units"), f"RAG {case_id}: unanswerable case has gold units", errors)
            anchor = row.get("negative_anchor") or ""
            _require(bool(anchor), f"RAG {case_id}: missing negative anchor", errors)
            _require(_normalize(anchor) not in corpus_text, f"RAG {case_id}: negative anchor appears in corpus", errors)
    connection.close()
    leaks = _family_split_leaks(rows)
    _require(not leaks, f"RAG: family split leakage: {leaks[:8]}", errors)
    summary = {
        "case_count": len(rows),
        "answerable_count": len(answerable),
        "unanswerable_count": len(unanswerable),
        "multi_evidence_count": sum(len(row.get("gold_units") or []) > 1 for row in rows),
        "gold_evidence_span_count": evidence_count,
        "covered_source_count": len(source_names),
        "covered_sources": sorted(source_names),
        "question_type_counts": dict(sorted(Counter(row.get("question_type") for row in rows).items())),
        "split_counts": dict(sorted(Counter(row.get("split") for row in rows).items())),
        "sha256": _digest(rows),
    }
    return summary, errors


def validate_router(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    _require(len(rows) == 300, f"Router: expected 300 rows, got {len(rows)}", errors)
    _check_unique(rows, "case_id", "Router", errors)
    query_counts = Counter(_normalize((row.get("turns") or [{}])[-1].get("query")) for row in rows)
    duplicate_queries = [query for query, count in query_counts.items() if count > 1]
    _require(not duplicate_queries, f"Router: duplicate queries: {duplicate_queries[:8]}", errors)
    label_counts = Counter(row.get("gold_route_type") for row in rows)
    _require(set(label_counts) == ROUTE_LABELS, f"Router: invalid label set: {sorted(label_counts)}", errors)
    for label in ROUTE_LABELS:
        _require(label_counts[label] == 60, f"Router: expected 60 {label}, got {label_counts[label]}", errors)
    boundary_count = sum("boundary" in (row.get("tags") or []) for row in rows)
    _require(boundary_count >= 75, f"Router: expected at least 75 boundary cases, got {boundary_count}", errors)
    for row in rows:
        case_id = row.get("case_id")
        turns = row.get("turns") or []
        _require(bool(turns and turns[-1].get("query")), f"Router {case_id}: missing query", errors)
        _require(isinstance(row.get("gold_model_required"), bool), f"Router {case_id}: gold_model_required is not bool", errors)
        _require(isinstance(row.get("attachment_state"), dict), f"Router {case_id}: attachment_state is not object", errors)
        _require(bool(row.get("rationale")), f"Router {case_id}: missing rationale", errors)
    leaks = _family_split_leaks(rows)
    _require(not leaks, f"Router: family split leakage: {leaks[:8]}", errors)
    summary = {
        "case_count": len(rows),
        "label_counts": dict(sorted(label_counts.items())),
        "boundary_count": boundary_count,
        "boundary_ratio": boundary_count / len(rows),
        "gold_model_required_count": sum(row.get("gold_model_required") is True for row in rows),
        "gold_rule_eligible_count": sum(row.get("gold_model_required") is False for row in rows),
        "split_counts": dict(sorted(Counter(row.get("split") for row in rows).items())),
        "family_count": len({row.get("family_id") for row in rows}),
        "sha256": _digest(rows),
    }
    return summary, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate project evaluation seed datasets.")
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    gis_rows = _read_jsonl(DATASET_DIR / "gis_e2e_v1.jsonl")
    rag_rows = json.loads((DATASET_DIR / "rag_gold_v1.json").read_text(encoding="utf-8"))
    route_rows = _read_jsonl(DATASET_DIR / "router_v1.jsonl")
    manifest = json.loads((DATASET_DIR / "manifest_v1.json").read_text(encoding="utf-8"))

    gis_summary, gis_errors = validate_gis(gis_rows)
    rag_summary, rag_errors = validate_rag(rag_rows)
    route_summary, route_errors = validate_router(route_rows)
    errors = gis_errors + rag_errors + route_errors

    for filename, summary in (
        ("gis_e2e_v1.jsonl", gis_summary),
        ("rag_gold_v1.json", rag_summary),
        ("router_v1.jsonl", route_summary),
    ):
        expected = manifest["datasets"][filename]["sha256"]
        if summary["sha256"] != expected:
            errors.append(f"Manifest digest mismatch for {filename}")

    report = {
        "status": "passed" if not errors else "failed",
        "datasets": {"gis": gis_summary, "rag": rag_summary, "router": route_summary},
        "errors": errors,
        "warnings": [
            "All three datasets are project-grounded seeds and still require a human lock/review pass.",
            "A passed report proves structure and provenance, not model or Agent quality.",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
