"""Enrich a dental practice with public web info: website, reviews, KvK, BIG checks.

Everything here is best-effort and degrades gracefully — any source that fails returns
empty/None rather than raising, so one bad lookup never sinks a practice's enrichment.
Results are meant to be cached per practice (see enrich_runner / watcher integration).
"""
from __future__ import annotations

import json
import re
import time
from html import unescape
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import bigregister, http

# Domains that are directories/aggregators/socials — never the practice's own site.
_AGGREGATORS = {
    "zorgkaartnederland.nl", "knmt.nl", "indeed.com", "indeed.nl", "linkedin.com",
    "facebook.com", "instagram.com", "yelp.com", "yelp.nl", "google.com", "goo.gl",
    "tandarts.nl", "nationalevacaturebank.nl", "werkenbijdetandarts.nl", "glassdoor.com",
    "dentalpost.nl", "youtube.com", "twitter.com", "x.com", "tiktok.com", "maps.google.com",
    "allecijfers.nl", "drimble.nl", "companyinfo.nl", "kvk.nl", "telefoonboek.nl",
    "detelefoongids.nl", "openingstijden.nl", "tandartsennet.nl", "solvari.nl",
    "vindtandarts.nl", "tandartsvergelijken.nl", "mijntandartsen.nl", "zorgvinder.nl",
    "independer.nl", "trustpilot.com", "trustpilot.nl", "goudengids.nl", "wikipedia.org",
    "dentalclinics.nl/en", "werkenbij.nl", "nationalevacaturebank.nl", "jobbird.com",
}
_TITLE_WORDS = [
    "tandarts", "mondhygiënist", "mondhygienist", "tandarts-implantoloog",
    "orthodontist", "kaakchirurg", "tandprotheticus", "praktijkmanager",
    "tandartsassistent", "preventieassistent", "balieassistent", "endodontoloog",
]
_PROTECTED = {"tandarts", "mondhygiënist", "mondhygienist", "orthodontist",
              "kaakchirurg", "endodontoloog", "tandarts-implantoloog"}

_NAME_RE = re.compile(
    r"\b((?:[A-Z]\.\s*){0,3}[A-Z][a-zà-ÿ]+"   # initials or first name
    r"(?:\s+(?:van|de|der|den|ter|te|dos|del|van der|van den|van de))*"
    r"\s+[A-Z][a-zà-ÿ'’-]+(?:\s+[A-Z][a-zà-ÿ'’-]+)?)\b"
)
# Tokens that disqualify a "name" — website chrome, treatments, dental jargon.
_NAME_STOP = {
    "tandarts", "tandartsen", "tandartspraktijk", "tandartspraktijken", "tandartsenpraktijk",
    "praktijk", "praktijken", "mondzorg", "mondhygiënist", "mondhygienist", "mondhygiëne",
    "behandeling", "behandelingen", "contact", "welkom", "hoofdmenu", "menu", "terug",
    "bekijk", "lees", "meer", "home", "afspraak", "online", "samenwerken", "werken",
    "kindertandheelkunde", "wortelkanaalbehandeling", "wortelkanaalbehandelingen",
    "implantologie", "orthodontie", "kroon", "kronen", "vulling", "vullingen", "beugel",
    "beugels", "gebit", "gebitsreiniging", "angsttandarts", "apexresectie", "parodontitis",
    "periodiek", "sealen", "spenen", "kaaskiezen", "kiezen", "tandenpoetsen", "patiënt",
    "patient", "patiëntendossier", "patientendossier", "tandartsverzekering",
    "tandartstarieven", "klachtenregeling", "medische", "esthetische", "esthetisch",
    "anti", "snurkbeugel", "bleken", "facings", "implantaat", "implantaten", "rijksen",
    "team", "over", "ons", "onze", "het", "wie", "zijn", "wij", "een", "kliniek",
    "spoed", "spoedgevallen", "tarieven", "vacatures", "nieuws", "blog", "veelgestelde",
    "vragen", "openingstijden", "route", "parkeren", "verwijzers", "verwijzing",
    "privacy", "privacyverklaring", "klachten", "klachtenprocedure", "klachtenregeling",
    "voorwaarden", "algemene", "disclaimer", "cookie", "cookies", "sitemap", "copyright",
    "kvk", "btw", "iban", "nieuwsbrief", "inschrijven", "inschrijfformulier", "formulier",
    # contact-form field labels
    "naam", "voornaam", "achternaam", "geboortedatum", "telefoonnummer", "telefoon",
    "emailadres", "email", "e-mail", "opmerking", "opmerkingen", "aanhef", "heer",
    "mevrouw", "anders", "bericht", "onderwerp", "adres", "postcode", "woonplaats",
    # regions / generic geo that show up in footers & directories
    "nederland", "holland", "noord", "zuid", "oost", "west", "brabant", "gelderland",
    "limburg", "flevoland", "drenthe", "friesland", "groningen", "overijssel", "zeeland",
    "regio", "types", "website", "tandprotheticus", "prothese", "gebitsprothesen",
    "klinisch", "expertise", "tandartsassistenten", "assistenten",
    # job-title tokens — a title inside a "name" means two people got merged in the text
    "preventieassistent", "preventieassistente", "balieassistent", "balieassistente",
    "tandartsassistent", "tandartsassistente", "mondzorgkundige", "praktijkmanager",
    "technicus", "tandtechnicus", "paro", "implantoloog", "orthodontist", "kaakchirurg",
    "endodontoloog", "praktijkhouder", "praktijkeigenaar", "stoelassistente", "stoelassistent",
}
# Person context that makes a nearby capitalised phrase plausibly a real name.
_HONORIFIC = re.compile(r"\b(drs?\.?|dr\.?|dhr\.?|mevr?\.?|mw\.?|de heer|mevrouw|tandarts|"
                        r"mondhygi[eë]nist|orthodontist|kaakchirurg|big[-\s:])", re.I)
_BIGNUM_RE = re.compile(r"\b(\d{11})\b")
_KVK_RE = re.compile(r"k\.?v\.?k\.?(?:[-\s]*nummer)?[:\s]*([0-9]{8})", re.I)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _ddg(query: str, max_results: int = 5) -> list[dict]:
    try:
        from ddgs import DDGS
        return list(DDGS().text(query, region="nl-nl", max_results=max_results))
    except Exception:  # noqa: BLE001
        return []


def _domain(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:  # noqa: BLE001
        return ""


def _fetch(url: str, max_bytes: int = 600_000) -> str:
    try:
        resp = http.get(url, retries=1, timeout=20)
        return resp.text[:max_bytes]
    except Exception:  # noqa: BLE001
        return ""


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ---------------- website ----------------
def find_website(practice: str, city: str) -> dict:
    """Find the practice's own website + basic on-site info (KvK, emails, description)."""
    out = {"website": None, "website_title": None, "description": None, "kvk": None,
           "emails": [], "practice_photo": None}
    results = _ddg(f"{practice} {city} tandarts", 6) or _ddg(f"{practice} {city}", 6)
    pick = None
    pname = re.sub(r"[^a-z0-9]", "", practice.lower())
    for r in results:
        href = r.get("href") or ""
        dom = _domain(href)
        if not dom or any(dom == a or dom.endswith("." + a) for a in _AGGREGATORS):
            continue
        # Prefer a domain that shares a token with the practice name.
        dtok = re.sub(r"[^a-z0-9]", "", dom.split(".")[0])
        if pick is None:
            pick = href
        if dtok and (dtok in pname or pname[:6] in dtok):
            pick = href
            break
    if not pick:
        return out
    out["website"] = f"{urlparse(pick).scheme}://{_domain_full(pick)}"
    html = _fetch(pick)
    if not html:
        return out
    soup = BeautifulSoup(html, "lxml")
    if soup.title:
        out["website_title"] = _clean(soup.title.get_text())
    md = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if md and md.get("content"):
        out["description"] = _clean(md["content"])[:300]
    og = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "og:image"})
    if og and og.get("content"):
        out["practice_photo"] = urljoin(pick, og["content"])
    text = soup.get_text(" ")
    m = _KVK_RE.search(text)
    if m:
        out["kvk"] = m.group(1)
    out["emails"] = sorted(set(e.lower() for e in _EMAIL_RE.findall(text)
                               if not e.lower().endswith((".png", ".jpg", ".gif"))))[:5]
    out["_html"] = html  # internal: reused for team extraction
    out["_url"] = pick
    return out


def _domain_full(url: str) -> str:
    return urlparse(url).netloc


# ---------------- reviews (Zorgkaart) ----------------
def find_zorgkaart(practice: str, city: str) -> dict:
    """Rating + review count from ZorgkaartNederland via its JSON-LD aggregateRating."""
    out = {"zorgkaart_url": None, "rating": None, "reviews": None}
    results = _ddg(f"site:zorgkaartnederland.nl {practice} {city}", 3)
    url = next((r.get("href") for r in results
                if "/zorginstelling/" in (r.get("href") or "")), None)
    if not url:
        return out
    out["zorgkaart_url"] = url
    html = _fetch(url)
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue
        for d in (data if isinstance(data, list) else [data]):
            agg = isinstance(d, dict) and d.get("aggregateRating")
            if agg:
                out["rating"] = agg.get("ratingValue")
                out["reviews"] = agg.get("ratingCount") or agg.get("reviewCount")
                return out
    return out


# ---------------- people / BIG ----------------
def _looks_like_name(s: str) -> bool:
    """A real person name: 2–3 tokens, none of them website chrome / dental jargon."""
    if not s or len(s) > 45 or any(ch.isdigit() for ch in s):
        return False
    toks = [t for t in re.split(r"\s+", s) if t]
    if not (2 <= len(toks) <= 4):
        return False
    for t in toks:
        if t.rstrip(".").lower() in _NAME_STOP:
            return False
    # At least one token must be a "real" word part (>1 letter, not all caps initials).
    return any(len(t.strip(".")) > 1 and t[:1].isupper() and t.lower() not in _NAME_STOP
               for t in toks)


def find_team_page(base_url: str, home_html: str) -> str | None:
    soup = BeautifulSoup(home_html, "lxml")
    want = ("team", "over-ons", "overons", "over_ons", "medewerkers", "tandartsen",
            "ons-team", "wie-zijn-wij", "het-team")
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        txt = a.get_text(" ", strip=True).lower()
        if any(w in href for w in want) or txt in ("team", "ons team", "over ons", "medewerkers"):
            u = a["href"]
            if u.startswith("/"):
                p = urlparse(base_url)
                u = f"{p.scheme}://{p.netloc}{u}"
            if u.startswith("http"):
                return u
    return None


def _img_is_photo(src: str) -> bool:
    """Reject logos/icons/sprites/placeholders — keep plausible portrait images."""
    s = (src or "").lower()
    if not s or s.startswith("data:"):
        return False
    bad = ("logo", "icon", "sprite", "favicon", "placeholder", "avatar-default",
           "whatsapp", "facebook", "instagram", "linkedin", "google", "map", "vlag",
           "flag", "banner", "header", "footer", "loading", "spinner")
    return not any(w in s for w in bad)


def _pick_img(node, base_url: str) -> str | None:
    """First plausible portrait <img> within `node` (handles lazy-load attrs)."""
    for img in node.find_all("img"):
        src = (img.get("src") or img.get("data-src") or img.get("data-lazy-src")
               or img.get("data-original") or "")
        if _img_is_photo(src):
            return urljoin(base_url, src)
    return None


def extract_people(html: str, base_url: str = "") -> list[dict]:
    """Pull (name, title, photo?, big_number?) from a team page — high precision, DOM-aware.

    Pass 1 (DOM): for each container that holds a job title + a person name, also grab the
    nearest portrait image -> people WITH photos. Pass 2 (text): names next to a title we
    might have missed -> people without photos. A name only counts with a title/BIG nearby,
    which kills menu/treatment/form noise.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    people: dict[str, dict] = {}

    # Pass 1: structural — blocks that look like a person card (title + name [+ photo]).
    for block in soup.find_all(["li", "article", "figure", "div", "section"]):
        if len(people) >= 10:
            break
        txt = _clean(block.get_text(" "))
        if len(txt) > 240 or not txt:
            continue  # whole-page wrappers: skip, we want small person cards
        low = txt.lower()
        title = next((t for t in _TITLE_WORDS if t in low), "")
        if not title:
            continue
        m = _NAME_RE.search(txt)
        if not m:
            continue
        name = re.sub(r"[-–\s]+$", "", m.group(1).strip())
        if not _looks_like_name(name) or name in people:
            continue
        big = _BIGNUM_RE.search(txt)
        people[name] = {"name": name, "title": title or "tandarts",
                        "photo": _pick_img(block, base_url),
                        "big_number": big.group(1) if big else None}

    # Pass 2: text fallback for names without a tidy card (no photo).
    text = _clean(html)
    for m in _NAME_RE.finditer(text):
        if len(people) >= 10:
            break
        name = re.sub(r"[-–\s]+$", "", m.group(1).strip())
        if not _looks_like_name(name) or name in people:
            continue
        a, b = m.start(), m.end()
        ctx = text[max(0, a - 40): min(len(text), b + 40)]
        title = next((t for t in _TITLE_WORDS if t in ctx.lower()), "")
        big = _BIGNUM_RE.search(ctx)
        if not title and not big:
            continue
        people[name] = {"name": name, "title": title or "tandarts", "photo": None,
                        "big_number": big.group(1) if big else None}
    return list(people.values())


def big_check_people(people: list[dict]) -> list[dict]:
    out = []
    for p in people:
        if p["title"].lower() not in _PROTECTED:
            continue  # only verify titles that legally require BIG
        verdict = bigregister.verify_person(p["name"], p["title"])
        if p.get("big_number"):
            verdict["site_big_number"] = p["big_number"]
        out.append(verdict)
        time.sleep(0.3)
    return out


_DAY_EN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_NL = {"monday": "Ma", "tuesday": "Di", "wednesday": "Wo", "thursday": "Do",
           "friday": "Vr", "saturday": "Za", "sunday": "Zo"}
_DAY_CODE = {"mo": "monday", "tu": "tuesday", "we": "wednesday", "th": "thursday",
             "fr": "friday", "sa": "saturday", "su": "sunday"}
_NL_DAY = {"maandag": "monday", "dinsdag": "tuesday", "woensdag": "wednesday",
           "donderdag": "thursday", "vrijdag": "friday", "zaterdag": "saturday",
           "zondag": "sunday", "ma": "monday", "di": "tuesday", "wo": "wednesday",
           "do": "thursday", "vr": "friday", "za": "saturday", "zo": "sunday"}
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")


def _mins(hhmm: str) -> int | None:
    m = _TIME_RE.search(hhmm or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _day_span(a: str, b: str, lookup: dict) -> list[str]:
    a, b = lookup.get(a), lookup.get(b)
    if a in _DAY_EN and b in _DAY_EN:
        i, j = _DAY_EN.index(a), _DAY_EN.index(b)
        return _DAY_EN[i:j + 1] if i <= j else []
    return []


def extract_opening_hours(html: str) -> dict:
    """Best-effort opening hours from JSON-LD or the 'Openingstijden' text.

    Returns {opening_hours: str|None, has_weekend: bool, has_evening: bool,
             latest_close: 'HH:MM'|None}. "Evening" = closes after 17:00.
    """
    out = {"opening_hours": None, "has_weekend": False, "has_evening": False, "latest_close": None}
    spec: list[tuple[str, int, int]] = []  # (day_en, open_min, close_min)

    # 1) JSON-LD openingHoursSpecification / openingHours string.
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue

        def walk(o):
            if isinstance(o, dict):
                ohs = o.get("openingHoursSpecification")
                for s in (ohs if isinstance(ohs, list) else [ohs] if ohs else []):
                    if not isinstance(s, dict):
                        continue
                    days = s.get("dayOfWeek") or []
                    days = days if isinstance(days, list) else [days]
                    op, cl = _mins(s.get("opens", "")), _mins(s.get("closes", ""))
                    for d in days:
                        dn = str(d).rsplit("/", 1)[-1].lower()
                        if dn in _DAY_EN and op is not None and cl is not None:
                            spec.append((dn, op, cl))
                oh = o.get("openingHours")
                for line in (oh if isinstance(oh, list) else [oh] if oh else []):
                    m = re.match(r"\s*([A-Za-z]{2})\s*-\s*([A-Za-z]{2})\s+(\d\d:\d\d)-(\d\d:\d\d)", str(line))
                    if m:
                        for dn in _day_span(m.group(1).lower(), m.group(2).lower(), _DAY_CODE):
                            spec.append((dn, _mins(m.group(3)), _mins(m.group(4))))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(data)

    # 2) Free-text fallback around an "Openingstijden" heading.
    if not spec:
        m = re.search(r"openingstijden", html, re.I)
        if m:
            chunk = _clean(html[m.start(): m.start() + 500]).lower()
            day_alt = "|".join(_NL_DAY)
            for mm in re.finditer(
                rf"({day_alt})(?:\s*(?:t/m|-|–|tot)\s*({day_alt}))?\s*:?\s*"
                rf"(\d{{1,2}}[:.]\d{{2}})\s*(?:tot|-|–|t/m)\s*(\d{{1,2}}[:.]\d{{2}})", chunk):
                d1, d2, t1, t2 = mm.groups()
                days = _day_span(d1, d2, _NL_DAY) if d2 else [_NL_DAY[d1]]
                op, cl = _mins(t1), _mins(t2)
                for dn in days:
                    if op is not None and cl is not None:
                        spec.append((dn, op, cl))

    if not spec:
        return out

    # Dedupe + compute flags.
    seen = {}
    for dn, op, cl in spec:
        seen[dn] = (op, cl)
    out["has_weekend"] = any(d in seen for d in ("saturday", "sunday"))
    closes = [cl for op, cl in seen.values()]
    if closes:
        latest = max(closes)
        out["latest_close"] = f"{latest // 60:02d}:{latest % 60:02d}"
        out["has_evening"] = latest > 17 * 60
    # Human summary in weekday order.
    parts = [f"{_DAY_NL[d]} {seen[d][0]//60:02d}:{seen[d][0]%60:02d}-{seen[d][1]//60:02d}:{seen[d][1]%60:02d}"
             for d in _DAY_EN if d in seen]
    out["opening_hours"] = ", ".join(parts)
    return out


def backfill_site(record: dict) -> bool:
    """Backfill photos + opening hours for an already-enriched record using its known
    website (no web search). Returns True if anything was added. Preserves other fields.
    """
    url = record.get("website")
    if not url:
        return False
    home = _fetch(url)
    if not home:
        return False
    soup = BeautifulSoup(home, "lxml")
    changed = False
    if not record.get("practice_photo"):
        og = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "og:image"})
        if og and og.get("content"):
            record["practice_photo"] = urljoin(url, og["content"])
            changed = True
    team_url = find_team_page(url, home)
    team_html = _fetch(team_url) if team_url else home
    fresh = extract_people(team_html, base_url=team_url or url)
    if fresh:
        by_name = {p["name"]: p for p in fresh}
        for p in record.get("team", []):
            fp = by_name.pop(p["name"], None)
            if fp and fp.get("photo") and not p.get("photo"):
                p["photo"] = fp["photo"]
                changed = True
        for leftover in by_name.values():
            record.setdefault("team", []).append(leftover)
            changed = True
    if not record.get("opening_hours"):
        oh = extract_opening_hours(home)
        if not oh["opening_hours"] and team_html != home:
            oh = extract_opening_hours(team_html)
        if oh["opening_hours"]:
            record.update(oh)
            changed = True
    return changed


# ---------------- orchestrator ----------------
def enrich_practice(practice: str, city: str, contact_name: str = "") -> dict:
    """Full best-effort enrichment for one practice. Safe to call offline-tolerant."""
    result = {
        "practice": practice, "city": city,
        "website": None, "website_title": None, "description": None,
        "kvk": None, "kvk_url": None, "emails": [], "practice_photo": None,
        "rating": None, "reviews": None, "zorgkaart_url": None,
        "opening_hours": None, "has_weekend": False, "has_evening": False, "latest_close": None,
        "team": [], "big_checks": [], "enriched_at": None,
    }
    site = find_website(practice, city)
    result.update({k: site.get(k) for k in
                   ("website", "website_title", "description", "kvk", "emails", "practice_photo")})

    zk = find_zorgkaart(practice, city)
    result.update({k: zk.get(k) for k in ("rating", "reviews", "zorgkaart_url")})

    # Team + BIG checks (from the team/over-ons page if we can find one).
    people: list[dict] = []
    if site.get("_html") and site.get("_url"):
        team_url = find_team_page(site["_url"], site["_html"])
        team_html = _fetch(team_url) if team_url else site["_html"]
        people = extract_people(team_html, base_url=team_url or site["_url"])
        # KvK fallback: often only on the contact/over-ons page, not the homepage.
        if not result.get("kvk") and team_html:
            m = _KVK_RE.search(BeautifulSoup(team_html, "lxml").get_text(" "))
            if m:
                result["kvk"] = m.group(1)
        # Opening hours from homepage, else the team/contact page.
        oh = extract_opening_hours(site.get("_html") or "")
        if not oh["opening_hours"] and team_html:
            oh = extract_opening_hours(team_html)
        result.update(oh)
    result["team"] = people
    result["big_checks"] = big_check_people(people)

    if result["kvk"]:
        result["kvk_url"] = f"https://www.kvk.nl/zoeken/?source=all&q={result['kvk']}"

    result["enriched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result
