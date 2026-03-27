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
from app.scrapers.indeed_scraper import fetch_indeed_jobs
from app.scrapers.stepstone_scraper import fetch_stepstone_jobs
from app.services.pinecone_store import upsert_job as upsert_job_to_pinecone
from app.services.skill_extractor import extract_skills

MAX_ROLES = 3
MAX_CITIES = 3

FALLBACK_QUERIES: list[tuple[str, str]] = [
    ("Data Analyst", "Berlin"),
    ("Data Analyst", "Hamburg"),
    ("Data Analyst", "Munchen"),
    ("Data Analyst", "Stuttgart"),
    ("Business Intelligence", "Berlin"),
    ("Business Intelligence", "Munchen"),
]


def _build_search_queries() -> list[tuple[str, str]]:
    """Build (role, city) pairs from the saved profile, or fall back to defaults."""
    if not SessionLocal:
        return FALLBACK_QUERIES

    try:
        with SessionLocal() as session:
            profile = get_effective_profile(session=session)
    except Exception:
        return FALLBACK_QUERIES

    roles = profile.get("target_roles", [])[:MAX_ROLES]
    cities = profile.get("priority_cities", [])[:MAX_CITIES]

    if not roles or not cities:
        return FALLBACK_QUERIES

    queries = [(role, city) for role in roles for city in cities]
    print(f"Profile-driven scrape: {len(queries)} queries from {len(roles)} roles x {len(cities)} cities")
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


def run() -> None:
    # Stop early with a clear message when DB settings are missing.
    if not engine or not SessionLocal:
        raise RuntimeError("DATABASE_URL is missing. Set it in your .env file.")

    # Create tables once if they do not exist yet.
    Base.metadata.create_all(bind=engine)

    # Build search queries from saved profile (or fallback defaults).
    search_queries = _build_search_queries()

    # Collect jobs from all query combinations.
    jobs = []
    for role, city in search_queries:
        print(f"  Scraping: \"{role}\" in \"{city}\" ...")
        try:
            batch = fetch_stepstone_jobs(
                query=role,
                location=city,
                max_pages=2,
                fetch_full_description=True,
            )
            jobs.extend(batch)
            print(f"    -> {len(batch)} jobs")
        except Exception as e:
            print(f"    -> FAILED: {e}")

    if settings.use_mock_indeed:
        for role, city in search_queries[:3]:
            jobs.extend(fetch_indeed_jobs(query=role, location=city))
    else:
        print("Skipping placeholder Indeed source (set USE_MOCK_INDEED=true to include mock data).")
    print(f"Fetched {len(jobs)} jobs total across {len(search_queries)} queries")

    inserted = 0
    updated = 0
    extracted_skills = 0
    pinecone_upserted = 0
    failed = 0
    skipped = 0
    parse_failures = 0
    jobs_per_source: dict[str, int] = {}
    description_lengths: list[int] = []

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
                is_existing = (
                    session.query(Job.id).filter(Job.url == _canonical_url(job.get("url"))).first() is not None
                )
                job_row = upsert_job(session, job)
                if is_existing:
                    updated += 1
                else:
                    inserted += 1
                description_lengths.append(len((job_row.description or "").strip()))
                extracted_skills += sync_job_skills(session, job_row)

                # Pinecone vector upsert (best-effort; never abort the run).
                try:
                    vector_id = upsert_job_to_pinecone(
                        job_id=int(job_row.id),
                        title=job_row.title or "",
                        company=job_row.company or "",
                        location=job_row.location or "",
                        description=job_row.description or "",
                    )
                    job_row.pinecone_id = vector_id
                    pinecone_upserted += 1
                except Exception as e:
                    print(f"Pinecone upsert failed for job_id={job_row.id}: {e}")
            except Exception as e:
                print(f"Failed to process job url={job.get('url')} source={_source_name(job)} error={e}")
                failed += 1

        # Single commit keeps the run atomic and easier to reason about.
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
    run()
