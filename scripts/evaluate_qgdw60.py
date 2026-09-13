import argparse
import json
import re
import sys
from pathlib import Path
from time import perf_counter
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag import LightweightReranker, LocalRetriever, RAGIndexStore  # noqa: E402
from app.rag.rag_pipeline import RAGPipeline  # noqa: E402


DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "qgdw11809_2023_rag_gold_60.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "runtime" / "verification" / "qgdw11809_2023_rag_eval.json"


class _NoGeneration:
    def answer(self, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError("EVALUATION_GENERATION_DISABLED")


def evaluate(cases_path: Path, output_path: Path, *, top_k: int = 10, after_only: bool = False, case_ids: set[str] | None = None) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if case_ids:
        cases = [case for case in cases if str(case["case_id"]) in case_ids]
    store = RAGIndexStore()
    store.ensure_built()
    chunks = store.list_chunks()
    retriever = LocalRetriever(index_store=store)
    reranker = LightweightReranker()
    pipeline = RAGPipeline(
        index_store=store,
        retriever=retriever,
        reranker=reranker,
        qwen_rag_answer=_NoGeneration(),
        top_k=top_k,
    )
    baseline_runs = {} if after_only else {
        str(limit): _evaluate_mode(
            cases,
            lambda query, candidate_limit=limit: reranker.rerank(
                query,
                retriever.retrieve(query, top_k=candidate_limit, candidate_k=candidate_limit),
                top_k=top_k,
            ),
            top_k=top_k,
            label=f"baseline_candidate_{limit}",
            chunks=chunks,
        )
        for limit in (24, 40, 60)
    }
    upgraded = _evaluate_mode(
        cases,
        lambda query: pipeline.answer(query, top_k=top_k, request_id="qgdw60-eval"),
        top_k=top_k,
        label="production_adaptive_neighbor",
        chunks=chunks,
    )
    report = {
        "fixture": cases_path.name,
        "gold_audit": _audit_gold_cases(cases),
        "metric_definition": {
            "Hit": "Top-K aggregate evidence from expected source covers all normalized scoring units",
            "EvidenceHit": "Top-K aggregate evidence from any source covers all normalized scoring units",
            "SourceHit": "Expected source appears by rank K",
            "LocationHit": "A chunk at the workbook's source location, or its immediate neighbor, appears by rank K",
        },
        "top_k": top_k,
        "baseline_candidate_experiments": baseline_runs,
        "before": baseline_runs.get("24"),
        "after": upgraded,
    }
    report["failure_taxonomy"] = _failure_taxonomy(upgraded, cases)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _evaluate_mode(cases: list[dict[str, Any]], retrieve, *, top_k: int, label: str, chunks: list[Any]) -> dict[str, Any]:
    started = perf_counter()
    results: list[dict[str, Any]] = []
    reciprocal_ranks: list[float] = []
    evidence_reciprocal_ranks: list[float] = []
    hits = {1: 0, 3: 0, 5: 0}
    evidence_hits = {1: 0, 3: 0, 5: 0}
    source_hits = {1: 0, 3: 0, 5: 0}
    location_hits = {1: 0, 3: 0, 5: 0}
    source_reciprocal_ranks: list[float] = []
    location_reciprocal_ranks: list[float] = []
    coverage_at_5: list[float] = []
    evidence_coverage_at_5: list[float] = []
    no_answer_correct = 0
    answerable_count = sum(bool(case["answerable"]) for case in cases)
    location_case_count = 0
    for case in cases:
        query = str(case["query"])
        retrieval_output = retrieve(query)
        pipeline_result = retrieval_output if hasattr(retrieval_output, "retrieved_hits") else None
        selected = list(pipeline_result.retrieved_hits if pipeline_result is not None else retrieval_output)[:top_k]
        if not case["answerable"]:
            decision = getattr(getattr(pipeline_result, "retrieval", None), "decision", None)
            no_answer_pass = (
                not getattr(pipeline_result, "evidence", [])
                and getattr(decision, "answerability_status", None) != "answerable"
                if pipeline_result is not None
                else not selected
            )
            no_answer_correct += int(no_answer_pass)
            results.append({
                "case_id": case["case_id"],
                "query": query,
                "answerable": False,
                "retrieved_count": len(selected),
                "final_evidence_count": len(getattr(pipeline_result, "evidence", [])) if pipeline_result is not None else len(selected),
                "answerability_status": getattr(decision, "answerability_status", None),
                "decision_reason": getattr(decision, "decision_reason", None),
                "no_answer_pass": no_answer_pass,
            })
            continue
        units = _term_units(case.get("expected_terms") or [])
        expected_source = str(case.get("expected_source") or "")
        location_chunk_ids = _location_gold_chunk_ids(chunks, case)
        location_case_count += int(bool(location_chunk_ids))
        first_source_rank = next(
            (
                rank
                for rank, hit in enumerate(selected, start=1)
                if expected_source == Path(str(hit.chunk.source_path)).name
            ),
            None,
        )
        first_location_rank = next(
            (
                rank
                for rank, hit in enumerate(selected, start=1)
                if hit.chunk.chunk_id in location_chunk_ids
            ),
            None,
        )
        source_reciprocal_ranks.append(1.0 / first_source_rank if first_source_rank else 0.0)
        if location_chunk_ids:
            location_reciprocal_ranks.append(1.0 / first_location_rank if first_location_rank else 0.0)
        for cutoff in source_hits:
            if first_source_rank and first_source_rank <= cutoff:
                source_hits[cutoff] += 1
            if location_chunk_ids and first_location_rank and first_location_rank <= cutoff:
                location_hits[cutoff] += 1
        first_complete_rank = None
        evidence_first_complete_rank = None
        coverage_by_rank: list[float] = []
        evidence_coverage_by_rank: list[float] = []
        for rank in range(1, top_k + 1):
            text = "\n".join(
                hit.chunk.content
                for hit in selected[:rank]
                if expected_source == Path(str(hit.chunk.source_path)).name
            )
            coverage = _coverage(text, units)
            coverage_by_rank.append(coverage)
            if coverage >= 1.0 and first_complete_rank is None:
                first_complete_rank = rank
            evidence_text = "\n".join(hit.chunk.content for hit in selected[:rank])
            evidence_coverage = _coverage(evidence_text, units)
            evidence_coverage_by_rank.append(evidence_coverage)
            if evidence_coverage >= 1.0 and evidence_first_complete_rank is None:
                evidence_first_complete_rank = rank
        reciprocal_ranks.append(1.0 / first_complete_rank if first_complete_rank else 0.0)
        evidence_reciprocal_ranks.append(1.0 / evidence_first_complete_rank if evidence_first_complete_rank else 0.0)
        for cutoff in hits:
            if first_complete_rank and first_complete_rank <= cutoff:
                hits[cutoff] += 1
            if evidence_first_complete_rank and evidence_first_complete_rank <= cutoff:
                evidence_hits[cutoff] += 1
        coverage_at_5.append(coverage_by_rank[4] if len(coverage_by_rank) >= 5 else (coverage_by_rank[-1] if coverage_by_rank else 0.0))
        evidence_coverage_at_5.append(evidence_coverage_by_rank[4] if len(evidence_coverage_by_rank) >= 5 else (evidence_coverage_by_rank[-1] if evidence_coverage_by_rank else 0.0))
        results.append({
            "case_id": case["case_id"],
            "dataset": case.get("dataset"),
            "question_type": case.get("question_type"),
            "query": query,
            "first_complete_rank": first_complete_rank,
            "evidence_first_complete_rank": evidence_first_complete_rank,
            "first_source_rank": first_source_rank,
            "first_location_rank": first_location_rank,
            "location_gold_chunk_count": len(location_chunk_ids),
            "coverage@5": round(coverage_at_5[-1], 4),
            "evidence_coverage@5": round(evidence_coverage_at_5[-1], 4),
            "required_units": units,
            "top_hits": [
                {
                    "rank": index,
                    "chunk_id": hit.chunk.chunk_id,
                    "ordinal": hit.chunk.ordinal,
                    "source_name": Path(str(hit.chunk.source_path)).name,
                    "section_path": hit.chunk.section_path,
                    "score": hit.score,
                    "method": hit.metadata.get("retrieval_method"),
                    "neighbor_of": hit.metadata.get("neighbor_of"),
                }
                for index, hit in enumerate(selected, start=1)
            ],
        })
    answerable_denominator = max(1, answerable_count)
    return {
        "label": label,
        "total_cases": len(cases),
        "answerable_cases": answerable_count,
        "Hit@1": round(hits[1] / answerable_denominator, 4),
        "Hit@3": round(hits[3] / answerable_denominator, 4),
        "Hit@5": round(hits[5] / answerable_denominator, 4),
        "MRR": round(sum(reciprocal_ranks) / answerable_denominator, 4),
        "Recall@5": round(sum(coverage_at_5) / answerable_denominator, 4),
        "EvidenceHit@1": round(evidence_hits[1] / answerable_denominator, 4),
        "EvidenceHit@3": round(evidence_hits[3] / answerable_denominator, 4),
        "EvidenceHit@5": round(evidence_hits[5] / answerable_denominator, 4),
        "EvidenceMRR": round(sum(evidence_reciprocal_ranks) / answerable_denominator, 4),
        "EvidenceRecall@5": round(sum(evidence_coverage_at_5) / answerable_denominator, 4),
        "SourceHit@1": round(source_hits[1] / answerable_denominator, 4),
        "SourceHit@3": round(source_hits[3] / answerable_denominator, 4),
        "SourceHit@5": round(source_hits[5] / answerable_denominator, 4),
        "SourceMRR": round(sum(source_reciprocal_ranks) / answerable_denominator, 4),
        "LocationHit@1": round(location_hits[1] / location_case_count, 4) if location_case_count else None,
        "LocationHit@3": round(location_hits[3] / location_case_count, 4) if location_case_count else None,
        "LocationHit@5": round(location_hits[5] / location_case_count, 4) if location_case_count else None,
        "LocationMRR": round(sum(location_reciprocal_ranks) / location_case_count, 4) if location_case_count else None,
        "location_cases": location_case_count,
        "no_answer_pass": no_answer_correct,
        "latency_ms": round((perf_counter() - started) * 1000),
        "results": results,
    }


def _term_units(terms: list[str]) -> list[str]:
    units: list[str] = []
    for term in terms:
        parts = re.split(r"[+/]", str(term))
        for part in parts:
            normalized = _normalize(part)
            if normalized and normalized not in units:
                units.append(normalized)
    return units


def _audit_gold_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case in cases:
        units = _term_units(case.get("expected_terms") or [])
        expected_answer = str(case.get("expected_answer") or "")
        missing = [unit for unit in units if unit not in _normalize(expected_answer)]
        if not case.get("answerable"):
            issue = "no_answer_policy_case"
            eligible = False
        elif missing:
            issue = "abstract_or_ungrounded_expected_terms"
            eligible = False
        else:
            issue = "clean_literal_gold"
            eligible = True
        records.append({
            "case_id": case.get("case_id"),
            "literal_score_eligible": eligible,
            "issue": issue,
            "missing_normalized_units": missing,
            "has_source_location": bool(str(case.get("source_location") or "").strip()),
            "has_expected_source": bool(case.get("expected_source")),
        })
    counts: dict[str, int] = {}
    for record in records:
        counts[record["issue"]] = counts.get(record["issue"], 0) + 1
    return {
        "case_count": len(records),
        "issue_counts": counts,
        "literal_score_eligible_count": sum(record["literal_score_eligible"] for record in records),
        "source_location_complete_count": sum(record["has_source_location"] for record in records),
        "records": records,
    }


def _failure_taxonomy(mode: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    cases_by_id = {str(case.get("case_id")): case for case in cases}
    buckets: dict[str, list[str]] = {
        "passed_literal_hit_at_5": [],
        "gold_literal_scoring_defect": [],
        "source_or_version_routing": [],
        "section_or_table_location_miss": [],
        "fragmented_multi_span_evidence": [],
        "no_answer_gate_failure": [],
    }
    for result in mode.get("results") or []:
        case_id = str(result.get("case_id") or "")
        case = cases_by_id.get(case_id) or {}
        if not case.get("answerable"):
            if not result.get("no_answer_pass"):
                buckets["no_answer_gate_failure"].append(case_id)
            continue
        if float(result.get("coverage@5") or 0.0) >= 1.0:
            buckets["passed_literal_hit_at_5"].append(case_id)
            continue
        units = _term_units(case.get("expected_terms") or [])
        expected = _normalize(str(case.get("expected_answer") or ""))
        if any(unit not in expected for unit in units):
            buckets["gold_literal_scoring_defect"].append(case_id)
        elif not result.get("first_source_rank") or int(result["first_source_rank"]) > 5:
            buckets["source_or_version_routing"].append(case_id)
        elif not result.get("first_location_rank") or int(result["first_location_rank"]) > 5:
            buckets["section_or_table_location_miss"].append(case_id)
        else:
            buckets["fragmented_multi_span_evidence"].append(case_id)
    return {
        "counts": {name: len(case_ids) for name, case_ids in buckets.items()},
        "case_ids": buckets,
    }


def _normalize(value: str) -> str:
    value = str(value or "").casefold().replace("*", "")
    value = re.sub(r"[\s，。；、：:（）()【】\[\]“”‘’～~—–_.-]", "", value)
    return value


def _coverage(text: str, units: list[str]) -> float:
    normalized = _normalize(text)
    if not units:
        return 1.0
    return sum(unit in normalized for unit in units) / len(units)


def _location_gold_chunk_ids(chunks: list[Any], case: dict[str, Any]) -> set[str]:
    """Resolve the workbook's human-authored source location to real chunks.

    The 60-question sheet uses clause/table locations rather than chunk ids.
    This resolver deliberately uses only the expected source and those location
    references; it never uses the generated answer or a model prediction.
    """
    expected_source = str(case.get("expected_source") or "")
    location = re.sub(r"\s+", "", str(case.get("source_location") or "")).upper()
    if not expected_source or not location:
        return set()
    source_chunks = [
        chunk for chunk in chunks
        if Path(str(chunk.source_path)).name == expected_source
    ]
    if not source_chunks:
        return set()
    references: list[tuple[str, str]] = []
    for value in re.findall(r"表([A-D]?\d+(?:\.\d+)*)", location):
        references.append(("table", value))
    for value in re.findall(r"§(\d+(?:\.\d+)*)", location):
        references.append(("section", value))
    for value in re.findall(r"附录([A-D](?:\.\d+)*)", location):
        references.append(("appendix", value))
    # Range endpoints do not repeat the prefix: A.3.14～A.3.16 / §7.1～§7.3.
    for value in re.findall(r"(?:～|~)([A-D]?\d+(?:\.\d+)*)", location):
        references.append(("reference", value))
    references = list(dict.fromkeys(references))
    table_references = [item for item in references if item[0] == "table"]
    if table_references:
        # A workbook location like ``附录 D 表 D.1`` identifies the table,
        # not every earlier sentence that merely says ``见附录 D``.
        references = table_references
    direct: set[str] = set()
    for chunk in source_chunks:
        compact = re.sub(r"\s+", "", str(chunk.content or "")).upper()
        for kind, value in references:
            needles = (
                (f"表{value}",)
                if kind == "table"
                else (f"{value}",)
            )
            if any(needle in compact for needle in needles):
                direct.add(chunk.chunk_id)
                break
    if not direct:
        return set()
    chunk_map = {chunk.chunk_id: chunk for chunk in source_chunks}
    expanded = set(direct)
    for chunk_id in list(direct):
        chunk = chunk_map.get(chunk_id)
        if chunk is None:
            continue
        if chunk.previous_chunk_id in chunk_map:
            expanded.add(chunk.previous_chunk_id)
        if chunk.next_chunk_id in chunk_map:
            expanded.add(chunk.next_chunk_id)
    return expanded


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Q/GDW 11809-2023 60-case retrieval quality without generation.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--after-only", action="store_true")
    parser.add_argument("--case-ids", default="")
    args = parser.parse_args()
    ids = {item.strip() for item in args.case_ids.split(",") if item.strip()}
    report = evaluate(Path(args.cases), Path(args.output), top_k=max(5, args.top_k), after_only=args.after_only, case_ids=ids or None)
    metric_keys = (
        "Hit@1", "Hit@3", "Hit@5", "MRR", "Recall@5",
        "EvidenceHit@1", "EvidenceHit@3", "EvidenceHit@5", "EvidenceMRR", "EvidenceRecall@5",
        "SourceHit@1", "SourceHit@3", "SourceHit@5", "SourceMRR",
        "LocationHit@1", "LocationHit@3", "LocationHit@5", "LocationMRR", "latency_ms",
    )
    summary = {"after": {key: report["after"][key] for key in metric_keys}}
    if report["before"] is not None:
        summary["before"] = {key: report["before"][key] for key in metric_keys}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
