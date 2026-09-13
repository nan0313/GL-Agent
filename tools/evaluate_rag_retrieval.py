"""Offline RAG retrieval benchmark; never calls Qwen or downloads models."""

import argparse
import json
from math import log2
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.config import get_rag_config  # noqa: E402
from app.rag.decision import evaluate_retrieval_decision  # noqa: E402
from app.rag.embeddings import LocalSentenceTransformerEmbeddingProvider  # noqa: E402
from app.rag.index_manager import RAGIndexManager  # noqa: E402
from app.rag.prompt_builder import PromptBuilder  # noqa: E402
from app.rag.reranker import LightweightReranker, LocalCrossEncoderReranker  # noqa: E402


DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "rag_retrieval_benchmark.json"
DEFAULT_KNOWLEDGE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "rag_knowledge"
DEFAULT_JSON = PROJECT_ROOT / "outputs" / "rag_retrieval_benchmark.json"
DEFAULT_MD = PROJECT_ROOT / "docs" / "RAG_RETRIEVAL_BENCHMARK.md"


def load_cases(
    path: Path = DEFAULT_CASES,
    knowledge_root: Path = DEFAULT_KNOWLEDGE_ROOT,
) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("Benchmark must be a non-empty JSON list")
    ids = [str(item.get("case_id")) for item in data]
    if any(not item or not item.get("query") for item in data) or len(set(ids)) != len(ids):
        raise ValueError("Each benchmark case needs a unique case_id and query")
    valid_sources = {item.name for item in knowledge_root.iterdir() if item.is_file()} if knowledge_root.exists() else set()
    for item in data:
        if not set(item.get("gold_sources") or []).issubset(valid_sources):
            raise ValueError(f"Invalid gold source in {item.get('case_id')}")
        if item.get("should_have_evidence") and not item.get("gold_sources"):
            raise ValueError(f"Positive benchmark case needs gold_sources: {item.get('case_id')}")
    return data


def evaluate_cases(
    cases: list[dict[str, Any]],
    top_k: int = 5,
    real_model: str | None = None,
    *,
    vector_backend: str | None = None,
    fixed_thresholds: dict[str, float] | None = None,
    knowledge_root: Path = DEFAULT_KNOWLEDGE_ROOT,
) -> dict[str, Any]:
    base = get_rag_config().model_copy(update={"knowledge_roots": [str(knowledge_root)]})
    selected_backend = vector_backend or base.vector_backend
    with TemporaryDirectory(prefix="rag-benchmark-") as directory:
        root = Path(directory)
        lexical_config = base.model_copy(update={
            "database_path": str(root / "lexical.sqlite3"),
            "index_path": str(root / "lexical-index"),
            "dense_enabled": False,
            "embedding_provider": "disabled",
            "hybrid_enabled": False,
        })
        fixture_config = base.model_copy(update={
            "database_path": str(root / "fixture.sqlite3"),
            "index_path": str(root / "fixture-index"),
            "dense_enabled": True,
            "embedding_provider": "fixture",
            "hybrid_enabled": True,
            "vector_backend": selected_backend,
        })
        dense_config = fixture_config.model_copy(update={
            "database_path": str(root / "dense.sqlite3"),
            "index_path": str(root / "dense-index"),
            "lexical_enabled": False,
            "hybrid_enabled": False,
        })
        lexical = RAGIndexManager(lexical_config)
        fixture = RAGIndexManager(fixture_config)
        dense = RAGIndexManager(dense_config)
        try:
            for manager in (lexical, fixture, dense):
                manager.rebuild("full")
            modes = {
                "lexical": (lexical, None),
                "dense_fixture": (dense, None),
                "hybrid_fixture": (fixture, None),
                "hybrid_fixture_lightweight_rerank": (fixture, LightweightReranker()),
            }
            reports = {name: _evaluate_mode(cases, manager, reranker, top_k, fixed_thresholds=fixed_thresholds) for name, (manager, reranker) in modes.items()}
            real_status = "not_requested"
            real_managers: list[RAGIndexManager] = []
            if real_model:
                real_provider = LocalSentenceTransformerEmbeddingProvider(real_model, base.embedding_device, base.embedding_batch_size)
                real_base = base.model_copy(update={
                    "database_path": str(root / "real-hybrid.sqlite3"),
                    "index_path": str(root / "real-hybrid-index"),
                    "dense_enabled": True,
                    "embedding_provider": "sentence_transformers",
                    "embedding_model": real_model,
                    "hybrid_enabled": True,
                    "vector_backend": selected_backend,
                })
                real_hybrid = RAGIndexManager(real_base, embedding_provider=real_provider)
                real_dense = RAGIndexManager(real_base.model_copy(update={
                    "database_path": str(root / "real-dense.sqlite3"),
                    "index_path": str(root / "real-dense-index"),
                    "lexical_enabled": False,
                    "hybrid_enabled": False,
                }), embedding_provider=real_provider)
                real_managers = [real_hybrid, real_dense]
                try:
                    for manager in real_managers:
                        manager.rebuild("full")
                    if not real_provider.health_check(load=False).get("available"):
                        real_status = real_provider.health_check(load=True).get("error_code", "unavailable")
                    else:
                        reports["lexical_real"] = _evaluate_mode(cases, lexical, None, top_k, fixed_thresholds=fixed_thresholds)
                        reports["dense_real"] = _evaluate_mode(cases, real_dense, None, top_k, fixed_thresholds=fixed_thresholds)
                        reports["hybrid_real"] = _evaluate_mode(cases, real_hybrid, None, top_k, fixed_thresholds=fixed_thresholds)
                        reports["hybrid_real_lightweight_rerank"] = _evaluate_mode(cases, real_hybrid, LightweightReranker(), top_k, fixed_thresholds=fixed_thresholds)
                        real_status = "evaluated"
                finally:
                    for manager in real_managers:
                        manager.close()
            semantic = LocalCrossEncoderReranker(base.rerank_model, candidate_limit=base.rerank_candidate_limit)
            if semantic.available:
                reports["hybrid_semantic_rerank"] = _evaluate_mode(cases, fixture, semantic, top_k, fixed_thresholds=fixed_thresholds)
                semantic_status = "evaluated"
            else:
                semantic_status = semantic.health_check().get("error_code", "unavailable")
            return {
                "schema_version": "rag-benchmark-v1",
                "case_count": len(cases),
                "fixture_embedding": True,
                "dense_real_model_evaluated": real_status == "evaluated",
                "real_model_status": real_status,
                "semantic_reranker_status": semantic_status,
                "vector_backend": selected_backend,
                "fixed_thresholds": fixed_thresholds,
                "calibration": _split_case_ids(cases),
                "modes": reports,
            }
        finally:
            for manager in (lexical, fixture, dense):
                manager.close()


def _evaluate_mode(cases: list[dict[str, Any]], manager: RAGIndexManager, reranker: Any | None, top_k: int, *, fixed_thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    calibration_cases, evaluation_cases = _split_cases(cases)
    evaluation_cases = evaluation_cases or cases
    thresholds = fixed_thresholds or _calibrate_thresholds(calibration_cases or cases, manager, top_k)
    metrics = _score_cases(evaluation_cases, manager, reranker, top_k, thresholds)
    metrics.update({
        "calibration_case_count": len(calibration_cases),
        "evaluation_case_count": len(evaluation_cases),
        "calibration_case_ids": [str(item["case_id"]) for item in calibration_cases],
        "evaluation_case_ids": [str(item["case_id"]) for item in evaluation_cases],
        "calibrated_thresholds": thresholds,
        "calibration_metrics": _score_cases(calibration_cases, manager, reranker, top_k, thresholds) if calibration_cases else {},
    })
    return metrics


def _score_cases(cases: list[dict[str, Any]], manager: RAGIndexManager, reranker: Any | None, top_k: int, thresholds: dict[str, float]) -> dict[str, Any]:
    positive = [item for item in cases if item.get("should_have_evidence")]
    negative = [item for item in cases if not item.get("should_have_evidence")]
    hits_at = {1: 0, 3: 0, 5: 0}
    reciprocal = 0.0
    ndcg = 0.0
    no_answer_correct = 0
    false_positive = 0
    duplicate_total = 0
    evidence_total = 0
    latencies: list[float] = []
    case_results: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    statuses: dict[str, int] = {}
    candidate_recall = 0
    rerankable_failures = 0
    non_rerankable_failures = 0
    for case in cases:
        started = perf_counter()
        candidate_hits = manager.search(str(case["query"]), top_k=max(10, top_k, 5), candidate_limit=24)
        hits = candidate_hits[: max(top_k, 5)]
        if reranker is not None:
            hits = reranker.rerank(str(case["query"]), hits, top_k=max(top_k, 5))
        evidence = PromptBuilder(max_evidences=top_k).build_evidence(hits[:top_k])
        decision = evaluate_retrieval_decision(
            str(case["query"]),
            hits,
            evidence,
            minimum_relevance=thresholds["minimum_relevance"],
            dense_min_similarity=thresholds["dense_min_similarity"],
        )
        if decision.answerability_status != "answerable":
            evidence = []
        reasons[decision.decision_reason] = reasons.get(decision.decision_reason, 0) + 1
        statuses[decision.answerability_status] = statuses.get(decision.answerability_status, 0) + 1
        latencies.append((perf_counter() - started) * 1000)
        gold = set(case.get("gold_sources") or [])
        candidate_sources = {item.chunk.relative_location for item in candidate_hits if item.chunk.relative_location}
        if case.get("should_have_evidence") and candidate_sources.intersection(gold):
            candidate_recall += 1
        elif case.get("should_have_evidence") and reranker is not None:
            non_rerankable_failures += 1
        ranks = [index + 1 for index, item in enumerate(evidence) if item.source_name in gold]
        if case.get("should_have_evidence"):
            for limit in hits_at:
                if any(rank <= limit for rank in ranks):
                    hits_at[limit] += 1
            if ranks:
                reciprocal += 1 / ranks[0]
                if ranks[0] <= 3:
                    ndcg += 1 / log2(ranks[0] + 1)
            if reranker is not None and ranks and ranks[0] > 1 and candidate_sources.intersection(gold):
                rerankable_failures += 1
        elif not evidence:
            no_answer_correct += 1
        else:
            false_positive += 1
        content_keys = [(item.document_id, item.content) for item in evidence]
        duplicate_total += len(content_keys) - len(set(content_keys))
        evidence_total += len(content_keys)
        case_results.append({
            "case_id": case["case_id"],
            "relevant_rank": ranks[0] if ranks else None,
            "returned_count": len(evidence),
            "sources": [item.source_name for item in evidence],
            "retrieval_method": evidence[0].retrieval_method if evidence else "none",
            "answerability_status": decision.answerability_status,
            "decision_reason": decision.decision_reason,
        })
    positive_count = len(positive)
    negative_count = len(negative)
    no_answer_precision = no_answer_correct / max(1, no_answer_correct + false_positive)
    no_answer_recall = no_answer_correct / max(1, negative_count)
    sorted_latencies = sorted(latencies)
    return {
        "positive_count": positive_count,
        "negative_count": negative_count,
        "recall@1": round(hits_at[1] / max(1, positive_count), 4),
        "recall@3": round(hits_at[3] / max(1, positive_count), 4),
        "recall@5": round(hits_at[5] / max(1, positive_count), 4),
        "mrr": round(reciprocal / max(1, positive_count), 4),
        "ndcg@3": round(ndcg / max(1, positive_count), 4),
        "no_answer_precision": round(no_answer_precision, 4),
        "no_answer_recall": round(no_answer_recall, 4),
        "false_positive_rate": round(false_positive / max(1, negative_count), 4),
        "evidence_duplicate_rate": round(duplicate_total / max(1, evidence_total), 4),
        "candidate_recall@10": round(candidate_recall / max(1, positive_count), 4),
        "rerankable_failure_count": rerankable_failures,
        "non_rerankable_failure_count": non_rerankable_failures,
        "decision_reasons": reasons,
        "answerability_statuses": statuses,
        "latency_p50_ms": round(_percentile(sorted_latencies, 0.50), 3),
        "latency_p95_ms": round(_percentile(sorted_latencies, 0.95), 3),
        "case_results": case_results,
    }


def _calibrate_thresholds(cases: list[dict[str, Any]], manager: RAGIndexManager, top_k: int) -> dict[str, float]:
    base = manager.config
    lexical_values = sorted({round(float(value), 4) for value in (base.minimum_relevance, 2.5, 3.0, 4.0)})
    dense_values = sorted({round(float(value), 4) for value in (base.dense_min_similarity, 0.35, 0.45, 0.55)})
    best: tuple[tuple[float, ...], dict[str, float]] | None = None
    for lexical_threshold in lexical_values:
        for dense_threshold in dense_values:
            candidate = {"minimum_relevance": lexical_threshold, "dense_min_similarity": dense_threshold}
            metrics = _score_cases_without_calibration(cases, manager, top_k, candidate)
            recall_target = metrics["recall@3"] >= 0.9
            key = (
                1.0 if recall_target else 0.0,
                metrics["no_answer_precision"],
                metrics["no_answer_recall"],
                metrics["recall@3"],
                -metrics["false_positive_rate"],
            )
            if best is None or key > best[0]:
                best = (key, candidate)
    return best[1] if best else {"minimum_relevance": float(base.minimum_relevance), "dense_min_similarity": float(base.dense_min_similarity)}


def _score_cases_without_calibration(cases: list[dict[str, Any]], manager: RAGIndexManager, top_k: int, thresholds: dict[str, float]) -> dict[str, float]:
    positive = [item for item in cases if item.get("should_have_evidence")]
    negative = [item for item in cases if not item.get("should_have_evidence")]
    positive_hits = 0
    no_answer_correct = 0
    false_positive = 0
    for case in cases:
        hits = manager.search(str(case["query"]), top_k=max(top_k, 5), candidate_limit=24)
        evidence = PromptBuilder(max_evidences=top_k).build_evidence(hits[:top_k])
        decision = evaluate_retrieval_decision(str(case["query"]), hits, evidence, minimum_relevance=thresholds["minimum_relevance"], dense_min_similarity=thresholds["dense_min_similarity"])
        if decision.answerability_status != "answerable":
            evidence = []
        gold = set(case.get("gold_sources") or [])
        ranks = [index + 1 for index, item in enumerate(evidence) if item.source_name in gold]
        if case.get("should_have_evidence") and any(rank <= 3 for rank in ranks):
            positive_hits += 1
        elif not case.get("should_have_evidence") and not evidence:
            no_answer_correct += 1
        elif not case.get("should_have_evidence"):
            false_positive += 1
    return {
        "recall@3": positive_hits / max(1, len(positive)),
        "no_answer_precision": no_answer_correct / max(1, no_answer_correct + false_positive),
        "no_answer_recall": no_answer_correct / max(1, len(negative)),
        "false_positive_rate": false_positive / max(1, len(negative)),
    }


def _split_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic 60/40 split stratified by answerability."""
    calibration: list[dict[str, Any]] = []
    evaluation: list[dict[str, Any]] = []
    for answerable in (True, False):
        group = sorted((item for item in cases if bool(item.get("should_have_evidence")) is answerable), key=lambda item: str(item["case_id"]))
        for index, item in enumerate(group):
            (calibration if index % 5 < 3 else evaluation).append(item)
    calibration.sort(key=lambda item: str(item["case_id"]))
    evaluation.sort(key=lambda item: str(item["case_id"]))
    return calibration, evaluation


def _split_case_ids(cases: list[dict[str, Any]]) -> dict[str, Any]:
    calibration, evaluation = _split_cases(cases)
    return {
        "strategy": "case_id_sorted_stratified_mod5",
        "calibration_case_ids": [str(item["case_id"]) for item in calibration],
        "evaluation_case_ids": [str(item["case_id"]) for item in evaluation],
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * fraction))))
    return values[index]


def write_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# RAG Offline Retrieval Benchmark",
        "",
        f"- Cases: `{report['case_count']}`",
        f"- Real dense model status: `{report.get('real_model_status', 'not_requested')}`",
        "- Fixture embedding: test-only; not real semantic retrieval",
        f"- Calibration split: `{len(report.get('calibration', {}).get('calibration_case_ids', []))}` cases",
        f"- Evaluation split: `{len(report.get('calibration', {}).get('evaluation_case_ids', []))}` cases",
        "",
        "| mode | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@3 | no-answer precision | no-answer recall | FPR | p50 ms | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, item in report["modes"].items():
        lines.append(f"| {name} | {item['recall@1']} | {item['recall@3']} | {item['recall@5']} | {item['mrr']} | {item['ndcg@3']} | {item['no_answer_precision']} | {item['no_answer_recall']} | {item['false_positive_rate']} | {item['latency_p50_ms']} | {item['latency_p95_ms']} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--knowledge-root", default=str(DEFAULT_KNOWLEDGE_ROOT))
    parser.add_argument("--json-output", default=str(DEFAULT_JSON))
    parser.add_argument("--markdown-output", default=str(DEFAULT_MD))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--real-model", default=None, help="Explicit local path or cached model id; never downloaded by this tool")
    parser.add_argument("--vector-backend", default=None, choices=("python_cosine_fallback", "hnsw"))
    args = parser.parse_args()
    knowledge_root = Path(args.knowledge_root)
    report = evaluate_cases(
        load_cases(Path(args.cases), knowledge_root),
        max(1, args.top_k),
        args.real_model,
        vector_backend=args.vector_backend,
        knowledge_root=knowledge_root,
    )
    write_report(report, Path(args.json_output), Path(args.markdown_output))
    summary = {
        "case_count": report["case_count"],
        "modes": {
            name: {key: value for key, value in item.items() if key.startswith(("recall", "mrr", "ndcg", "no_answer", "false_positive", "latency"))}
            for name, item in report["modes"].items()
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
