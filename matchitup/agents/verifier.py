"""Verifier agent: a deterministic grounding guardrail.

Every number in a generated card must exist in the moment's fact sheet
(or be one of a few structural constants such as "5 minutes"). Anything
else is treated as a possible hallucination and the card is rejected.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Devanagari, Arabic-Indic, Extended Arabic-Indic, Bengali digits -> ASCII.
_DIGIT_MAP = {}
for base in (0x0966, 0x0660, 0x06F0, 0x09E6):
    for i in range(10):
        _DIGIT_MAP[chr(base + i)] = str(i)
_TRANS = str.maketrans(_DIGIT_MAP)

_NUM_RE = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?")

# Numbers that describe the analysis itself, not the match (windows, squad sizes).
STRUCTURAL = {0.0, 1.0, 2.0, 3.0, 5.0, 10.0, 11.0, 45.0, 90.0, 100.0}


def extract_numbers(text: str) -> list[float]:
    text = text.translate(_TRANS)
    out = []
    for m in _NUM_RE.finditer(text):
        token = m.group().replace(",", ".")
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def allowed_numbers(facts: Iterable[dict[str, Any]], extra: Iterable[float] = ()) -> set[float]:
    allowed = set(STRUCTURAL)
    for f in facts:
        v = f.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        for candidate in (v, round(v), round(v, 1), round(v, 2), abs(v), round(abs(v))):
            allowed.add(float(candidate))
        if isinstance(v, float) and 0 < v < 1:   # xG 0.27 may be written as 27%
            allowed.add(float(round(v * 100)))
    allowed.update(float(x) for x in extra)
    return allowed


def verify(texts: Iterable[str], facts: list[dict[str, Any]], extra: Iterable[float] = ()) -> dict[str, Any]:
    """Return {grounded, numbers_checked, unsupported} for the given texts."""
    allowed = allowed_numbers(facts, extra)
    nums: list[float] = []
    for t in texts:
        if t:
            nums.extend(extract_numbers(t))
    unsupported = sorted({n for n in nums if not any(abs(n - a) < 0.051 for a in allowed)})
    return {"grounded": not unsupported, "numbers_checked": len(nums), "unsupported": unsupported}


def check_card(card: dict[str, Any], facts: list[dict[str, Any]], max_chars: int = 420) -> dict[str, Any]:
    texts = [card.get("headline", ""), card.get("body", ""), card.get("why", "")]
    result = verify(texts, facts)
    total_len = sum(len(t or "") for t in texts)
    result["length_ok"] = total_len <= max_chars
    result["has_headline"] = bool(card.get("headline"))
    result["passed"] = result["grounded"] and result["length_ok"] and result["has_headline"]
    return result
