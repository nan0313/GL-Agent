"""Deterministic, domain-safe query normalization and tokenization."""

import re
import unicodedata
from typing import Any

from app.rag.models import MetadataFilter


_MEANINGLESS = {"", "?", "？", "!", "！", ".", "。", "请问", "帮我", "告诉我"}
_PROTECTED = re.compile(
    r"(?i)(?:(?:附录|表)\s*[a-d](?:\.\d+)+|q\s*/\s*gdw\s*\d+(?:[—-]\d+)?|"
    r"(?:project|subsystems|subsystem|solidmodels|solidmodel|transformmatrix|objectmodelpointer|"
    r"strainsections|strainsection|groups|group|sections|section|sch|blha|modleg|wiretype|grouptype|"
    r"isjumper|backstring|frontstring|entityname|basefamily|sysclassifyname|ifcfile|ifcguid)"
    r"(?:<n>)?(?:\.(?:num|cbm|dev|phm|mod|stl|sch|std|sld|spd|scd))?|"
    r"\*\.(?:gim|cbm|dev|phm|mod|stl|sch|std|sld|spd|scd|fam|ifc)|"
    r"[a-z]\d+(?:\.\d+)+|epsg:\d+|wgs84|webgl|qwengateway|[a-z]+gateway|"
    r"\d+(?:\.\d+)?\s*(?:km|kv|m|米|千米|公里|毫米|厘米))"
)
_TOKEN = re.compile(
    r"(?i)(?:__rag_term_\d+__|epsg:\d+|wgs84|webgl|qwengateway|[a-z]+gateway|\d+(?:\.\d+)?\s*(?:km|m|米|千米|公里|毫米|厘米)|[a-z][a-z0-9_-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff])"
)

_DOMAIN_SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("层次", ("层级",)),
    ("层级", ("层次",)),
    ("包括", ("包含", "组成")),
    ("包含", ("包括", "组成")),
    ("组成", ("包括", "包含")),
    ("移交", ("交付",)),
    ("交付", ("移交",)),
    ("规范", ("标准",)),
    ("标准", ("规范",)),
    ("坐标系", ("地理坐标系统", "坐标系统")),
    ("坐标系统", ("坐标系", "地理坐标系统")),
)
_KNOWN_PHRASES = (
    "输变电工程逻辑模型",
    "三维设计模型框架",
    "二次系统逻辑模型",
    "设备数字化设计描述模型",
    "模型及文件移交",
    "系统拓扑描述模型",
    "回路描述模型",
    "模型层级",
    "变电工程",
    "架空线路工程",
    "电缆工程",
    "基本图元",
    "物理模型",
    "逻辑模型",
    "几何模型",
    "五统一",
    "地理坐标系统",
    "坐标系",
    "高程基准",
)
_QUESTION_SUFFIX = re.compile(
    r"(?:包括|包含|组成)?(?:哪|有哪|是哪|分别是)?(?:四|三|两|几|多少)?(?:个)?(?:层次|层级|类|种|项|部分|模型)?(?:是什么|有哪些|是哪些|分别是什么|吗|呢)?$"
)


def normalize_query(query: str) -> str:
    value = unicodedata.normalize("NFKC", str(query or "")).strip()
    if not value:
        return ""
    value = value.replace("\u3000", " ")
    value = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", value)
    value = re.sub(r"[\u3001\u3002\uff0c\uff01\uff1f]", " ", value)
    value = re.sub(r"\s+", " ", value)
    # Lowercase only ASCII terms; Chinese, digits, units and protected names
    # remain semantically unchanged.
    return value.lower()


def tokenize_query(query: str) -> list[str]:
    normalized = normalize_query(query)
    if not normalized or normalized in _MEANINGLESS:
        return []
    protected: dict[str, str] = {}

    def protect(match: re.Match[str]) -> str:
        key = f"__rag_term_{len(protected)}__"
        protected[key] = match.group(0).replace(" ", "")
        return f" {key} "

    prepared = _PROTECTED.sub(protect, normalized)
    tokens: list[str] = []
    for item in _TOKEN.findall(prepared):
        if item in protected:
            tokens.append(protected[item])
        elif item.startswith("__rag_term_"):
            continue
        elif item not in _MEANINGLESS:
            tokens.append(item)
    # Include CJK bigrams in addition to characters. This makes the FTS5
    # token stream useful for Chinese while preserving exact terms and units.
    chinese = "".join(char for char in normalized if "\u4e00" <= char <= "\u9fff")
    tokens.extend(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        token = token.strip()
        if token and token not in seen:
            unique.append(token)
            seen.add(token)
    return unique


def index_tokens(text: str) -> list[str]:
    return tokenize_query(text)


def query_phrases(query: str) -> list[str]:
    """Return a small set of meaningful phrases, never every CJK substring."""
    normalized = normalize_query(query)
    compact = re.sub(r"\s+", "", normalized)
    known = [phrase for phrase in _KNOWN_PHRASES if phrase in compact]
    identifiers = re.findall(
        r"(?i)(?:q\s*/\s*gdw\s*\d+(?:[—-]\d+)?|\*\.(?:gim|cbm|dev|phm|mod|stl|sch|std|sld|spd|scd|fam|ifc)|"
        r"(?:project|subsystems|subsystem|solidmodels|solidmodel|transformmatrix|objectmodelpointer|strainsections|strainsection|groups|group|sections|section|sch|blha|ifcfile|ifcguid)(?:<n>)?(?:\.(?:num|cbm|dev|phm|mod|stl|sch|std|sld|spd|scd))?)",
        normalized,
    )
    identifiers = [re.sub(r"\s+", "", item) for item in identifiers]
    # Standards are frequently queried by a clause/table reference.  Keep the
    # reference as an exact lexical signal instead of reducing ``表 C.1`` or
    # ``附录 D`` to unrelated single Chinese characters.
    references = re.findall(
        r"(?i)(?:附录\s*[A-D](?:\.\d+)*|表\s*(?:[A-D](?:\.\d+)+|[A-D]?\d+(?:\.\d+)*)|§\s*\d+(?:\.\d+)*)",
        normalized,
    )
    references = [re.sub(r"\s+", "", item) for item in references]
    definitions = []
    for match in re.finditer(r"(?:什么是|何为)[‘’“\"']?([^?？。；，,‘’“\"']{2,20})", normalized):
        value = re.sub(r"\s+", "", match.group(1))
        if value:
            definitions.append(value)
    # Exact table/clause identifiers have higher retrieval value than a long
    # natural-language suffix, so reserve phrase slots for them first.
    references.sort(key=lambda value: (0 if value.startswith("表") else 1, -len(value), value))
    generic: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]{4,}", compact):
        core = re.sub(r"^(?:请问|根据|按照)", "", run)
        core = _QUESTION_SUFFIX.sub("", core)
        core = re.sub(r"^(?:输变电工程|这个文件中|这份文件中|文档中|规范中)", "", core)
        if 4 <= len(core) <= 24:
            generic.append(core)
    unique: list[str] = []
    ordered = references + identifiers + definitions + sorted(set(known), key=lambda value: (-len(value), value)) + sorted(set(generic), key=lambda value: (-len(value), value))
    for phrase in ordered:
        if phrase not in unique:
            unique.append(phrase)
    return unique[:4]


def structural_query_terms(query: str) -> list[str]:
    """Return exact identifiers suitable for a bounded structural recall pass.

    FTS is intentionally broad for Chinese natural language, but standards
    questions frequently contain table numbers, file suffixes, or schema
    field names whose exact spelling is much more discriminative.  These
    terms are extracted from the user query only; no answer text is added.
    """
    normalized = normalize_query(query)
    terms = query_phrases(normalized)
    terms.extend(
        re.findall(
            r"(?i)(?:\*\.(?:gim|cbm|dev|phm|mod|stl|sch|std|sld|spd|scd|fam|ifc|xml)|"
            r"[a-z][a-z0-9_]*(?:<n>)?(?:\.[a-z0-9_]+)?)",
            normalized,
        )
    )
    result: list[str] = []
    for term in terms:
        value = re.sub(r"\s+", "", str(term)).lower()
        if len(value) < 2 or value in _MEANINGLESS or value in result:
            continue
        result.append(value)
    return result[:16]


def rewrite_queries(query: str, *, maximum: int = 2) -> list[str]:
    """Conservative deterministic rewrites; no answer terms are invented."""
    normalized = normalize_query(query)
    phrases = query_phrases(normalized)
    candidates: list[str] = []
    compact = normalized.replace(" ", "")
    if "坐标系" in compact and "高程基准" in compact:
        candidates.append("地理坐标系统 高程基准 坐标系")
    if (
        "模型层级" in compact
        and "变电工程" in compact
        and "架空线路工程" in compact
        and "电缆工程" in compact
    ):
        candidates.append("变电工程模型层级 线路工程模型层级 电缆工程模型层级")
    if phrases:
        tail = "四个层次" if re.search(r"哪四个|四个层", normalized) else ""
        candidates.append(" ".join([phrases[0], tail]).strip())
    synonym_terms: list[str] = []
    for term, alternatives in _DOMAIN_SYNONYMS:
        if term in compact:
            synonym_terms.extend(alternatives)
    if phrases and synonym_terms:
        candidates.append(" ".join([phrases[0], *synonym_terms[:3]]))
    elif synonym_terms:
        candidates.append(" ".join([normalized, *synonym_terms[:3]]))
    result: list[str] = []
    for item in candidates:
        value = normalize_query(item)
        if value and value != normalized and value not in result:
            result.append(value)
    return result[: max(0, int(maximum))]


def is_enumeration_query(query: str) -> bool:
    normalized = normalize_query(query)
    return bool(re.search(r"(?:包括哪些|包含哪些|有哪些|有哪几|哪[一二三四五六七八九十两\d]+个|分别是什么|几类|几种|几项)", normalized))


def reference_definition_bonus(phrases: list[str], text: str) -> float:
    """Boost the defining table/appendix block over a cross-reference.

    Standards repeat references such as ``见表 C.1`` throughout the body.
    A reference match alone must not outrank the block that actually starts
    the table and carries its column schema.
    """
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    bonus = 0.0
    for phrase in phrases:
        value = re.sub(r"\s+", "", phrase).lower()
        if not value:
            continue
        index = compact.find(value)
        if index < 0:
            continue
        window = compact[index : index + 420]
        if value.startswith("表") and any(
            marker in window
            for marker in ("属性名称", "数据类型", "关键字", "序号", "名称说明", "电压等级备注")
        ):
            bonus = max(bonus, 6.0)
        elif value.startswith("附录") and any(
            marker in window[:100]
            for marker in ("规范性", "资料性")
        ):
            bonus = max(bonus, 3.0)
    return bonus


def validate_metadata_filter(filters: dict[str, Any] | MetadataFilter | None) -> MetadataFilter:
    if filters is None:
        return MetadataFilter()
    if isinstance(filters, MetadataFilter):
        return filters
    try:
        return MetadataFilter.model_validate(filters)
    except Exception as exc:
        raise ValueError("RAG_INVALID_METADATA_FILTER") from exc


def matches_metadata_filter(metadata: dict[str, Any], filters: MetadataFilter) -> bool:
    expected = filters.model_dump(exclude_none=True, exclude_defaults=True)
    for key, value in expected.items():
        if key == "tags":
            actual = set(metadata.get("tags") or [])
            if not set(value).issubset(actual):
                return False
            continue
        actual = metadata.get(key)
        if key == "section":
            path = metadata.get("section_path") or []
            if value not in path and value != metadata.get("section"):
                return False
        elif key in {"date_from", "date_to"}:
            # Date range filters are applied to the safe source modified date.
            modified = str(metadata.get("modified_at") or "")
            if key == "date_from" and modified and modified < str(value):
                return False
            if key == "date_to" and modified and modified > str(value):
                return False
        elif str(actual) != str(value):
            return False
    return True
