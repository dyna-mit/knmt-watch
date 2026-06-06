"""Look up healthcare professionals in the Dutch BIG-register (public search API).

The BIG-register (bigregister.nl) is the official, public registry of healthcare
professionals. Protected titles like "tandarts" (dentist) and "mondhygiënist" legally
require a valid BIG registration, so we can verify whether a named person holds one.

API (reverse-engineered from the public search frontend at zoeken.bigregister.nl):
  GET /api/search/criteria?name=<surname>&initial=<X>&professionalGroup=<code>
  -> {"hcps": [{"mailingName","lastName","registrations":[
        {"professionalGroupCode","registrationNumber","strikedOut","registrationEnded"}]}]}
  -> 404 when nothing matches; ["tooManyFound"] (400) when the query is too broad.
"""
from __future__ import annotations

import re
import time

from . import http

API = "https://zoeken.bigregister.nl/api/search/criteria"
SEARCH_PAGE = "https://www.bigregister.nl/zoek-zorgverlener"
_last_call = 0.0

# Protected titles -> professionalGroupCode. These titles legally require BIG registration.
TITLE_GROUP = {
    "tandarts": "02",
    "mondhygienist": "92",
    "mondhygiënist": "92",
    "arts": "01",
    "apotheker": "03",
    "fysiotherapeut": "04",
    "psychotherapeut": "05",
    "verpleegkundige": "30",
    "verloskundige": "07",
    "gz-psycholoog": "82",
    "physician assistant": "81",
}
GROUP_LABEL = {
    "01": "Arts", "02": "Tandarts", "03": "Apotheker", "04": "Fysiotherapeut",
    "05": "Psychotherapeut", "07": "Verloskundige", "30": "Verpleegkundige",
    "81": "Physician assistant", "82": "GZ-psycholoog", "92": "Mondhygiënist",
}

_TUSSEN = {"van", "de", "der", "den", "van der", "van den", "van de", "ter", "te",
           "dos", "del", "di", "el", "al", "op", "in", "'t", "ten", "uit"}


def _throttle(min_gap: float = 2.0) -> None:
    global _last_call
    wait = min_gap - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def parse_name(full: str) -> tuple[str, str]:
    """Split a display name into (given_initials, surname).

    given_initials = first letters of all given names before the surname, e.g.
    "Jan Pieter Ruiter" -> ("JP", "Ruiter"), "Kristel van Velthoven" -> ("K", "van Velthoven"),
    "A.S. Habibi" -> ("AS", "Habibi").
    """
    full = re.sub(r",.*$", "", full).strip()  # drop trailing ", HR Partner" etc.
    full = re.sub(r"\s+", " ", full)
    if not full:
        return "", ""
    parts = full.split(" ")
    lower = [p.lower().strip(".") for p in parts]
    # Surname starts at the first tussenvoegsel, else the last token.
    surname_start = next((i for i in range(1, len(parts)) if lower[i] in _TUSSEN), None)
    if surname_start is None:
        surname_start = len(parts) - 1
    given = parts[:surname_start] or parts[:1]
    initials = ""
    for tok in given:
        letters = [c for c in tok if c.isalpha()]
        if not letters:
            continue
        # "A.S." (initials form) -> all letters; "Jan" (a real name) -> first letter only.
        initials += "".join(letters) if "." in tok else letters[0]
    surname = " ".join(parts[surname_start:])
    return initials.upper(), surname


def _mailing_initials(mailing: str) -> str:
    """'J.P. Ruiter' -> 'JP'."""
    head = mailing.split(" ")[0] if mailing else ""
    return "".join(c for c in head if c.isalpha()).upper()


def _initials_match(person: str, mailing: str) -> bool:
    """True if the person's given initials are consistent with the register entry's."""
    if not person or not mailing:
        return False
    if person[0] != mailing[0]:
        return False
    # If we have >1 initial, require them to appear in order within the register initials.
    it = iter(mailing)
    return all(c in it for c in person)


def search(surname: str, initial: str = "", group_code: str = "") -> dict:
    """Raw BIG search. Returns {'status': 'ok'|'none'|'too_many'|'error', 'hcps': [...]}.

    Retries with backoff on rate-limiting (429) / transient errors so bulk runs don't
    silently turn every lookup into 'error'.
    """
    if not surname:
        return {"status": "error", "hcps": []}
    params = {"name": surname}
    if initial:
        params["initial"] = initial
    if group_code:
        params["professionalGroup"] = group_code

    for attempt in range(4):
        _throttle()
        try:
            resp = http.get(API, params=params, retries=1, timeout=20)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "404" in msg:
                return {"status": "none", "hcps": []}
            if "toomany" in msg:
                return {"status": "too_many", "hcps": []}
            if "400" in msg:  # bad/too-broad query, not transient
                return {"status": "too_many", "hcps": []}
            if "429" in msg or "timed out" in msg or "timeout" in msg or "connection" in msg:
                time.sleep(3 * (attempt + 1))  # rate-limited / transient: back off & retry
                continue
            return {"status": "error", "hcps": []}
        if isinstance(data, list) and "tooManyFound" in data:
            return {"status": "too_many", "hcps": []}
        hcps = (data or {}).get("hcps", []) if isinstance(data, dict) else []
        return {"status": "ok" if hcps else "none", "hcps": hcps}
    return {"status": "error", "hcps": []}


def _active_groups(hcp: dict) -> set[str]:
    return {
        r.get("professionalGroupCode")
        for r in hcp.get("registrations", [])
        if not r.get("strikedOut") and not r.get("registrationEnded")
    }


_verify_cache: dict[tuple[str, str], dict] = {}


def verify_person(name: str, title: str) -> dict:
    """Check whether `name`, who is presented with `title`, holds a matching BIG registration.

    Returns a verdict dict: {name, title, expected_group, status, big_number?, matched_name?, note}.
    status: 'registered' | 'unverified' | 'no_title_check'.
    """
    ck = (name.strip().lower(), (title or "").strip().lower())
    if ck in _verify_cache:
        return dict(_verify_cache[ck])
    verdict = _verify_person(name, title)
    _verify_cache[ck] = verdict
    return dict(verdict)


def _verify_person(name: str, title: str) -> dict:
    initials, surname = parse_name(name)
    title_key = (title or "").lower().strip()
    group = TITLE_GROUP.get(title_key)
    out = {
        "name": name, "title": title, "expected_group": GROUP_LABEL.get(group, title),
        "search_url": SEARCH_PAGE,
    }
    if not group:
        # Title is not a BIG-protected one (e.g. assistant, office manager): no check needed.
        out.update(status="no_title_check", note="titel vereist geen BIG-registratie")
        return out
    if not surname:
        out.update(status="unverified", note="kon naam niet ontleden — controleer handmatig")
        return out

    res = search(surname, initials[:1], group)
    glabel = GROUP_LABEL.get(group, group)

    # IMPORTANT: fuzzy name search can confidently CONFIRM a registration, but a miss is
    # NOT proof someone is unregistered (spelling, tussenvoegsel, parsing, foreign training).
    # So we only ever assert "registered"; everything else is "unverified — check manually".
    if res["status"] == "ok":
        active = [h for h in res["hcps"] if group in _active_groups(h)]
        # Disambiguate namesakes by matching given initials against the register's mailingName.
        strong = [h for h in active if _initials_match(initials, _mailing_initials(h.get("mailingName", "")))]
        pick = strong if strong else active
        if len(pick) == 1:
            h = pick[0]
            reg = next(r for r in h["registrations"]
                       if r.get("professionalGroupCode") == group
                       and not r.get("strikedOut") and not r.get("registrationEnded"))
            out.update(status="registered", matched_name=h.get("mailingName"),
                       big_number=reg.get("registrationNumber"),
                       note=f"actieve BIG-registratie als {glabel}")
            return out
        if len(pick) > 1:
            out.update(status="unverified",
                       note=f"{len(pick)} naamgenoten met {glabel}-registratie; verifieer handmatig")
            return out

    out.update(status="unverified",
               note=f"niet automatisch te bevestigen als {glabel} — controleer handmatig")
    return out
