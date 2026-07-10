"""
Bundesagentur für Arbeit "Jobsuche" scraper (official JSON API).

Unlike the StepStone scraper (which parses HTML), this talks to the federal
employment agency's public REST API, so the contract is stable JSON instead of
brittle CSS selectors. It is the largest German-language job board and needs no
signup: requests carry a single well-known static ``X-API-Key`` client key.

Two endpoints are used:
  - list:   /pc/v4/jobs           -> search results (no description text)
  - detail: /pc/v4/jobdetails/ID  -> full description (ID = base64 of refnr)

We return the same normalized dict shape as the other scrapers so
``scripts/run_scrape.py`` can consume both sources identically:
title, company, location, description, url, source, date_posted (optional).
"""

from __future__ import annotations

import base64
import time
from datetime import date
from typing import TYPE_CHECKING
from urllib.parse import quote

import requests

if TYPE_CHECKING:
    from requests import Session

# Public client key shipped with the Arbeitsagentur web app; no registration.
API_KEY = "jobboerse-jobsuche"
API_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4"
LIST_URL = f"{API_BASE}/jobs"
DETAIL_URL = f"{API_BASE}/jobdetails"
# Human-facing detail page (opens in a browser; the API returns JSON only).
PUBLIC_DETAIL_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail"


def _default_session() -> Session:
    s = requests.Session()
    s.headers.update(
        {
            "X-API-Key": API_KEY,
            "Accept": "application/json",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }
    )
    return s


def _encode_refnr(refnr: str) -> str:
    """The detail endpoint keys jobs by the base64 of their reference number."""
    return base64.b64encode(refnr.encode("utf-8")).decode("ascii")


def _public_url(refnr: str) -> str:
    return f"{PUBLIC_DETAIL_URL}/{quote(_encode_refnr(refnr), safe='')}"


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # API returns 'YYYY-MM-DD'; guard against stray time components too.
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _clean(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return " ".join(text.split()).strip()


def _location_from_arbeitsort(arbeitsort: object) -> str:
    if not isinstance(arbeitsort, dict):
        return ""
    ort = _clean(arbeitsort.get("ort"))
    region = _clean(arbeitsort.get("region"))
    # The API sometimes already bakes the region into ``ort`` (e.g.
    # "Kassel, Hessen"), so only append region when it isn't already there.
    if ort and region and region.casefold() not in ort.casefold():
        return f"{ort}, {region}"
    return ort or region


def _fetch_description(
    session: Session,
    refnr: str,
    *,
    timeout_sec: int = 40,
) -> str:
    """Fetch the full job description via the detail endpoint (best effort)."""
    try:
        resp = session.get(f"{DETAIL_URL}/{_encode_refnr(refnr)}", timeout=timeout_sec)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return _clean(data.get("stellenangebotsBeschreibung"))


def _one_listing_to_job(session: Session, item: dict, *, fetch_full_description: bool) -> dict | None:
    if not isinstance(item, dict):
        return None
    refnr = _clean(item.get("refnr"))
    if not refnr:
        return None

    title = _clean(item.get("titel")) or _clean(item.get("beruf"))
    if not title:
        return None

    company = _clean(item.get("arbeitgeber"))
    location = _location_from_arbeitsort(item.get("arbeitsort"))
    posted = _parse_iso_date(item.get("aktuelleVeroeffentlichungsdatum"))

    description = ""
    if fetch_full_description:
        description = _fetch_description(session, refnr)

    out: dict = {
        "title": title,
        "company": company,
        "location": location,
        "description": description or title,
        # Stable, unique dedup key + browser-openable listing page.
        "url": _public_url(refnr),
        "source": "arbeitsagentur",
    }
    # Some listings link straight to the employer's application page.
    external = _clean(item.get("externeUrl"))
    if external:
        out["apply_url"] = external
    if posted:
        out["date_posted"] = posted.isoformat()
    return out


def fetch_arbeitsagentur_jobs(
    query: str,
    location: str | None = None,
    *,
    max_pages: int = 1,
    size: int = 25,
    radius_km: int = 25,
    published_since_days: int | None = None,
    page_delay_sec: float = 1.0,
    fetch_full_description: bool = True,
    session: Session | None = None,
) -> list[dict]:
    """
    Fetch jobs from the Bundesagentur für Arbeit Jobsuche API for a keyword + city.

    Parameters
    ----------
    query, location
        Free-text role and city (mapped to the API's ``was`` and ``wo`` params).
        Pass an empty/``None`` location to search all of Germany (the ``wo`` and
        ``umkreis`` filters are then omitted).
    max_pages
        How many result pages to walk. The API caps ``maxErgebnisse`` per query.
    size
        Results per page (API max is 100).
    radius_km
        Search radius around the city (``umkreis``).
    published_since_days
        If set, only return ads published within the last N days
        (``veroeffentlichtseit``), which the API accepts up to ~100.
    page_delay_sec
        Pause between page requests to stay polite to the API.
    session
        Optional ``requests.Session`` for connection reuse / custom settings.

    Returns
    -------
    Normalized dicts: title, company, location, description, url, source,
    date_posted (optional ISO date), apply_url (optional external link).
    """
    owns_session = session is None
    sess = session or _default_session()
    jobs: list[dict] = []
    seen_refs: set[str] = set()

    def _get_page(page: int, *, attempts: int = 3) -> dict | None:
        params: dict[str, object] = {
            "was": query,
            "size": size,
            "page": page,
        }
        # Empty location => nationwide search (omit the city/radius filters).
        if location and location.strip():
            params["wo"] = location.strip()
            params["umkreis"] = radius_km
        if published_since_days is not None:
            params["veroeffentlichtseit"] = published_since_days

        delay = 1.5
        last_exc: requests.RequestException | None = None
        for attempt in range(attempts):
            try:
                response = sess.get(LIST_URL, params=params, timeout=40)
                if response.status_code in {502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(delay)
                    delay *= 2
                    continue
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc if isinstance(exc, requests.RequestException) else last_exc
                if attempt + 1 < attempts:
                    time.sleep(delay)
                    delay *= 2
        if last_exc:
            raise last_exc
        return None

    try:
        page = 1
        pages_read = 0
        while pages_read < max_pages:
            payload = _get_page(page)
            if not isinstance(payload, dict):
                break

            listings = payload.get("stellenangebote")
            if not isinstance(listings, list) or not listings:
                break

            for item in listings:
                ref = _clean(item.get("refnr")) if isinstance(item, dict) else ""
                if not ref or ref in seen_refs:
                    continue
                seen_refs.add(ref)
                job = _one_listing_to_job(
                    sess, item, fetch_full_description=fetch_full_description
                )
                if job:
                    jobs.append(job)

            pages_read += 1

            # Stop when we've seen everything the query returned.
            max_results = payload.get("maxErgebnisse")
            if isinstance(max_results, int) and page * size >= max_results:
                break
            if pages_read >= max_pages:
                break

            page += 1
            if page_delay_sec > 0:
                time.sleep(page_delay_sec)
    finally:
        if owns_session:
            sess.close()

    return jobs
