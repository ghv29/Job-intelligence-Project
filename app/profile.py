"""
User profile used for scoring job matches.

This file keeps the "business logic" (what you care about) separate from the
scoring implementation so you can tune weights without touching the matcher.
"""

from __future__ import annotations


def build_profile() -> dict:
    # Canonical skills you already have (from your bootcamp + projects).
    # These should match the canonical skill names produced by skill_extractor.py.
    skills_you_have = [
        "Python",
        "SQL",
        "Tableau",
        "Power BI",  # you can keep this here if you consider it "have"
        "Excel",
        "Pandas",
        "NumPy",
        "ETL",
        "Forecasting",
        "Dashboarding",
    ]

    # Skills you want to see (bonus) even if they are not always extracted.
    # These should match canonical skill names produced by skill_extractor.py.
    skills_to_watch_for = [
        "Power BI",
        "SAP",
        "dbt",
        "Azure",
        "AWS",
    ]

    # Role keywords (English + German where it’s obvious).
    target_roles = [
        "Data Analyst",
        "Business Intelligence Analyst",
        "BI Analyst",
        "Manufacturing Analytics",
        "Supply Chain Analytics",
        "Predictive Maintenance Engineer",
        "Fertigungsanalyst",
    ]

    # Priority cities. We handle umlauts in the matcher by normalization.
    priority_cities = ["Berlin","Hamburg", "München"]
    secondary_cities = [ "Stuttgart", "Frankfurt", "Dresden", "Leipzig", 
    "Chemnitz", "Zwickau", "Dortmund", "Bonn", "Köln", "Essen", "Duisburg", 
    "Bochum", "Wuppertal", "Mönchengladbach", "Bonn", "Köln", "Essen", "Duisburg", "Bochum", "Wuppertal", "Mönchengladbach"]

    # Sector / engineering-domain signals we want to weight higher.
    # These are not "skills"; they’re hints from text that the job fits your
    # engineering + analytics transition.
    sector_edge_keywords = [
        "engineering",
        "ingenieur",
        "manufacturing",
        "fertigung",
        "automotive",
        "automobil",
        "logistics",
        "logistik",
        "supply chain",
        "lieferkette",
        "instandhaltung",
        "maintenance",
        "predictive",
        "vorhersage",
        "forecast",
        "forecasting",
        "predict",
        "predic",
    ]

    # German requirement preference: B2 is acceptable; C1 is a stretch.
    # For now we just treat "B2/C1 requirement" as a mild modifier.
    language_tolerance = {"preferred": "B2", "stretch": "C1"}

    return {
        "target_roles": target_roles,
        "priority_cities": priority_cities,
        "secondary_cities": secondary_cities,
        "skills_you_have": skills_you_have,
        "skills_to_watch_for": skills_to_watch_for,
        "sector_edge_keywords": sector_edge_keywords,
        "language_tolerance": language_tolerance,
    }

