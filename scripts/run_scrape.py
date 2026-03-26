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

# Ensure repository root is on sys.path when running as a file.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.models import Job, Skill
from app.db.session import Base, SessionLocal, engine
from app.scrapers.indeed_scraper import fetch_indeed_jobs
from app.scrapers.stepstone_scraper import fetch_stepstone_jobs
from app.services.skill_extractor import extract_skills


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


def upsert_job(session, job_data: dict) -> Job:
    # URL is our dedup key: same URL means same job posting.
    existing = session.query(Job).filter(Job.url == job_data["url"]).first()
    parsed_date = _parse_date(job_data.get("date_posted"))

    if existing:
        existing.title = job_data.get("title")
        existing.company = job_data.get("company")
        existing.location = job_data.get("location")
        existing.description = job_data.get("description")
        existing.date_posted = parsed_date
        existing.status = "active"
        return existing

    new_job = Job(
        title=job_data.get("title"),
        company=job_data.get("company"),
        location=job_data.get("location"),
        description=job_data.get("description"),
        url=job_data.get("url"),
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

    # Collect jobs from both sources into one pipeline list.
    jobs = []
    jobs.extend(fetch_stepstone_jobs(query="Data Analyst", location="Stuttgart"))
    jobs.extend(fetch_indeed_jobs(query="Data Analyst", location="Munchen"))
    print(f"Fetched {len(jobs)} jobs")

    inserted = 0
    updated = 0
    extracted_skills = 0
    failed = 0

    with SessionLocal() as session:
        # Process each job, upsert it, and track run metrics.
        for job in jobs:
            try:
                is_existing = (
                    session.query(Job.id).filter(Job.url == job["url"]).first() is not None
                )
                job_row = upsert_job(session, job)
                if is_existing:
                    updated += 1
                else:
                    inserted += 1
                extracted_skills += sync_job_skills(session, job_row)
            except Exception:
                failed += 1

        # Single commit keeps the run atomic and easier to reason about.
        session.commit()

    print(f"Inserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Extracted skills: {extracted_skills}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    run()
