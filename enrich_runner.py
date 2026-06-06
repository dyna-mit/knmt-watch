#!/usr/bin/env python3
"""Enrich every distinct practice in state.json with public web info.

Best-effort, cached, and incremental: each practice is enriched once and stored in
enrichment.json keyed by "practice|city". Re-runs only process practices not yet cached
(or with --force), so steady-state cost is tiny. The heavy first pass is meant to run
locally/overnight. Gentle by default (delay between practices).

Usage:
    python enrich_runner.py [--state state.json] [--out enrichment.json]
                            [--limit N] [--force] [--delay 3] [--refresh-data]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

from knmt import enrich

ROOT = Path(__file__).resolve().parent


def key(practice: str, city: str) -> str:
    return f"{(practice or '').strip().lower()}|{(city or '').strip().lower()}"


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def distinct_practices(vacancies: dict) -> list[tuple[str, str, str]]:
    """Unique (practice, city, a-contact-name) from active vacancies."""
    seen: dict[str, tuple[str, str, str]] = {}
    for v in vacancies.values():
        if v.get("removed_on"):
            continue
        practice = (v.get("practice") or "").strip()
        city = (v.get("city") or "").strip()
        if not practice:
            continue
        k = key(practice, city)
        if k not in seen:
            seen[k] = (practice, city, v.get("contact_name") or "")
    return list(seen.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", default=str(ROOT / "state.json"))
    ap.add_argument("--out", default=str(ROOT / "enrichment.json"))
    ap.add_argument("--data", default=str(ROOT / "docs" / "data.json"))
    ap.add_argument("--limit", type=int, default=None, help="max practices to enrich this run")
    ap.add_argument("--force", action="store_true", help="re-enrich even if cached")
    ap.add_argument("--max-age-days", type=int, default=None,
                    help="also re-enrich cached practices older than N days (for monthly refresh)")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between practices")
    ap.add_argument("--refresh-data", action="store_true",
                    help="rewrite docs/data.json with enrichment merged after the run")
    args = ap.parse_args()

    state = load_json(Path(args.state), {"vacancies": {}})
    cache = load_json(Path(args.out), {})
    practices = distinct_practices(state.get("vacancies", {}))

    def is_stale(rec: dict) -> bool:
        if args.max_age_days is None:
            return False
        ts = (rec or {}).get("enriched_at")
        if not ts:
            return True
        try:
            age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
                ts.replace("Z", "+00:00"))
            return age.days >= args.max_age_days
        except ValueError:
            return True

    def needs(p) -> bool:
        k = key(p[0], p[1])
        return args.force or k not in cache or is_stale(cache.get(k))

    todo = [p for p in practices if needs(p)]
    if args.limit:
        todo = todo[: args.limit]
    print(f"[enrich] {len(practices)} distinct practices, {len(todo)} to process "
          f"({len(cache)} already cached)")

    done = 0
    for practice, city, contact in todo:
        try:
            rec = enrich.enrich_practice(practice, city, contact)
        except Exception as exc:  # noqa: BLE001 - never let one practice kill the run
            print(f"  [FAIL] {practice} / {city}: {exc}")
            rec = {"practice": practice, "city": city, "error": str(exc),
                   "enriched_at": dt.datetime.utcnow().isoformat(timespec="seconds")}
        cache[key(practice, city)] = rec
        done += 1
        bits = []
        if rec.get("website"):
            bits.append("web")
        if rec.get("rating"):
            bits.append(f"★{rec['rating']}")
        if rec.get("kvk"):
            bits.append("kvk")
        if rec.get("big_checks"):
            bits.append(f"big×{len(rec['big_checks'])}")
        print(f"  [{done}/{len(todo)}] {practice[:38]:38} {city[:14]:14} {' '.join(bits)}")
        # Save incrementally so a long run is crash-safe.
        Path(args.out).write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        if args.delay and done < len(todo):
            time.sleep(args.delay)

    print(f"[enrich] done. cache now holds {len(cache)} practices -> {args.out}")

    if args.refresh_data:
        import yaml
        import watcher
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
        ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        watcher.write_dashboard(args.data, state["vacancies"], cfg, ts, enrichment=cache)
        print(f"[enrich] refreshed {args.data} with enrichment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
