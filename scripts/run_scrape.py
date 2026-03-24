from datetime import date

from app.db.models import Job
from app.db.session import Base, SessionLocal, engine
from app.scrapers.indeed_scraper import fetch_indeed_jobs
from app.scrapers.stepstone_scraper import fetch_stepstone_jobs


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


def upsert_job(session, job_data: dict) -> str:
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
        return "updated"

    session.add(
        Job(
            title=job_data.get("title"),
            company=job_data.get("company"),
            location=job_data.get("location"),
            description=job_data.get("description"),
            url=job_data.get("url"),
            date_posted=parsed_date,
            status="active",
        )
    )
    return "inserted"


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
    failed = 0

    with SessionLocal() as session:
        # Process each job, upsert it, and track run metrics.
        for job in jobs:
            try:
                result = upsert_job(session, job)
                if result == "inserted":
                    inserted += 1
                else:
                    updated += 1
            except Exception:
                failed += 1

        # Single commit keeps the run atomic and easier to reason about.
        session.commit()

    print(f"Inserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    run()
