def fetch_indeed_jobs(query: str, location: str) -> list[dict]:
    """
    Placeholder scraping adapter for Indeed.
    Return normalized jobs: title, company, location, description, url, date_posted.
    """
    # Mock records for Phase 1 pipeline testing.
    # Later this function will call real Indeed scraping/search logic.
    q = "-".join(query.lower().split())
    loc = "-".join(location.lower().split())
    return [
        {
            "title": f"{query} - BI and Reporting",
            "company": "Bayern Manufacturing AG",
            "location": location,
            "description": "Analyze plant data with SQL, Power BI, and stakeholder reporting.",
            "url": f"https://example.com/indeed/{q}/{loc}/job-101",
            "date_posted": "2026-03-22",
            "source": "indeed_mock",
        },
        {
            "title": f"{query} (Supply Chain)",
            "company": "Munchen Supply Systems",
            "location": location,
            "description": "Forecast demand and improve warehouse efficiency with Python.",
            "url": f"https://example.com/indeed/{q}/{loc}/job-102",
            "date_posted": "2026-03-19",
            "source": "indeed_mock",
        },
    ]
