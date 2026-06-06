# Practice Enrichment — build review (2026-06-06)

You asked me to enrich each scraped practice with web info — ratings/reviews, KvK,
financials, website + on-site info, and BIG-register checks of the people listed. Here's
what I built overnight, honestly scoped: what works well, what's best-effort, and what
isn't feasible.

## TL;DR
- **New:** every practice on the dashboard now has an **"Over de praktijk"** section
  (expand a card) with website, Zorgkaart rating + review count, KvK number (when found),
  emails, and a **BIG-register check** of dentists found on the practice's team page.
- **New controls:** sort by **Beste beoordeling** (rating) and a **"alleen met reviews"** filter.
- It's all driven by a new, cached, incremental enrichment pass (`enrich_runner.py` +
  `knmt/enrich.py` + `knmt/bigregister.py`), wired into the twice-weekly pipeline.

## What works well ✅
| Field | Source | Quality |
|---|---|---|
| **Website** | DuckDuckGo search → first non-directory domain | High hit rate (~100% in sampling) |
| **Rating + #reviews** | ZorgkaartNederland JSON-LD `aggregateRating` | Reliable when the practice is listed (~70%) |
| **Emails** | Scraped from the practice site | Good |
| **BIG: confirmed registrations** | bigregister.nl public search API, matched by name + initials | Trustworthy *positives* (e.g. "J.P. Ruiter → BIG 99057989202") |

## Best-effort / partial 🟡
- **KvK number** — pulled from the site footer/contact page when present. Many sites don't
  show it on pages we fetch, so it's often blank. A KvK number links out to kvk.nl.
- **BIG checks** — see the important caveat below. We **only assert "registered"** (green ✓
  with the BIG number). Everyone else found on the site is collapsed into *"N other names
  not auto-verified — check in BIG-register"* with a deep link. We never claim someone is
  *not* registered.
- **Team/people extraction** — names are scraped heuristically from the team/"over-ons"
  page. It's decent but imperfect: occasionally two adjacent people merge into one string,
  or a real dentist is missed. That's why non-confirmed names are summarised, not listed.

## Not feasible ❌
- **Financial info** — KvK annual accounts (turnover, equity, etc.) are behind KvK's **paid**
  API / paid document downloads. There's no free, lawful source, so this is out. We surface
  the KvK number + a kvk.nl link so you can pull the paid extract yourself if a practice
  really matters.

## ⚠️ Important caveat on the BIG checks (please read)
The BIG-register is the official, public registry, and protected titles (tandarts,
mondhygiënist) legally require a registration — so this check is legitimate due diligence.
**But** our name-matching is fuzzy (initials + surname against the register), so:
- A **green ✓ "registered"** is high-confidence — we matched the person to an *active*
  registration of the right profession and show the BIG number.
- A **non-match is NOT evidence someone is unregistered.** It usually means name spelling,
  a Dutch *tussenvoegsel*, foreign training, or our text-scraping merged/missed the name.
  So we deliberately **do not** display "not registered" — only "verify manually".
Treat the confirmed ✓ as a green flag, and use the BIG-register link to check the rest.

## How it's built
- `knmt/bigregister.py` — public BIG search API client (`/api/search/criteria`), name→initials
  parsing, namesake disambiguation, 2s throttle + backoff (the API rate-limits bulk use),
  in-process memoization. Returns only `registered` / `unverified` / `no_title_check`.
- `knmt/enrich.py` — website discovery (DDG, with a directory/aggregator blocklist),
  Zorgkaart rating, KvK + email regex, team-page person extraction (requires a job-title or
  BIG number next to a candidate name to avoid menu/form noise), orchestration.
- `enrich_runner.py` — dedupes practices from `state.json`, enriches uncached ones, writes
  `enrichment.json` (incremental, crash-safe), optionally refreshes `docs/data.json`.
- `watcher.py` merges `enrichment.json` into each vacancy in `docs/data.json` by
  `practice|city`. Dashboard renders it in the card's "Over de praktijk" section.
- Pipeline: the workflow runs `enrich_runner.py --limit 25` after the scrape
  (`continue-on-error` — web search can be flaky from CI), and commits `enrichment.json`.

## Known limitations / things to tweak together
1. **CI web search reliability** — DuckDuckGo may be blocked from GitHub's IPs, so new
   practices might not enrich on the cron run. The full pass was done locally (reliable IP);
   re-run `python enrich_runner.py --refresh-data` locally any time to fill gaps.
2. **BIG rate-limiting** — the register throttles bulk lookups; the run is paced at ~2s and
   backs off, so the full pass is slow (deliberately) but complete.
3. **Team extraction precision** — good but not perfect (HTML has no clean person structure).
   If you want, we can improve it per-site or add Google/Maps reviews as a second rating source.

## To run/refresh manually
```bash
. .venv/bin/activate
python enrich_runner.py --refresh-data            # enrich new practices, refresh dashboard
python enrich_runner.py --force --limit 5         # re-enrich a few from scratch
python -m http.server -d docs 8000                # preview the dashboard locally
```
