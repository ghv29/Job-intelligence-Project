def fetch_stepstone_jobs(query: str, location: str) -> list[dict]:
    """
    Placeholder scraping adapter for StepStone.
    Return normalized jobs: title, company, location, description, url, date_posted.
    """
    # Mock records for Phase 1 pipeline testing.
    # Later this function will call real StepStone scraping/search logic.
    return [
        {
            "title": f"{query} (Manufacturing Focus)",
            "company": "Muster Automotive GmbH",
            "location": location,
            "description": "Build dashboards, SQL reporting, and production KPI analysis.",
            "url": "https://example.com/stepstone/job-001",
            "date_posted": "2026-03-20",
        },
        {
            "title": f"Junior {query}",
            "company": "Stuttgart Logistics Analytics",
            "location": location,
            "description": "Support supply chain optimization and Python data pipelines.",
            "url": "https://example.com/stepstone/job-002",
            "date_posted": "2026-03-18",
        },
    ]
