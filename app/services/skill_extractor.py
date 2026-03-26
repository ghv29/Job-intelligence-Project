def extract_skills(description: str) -> list[dict]:
    """
    Very simple Phase 2 - Step 1 skill extractor (rule-based).

    Input:
    - Raw job description text

    Output:
    - List like: [{"skill_name": "Python", "category": "technical"}, ...]

    Note:
    - This is intentionally beginner-friendly and deterministic.
    - Later phases can replace this with NLP/LLM extraction.
    """
    if not description:
        return []

    # Normalize once so matching is case-insensitive.
    # Example: "SQL" and "sql" should both be detected.
    lowered = description.lower()

    # A small starter dictionary for skill detection.
    # Key = canonical skill name to save in DB.
    # Value = tuple of keywords that trigger that skill.
    # We keep keywords simple in this phase for readability.
    technical_rules = {
        "Python": ("python",),
        "SQL": ("sql",),
        # Enterprise / analytics stack signals
        # Note: these keywords are intentionally explicit (not too broad) so we
        # don't create many false positives from short substrings.
        "SAP": ("sap",),
        "dbt": ("dbt",),
        "Azure": ("azure",),
        "AWS": ("aws", "amazon web services",),
        "Power BI": ("power bi", "powerbi"),
        "Tableau": ("tableau",),
        "Excel": ("excel",),
        "Pandas": ("pandas",),
        "NumPy": ("numpy",),
        "ETL": ("etl", "data pipeline", "pipelines"),
        "Forecasting": ("forecast", "forecasting"),
        "Dashboarding": ("dashboard", "dashboards"),
    }

    soft_rules = {
        "Stakeholder Communication": ("stakeholder", "communication", "present"),
        "Problem Solving": ("problem solving", "problem-solving"),
        "Teamwork": ("team", "collaboration", "collaborate"),
    }

    extracted: list[dict] = []
    seen: set[str] = set()

    def add_matches(rules: dict[str, tuple[str, ...]], category: str) -> None:
        # For each canonical skill, check if any keyword is present.
        for skill_name, keywords in rules.items():
            if skill_name in seen:
                continue
            if any(keyword in lowered for keyword in keywords):
                extracted.append({"skill_name": skill_name, "category": category})
                seen.add(skill_name)

    add_matches(technical_rules, "technical")
    add_matches(soft_rules, "soft")

    return extracted
