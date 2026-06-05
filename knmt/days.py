"""Best-effort extraction of required workdays from a vacancy's free text.

KNMT has no structured "days" field — practices mention days (if at all) in prose,
e.g. "2-3 dagen waaronder de donderdag", "ma, wo en/of do", "ma t/m vr", or just
"dagen in overleg". This parser is heuristic: it favours precision (full day names and
clear abbreviation chains/ranges) over recall, and separately flags "negotiable" phrasing.
"""
from __future__ import annotations

import re

ORDER = ["ma", "di", "wo", "do", "vr", "za", "zo"]
_FULL = {
    "maandag": "ma", "dinsdag": "di", "woensdag": "wo", "donderdag": "do",
    "vrijdag": "vr", "zaterdag": "za", "zondag": "zo",
}
_ABBR = set(ORDER)

# A day token = a full name or a 2-letter abbreviation.
_TOKEN = r"(?:maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag|ma|di|wo|do|vr|za|zo)"
_SEP = r"[\s,/&+]+|(?:\s+en/?of\s+)|(?:\s+en\s+)"
# Range: "<day> t/m <day>", "<day> tot (en met) <day>", "<day> - <day>".
_RANGE_RE = re.compile(
    rf"\b({_TOKEN})\s*(?:t/m|tot en met|tot|–|—|-)\s*({_TOKEN})\b", re.I
)
# Chain of 2+ tokens: "ma, wo en/of do", "ma/wo/vr".
_CHAIN_RE = re.compile(rf"\b{_TOKEN}\b(?:(?:{_SEP})\b{_TOKEN}\b)+", re.I)
_FULL_RE = re.compile(r"\b(maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)\b", re.I)
_TOKEN_RE = re.compile(rf"\b{_TOKEN}\b", re.I)

_NEGOTIABLE_RE = re.compile(
    r"in overleg|onderling overleg|bespreekbaar|flexibe|nader te bepalen|nader overeen|"
    r"\bn\.?t\.?b\.?\b|in onderling|dagen.{0,12}overleg",
    re.I,
)


def _code(tok: str) -> str:
    tok = tok.lower()
    return _FULL.get(tok, tok if tok in _ABBR else "")


def extract_workdays(text: str) -> tuple[list[str], bool]:
    """Return (sorted day codes like ['di','wo'], negotiable_bool).

    negotiable is True when the text uses 'in overleg'/'bespreekbaar'/etc., OR when no
    specific day could be detected at all (so the UI can treat it as unspecified).
    """
    if not text:
        return [], True
    t = text.lower()
    found: set[str] = set()

    # 1. Ranges (expand inclusive over the Mon→Sun order).
    for a, b in _RANGE_RE.findall(t):
        ca, cb = _code(a), _code(b)
        if ca in ORDER and cb in ORDER and ORDER.index(ca) <= ORDER.index(cb):
            found.update(ORDER[ORDER.index(ca):ORDER.index(cb) + 1])

    # 2. Full day names are always reliable.
    for m in _FULL_RE.findall(t):
        found.add(_code(m))

    # 3. Abbreviation chains (2+ tokens together) — avoids lone-abbrev false positives
    #    like "zo" (= "so") or English "do".
    for chunk in _CHAIN_RE.findall(t):
        for tok in _TOKEN_RE.findall(chunk):
            c = _code(tok)
            if c:
                found.add(c)

    days = [d for d in ORDER if d in found]
    negotiable = bool(_NEGOTIABLE_RE.search(t)) or not days
    return days, negotiable
