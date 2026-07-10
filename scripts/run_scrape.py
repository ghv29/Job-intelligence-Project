"""
Scrape jobs and upsert them into the database.

Important Windows note:
If you run this file as `python scripts/run_scrape.py`, Python sets the import root
to the `scripts/` folder, so `import app...` may fail.

This small bootstrap makes both of these work:
  - python scripts/run_scrape.py
  - python -m scripts.run_scrape   (recommended)
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# Ensure repository root is on sys.path when running as a file.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.models import Job, Skill
from app.db.session import Base, SessionLocal, engine
from app.config import settings
from app.profile import get_effective_profile
from app.scrapers.arbeitsagentur_scraper import fetch_arbeitsagentur_jobs
from app.scrapers.indeed_scraper import fetch_indeed_jobs
from app.scrapers.stepstone_scraper import fetch_stepstone_jobs
from app.services.pinecone_store import upsert_job as upsert_job_to_pinecone
from app.services.skill_extractor import extract_skills

DEFAULT_MAX_ROLES = 12
DEFAULT_MAX_CITIES = 3
DEFAULT_MAX_PAGES = 3
# Search all of Germany by default and let the matcher's location score
# reward jobs in the profile's priority/secondary cities (see matcher.py).
DEFAULT_NATIONWIDE = True

# Short words that appear in role names but are too generic to use as title filters.
_NOISE_WORDS = {"und", "fur", "für", "der", "die", "das", "mit", "von", "bei", "im", "in", "an", "am", "auf", "als", "zum", "zur", "and", "for", "the"}
_MIN_KEYWORD_LEN = 4


def _extract_title_keywords(roles: list[str]) -> set[str]:
    """Derive meaningful keywords from target role names for post-scrape filtering."""
    keywords: set[str] = set()
    for role in roles:
        for word in role.lower().split():
            word = word.strip("()/+&")
            if len(word) >= _MIN_KEYWORD_LEN and word not in _NOISE_WORDS:
                keywords.add(word)
    return keywords


def _is_relevant_title(title: str, keywords: set[str]) -> bool:
    """Return True if the job title contains at least one target role keyword."""
    lower = title.lower()
    return any(kw in lower for kw in keywords)


FALLBACK_ROLES: list[str] = [
    "Data Analyst",
    "Business Intelligence Analyst",
    "Operations Analyst",
]

FALLBACK_QUERIES: list[tuple[str, str]] = [
    ("Data Analyst", "Berlin"),
    ("Data Analyst", "Hamburg"),
    ("Data Analyst", "Munchen"),
    ("Data Analyst", "Stuttgart"),
    ("Business Intelligence", "Berlin"),
    ("Business Intelligence", "Munchen"),
]


def _profile_roles(max_roles: int) -> list[str]:
    """Load target roles from the saved profile, or fall back to defaults."""
    if not SessionLocal:
        return FALLBACK_ROLES[:max_roles]
    try:
        with SessionLocal() as session:
            profile = get_effective_profile(session=session)
    except Exception:
        return FALLBACK_ROLES[:max_roles]
    roles = profile.get("target_roles", [])[:max_roles]
    return roles or FALLBACK_ROLES[:max_roles]


def _build_search_queries(
    *, max_roles: int, max_cities: int, nationwide: bool
) -> list[tuple[str, str]]:
    """
    Build (role, location) pairs to scrape.

    Nationwide mode uses an empty location so the scrapers search all of Germany;
    the matcher then scores each job by whether its city is in the profile's
    priority/secondary lists. City mode (legacy) multiplies roles x cities.
    """
    if nationwide:
        roles = _profile_roles(max_roles)
        queries = [(role, "") for role in roles]
        print(f"Nationwide scrape: {len(queries)} role queries across all of Germany")
        return queries

    if not SessionLocal:
        return FALLBACK_QUERIES
    try:
        with SessionLocal() as session:
            profile = get_effective_profile(session=session)
    except Exception:
        return FALLBACK_QUERIES

    roles = profile.get("target_roles", [])[:max_roles]
    cities = profile.get("priority_cities", [])[:max_cities]

    if not roles or not cities:
        return FALLBACK_QUERIES

    queries = [(role, city) for role in roles for city in cities]
    print(f"City scrape: {len(queries)} queries from {len(roles)} roles x {len(cities)} cities")
    return queries


def _parse_date(value: object) -> date | None:
    # Scrapers may return string dates; DB model expects Python date.
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _canonical_url(raw: str | None) -> str:
    if not raw:
        return ""
    parts = urlsplit(raw.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def _source_name(job_data: dict) -> str:
    return str(job_data.get("source") or "unknown").strip() or "unknown"


def upsert_job(session, job_data: dict) -> Job:
    # URL is our dedup key: same URL means same job posting.
    url = _canonical_url(job_data.get("url"))
    if not url:
        raise ValueError("Missing URL in scraped job payload.")
    existing = session.query(Job).filter(Job.url == url).first()
    parsed_date = _parse_date(job_data.get("date_posted"))

    if existing:
        existing.title = job_data.get("title")
        existing.company = job_data.get("company")
        existing.location = job_data.get("location")
        existing.description = job_data.get("description")
        existing.date_posted = parsed_date
        existing.status = "active"
        existing.url = url
        return existing

    new_job = Job(
        title=job_data.get("title"),
        company=job_data.get("company"),
        location=job_data.get("location"),
        description=job_data.get("description"),
        url=url,
        date_posted=parsed_date,
        status="active",
    )
    session.add(new_job)
    # Flush asks SQLAlchemy to send INSERT now, so new_job.id is available immediately.
    # We need job_id now because skills reference the job row.
    session.flush()
    return new_job


def sync_job_skills(session, job: Job) -> int:
    """
    Extract skills from one job description and keep DB skills in sync.

    Returns number of extracted skills saved for this job.
    """
    # If description is empty, there is nothing to extract.
    if not job.description:
        return 0

    extracted = extract_skills(job.description)

    # Beginner-safe approach:
    # 1) Remove old skills for this job.
    # 2) Insert fresh skills from current description.
    # This avoids stale/duplicate skill rows after re-scrapes.
    session.query(Skill).filter(Skill.job_id == job.id).delete()

    for item in extracted:
        session.add(
            Skill(
                job_id=job.id,
                skill_name=item["skill_name"],
                category=item["category"],
            )
        )

    return len(extracted)


def run(
    *,
    max_roles: int = DEFAULT_MAX_ROLES,
    max_cities: int = DEFAULT_MAX_CITIES,
    max_pages: int = DEFAULT_MAX_PAGES,
    nationwide: bool = DEFAULT_NATIONWIDE,
) -> None:
    # Stop early with a clear message when DB settings are missing.
    if not engine or not SessionLocal:
        raise RuntimeError("DATABASE_URL is missing. Set it in your .env file.")

    # Create tables once if they do not exist yet.
    Base.metadata.create_all(bind=engine)

    # Load profile once — used for both building queries and title filtering.
    try:
        with SessionLocal() as session:
            profile = get_effective_profile(session=session)
    except Exception:
        profile = {}

    title_keywords = _extract_title_keywords(profile.get("target_roles", []))

    # Build search queries from saved profile (or fallback defaults).
    search_queries = _build_search_queries(
        max_roles=max_roles, max_cities=max_cities, nationwide=nationwide
    )

    # Collect jobs from all query combinations, across every source.
    jobs = []
    for role, city in search_queries:
        where = city if city else "all of Germany"
        print(f"  Scraping: \"{role}\" in \"{where}\" ...")
        try:
            batch = fetch_stepstone_jobs(
                query=role,
                location=city,
                max_pages=max_pages,
                fetch_full_description=True,
            )
            jobs.extend(batch)
            print(f"    -> stepstone: {len(batch)} jobs")
        except Exception as e:
            print(f"    -> stepstone FAILED: {e}")

        try:
            batch = fetch_arbeitsagentur_jobs(
                query=role,
                location=city,
                max_pages=max_pages,
                fetch_full_description=True,
            )
            jobs.extend(batch)
            print(f"    -> arbeitsagentur: {len(batch)} jobs")
        except Exception as e:
            print(f"    -> arbeitsagentur FAILED: {e}")

    if settings.use_mock_indeed:
        for role, city in search_queries[:3]:
            jobs.extend(fetch_indeed_jobs(query=role, location=city))
    else:
        print("Skipping placeholder Indeed source (set USE_MOCK_INDEED=true to include mock data).")
    print(f"Fetched {len(jobs)} jobs total across {len(search_queries)} queries")

    # Drop jobs whose title doesn't match any target role keyword.
    # StepStone pads results for small cities with unrelated local jobs (e.g. forklift
    # drivers when searching "Data Analyst in Helmstedt") — this removes that noise.
    if title_keywords:
        before = len(jobs)
        jobs = [j for j in jobs if _is_relevant_title(j.get("title", ""), title_keywords)]
        removed = before - len(jobs)
        if removed:
            print(f"Title filter: removed {removed} irrelevant jobs, {len(jobs)} remaining")

    inserted = 0
    updated = 0
    extracted_skills = 0
    pinecone_upserted = 0
    failed = 0
    skipped = 0
    parse_failures = 0
    jobs_per_source: dict[str, int] = {}
    description_lengths: list[int] = []

    # Embeddings are optional: the keyword/skill scoring and the career-ops
    # export work without them. Vectors are generated by a local (free) model,
    # so only a Pinecone destination is required. Stop retrying after a
    # persistent error instead of burning a call per job.
    embeddings_enabled = bool(settings.pinecone_api_key and settings.pinecone_index)
    if not embeddings_enabled:
        print("Embeddings disabled (PINECONE_API_KEY / PINECONE_INDEX not set) — skipping Pinecone sync.")

    _FATAL_EMBEDDING_MARKERS = ("insufficient_quota", "invalid_api_key", "authentication", "401")

    with SessionLocal() as session:
        # Process each job, upsert it, and track run metrics.
        for job in jobs:
            try:
                source = _source_name(job)
                jobs_per_source[source] = jobs_per_source.get(source, 0) + 1
                if not _canonical_url(job.get("url")):
                    skipped += 1
                    continue
                if not _parse_date(job.get("date_posted")) and job.get("date_posted") is not None:
                    parse_failures += 1
                canon = _canonical_url(job.get("url"))
                # SAVEPOINT: one bad row (e.g. lost SSL) must not poison the whole batch.
                with session.begin_nested():
                    is_existing = session.query(Job.id).filter(Job.url == canon).first() is not None
                    job_row = upsert_job(session, job)
                    if is_existing:
                        updated += 1
                    else:
                        inserted += 1
                    description_lengths.append(len((job_row.description or "").strip()))
                    extracted_skills += sync_job_skills(session, job_row)
                    job_id = int(job_row.id)
                    title_txt = job_row.title or ""
                    company_txt = job_row.company or ""
                    location_txt = job_row.location or ""
                    description_txt = job_row.description or ""

                # Pinecone vector upsert (best-effort; never abort the run).
                if embeddings_enabled:
                    try:
                        vector_id = upsert_job_to_pinecone(
                            job_id=job_id,
                            title=title_txt,
                            company=company_txt,
                            location=location_txt,
                            description=description_txt,
                        )
                        session.query(Job).filter(Job.id == job_id).update(
                            {"pinecone_id": vector_id},
                            synchronize_session=False,
                        )
                        pinecone_upserted += 1
                    except Exception as e:
                        message = str(e).lower()
                        if any(marker in message for marker in _FATAL_EMBEDDING_MARKERS):
                            embeddings_enabled = False
                            print(f"Embedding API unavailable ({e.__class__.__name__}) — skipping Pinecone sync for the rest of this run.")
                        else:
                            print(f"Pinecone upsert failed for job_id={job_id}: {e}")
            except Exception as e:
                print(f"Failed to process job url={job.get('url')} source={_source_name(job)} error={e}")
                failed += 1

        # One commit for all successful savepoints.
        session.commit()

    print(f"Inserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Extracted skills: {extracted_skills}")
    print(f"Upserted {pinecone_upserted} jobs to Pinecone")
    print(f"Failed: {failed}")
    print(f"Skipped (invalid payload): {skipped}")
    print(f"Date parse issues: {parse_failures}")
    if description_lengths:
        avg_desc = sum(description_lengths) / len(description_lengths)
        print(f"Average description length: {avg_desc:.1f} chars")
    if jobs_per_source:
        print("Jobs by source:")
        for source, count in sorted(jobs_per_source.items()):
            print(f"  - {source}: {count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape jobs and upsert into the database.")
    parser.add_argument("--max-roles", type=int, default=DEFAULT_MAX_ROLES, help="Use only the first N target roles.")
    parser.add_argument("--max-cities", type=int, default=DEFAULT_MAX_CITIES, help="City mode only: use the first N priority cities.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="How many result pages per query, per source.")
    parser.add_argument(
        "--nationwide",
        dest="nationwide",
        action="store_true",
        default=DEFAULT_NATIONWIDE,
        help="Search all of Germany (default); location is scored, not filtered.",
    )
    parser.add_argument(
        "--no-nationwide",
        dest="nationwide",
        action="store_false",
        help="Legacy city mode: scrape each priority city separately.",
    )
    args = parser.parse_args()
    run(
        max_roles=max(1, int(args.max_roles)),
        max_cities=max(1, int(args.max_cities)),
        max_pages=max(1, int(args.max_pages)),
        nationwide=bool(args.nationwide),
    )
