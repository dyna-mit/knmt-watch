#!/usr/bin/env python3
"""KNMT tandarts vacancy watcher.

Scrapes the KNMT vacancy bank (Tandarts only), incrementally fetches detail pages
for new/changed postings, geocodes their city, diffs against the previous run, pushes
changes to Telegram, and writes a dashboard dataset (docs/data.json).

Usage:
    python watcher.py [--dry-run] [--no-detail] [--config config.yaml]
                      [--state state.json] [--out docs/data.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import time
from pathlib import Path

import yaml

from knmt import days as days_mod
from knmt import detail as detail_mod
from knmt import geocode, notify, scrape, store

ROOT = Path(__file__).resolve().parent

DETAIL_FIELDS = (
    "practice", "city", "country", "employment_type", "date_posted",
    "description", "requirements", "what_we_offer",
    "contact_name", "contact_email", "contact_phone",
)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def annotate_days(rec: dict) -> None:
    """Derive required workdays from the posting text (no extra request needed)."""
    text = " ".join(str(rec.get(k) or "") for k in
                    ("title", "description", "requirements", "what_we_offer"))
    rec["days"], rec["days_negotiable"] = days_mod.extract_workdays(text)


def content_hash(record: dict) -> str:
    basis = "|".join(
        str(record.get(k, "")) for k in ("description", "requirements", "practice", "city",
                                         "employment_type", "date_posted", "what_we_offer")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def passes_client_filters(rec: dict, cfg: dict) -> bool:
    f = cfg.get("filters", {})
    hours_min = f.get("hours_min")
    if hours_min and (rec.get("hours_max") is None or rec["hours_max"] < hours_min):
        return False
    title = (rec.get("title") or "").lower()
    inc = [k.lower() for k in cfg.get("keyword_include") or []]
    if inc and not any(k in title for k in inc):
        return False
    exc = [k.lower() for k in cfg.get("keyword_exclude") or []]
    if exc and any(k in title for k in exc):
        return False
    return True


def run(args) -> int:
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    state = store.load_state(args.state)
    vacancies: dict = state["vacancies"]
    geocache: dict = state["geocache"]
    today = now_iso()

    prev_active = {s for s, v in vacancies.items() if not v.get("removed_on")}
    first_run = len(vacancies) == 0

    delay = cfg.get("request_delay_sec") or 0
    listings = scrape.fetch_listings(cfg.get("filters", {}), cfg.get("max_pages", 100), delay)
    print(f"[scrape] {len(listings)} tandarts listings found")

    current_slugs = set()
    details_fetched = 0
    max_new = cfg.get("max_new_details")

    for lst in listings:
        current_slugs.add(lst.slug)
        existing = vacancies.get(lst.slug)
        # Carry over listing-level fields every run (cheap, always fresh).
        base = {
            "slug": lst.slug, "url": lst.url, "title": lst.title,
            "changed_date": lst.changed_date, "work_area": lst.work_area,
            "work_areas": lst.work_areas,
            "vacancy_type": lst.vacancy_type, "hours": lst.hours,
            "hours_max": lst.hours_max,
        }

        needs_detail = (
            existing is None
            or existing.get("changed_date") != lst.changed_date
            or not existing.get("description")
        )
        if needs_detail and not args.no_detail:
            if max_new is not None and details_fetched >= max_new:
                needs_detail = False  # throttled this run; pick up next run

        if existing is None:
            rec = {**base, "first_seen": today}
        else:
            rec = {**existing, **base}
            rec.pop("removed_on", None)  # reappeared or still present

        if needs_detail and not args.no_detail:
            try:
                d = detail_mod.fetch_detail(lst.url)
                rec.update({k: d.get(k, rec.get(k, "")) for k in DETAIL_FIELDS})
                geo = geocode.geocode_city(rec.get("city", ""), rec.get("country", "nl"), geocache)
                rec["lat"] = geo["lat"] if geo else None
                rec["lng"] = geo["lng"] if geo else None
                rec["last_scraped"] = today
                rec["content_hash"] = content_hash(rec)
                details_fetched += 1
                if not args.quiet:
                    print(f"  [detail] {lst.slug} ({rec.get('city','?')})")
                if delay:
                    time.sleep(delay)
            except Exception as exc:  # noqa: BLE001
                print(f"  [detail] FAILED {lst.slug}: {exc}")

        rec["last_seen"] = today
        annotate_days(rec)
        vacancies[lst.slug] = rec

    # Mark vacancies that disappeared.
    for slug in prev_active - current_slugs:
        vacancies[slug]["removed_on"] = today

    print(f"[scrape] detail pages fetched this run: {details_fetched}")

    # Published set = active + passes client-side filters.
    def is_published(v: dict) -> bool:
        return not v.get("removed_on") and passes_client_filters(v, cfg)

    now_published = {s for s, v in vacancies.items() if is_published(v)}
    added = sorted(now_published - prev_active)
    removed = sorted(prev_active - now_published)

    if args.dry_run:
        print(f"[dry-run] published={len(now_published)} added={len(added)} removed={len(removed)}")
        for s in added[:20]:
            print(f"  + {vacancies[s].get('title')} ({vacancies[s].get('city')})")
        for s in removed[:20]:
            print(f"  - {vacancies[s].get('title')}")
        return 0

    # Persist state + dashboard dataset.
    state["generated_at"] = today
    store.save_state(args.state, state)
    write_dashboard(args.out, vacancies, cfg, today)

    # Notify.
    if first_run:
        notify.send(
            f"✅ KNMT vacancy-watcher actief. Basislijn: {len(now_published)} "
            f"tandarts-vacatures gevolgd. Je krijgt vanaf nu alleen wijzigingen."
        )
    elif added or removed:
        msg = notify.build_message(
            [vacancies[s] for s in added],
            [vacancies[s] for s in removed],
            cfg.get("dashboard_url", ""),
        )
        notify.send(msg)
    else:
        print("[notify] no changes today — nothing sent.")
    print(f"[done] published={len(now_published)} added={len(added)} removed={len(removed)}")
    return 0


def write_dashboard(out_path: str, vacancies: dict, cfg: dict, generated_at: str) -> None:
    items = []
    for v in vacancies.values():
        if v.get("removed_on") or not passes_client_filters(v, cfg):
            continue
        items.append({k: v.get(k) for k in (
            "slug", "url", "title", "city", "practice", "work_area", "work_areas",
            "vacancy_type",
            "hours", "hours_max", "employment_type", "date_posted", "changed_date",
            "days", "days_negotiable",
            "description", "requirements", "what_we_offer",
            "contact_name", "contact_email", "contact_phone",
            "lat", "lng", "first_seen",
        )})
    items.sort(key=lambda x: (x.get("date_posted") or ""), reverse=True)
    # Facet option lists computed from the actual data so the UI is always in sync.
    def distinct(field: str) -> list[str]:
        return sorted({(i.get(field) or "").strip() for i in items if i.get(field)})

    areas = sorted({a for i in items for a in (i.get("work_areas") or []) if a})
    payload = {
        "generated_at": generated_at,
        "count": len(items),
        "facets": {
            "work_area": areas,
            "employment_type": distinct("employment_type"),
        },
        "vacancies": items,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[dashboard] wrote {len(items)} vacancies to {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--state", default=str(ROOT / "state.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data.json"))
    ap.add_argument("--dry-run", action="store_true", help="print diff, write nothing, send nothing")
    ap.add_argument("--no-detail", action="store_true", help="skip detail-page fetches (listing only)")
    ap.add_argument("--quiet", action="store_true", help="less per-detail logging")
    return run(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
