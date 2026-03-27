from app.services.matcher import score_job_for_profile


def test_score_job_for_profile_includes_component_breakdown():
    profile = {
        "priority_cities": ["Berlin"],
        "secondary_cities": [],
        "skills_you_have": ["Python", "SQL"],
        "skills_to_watch_for": ["Power BI"],
        "sector_edge_keywords": ["manufacturing"],
        "weights": {
            "role": 0.35,
            "location": 0.25,
            "skills": 0.4,
            "watch_bonus": 0.15,
            "sector_edge": 0.15,
            "semantic": 0.2,
            "freshness": 0.1,
        },
    }
    job = {
        "title": "Data Analyst Manufacturing",
        "description": "Python SQL Power BI for automotive factory reporting",
        "location": "Berlin",
        "skills": ["Python", "SQL", "Power BI"],
        "date_posted": "2026-03-25",
        "semantic_score": 0.8,
    }
    result = score_job_for_profile(job=job, profile=profile)
    assert 0 <= result["match_score"] <= 1
    components = result["debug"]["score_components"]
    assert components["semantic"] > 0
    assert components["location"] > 0
