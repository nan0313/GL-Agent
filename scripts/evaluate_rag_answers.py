import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.llm.qwen_rag_answer import QwenRAGAnswer  # noqa: E402
from app.rag import LightweightReranker, LocalRetriever, PromptBuilder  # noqa: E402
from scripts.evaluate_rag_retrieval import load_cases  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "rag_answer_eval.json"


def evaluate_answers(
    cases: list[dict[str, Any]],
    top_k: int = 3,
    real_qwen: bool = False,
) -> dict[str, Any]:
    retriever = LocalRetriever()
    reranker = LightweightReranker()
    prompt_builder = PromptBuilder()
    answerer = QwenRAGAnswer() if real_qwen else None
    results: list[dict[str, Any]] = []

    for case in cases:
        query = str(case["query"])
        retrieved = retriever.retrieve(query, top_k=top_k)
        reranked = reranker.rerank(query, retrieved, top_k=top_k)
        evidence = prompt_builder.build_evidence(reranked)
        prompt_context = prompt_builder.build_prompt_context(query, evidence) if evidence else {}
        evidence_payload = [item.model_dump() for item in evidence]

        answer = None
        error = None
        if real_qwen and evidence and answerer is not None:
            try:
                answer = answerer.answer(query=query, evidence=evidence, prompt_context=prompt_context)
            except Exception as exc:
                error = _safe_error(exc)

        results.append(
            {
                "case_id": case.get("case_id"),
                "query": query,
                "dry_run": not real_qwen,
                "should_have_evidence": bool(case.get("should_have_evidence")),
                "evidence": evidence_payload,
                "built_prompt_preview": _prompt_preview(prompt_context),
                "answer": answer,
                "error": error,
                "expected_keywords": case.get("expected_keywords", []),
                "keyword_hits": _keyword_hits(evidence_payload, case.get("expected_keywords", [])),
                "expected_behavior": case.get("expected_behavior"),
            }
        )

    return {
        "provider": "local_rag_v2",
        "real_qwen": real_qwen,
        "dry_run": not real_qwen,
        "top_k": top_k,
        "total_cases": len(cases),
        "results": results,
    }


def write_output(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _prompt_preview(prompt_context: dict[str, Any], max_chars: int = 800) -> dict[str, str]:
    if not prompt_context:
        return {}
    return {
        "system_prompt": str(prompt_context.get("system_prompt", ""))[:max_chars],
        "user_prompt": str(prompt_context.get("user_prompt", ""))[:max_chars],
        "evidence_context": str(prompt_context.get("evidence_context", ""))[:max_chars],
    }


def _keyword_hits(evidence_payload: list[dict[str, Any]], expected_keywords: list[str]) -> list[str]:
    text = json.dumps(evidence_payload, ensure_ascii=False)
    return [keyword for keyword in expected_keywords if keyword in text]


def _safe_error(error: Exception) -> str:
    text = str(error) or error.__class__.__name__
    for marker in ("api_key", "API key", "Authorization", "Bearer", "sk-"):
        if marker in text:
            return "Qwen answer evaluation failed; sensitive config was redacted."
    return text[:300]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate local_rag_v2 answer prompts; dry-run by default.")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--real-qwen", action="store_true", help="Actually call QwenRAGAnswer.")
    args = parser.parse_args()

    cases = load_cases(Path(args.cases))
    report = evaluate_answers(cases, top_k=args.top_k, real_qwen=args.real_qwen)
    write_output(report, Path(args.output))
    print(json.dumps({k: report[k] for k in ["provider", "dry_run", "real_qwen", "total_cases"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
