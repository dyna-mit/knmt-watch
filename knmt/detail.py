"""Parse a KNMT vacancy detail page.

Primary source is the embedded JSON-LD JobPosting block (clean & structured);
a few extra fields (contact, salary, precise location) are read from the HTML.
"""
from __future__ import annotations

import json
import re
from html import unescape

from bs4 import BeautifulSoup

from . import http


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _jsonld(soup: BeautifulSoup) -> dict:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for d in candidates:
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                return d
    return {}


def _field_item_text(soup: BeautifulSoup, name: str) -> str:
    """Read the first .field__item directly inside the named field wrapper."""
    wrapper = soup.select_one(f".field--name-{name}")
    if not wrapper:
        return ""
    item = wrapper.select_one(".field__item")
    return _clean((item or wrapper).get_text(" ", strip=True))


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    ld = _jsonld(soup)

    location = ld.get("jobLocation") or {}
    address = location.get("address") or {}
    org = ld.get("hiringOrganization") or {}

    record = {
        "title": _clean(ld.get("title")),
        "practice": _clean(org.get("name")) or _field_item_text(
            soup, "dynamic-twig-fieldnode-ds-praktijknaam"
        ),
        "city": _clean(address.get("addressLocality")) or _clean(location.get("name")),
        "country": (address.get("addressCountry") or "nl").lower(),
        "employment_type": _clean(ld.get("employmentType")),
        "date_posted": _clean(ld.get("datePosted")),
        "description": _clean(ld.get("description")),
        "requirements": _clean(ld.get("experienceRequirements")),
        # Contact / extras from the HTML (not present in JSON-LD).
        "contact_name": _field_item_text(soup, "field-contactperson"),
        "contact_email": _field_item_text(soup, "field-emailaddress-contact"),
        "contact_phone": _field_item_text(soup, "field-phonenumber-contact"),
        "what_we_offer": _field_item_text(soup, "field-what-we-offer"),
    }
    return record


def fetch_detail(url: str) -> dict:
    resp = http.get(url)
    return parse_detail(resp.text)
