"""Evaluate deterministic/hybrid/LLM-only routing on router_v1.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.router import AgentRouter  # noqa: E402
from app.llm.qwen_router import QwenRouter  # noqa: E402
from app.schemas.request import AgentChatRequest  # noqa: E402


DEFAULT_DATASET = PROJECT_ROOT / "evaluation" / "datasets" / "router_v1.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "evaluation" / "reports" / "router_eval_v1.json"
LABELS = ("anchor_task", "rag_qa", "document_task", "general_chat", "unknown")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class CountingQwenRouter:
    def __init__(self) -> None:
        self.inner = QwenRouter()
        self.logical_calls = 0
        self.successful_calls = 0
        self.failed_calls = 0

    def decide(self, request: AgentChatRequest):
        self.logical_calls += 1
        try:
            decision = self.inner.decide(request)
            self.successful_calls += 1
            return decision
        except Exception:
            self.failed_calls += 1
            raise


def _make_request(case: dict[str, Any]) -> AgentChatRequest:
    last_turn = case["turns"][-1]
    gis_context = dict(last_turn.get("gis_context") or {})
    return AgentChatRequest.model_validate(
        {
            "session_id": f"router-eval-{case['case_id']}",
            "conversation_id": f"router-eval-{case['case_id']}",
            "request_id": f"router-eval-{case['case_id']}",
            "user_id": "offline-evaluator",
            "role": "user",
            "query": last_turn["query"],
            "gis_context": gis_context,
        }
    )


def _has_attachment_context(case: dict[str, Any]) -> bool:
    state = case.get("attachment_state") or {}
    return bool(
        int(state.get("active_attachment_count") or 0) == 1
        or state.get("selected_attachment_ids")
    )


def _classification_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    confusion = {gold: {pred: 0 for pred in LABELS} for gold in LABELS}
    for row in rows:
        gold = row["gold_route_type"]
        prediction = row["predicted_route_type"]
        if gold in confusion and prediction in confusion[gold]:
            confusion[gold][prediction] += 1

    per_class: dict[str, Any] = {}
    f1_values: list[float] = []
    for label in LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[gold][label] for gold in LABELS if gold != label)
        fn = sum(confusion[label][pred] for pred in LABELS if pred != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        per_class[label] = {
            "support": sum(confusion[label].values()),
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }

    accuracy = sum(row["correct"] for row in rows) / len(rows) if rows else 0.0
    macro_f1 = sum(f1_values) / len(f1_values) if f1_values else 0.0
    return {
        "accuracy": round(accuracy, 6),
        "accuracy_percent": round(accuracy * 100.0, 2),
        "macro_f1": round(macro_f1, 6),
        "macro_f1_percent": round(macro_f1 * 100.0, 2),
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def evaluate(
    dataset_path: Path,
    output_path: Path,
    *,
    split: str,
    mode: str,
    max_cases: int | None,
    require_human_locked: bool,
) -> dict[str, Any]:
    cases = _read_jsonl(dataset_path)
    if split != "all":
        cases = [case for case in cases if case.get("split") == split]
    if max_cases is not None:
        cases = cases[: max(0, max_cases)]
    if not cases:
        raise ValueError("No router cases selected")

    unlocked = [case["case_id"] for case in cases if case.get("review_status") != "human_locked"]
    if require_human_locked and unlocked:
        raise ValueError(f"{len(unlocked)} cases are not human_locked; first={unlocked[0]}")

    counter = CountingQwenRouter()
    router = AgentRouter(qwen_router=counter)
    rows: list[dict[str, Any]] = []
    rule_handled = 0
    rule_correct = 0
    projected_hybrid_calls = 0
    started = perf_counter()

    for index, case in enumerate(cases, start=1):
        request = _make_request(case)
        has_document_context = _has_attachment_context(case)
        router._has_document_context = lambda _request, active=has_document_context: active  # type: ignore[method-assign]
        rule_decision = router._rule_decision(request)
        rule_is_handled = rule_decision.route_type != "unknown"
        rule_handled += int(rule_is_handled)
        projected_hybrid_calls += int(not rule_is_handled)
        rule_correct += int(rule_is_handled and rule_decision.route_type == case["gold_route_type"])

        error: str | None = None
        if mode == "rule-stage":
            decision = rule_decision
        elif mode == "hybrid":
            decision = router.decide(request)
        else:
            try:
                decision = counter.decide(request)
            except Exception as exc:
                decision = rule_decision.model_copy(
                    update={
                        "route_type": "unknown",
                        "reason": "LLM-only evaluation call failed",
                        "router": "evaluation_failure",
                    }
                )
                error = f"{type(exc).__name__}: {exc}"

        prediction = str(decision.route_type)
        row = {
            "case_id": case["case_id"],
            "query": request.query,
            "gold_route_type": case["gold_route_type"],
            "predicted_route_type": prediction,
            "correct": prediction == case["gold_route_type"],
            "rule_route_type": str(rule_decision.route_type),
            "rule_handled": rule_is_handled,
            "router": decision.router,
            "reason": decision.reason,
            "has_document_context": has_document_context,
            "error": error,
        }
        rows.append(row)
        print(
            f"[{index:03d}/{len(cases):03d}] {case['case_id']} "
            f"gold={case['gold_route_type']} pred={prediction}",
            flush=True,
        )

    quality = _classification_metrics(rows)
    rule_coverage = rule_handled / len(cases)
    rule_precision = rule_correct / rule_handled if rule_handled else 0.0
    llm_only_calls = len(cases)
    actual_hybrid_calls = counter.logical_calls if mode == "hybrid" else None
    projected_reduction = 1.0 - projected_hybrid_calls / llm_only_calls
    actual_reduction = (
        1.0 - actual_hybrid_calls / llm_only_calls
        if actual_hybrid_calls is not None
        else None
    )
    probe_prompt = counter.inner._build_prompt(_make_request(cases[0]))
    warnings: list[str] = []
    if "document_task" not in probe_prompt:
        warnings.append(
            "Current QwenRouter prompt only permits anchor_task/general_chat/rag_qa/unknown; "
            "it does not permit dataset label document_task."
        )
    safe_for_resume = (
        not unlocked
        and mode == "hybrid"
        and counter.failed_calls == 0
        and actual_reduction is not None
        and not warnings
    )
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "dataset_version": cases[0].get("dataset_version"),
        "split": split,
        "mode": mode,
        "case_count": len(cases),
        "label_counts": dict(Counter(case["gold_route_type"] for case in cases)),
        "review_gate": {
            "required_human_locked": require_human_locked,
            "unlocked_case_count": len(unlocked),
            "safe_for_resume_claim": safe_for_resume,
        },
        "quality": quality,
        "rule_stage": {
            "handled_count": rule_handled,
            "coverage": round(rule_coverage, 6),
            "coverage_percent": round(rule_coverage * 100.0, 2),
            "correct_among_handled": rule_correct,
            "precision_on_handled": round(rule_precision, 6),
            "precision_on_handled_percent": round(rule_precision * 100.0, 2),
        },
        "llm_calls": {
            "llm_only_logical_baseline": llm_only_calls,
            "projected_hybrid_calls_from_rule_misses": projected_hybrid_calls,
            "projected_reduction": round(projected_reduction, 6),
            "projected_reduction_percent": round(projected_reduction * 100.0, 2),
            "actual_hybrid_logical_calls": actual_hybrid_calls,
            "actual_hybrid_successful_calls": counter.successful_calls if mode == "hybrid" else None,
            "actual_hybrid_failed_calls": counter.failed_calls if mode == "hybrid" else None,
            "actual_reduction": round(actual_reduction, 6) if actual_reduction is not None else None,
            "actual_reduction_percent": round(actual_reduction * 100.0, 2) if actual_reduction is not None else None,
            "note": "Logical calls count entries into QwenRouter.decide, not HTTP retries inside the Qwen client.",
        },
        "warnings": warnings,
        "elapsed_seconds": round(perf_counter() - started, 2),
        "results": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate router quality and logical LLM-call reduction")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--mode", choices=("rule-stage", "hybrid", "llm-only"), default="rule-stage")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--require-human-locked", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = evaluate(
        args.dataset.resolve(),
        args.output.resolve(),
        split=args.split,
        mode=args.mode,
        max_cases=args.max_cases,
        require_human_locked=args.require_human_locked,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "mode": report["mode"],
                "case_count": report["case_count"],
                "quality": report["quality"],
                "rule_stage": report["rule_stage"],
                "llm_calls": report["llm_calls"],
                "safe_for_resume_claim": report["review_gate"]["safe_for_resume_claim"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
