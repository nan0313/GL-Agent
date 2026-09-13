"""Compare Python cosine and HNSW on the same real-embedding benchmark.

This tool is offline: it never calls Qwen and it never downloads a model. The
model must be supplied as an already available local path or model id.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.evaluate_rag_retrieval import (  # noqa: E402
    DEFAULT_KNOWLEDGE_ROOT,
    evaluate_cases,
    load_cases,
)


def _metrics(report: dict[str, Any], mode: str = "hybrid_real") -> dict[str, Any]:
    value = report["modes"].get(mode) or {}
    return {key: value.get(key) for key in ("recall@1", "recall@3", "recall@5", "mrr", "ndcg@3", "no_answer_precision", "no_answer_recall", "false_positive_rate", "evidence_duplicate_rate", "candidate_recall@10", "latency_p50_ms", "latency_p95_ms")}


def compare(
    cases: list[dict[str, Any]],
    model: str,
    top_k: int = 5,
    knowledge_root: Path = DEFAULT_KNOWLEDGE_ROOT,
) -> dict[str, Any]:
    base_thresholds = {"minimum_relevance": 2.0, "dense_min_similarity": 0.05}
    python_report = evaluate_cases(cases, top_k, model, vector_backend="python_cosine_fallback", fixed_thresholds=base_thresholds, knowledge_root=knowledge_root)
    hnsw_report = evaluate_cases(cases, top_k, model, vector_backend="hnsw", fixed_thresholds=base_thresholds, knowledge_root=knowledge_root)
    python_results = {str(item["case_id"]): item for item in python_report["modes"]["hybrid_real"]["case_results"]}
    hnsw_results = {str(item["case_id"]): item for item in hnsw_report["modes"]["hybrid_real"]["case_results"]}
    refusal_differences = 0
    rank_differences = 0
    overlap_total = 0.0
    overlap_cases = 0
    for case_id in sorted(set(python_results) & set(hnsw_results)):
        left = python_results[case_id]
        right = hnsw_results[case_id]
        if left.get("answerability_status") != right.get("answerability_status"):
            refusal_differences += 1
        if left.get("relevant_rank") != right.get("relevant_rank"):
            rank_differences += 1
        left_sources = set(left.get("sources") or [])
        right_sources = set(right.get("sources") or [])
        if left_sources or right_sources:
            overlap_total += len(left_sources & right_sources) / max(1, len(left_sources | right_sources))
            overlap_cases += 1
    return {
        "schema_version": "rag-vector-backend-comparison-v1",
        "case_count": len(cases),
        "embedding_model_type": "real_local_required",
        "thresholds": base_thresholds,
        "python_cosine_hybrid": _metrics(python_report),
        "hnsw_hybrid": _metrics(hnsw_report),
        "hnsw_backend": hnsw_report.get("vector_backend"),
        "top3_overlap_mean": round(overlap_total / max(1, overlap_cases), 4),
        "top3_overlap_case_count": overlap_cases,
        "ranking_difference_count": rank_differences,
        "refusal_difference_count": refusal_differences,
        "calibration": python_report.get("calibration"),
        "evaluation_case_count": python_report["modes"]["hybrid_real"].get("evaluation_case_count"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True)
    parser.add_argument("--real-model", required=True)
    parser.add_argument("--knowledge-root", default=str(DEFAULT_KNOWLEDGE_ROOT))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    knowledge_root = Path(args.knowledge_root)
    cases = load_cases(Path(args.cases), knowledge_root)
    print(json.dumps(compare(cases, args.real_model, max(1, args.top_k), knowledge_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
