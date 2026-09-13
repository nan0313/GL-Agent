import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag import LightweightReranker, LocalRetriever, PromptBuilder, RAGIndexStore  # noqa: E402


DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "runtime" / "verification" / "rag_retrieval_eval.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("RAG evaluation cases must be a non-empty JSON list.")
    case_ids = [str(item.get("case_id") or "") for item in data]
    if any(not case_id for case_id in case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("Each RAG evaluation case needs a unique case_id.")
    for item in data:
        if not str(item.get("query") or "").strip():
            raise ValueError(f"RAG evaluation case {item.get('case_id')} needs a query.")
        if not isinstance(item.get("answerable", item.get("should_have_evidence")), bool):
            raise ValueError(f"RAG evaluation case {item.get('case_id')} needs answerable.")
    return data


def evaluate_cases(
    cases: list[dict[str, Any]],
    top_k: int = 5,
    knowledge_root: Path | None = None,
    candidate_limit: int = 24,
) -> dict[str, Any]:
    store = RAGIndexStore(knowledge_dir=knowledge_root) if knowledge_root else None
    retriever = LocalRetriever(index_store=store) if store else LocalRetriever()
    reranker = LightweightReranker()
    prompt_builder = PromptBuilder(max_evidences=max(5, top_k), max_excerpt_chars=1200, max_context_chars=12000)
    results: list[dict[str, Any]] = []
    reciprocal_ranks: list[float] = []
    hit_counts = {1: 0, 3: 0, 5: 0}
    recall_at_5: list[float] = []
    answerable_count = 0

    for case in cases:
        query = str(case["query"])
        retrieved = retriever.retrieve(query, top_k=candidate_limit, candidate_k=candidate_limit)
        reranked = reranker.rerank(query, retrieved, top_k=len(retrieved))
        evidence = prompt_builder.build_evidence(reranked[: max(top_k, 5)])
        expected_sources = _expected_sources(case)
        expected_terms = list(case.get("expected_terms") or case.get("expected_keywords") or [])
        answerable = bool(case.get("answerable", case.get("should_have_evidence")))
        ranks = [index for index, hit in enumerate(reranked, start=1) if _matches_expected_source(hit.chunk.source_path, expected_sources)]
        first_rank = min(ranks) if ranks else None
        if answerable:
            answerable_count += 1
            reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)
            for cutoff in hit_counts:
                if first_rank and first_rank <= cutoff:
                    hit_counts[cutoff] += 1
            found_sources = {
                expected for expected in expected_sources
                if any(_matches_expected_source(hit.chunk.source_path, [expected]) for hit in reranked[:5])
            }
            recall_at_5.append(len(found_sources) / max(1, len(expected_sources)))
        payload = [item.model_dump() for item in evidence]
        text = json.dumps(payload, ensure_ascii=False)
        results.append({
            "case_id": case["case_id"], "query": query, "answerable": answerable,
            "question_type": case.get("question_type"),
            "expected_source": expected_sources, "expected_chunk_id": case.get("expected_chunk_id"),
            "expected_terms": expected_terms, "first_source_rank": first_rank,
            "source_hit@1": bool(first_rank and first_rank <= 1),
            "source_hit@3": bool(first_rank and first_rank <= 3),
            "source_hit@5": bool(first_rank and first_rank <= 5),
            "expected_term_hits": [term for term in expected_terms if term in text],
            "top_evidence": payload,
            "candidate_trace": [_hit_trace(hit, index + 1) for index, hit in enumerate(reranked[:10])],
            "lexical_top_10": [
                _hit_trace(hit, int(hit.metadata.get("lexical_rank")))
                for hit in sorted(
                    (item for item in retrieved if item.metadata.get("lexical_rank") is not None),
                    key=lambda item: int(item.metadata["lexical_rank"]),
                )[:10]
            ],
            "dense_top_10": [
                _hit_trace(hit, int(hit.metadata.get("dense_rank")))
                for hit in sorted(
                    (item for item in retrieved if item.metadata.get("dense_rank") is not None),
                    key=lambda item: int(item.metadata["dense_rank"]),
                )[:10]
            ],
            "rrf_top_10": [
                _hit_trace(hit, index + 1)
                for index, hit in enumerate(sorted(retrieved, key=lambda item: -float(item.metadata.get("fusion_score") or 0.0))[:10])
            ],
            "reranker_top_10": [_hit_trace(hit, index + 1) for index, hit in enumerate(reranked[:10])],
        })

    report = {
        "provider": "local_rag_v3", "top_k": top_k, "candidate_limit": candidate_limit,
        "total_cases": len(cases), "answerable_cases": answerable_count,
        "Hit@1": _ratio(hit_counts[1], answerable_count),
        "Hit@3": _ratio(hit_counts[3], answerable_count),
        "Hit@5": _ratio(hit_counts[5], answerable_count),
        "MRR": round(sum(reciprocal_ranks) / max(1, len(reciprocal_ranks)), 4),
        "Recall@5": round(sum(recall_at_5) / max(1, len(recall_at_5)), 4),
        "results": results,
    }
    return report


def write_output(report: dict[str, Any], json_output: Path) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _expected_sources(case: dict[str, Any]) -> list[str]:
    value = case.get("expected_source")
    if value:
        return [str(value)] if isinstance(value, str) else [str(item) for item in value]
    return [str(item) for item in case.get("expected_evidence_source") or case.get("gold_sources") or []]


def _matches_expected_source(source: str, expected_sources: list[str]) -> bool:
    source_name = Path(str(source)).name
    return any(expected in str(source) or expected == source_name for expected in expected_sources)


def _hit_trace(hit: Any, rank: int) -> dict[str, Any]:
    return {
        "rank": rank, "source_name": Path(hit.chunk.source_path).name,
        "chunk_id": hit.chunk.chunk_id, "ordinal": hit.chunk.ordinal,
        "section_path": hit.chunk.section_path, "score": hit.score,
        "lexical_rank": hit.metadata.get("lexical_rank"),
        "dense_rank": hit.metadata.get("dense_rank"),
        "fusion_score": hit.metadata.get("fusion_score"),
        "rerank_bonus": hit.metadata.get("rerank_bonus"),
        "rerank_provider": hit.metadata.get("rerank_provider"),
        "phrase_title_match": hit.metadata.get("phrase_title_match"),
        "phrase_section_match": hit.metadata.get("phrase_section_match"),
        "phrase_content_match": hit.metadata.get("phrase_content_match"),
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate grounded retrieval without calling Qwen.")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--knowledge-root", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-limit", type=int, default=24)
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    args = parser.parse_args()
    report = evaluate_cases(
        load_cases(Path(args.cases)), top_k=args.top_k,
        knowledge_root=Path(args.knowledge_root) if args.knowledge_root else None,
        candidate_limit=args.candidate_limit,
    )
    write_output(report, Path(args.json_output))
    print(json.dumps({key: report[key] for key in ("total_cases", "answerable_cases", "Hit@1", "Hit@3", "Hit@5", "MRR", "Recall@5")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
