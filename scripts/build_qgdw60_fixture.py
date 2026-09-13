import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "runtime" / "analysis" / "spreadsheet" / "output" / "workbook.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "tests" / "fixtures" / "qgdw11809_2023_rag_gold_60.json"
EXPECTED_SOURCE = "GDW 11809—2023输变电工程三维设计模型数据交互规范.pdf"


def build_fixture(source: Path = DEFAULT_INPUT, target: Path = DEFAULT_OUTPUT) -> list[dict[str, object]]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    sheet = next(item for item in payload["sheets"] if item["name"] == "测试集")
    headers = [str(value) for value in sheet["values"][0]]
    cases: list[dict[str, object]] = []
    for values in sheet["values"][1:]:
        row = dict(zip(headers, values))
        answerable = str(row["可回答性"]).strip() == "可回答"
        expected_terms = [item.strip() for item in str(row["必须命中要点"] or "").split("；") if item.strip()]
        cases.append({
            "case_id": f"qgdw11809_2023_{int(row['ID']):03d}",
            "query": row["问题"],
            "answerable": answerable,
            "dataset": row["数据集"],
            "capability": row["一级能力"],
            "question_type": row["题型"],
            "difficulty": int(row["难度"]),
            "expected_answer": row["标准答案"],
            "expected_terms": expected_terms,
            "expected_source": EXPECTED_SOURCE if answerable else None,
            "source_location": row["来源定位"],
            "retrieval_span": row["预期检索跨度"],
            "distractor": row["主要干扰点"],
            "max_score": int(row["满分"]),
        })
    if len(cases) != 60 or sum(bool(item["answerable"]) for item in cases) != 59:
        raise ValueError("Unexpected Q/GDW test-set shape.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cases


if __name__ == "__main__":
    result = build_fixture()
    print(json.dumps({"status": "success", "case_count": len(result), "output": str(DEFAULT_OUTPUT)}, ensure_ascii=False))
