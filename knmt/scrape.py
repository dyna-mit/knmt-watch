"""Scrape the KNMT vacancy listing pages (server-rendered Drupal)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from . import http

BASE_URL = "https://knmt.nl/vacatures"

# Maps our config keys to the facet name used in the `vacatures[N]=<facet>:<value>` query.
FACET_KEYS = {
    "work_area": "work_area",
    "contract": "contract_type",
    "type_samenwerking": "type_samenwerking",
    "praktijk_type": "praktijk_type",
}

_ONCLICK_RE = re.compile(r"location\.href=['\"]([^'\"]+)['\"]")


@dataclass
class Listing:
    """A single vacancy as seen on a listing page (cheap, no detail fetch)."""

    slug: str
    url: str
    title: str
    changed_date: str
    work_area: str = ""
    work_areas: list[str] = field(default_factory=list)
    vacancy_type: str = ""
    hours: str = ""
    hours_max: int | None = field(default=None)


def build_facets(config_filters: dict) -> list[str]:
    """Build the list of `<facet>:<value>` strings; vacature_type:Tandarts is always first."""
    facets = ["vacature_type:Tandarts"]
    for cfg_key, facet_name in FACET_KEYS.items():
        for value in config_filters.get(cfg_key) or []:
            facets.append(f"{facet_name}:{value}")
    return facets


def _facet_params(facets: list[str], page: int) -> list[tuple[str, str]]:
    params = [(f"vacatures[{i}]", facet) for i, facet in enumerate(facets)]
    params.append(("page", str(page)))
    return params


def _field_text(node, name: str) -> str:
    el = node.select_one(f".field--name-{name} .field__item") or node.select_one(
        f".field--name-{name}"
    )
    if not el:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _field_items(node, name: str) -> list[str]:
    """All values of a (possibly multi-valued) field, e.g. work area can be several regions."""
    items = node.select(f".field--name-{name} .field__item")
    out = []
    for el in items:
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if txt:
            out.append(txt)
    return out


def _parse_hours_max(hours_text: str) -> int | None:
    nums = [int(n) for n in re.findall(r"\d+", hours_text)]
    return max(nums) if nums else None


def parse_listing_page(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Listing] = []
    for node in soup.select(".node--type-vacancy"):
        onclick = node.get("onclick", "")
        m = _ONCLICK_RE.search(onclick)
        if not m:
            continue
        path = m.group(1)
        # Slugs can themselves contain "/", so strip the /vacatures/ prefix
        # rather than taking the last path segment.
        slug = path.split("/vacatures/", 1)[-1].strip("/")
        title_el = node.select_one(".field--name-node-title")
        hours = _field_text(node, "dynamic-twig-fieldnode-ds-working-hours")
        work_areas = _field_items(node, "field-work-area")
        out.append(
            Listing(
                slug=slug,
                url="https://knmt.nl" + path if path.startswith("/") else path,
                title=title_el.get_text(" ", strip=True) if title_el else "",
                changed_date=_field_text(node, "node-changed-date"),
                work_area=", ".join(work_areas),
                work_areas=work_areas,
                vacancy_type=_field_text(node, "field-vacancy-type"),
                hours=hours,
                hours_max=_parse_hours_max(hours),
            )
        )
    return out


def fetch_listings(config_filters: dict, max_pages: int = 100, delay: float = 0) -> list[Listing]:
    """Walk listing pages until one returns no cards (or max_pages reached).

    `delay` pauses between page fetches to keep the request rate gentle on KNMT.
    """
    facets = build_facets(config_filters)
    seen: dict[str, Listing] = {}
    for page in range(max_pages):
        if page and delay:
            time.sleep(delay)
        resp = http.get(BASE_URL, params=_facet_params(facets, page))
        rows = parse_listing_page(resp.text)
        if not rows:
            break
        new_on_page = 0
        for row in rows:
            if row.slug not in seen:
                seen[row.slug] = row
                new_on_page += 1
        # If a page contributed nothing new, we've wrapped/duplicated: stop.
        if new_on_page == 0:
            break
    return list(seen.values())
