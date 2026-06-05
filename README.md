# KNMT Tandarts Vacancy Watcher

Monitors the [KNMT vacancy bank](https://knmt.nl/vacatures) for **Tandarts** positions and gives you two things, both free and serverless:

1. **A browsable dashboard** (GitHub Pages) — search, filter by region / dienstverband / hours, read the full posting, and see **distance / driving time from your location**.
2. **A daily Telegram push** telling you **what changed** since yesterday (new ✚ / removed ✖).

One scheduled job (GitHub Actions, runs even when your PC is off) scrapes KNMT, fetches detail pages **incrementally** (only new/changed postings), geocodes each practice city, diffs against the previous run, updates the dashboard data, and notifies you.

```
watcher.py            # entrypoint: scrape → incremental detail → geocode → diff → notify → write
knmt/                 # scrape.py, detail.py, geocode.py, store.py, notify.py, http.py
config.yaml           # your filters (Tandarts is fixed; everything else configurable)
state.json            # the "database": every seen vacancy + geocode cache (committed by CI)
docs/                 # GitHub Pages dashboard (index.html, app.js, style.css, data.json)
.github/workflows/daily.yml
```

## How it works

- **Job type** is hardcoded to `vacature_type:Tandarts`. All other facets (region, contract,
  type samenwerking, praktijk type) are set in `config.yaml` and applied server-side via KNMT's
  own URL filters. `hours_min` and title keywords are applied client-side.
- **Incremental scraping:** each listing card carries a "gewijzigd" date. A detail page is
  fetched only when a posting is new, its date changed, or we don't yet have its text — so daily
  runs are light.
- **Geocoding:** practice **city** → lat/lng via OpenStreetMap Nominatim, cached by city name in
  `state.json` (so only a handful of new lookups per day; well within Nominatim's policy).
- **Travel time:** the dashboard computes straight-line distance instantly from your location.
  For real **driving time**, paste a free OpenRouteService key into the dashboard (stored only in
  your browser).

## One-time setup

### 1. Telegram bot (the daily push)
1. In Telegram, message **@BotFather** → `/newbot` → follow prompts → copy the **bot token**.
2. Send any message to your new bot (so it's allowed to message you).
3. Get your **chat id**: message **@userinfobot**, or open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id`.

### 2. Create the GitHub repo
```bash
git init && git add . && git commit -m "Initial KNMT vacancy watcher"
gh repo create knmt-watch --private --source . --push   # or create via the web UI
```
Then in **Settings → Secrets and variables → Actions**, add:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 3. Enable the dashboard (GitHub Pages)
**Settings → Pages → Build and deployment → Deploy from a branch → `main` / `/docs`.**
Your dashboard URL becomes `https://<user>.github.io/<repo>/`. Put that URL in `config.yaml`
(`dashboard_url:`) so Telegram messages link to it. Open it on your phone and **Add to Home Screen**.

### 4. Configure & seed
- Edit `config.yaml` (regions, hours, keywords).
- Trigger the first run: **Actions → Daily KNMT vacancy check → Run workflow**.
  The first run sends a one-line "baseline set" Telegram message and seeds `state.json`.
  After that you only get change alerts. The daily 07:00 (NL) cron takes over automatically.

### 5. (Optional) Real driving time
Sign up free at [openrouteservice.org](https://openrouteservice.org/dev/#/signup), and on the
dashboard open **"Reistijd (auto) ⚙"** and paste the key. Without it, you still get straight-line km.

## Run locally
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

python watcher.py --dry-run          # print what would change; writes/sends nothing
python watcher.py --no-detail        # listings only (fast, no detail fetches)
python watcher.py                    # full run: updates state.json, docs/data.json, sends Telegram

# preview the dashboard
python -m http.server -d docs 8000   # then open http://localhost:8000
```
Telegram env vars (only needed for real sends):
```bash
export TELEGRAM_BOT_TOKEN=...; export TELEGRAM_CHAT_ID=...
```

## Notes
- `state.json` and `docs/data.json` are committed by the workflow — this gives you a free
  change history and is the "database".
- Be polite: the defaults rate-limit detail fetches and geocoding. The first run is the only
  heavy one (it fetches all current detail pages once).
