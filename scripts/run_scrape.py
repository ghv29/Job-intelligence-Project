from app.scrapers.indeed_scraper import fetch_indeed_jobs
from app.scrapers.stepstone_scraper import fetch_stepstone_jobs


def run() -> None:
    jobs = []
    jobs.extend(fetch_stepstone_jobs(query="Data Analyst", location="Stuttgart"))
    jobs.extend(fetch_indeed_jobs(query="Data Analyst", location="Munchen"))
    print(f"Fetched {len(jobs)} jobs")


if __name__ == "__main__":
    run()
