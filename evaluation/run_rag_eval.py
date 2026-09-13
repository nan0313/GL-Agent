"""Run the project RAG pipeline against the evidence-unit gold set.

This evaluator intentionally disables answer generation: it measures retrieval and
answerability only, so it never needs a Qwen API key and never sends company
documents to an external model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag import LightweightReranker, LocalRetriever, RAGIndexStore  # noqa: E402
from app.rag.rag_pipeline import RAGPipeline  # noqa: E402


DEFAULT_DATASET = PROJECT_ROOT / "evaluation" / "datasets" / "rag_gold_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "evaluation" / "reports" / "rag_eval_v1.json"


class _NoGeneration:
    def answer(self, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError("EVALUATION_GENERATION_DISABLED")


def _normalize(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


def _source_name(hit: Any) -> str:
    return Path(str(hit.chunk.source_path or "")).name


def _span_matches_hit(span: dict[str, Any], hit: Any) -> bool:
    chunk = hit.chunk
    expected_source = str(span.get("source_name") or "")
    if expected_source and _source_name(hit) != expected_source:
        return False

    expected_chunk = str(span.get("chunk_id") or "")
    if expected_chunk and str(chunk.chunk_id) == expected_chunk:
        return True

    quote = _normalize(span.get("quote"))
    content = _normalize(chunk.content)
    return bool(quote and quote in content)


def _first_unit_rank(unit: dict[str, Any], hits: list[Any]) -> int | None:
    spans = list(unit.get("acceptable_spans") or [])
    for rank, hit in enumerate(hits, start=1):
        if any(_span_matches_hit(span, hit) for span in spans):
            return rank
    return None


def _percent(value: float) -> float:
    return round(value * 100.0, 2)


def evaluate(
    dataset_path: Path,
    output_path: Path,
    *,
    split: str,
    top_k: int,
    max_cases: int | None,
    require_human_locked: bool,
) -> dict[str, Any]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("RAG dataset must be a JSON list")
    if split != "all":
        cases = [case for case in cases if case.get("split") == split]
    if max_cases is not None:
        cases = cases[: max(0, max_cases)]
    if not cases:
        raise ValueError("No RAG cases selected")

    unlocked = [case["case_id"] for case in cases if case.get("review_status") != "human_locked"]
    if require_human_locked and unlocked:
        raise ValueError(f"{len(unlocked)} cases are not human_locked; first={unlocked[0]}")

    store = RAGIndexStore()
    store.ensure_built()
    stats = store.get_stats()
    retriever = LocalRetriever(index_store=store)
    reranker = LightweightReranker()
    pipeline = RAGPipeline(
        index_store=store,
        retriever=retriever,
        reranker=reranker,
        qwen_rag_answer=_NoGeneration(),
        top_k=top_k,
    )

    cutoffs = sorted({cutoff for cutoff in (1, 3, 5, top_k) if cutoff <= top_k})
    answerable_results: list[dict[str, Any]] = []
    negative_results: list[dict[str, Any]] = []
    latencies_ms: list[float] = []
    started = perf_counter()

    for index, case in enumerate(cases, start=1):
        case_start = perf_counter()
        result = pipeline.answer(
            str(case["query"]),
            top_k=top_k,
            request_id=f"rag-eval-{case['case_id']}",
        )
        latency_ms = round((perf_counter() - case_start) * 1000.0, 2)
        latencies_ms.append(latency_ms)
        selected = list(result.retrieved_hits)[:top_k]
        decision = getattr(getattr(result, "retrieval", None), "decision", None)
        answerability_status = getattr(decision, "answerability_status", None)

        if bool(case.get("answerable")):
            unit_ranks = [
                {
                    "unit_id": unit.get("unit_id"),
                    "first_hit_rank": _first_unit_rank(unit, selected),
                }
                for unit in case.get("gold_units") or []
            ]
            recall = {}
            hit = {}
            complete_hit = {}
            for cutoff in cutoffs:
                matched = sum(
                    1
                    for item in unit_ranks
                    if item["first_hit_rank"] is not None and item["first_hit_rank"] <= cutoff
                )
                total_units = len(unit_ranks)
                recall[str(cutoff)] = matched / total_units if total_units else 0.0
                hit[str(cutoff)] = matched > 0
                complete_hit[str(cutoff)] = bool(total_units) and matched == total_units
            answerable_results.append(
                {
                    "case_id": case["case_id"],
                    "query": case["query"],
                    "gold_unit_count": len(unit_ranks),
                    "retrieved_count": len(selected),
                    "retrieved_chunk_ids": [str(item.chunk.chunk_id) for item in selected],
                    "unit_ranks": unit_ranks,
                    "evidence_recall": recall,
                    "hit": hit,
                    "complete_hit": complete_hit,
                    "answerability_status": answerability_status,
                    "latency_ms": latency_ms,
                }
            )
        else:
            no_answer_correct = not result.evidence and answerability_status != "answerable"
            negative_results.append(
                {
                    "case_id": case["case_id"],
                    "query": case["query"],
                    "no_answer_correct": no_answer_correct,
                    "answerability_status": answerability_status,
                    "decision_reason": getattr(decision, "decision_reason", None),
                    "final_evidence_count": len(result.evidence),
                    "retrieved_count": len(selected),
                    "latency_ms": latency_ms,
                }
            )
        print(f"[{index:03d}/{len(cases):03d}] {case['case_id']} {latency_ms:.0f} ms", flush=True)

    metrics_by_k: dict[str, Any] = {}
    for cutoff in cutoffs:
        key = str(cutoff)
        recalls = [item["evidence_recall"][key] for item in answerable_results]
        hits = [item["hit"][key] for item in answerable_results]
        complete = [item["complete_hit"][key] for item in answerable_results]
        metrics_by_k[key] = {
            "evidence_recall": round(mean(recalls), 6) if recalls else None,
            "evidence_recall_percent": _percent(mean(recalls)) if recalls else None,
            "hit_rate": round(mean(hits), 6) if hits else None,
            "hit_rate_percent": _percent(mean(hits)) if hits else None,
            "complete_hit_rate": round(mean(complete), 6) if complete else None,
            "complete_hit_rate_percent": _percent(mean(complete)) if complete else None,
        }

    negative_accuracy = (
        mean(item["no_answer_correct"] for item in negative_results) if negative_results else None
    )
    report = {
        "status": "completed",
        "metric_scope": "retrieval_and_answerability_only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "dataset_version": cases[0].get("dataset_version"),
        "split": split,
        "top_k": top_k,
        "case_count": len(cases),
        "answerable_count": len(answerable_results),
        "hard_negative_count": len(negative_results),
        "review_gate": {
            "required_human_locked": require_human_locked,
            "unlocked_case_count": len(unlocked),
            "safe_for_resume_claim": len(unlocked) == 0,
        },
        "index": {
            "provider": stats.get("provider"),
            "index_version": stats.get("index_version"),
            "docs_count": stats.get("docs_count"),
            "chunks_count": stats.get("chunks_count"),
        },
        "metrics_by_k": metrics_by_k,
        "hard_negative": {
            "no_answer_accuracy": round(negative_accuracy, 6) if negative_accuracy is not None else None,
            "no_answer_accuracy_percent": _percent(negative_accuracy) if negative_accuracy is not None else None,
        },
        "latency": {
            "mean_ms": round(mean(latencies_ms), 2),
            "max_ms": round(max(latencies_ms), 2),
            "total_seconds": round(perf_counter() - started, 2),
        },
        "metric_definition": {
            "evidence_recall_at_k": "Per answerable question: matched gold evidence units / all gold evidence units; macro averaged.",
            "hit_at_k": "Share of answerable questions with at least one matched gold evidence unit.",
            "complete_hit_at_k": "Share of answerable questions with every gold evidence unit matched.",
            "no_answer_accuracy": "Share of hard negatives rejected with no final evidence and a non-answerable decision.",
            "citation_correctness": "Not measured here; semantic claim-to-citation support requires a generated answer and a separate human or judge annotation.",
        },
        "answerable_results": answerable_results,
        "hard_negative_results": negative_results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG Evidence Recall@K / Hit@K")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--require-human-locked", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.top_k <= 0:
        raise SystemExit("--top-k must be positive")
    report = evaluate(
        args.dataset.resolve(),
        args.output.resolve(),
        split=args.split,
        top_k=args.top_k,
        max_cases=args.max_cases,
        require_human_locked=args.require_human_locked,
    )
    summary = {
        "status": report["status"],
        "case_count": report["case_count"],
        "metrics_by_k": report["metrics_by_k"],
        "hard_negative": report["hard_negative"],
        "safe_for_resume_claim": report["review_gate"]["safe_for_resume_claim"],
        "output": str(args.output.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
