"""Extract a start date (and optional end date) from a vacancy's free text.

KNMT postings express availability in prose: "per direct", "z.s.m.", "vanaf 1 januari",
"met ingang van maart 2026", or "in overleg". Locum/maternity posts may give an end
("tot 1 juli", "t/m augustus"). This is heuristic and returns both a human label and a
sortable key so the dashboard can sort/filter on it.

Sort keys: "per direct" -> "0000-00-00" (soonest), explicit dates -> ISO, unknown/
negotiable -> "9999-99-99" (last).
"""
from __future__ import annotations

import datetime as dt
import re

_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dec": 12,
}
_MONTH_NAMES = ["", "januari", "februari", "maart", "april", "mei", "juni", "juli",
                "augustus", "september", "oktober", "november", "december"]

SOON = "0000-00-00"
LATER = "9999-99-99"

_DIRECT = re.compile(
    r"per\s+direct|z\.?\s?s\.?\s?m\.?|zo\s+spoedig\s+mogelijk|zo\s+snel\s+mogelijk|"
    r"\bdirect\b|\bmeteen\b|per\s+heden|\bacuut\b|met\s+spoed|"
    r"op\s+korte\s+termijn|zo\s+spoedig", re.I)
_NEGOTIABLE = re.compile(
    r"in\s+overleg|nader\s+(?:te\s+bepalen|overeen)|n\.?t\.?b\.?|in\s+onderling\s+overleg|"
    r"datum\s+in\s+overleg|startdatum\s+in\s+overleg", re.I)
_TEMP = re.compile(
    r"waarnem|zwangerschapsverlof|zwangerschap|tijdelijk|vervanging|ziektevervanging|"
    r"voor\s+de\s+duur|interim|locum", re.I)

_MONTH_ALT = "|".join(_MONTHS)
# "1 januari 2026" / "januari 2026" / "01-01-2026" / "1-1-26"
_DMY = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_ALT})\s*(\d{{4}})?\b", re.I)
_MY = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{4}})\b", re.I)
_NUM = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b")
# Words that introduce a start date.
_START_CUE = (r"(?:per|vanaf|met\s+ingang\s+van|ingang|startdatum|start(?:datum)?\s*:?|"
              r"beschikbaar\s+(?:per|vanaf)|aanvang|ingangsdatum)\s*")
_END_CUE = r"(?:tot\s+en\s+met|t/m|tot|tot\s+aan|tot\s+uiterlijk|einddatum\s*:?)\s*"


def _year(month: int, year: int | None, today: dt.date) -> int:
    if year:
        return year if year > 100 else 2000 + year
    # No year given: assume the next occurrence of that month.
    return today.year if month >= today.month else today.year + 1


def _parse_date_at(text: str, today: dt.date) -> tuple[str, str] | None:
    """Parse the first date in `text` -> (iso_sort, label). Returns None if none."""
    m = _DMY.search(text)
    if m:
        day = int(m.group(1)); month = _MONTHS[m.group(2).lower()]
        year = _year(month, int(m.group(3)) if m.group(3) else None, today)
        return f"{year:04d}-{month:02d}-{day:02d}", f"{day} {_MONTH_NAMES[month]} {year}"
    m = _MY.search(text)
    if m:
        month = _MONTHS[m.group(1).lower()]; year = int(m.group(2))
        return f"{year:04d}-{month:02d}-01", f"{_MONTH_NAMES[month]} {year}"
    m = _NUM.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)); year = year if year > 100 else 2000 + year
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}", f"{day}-{month}-{year}"
    return None


def extract_period(text: str, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    t = re.sub(r"\s+", " ", text or "")
    out = {"start_label": None, "start_sort": LATER,
           "end_label": None, "end_sort": None, "temporary": bool(_TEMP.search(t))}

    # Start: prefer an explicit cued date, then "per direct", then "in overleg".
    cued = re.search(_START_CUE + r"([^.;\n]{0,30})", t, re.I)
    parsed = _parse_date_at(cued.group(1), today) if cued else None
    if parsed:
        out["start_sort"], out["start_label"] = parsed
    elif _DIRECT.search(t):
        out["start_sort"], out["start_label"] = SOON, "Per direct"
    elif _NEGOTIABLE.search(t):
        out["start_sort"], out["start_label"] = LATER, "In overleg"
    else:
        # Last resort: any date anywhere in the text.
        anyd = _parse_date_at(t, today)
        if anyd:
            out["start_sort"], out["start_label"] = anyd

    # End: an explicit end-date cue.
    em = re.search(_END_CUE + r"([^.;\n]{0,30})", t, re.I)
    end = _parse_date_at(em.group(1), today) if em else None
    if end:
        out["end_sort"], out["end_label"] = end
        out["temporary"] = True
    return out
