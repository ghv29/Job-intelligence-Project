from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Job
from scripts.run_scrape import upsert_job


def test_upsert_job_uses_canonical_url_for_dedup():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    with Session() as session:
        payload_a = {
            "title": "Data Analyst",
            "company": "ACME",
            "location": "Berlin",
            "description": "SQL dashboards",
            "url": "https://example.com/jobs/123?ref=abc",
            "date_posted": "2026-03-20",
        }
        payload_b = {
            "title": "Data Analyst Updated",
            "company": "ACME GmbH",
            "location": "Berlin",
            "description": "SQL dashboards and forecasting",
            "url": "https://example.com/jobs/123",
            "date_posted": "2026-03-21",
        }
        upsert_job(session, payload_a)
        upsert_job(session, payload_b)
        session.commit()
        rows = session.query(Job).all()
        assert len(rows) == 1
        assert rows[0].title == "Data Analyst Updated"
