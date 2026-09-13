"""Citation validation for model-generated RAG answers."""

import re
from typing import Any


_CITATION_RE = re.compile(r"\[(E\d+)\]")


def validate_citations(
    answer: str,
    citations: list[dict[str, str]],
) -> dict[str, Any]:
    allowed = {str(item.get("citation_id")): item for item in citations if item.get("citation_id")}
    invalid: list[str] = []
    used: list[str] = []

    def replace(match: re.Match[str]) -> str:
        citation_id = match.group(1)
        if citation_id not in allowed:
            invalid.append(citation_id)
            return ""
        if citation_id not in used:
            used.append(citation_id)
        return match.group(0)

    cleaned = _CITATION_RE.sub(replace, answer or "")
    used_set = set(used)
    mapping = [allowed[item] for item in used if item in allowed]
    coverage = "present" if used else ("missing" if (answer or "").strip() else "none")
    return {
        "answer": cleaned.strip(),
        "citations": mapping,
        "status": "invalid" if invalid else ("valid" if used else "none"),
        "invalid_citations": list(dict.fromkeys(invalid)),
        "used_citations": used,
        "provided_citations": list(allowed),
        "citation_coverage": coverage,
    }
