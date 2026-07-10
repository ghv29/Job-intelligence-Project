"""
StepStone.de listing scraper (HTML, requests + BeautifulSoup).

Uses the public search results page. Each job card exposes stable `data-at`
attributes, so we avoid brittle CSS class names that change often.

We use the short teaser on the card as `description` to keep one HTTP request
per job listing page. Full job pages can be added later if you need long text
for embeddings (with extra politeness / rate limits).
"""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import date, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from requests import Session

BASE_URL = "https://www.stepstone.de"


def _slugify_segment(text: str) -> str:
    """
    Build a URL-friendly slug similar to StepStone paths (e.g. data-analyst-in-stuttgart).

    Strips combining marks (accents) but maps letters that NFKD leaves non-ASCII
    (e.g. Turkish dotless i, Polish ł) so words are not corrupted. Pure
    ``encode('ascii', 'ignore')`` can drop those letters and turn
    "Softwareentwickler" into "softwareentwicker".
    """
    normalized = unicodedata.normalize("NFKD", text)
    no_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    folded = (
        no_marks.replace("ß", "ss")
        .replace("æ", "ae")
        .replace("Æ", "ae")
        .replace("ø", "o")
        .replace("Ø", "o")
        .replace("ł", "l")
        .replace("Ł", "l")
        .replace("đ", "d")
        .replace("ı", "i")
        .replace("İ", "i")
    )
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower().strip()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    return ascii_text.strip("-")


def _search_url(query: str, location: str | None) -> str:
    q = _slugify_segment(query)
    loc = _slugify_segment(location) if location else ""
    # Empty location => nationwide search (StepStone: /work/{query}).
    if not loc:
        return f"{BASE_URL}/work/{q}"
    return f"{BASE_URL}/work/{q}-in-{loc}"


def _default_session() -> Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return s


def _normalize_teaser(raw: str) -> str:
    """
    StepStone cards sometimes repeat the same teaser in the DOM, and the raw
    ``get_text`` can include UI bits like 'more' next to the age of the ad.
    """
    text = " ".join(raw.split())
    if not text:
        return ""
    half = len(text) // 2
    if half > 30 and text[:half] == text[half:]:
        text = text[:half]
    text = re.sub(r"\s*more\s*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\d+\s+days?\s+ago\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*vor\s+\d+\s+tagen\s*$", "", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip()


def _parse_timeago_to_date(text: str | None) -> date | None:
    """Turn StepStone card text like '3 days ago' into a calendar date (best effort)."""
    if not text:
        return None
    t = text.strip().lower()
    today = date.today()
    if t in {"heute", "today"}:
        return today
    if t in {"gestern", "yesterday"}:
        return today - timedelta(days=1)
    m = re.match(r"(\d+)\s+days?\s+ago", t)
    if m:
        return today - timedelta(days=int(m.group(1)))
    m = re.match(r"(\d+)\s+hours?\s+ago", t)
    if m:
        return today
    m = re.match(r"vor\s+(\d+)\s+tagen", t)
    if m:
        return today - timedelta(days=int(m.group(1)))
    return None


def _absolute_url(href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(BASE_URL + "/", href.lstrip("/"))


def _job_url_from_card(card: BeautifulSoup) -> str | None:
    h2 = card.find("h2")
    if not h2:
        return None
    link = h2.find("a", href=True)
    if not link:
        return None
    return _absolute_url(link["href"])


def _one_card_to_job(card: BeautifulSoup) -> dict | None:
    title_el = card.select_one('[data-at="job-item-title"]')
    company_el = card.select_one('[data-at="job-item-company-name"]')
    location_el = card.select_one('[data-at="job-item-location"]')
    teaser_el = card.select_one('[data-at="job-item-middle"]') or card.select_one(
        '[data-at="jobcard-content"]'
    )
    time_el = card.select_one('[data-at="job-item-timeago"]')

    title = (title_el.get_text() if title_el else "") or ""
    title = " ".join(title.split())
    company = (company_el.get_text() if company_el else "") or ""
    company = " ".join(company.split())
    location = (location_el.get_text() if location_el else "") or ""
    location = " ".join(location.split())
    raw_teaser = (teaser_el.get_text(separator=" ", strip=True) if teaser_el else "") or ""
    description = _normalize_teaser(raw_teaser)
    posted = _parse_timeago_to_date(time_el.get_text() if time_el else None)
    url = _job_url_from_card(card)

    if not url or not title:
        return None

    out: dict = {
        "title": title,
        "company": company,
        "location": location,
        "description": description or title,
        "url": url,
        "source": "stepstone",
    }
    if posted:
        out["date_posted"] = posted.isoformat()
    return out


def _next_page_url(soup: BeautifulSoup, current_url: str) -> str | None:
    link = soup.find("link", rel="next")
    if not link or not link.get("href"):
        return None
    href = link["href"]
    if urlparse(href).netloc:
        return href
    return _absolute_url(href)


def _fetch_full_description(
    session: Session,
    job_url: str,
    teaser_text: str,
    *,
    timeout_sec: int = 40,
) -> str:
    """
    Fetch full job-page text when teaser is too short.
    Falls back to teaser when detail page parsing fails.
    """
    if len((teaser_text or "").strip()) >= 160:
        return teaser_text
    try:
        resp = session.get(job_url, timeout=timeout_sec)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = [
            soup.select_one('[data-at="job-ad-content"]'),
            soup.select_one("article"),
            soup.find("main"),
        ]
        for node in candidates:
            if not node:
                continue
            text = " ".join(node.get_text(separator=" ", strip=True).split())
            if len(text) >= 220:
                return text
    except Exception:
        return teaser_text
    return teaser_text


def fetch_stepstone_jobs(
    query: str,
    location: str | None = None,
    *,
    max_pages: int = 1,
    page_delay_sec: float = 1.0,
    fetch_full_description: bool = True,
    session: Session | None = None,
) -> list[dict]:
    """
    Fetch jobs from StepStone for a keyword + city.

    Parameters
    ----------
    query, location
        Same as in the StepStone URL path: /work/{query}-in-{location}
    max_pages
        How many result pages to walk (pagination via <link rel="next">).
    page_delay_sec
        Pause between pages to reduce load on StepStone servers.
    session
        Optional ``requests.Session`` for connection reuse or custom settings.

    Returns
    -------
    Normalized dicts: title, company, location, description, url, date_posted (optional ISO date).
    """
    owns_session = session is None
    sess = session or _default_session()
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    url = _search_url(query, location)
    pages_read = 0

    def _get_page(page_url: str, *, attempts: int = 3) -> requests.Response:
        delay = 1.5
        last_exc: requests.RequestException | None = None
        for attempt in range(attempts):
            try:
                response = sess.get(page_url, timeout=40)
                if response.status_code in {502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(delay)
                    delay *= 2
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    time.sleep(delay)
                    delay *= 2
        if last_exc:
            raise last_exc
        raise requests.RequestException("no response")

    try:
        while url and pages_read < max_pages:
            response = _get_page(url)
            soup = BeautifulSoup(response.text, "html.parser")

            for card in soup.select('[data-at="job-item"]'):
                job = _one_card_to_job(card)
                if not job:
                    continue
                u = job["url"]
                if u in seen_urls:
                    continue
                seen_urls.add(u)
                if fetch_full_description:
                    job["description"] = _fetch_full_description(
                        sess,
                        job_url=u,
                        teaser_text=job.get("description", ""),
                    )
                jobs.append(job)

            pages_read += 1
            if pages_read >= max_pages:
                break
            next_url = _next_page_url(soup, url)
            if not next_url:
                break
            url = next_url
            if page_delay_sec > 0:
                time.sleep(page_delay_sec)
    finally:
        if owns_session:
            sess.close()

    return jobs
