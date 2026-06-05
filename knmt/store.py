"""Load/save the JSON state file that acts as the database."""
from __future__ import annotations

import json
from pathlib import Path

STATE_VERSION = 1


def load_state(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"version": STATE_VERSION, "vacancies": {}, "geocache": {}}
    with p.open(encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("vacancies", {})
    data.setdefault("geocache", {})
    return data


def save_state(path: str | Path, state: dict) -> None:
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
    tmp.replace(p)
