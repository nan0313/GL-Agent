import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field


class NormalizedQuantity(BaseModel):
    source_text: str
    value: float
    canonical_unit: str
    dimension: Literal["length", "area", "angle"]
    start: int
    end: int
    confidence: float = 1.0


class NormalizedText(BaseModel):
    original_text: str
    normalized_text: str
    quantities: list[NormalizedQuantity] = Field(default_factory=list)


_DIGITS = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "兩": 2, "贰": 2, "貳": 2,
           "三": 3, "叁": 3, "參": 3, "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "陸": 6,
           "七": 7, "柒": 7, "八": 8, "捌": 8, "九": 9, "玖": 9}
_SMALL = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_LARGE = {"万": 10000, "萬": 10000}
_CN_CHARS = "零〇一壹二两兩贰貳三叁參四肆五伍六陆陸七柒八捌九玖十拾百佰千仟万萬点點"
_UNIT = r"平方公里|平方千米|平方米|平米|公里|千米|公尺|米|km²|km2|m²|m2|km|m|度"
_NUMBER = rf"(?:\d+(?:\.\d+)?|[{_CN_CHARS}]+|半)"
_QUANTITY_RE = re.compile(rf"(?P<num>{_NUMBER})(?P<prehalf>半)?\s*(?P<unit>{_UNIT})(?P<posthalf>半)?", re.I)


def chinese_number(value: str) -> float:
    value = value.strip()
    if value == "半":
        return 0.5
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)
    value = value.replace("點", "点")
    if "点" in value:
        whole, fraction = value.split("点", 1)
        whole_value = chinese_number(whole) if whole else 0.0
        digits = "".join(str(_DIGITS[c]) for c in fraction if c in _DIGITS)
        if not digits:
            raise ValueError(value)
        return whole_value + float("0." + digits)
    if all(c in _DIGITS for c in value):
        return float("".join(str(_DIGITS[c]) for c in value))
    total = section = number = 0
    for char in value:
        if char in _DIGITS:
            number = _DIGITS[char]
        elif char in _SMALL:
            unit = _SMALL[char]
            section += (number or 1) * unit
            number = 0
        elif char in _LARGE:
            section += number
            total += (section or 1) * _LARGE[char]
            section = number = 0
        else:
            raise ValueError(value)
    return float(total + section + number)


class ChineseTextNormalizer:
    """Conservatively normalizes explicit quantities; names and bare numbers remain untouched."""

    def normalize(self, text: str) -> NormalizedText:
        original = text or ""
        canonical = unicodedata.normalize("NFKC", original)
        # Command-scoped typo tolerance only.  Do not globally replace “清楚”.
        if "清楚" in canonical and "缓冲区" in canonical and "说明清楚" not in canonical:
            canonical = canonical.replace("清楚缓冲区", "清除缓冲区")
        quantities: list[NormalizedQuantity] = []
        replacements: list[tuple[int, int, str]] = []
        for match in _QUANTITY_RE.finditer(canonical):
            source = match.group(0)
            unit = match.group("unit").lower()
            try:
                value = chinese_number(match.group("num"))
            except ValueError:
                continue
            if match.group("prehalf") or match.group("posthalf"):
                value += 0.5
            if unit in {"公里", "千米", "km"}:
                value *= 1000
                canonical_unit, dimension = "m", "length"
            elif unit in {"米", "公尺", "m"}:
                canonical_unit, dimension = "m", "length"
            elif unit in {"平方公里", "平方千米", "km²", "km2"}:
                value *= 1_000_000
                canonical_unit, dimension = "m2", "area"
            elif unit in {"平方米", "平米", "m²", "m2"}:
                canonical_unit, dimension = "m2", "area"
            else:
                canonical_unit, dimension = "deg", "angle"
            q = NormalizedQuantity(source_text=source, value=value, canonical_unit=canonical_unit,
                                   dimension=dimension, start=match.start(), end=match.end())
            quantities.append(q)
            rendered = f"{value:g}{'米' if canonical_unit == 'm' else canonical_unit}"
            replacements.append((match.start(), match.end(), rendered))
        normalized = canonical
        for start, end, rendered in reversed(replacements):
            normalized = normalized[:start] + rendered + normalized[end:]
        return NormalizedText(original_text=original, normalized_text=normalized, quantities=quantities)
