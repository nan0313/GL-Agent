"""Combine GIS, RAG, and router evaluation reports into one auditable summary."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = PROJECT_ROOT / "evaluation" / "reports"


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _safe(report: dict[str, Any] | None) -> bool:
    return bool(report and report.get("review_gate", {}).get("safe_for_resume_claim"))


def _fmt(value: Any, suffix: str = "") -> str:
    return "未计算" if value is None else f"{value}{suffix}"


def build(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    gis = _load(args.gis_report.resolve())
    rag = _load(args.rag_report.resolve())
    router = _load(args.router_report.resolve())

    rag_at_k = None
    if rag:
        rag_at_k = rag.get("metrics_by_k", {}).get(str(args.rag_k))
    metrics = {
        "gis_e2e_task_success_rate_percent": (
            gis.get("metrics", {}).get("e2e_task_success_rate_percent") if gis else None
        ),
        "gis_executable_count": gis.get("metrics", {}).get("executable_count") if gis else None,
        "gis_abnormal_handling_accuracy_percent": (
            gis.get("metrics", {}).get("expected_handling_accuracy_percent") if gis else None
        ),
        "rag_k": args.rag_k,
        "rag_evidence_recall_percent": rag_at_k.get("evidence_recall_percent") if rag_at_k else None,
        "rag_hit_rate_percent": rag_at_k.get("hit_rate_percent") if rag_at_k else None,
        "rag_complete_hit_rate_percent": rag_at_k.get("complete_hit_rate_percent") if rag_at_k else None,
        "rag_answerable_count": rag.get("answerable_count") if rag else None,
        "rag_hard_negative_accuracy_percent": (
            rag.get("hard_negative", {}).get("no_answer_accuracy_percent") if rag else None
        ),
        "router_macro_f1_percent": router.get("quality", {}).get("macro_f1_percent") if router else None,
        "router_rule_coverage_percent": (
            router.get("rule_stage", {}).get("coverage_percent") if router else None
        ),
        "router_rule_precision_percent": (
            router.get("rule_stage", {}).get("precision_on_handled_percent") if router else None
        ),
        "router_projected_llm_call_reduction_percent": (
            router.get("llm_calls", {}).get("projected_reduction_percent") if router else None
        ),
        "router_actual_llm_call_reduction_percent": (
            router.get("llm_calls", {}).get("actual_reduction_percent") if router else None
        ),
        "router_case_count": router.get("case_count") if router else None,
    }
    reports_present = {"gis": gis is not None, "rag": rag is not None, "router": router is not None}
    safe_flags = {"gis": _safe(gis), "rag": _safe(rag), "router": _safe(router)}
    complete = all(reports_present.values()) and rag_at_k is not None
    safe_for_resume = complete and all(safe_flags.values()) and metrics["router_actual_llm_call_reduction_percent"] is not None
    summary = {
        "status": "completed" if complete else "incomplete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reports": {
            "gis": str(args.gis_report.resolve()),
            "rag": str(args.rag_report.resolve()),
            "router": str(args.router_report.resolve()),
        },
        "reports_present": reports_present,
        "review_safe_flags": safe_flags,
        "safe_for_resume_claim": safe_for_resume,
        "metrics": metrics,
        "notes": [
            "Projected LLM-call reduction is inferred from deterministic-rule misses; only actual reduction from hybrid mode is suitable for the final claim.",
            "A report is resume-safe only after the selected cases are human_locked and every runner-specific gate passes.",
        ],
    }

    lines = [
        "# EV WebGL Agent 评测指标汇总",
        "",
        f"生成时间：{summary['generated_at']}",
        "",
        f"状态：**{'可形成最终简历表述' if safe_for_resume else '测试/复核未完成，不应直接写入简历'}**",
        "",
        "| 指标 | 结果 | 样本范围 |",
        "|---|---:|---|",
        f"| GIS 严格端到端任务成功率 | {_fmt(metrics['gis_e2e_task_success_rate_percent'], '%')} | {_fmt(metrics['gis_executable_count'])} 条可执行任务 |",
        f"| GIS 异常处理正确率 | {_fmt(metrics['gis_abnormal_handling_accuracy_percent'], '%')} | 异常任务 |",
        f"| RAG Evidence Recall@{args.rag_k} | {_fmt(metrics['rag_evidence_recall_percent'], '%')} | {_fmt(metrics['rag_answerable_count'])} 条可回答问题 |",
        f"| RAG Hit@{args.rag_k} | {_fmt(metrics['rag_hit_rate_percent'], '%')} | 可回答问题 |",
        f"| RAG hard-negative 拒答正确率 | {_fmt(metrics['rag_hard_negative_accuracy_percent'], '%')} | hard negative |",
        f"| 路由 Macro-F1 | {_fmt(metrics['router_macro_f1_percent'], '%')} | {_fmt(metrics['router_case_count'])} 条五分类样本 |",
        f"| 规则覆盖率 / 命中精度 | {_fmt(metrics['router_rule_coverage_percent'], '%')} / {_fmt(metrics['router_rule_precision_percent'], '%')} | 路由集 |",
        f"| LLM 逻辑调用减少（实测） | {_fmt(metrics['router_actual_llm_call_reduction_percent'], '%')} | Hybrid 对比 LLM-only |",
        f"| LLM 逻辑调用减少（推算） | {_fmt(metrics['router_projected_llm_call_reduction_percent'], '%')} | 仅用于调试 |",
        "",
        "## 简历表述规则",
        "",
        "只有当本页状态变为“可形成最终简历表述”后，才使用最终数字。表述时同时写样本量、离线测试集及严格口径，避免把推算值写成实测值。",
    ]
    if safe_for_resume:
        lines.extend(
            [
                "",
                "## 可用候选表述",
                "",
                (
                    f"构建项目级离线评测体系，在 {metrics['gis_executable_count']} 条 GIS 可执行任务上实现严格端到端成功率 "
                    f"{metrics['gis_e2e_task_success_rate_percent']}%；在 {metrics['rag_answerable_count']} 条知识库问答上取得 "
                    f"Evidence Recall@{args.rag_k} {metrics['rag_evidence_recall_percent']}%；通过规则与模型混合路由，在 "
                    f"{metrics['router_case_count']} 条五分类测试集上保持 Macro-F1 {metrics['router_macro_f1_percent']}%，并将路由 LLM 逻辑调用减少 "
                    f"{metrics['router_actual_llm_call_reduction_percent']}%。"
                ),
            ]
        )
    return summary, "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one resume-metrics summary from evaluation reports")
    parser.add_argument("--gis-report", type=Path, default=REPORT_ROOT / "gis_e2e_eval_v1.json")
    parser.add_argument("--rag-report", type=Path, default=REPORT_ROOT / "rag_eval_v1.json")
    parser.add_argument("--router-report", type=Path, default=REPORT_ROOT / "router_eval_v1.json")
    parser.add_argument("--rag-k", type=int, default=5)
    parser.add_argument("--json-output", type=Path, default=REPORT_ROOT / "resume_metrics_v1.json")
    parser.add_argument("--md-output", type=Path, default=REPORT_ROOT / "resume_metrics_v1.md")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    summary, markdown = build(args)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.md_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.md_output.write_text(markdown, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"markdown={args.md_output.resolve()}")
    return 0 if summary["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
