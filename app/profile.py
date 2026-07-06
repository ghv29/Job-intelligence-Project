"""
User profile used for scoring job matches.

This file keeps the "business logic" (what you care about) separate from the
scoring implementation so you can tune weights without touching the matcher.
"""

from __future__ import annotations

import copy
import json

from app.db.models import UserProfileSettings

_PROFILE_MODE_KEY = "__profile_mode"
_PROFILE_MODE_DEFAULT = "default"
_PROFILE_MODE_CUSTOM = "custom"


def _default_profile() -> dict:
    return {
        "target_roles": [
            "Data Analyst",
            "Junior Data Analyst",
            "Business Intelligence Analyst",
            "Operations Analyst",
            "Supply Chain Analyst",
            "Process Improvement Analyst",
            "Logistics Analyst",
            "Manufacturing Data Analyst",
            "Werkstudent Data Analytics",
            "Project Manager",
            "Operations Coordinator",
            "Process Manager",
        ],
        "priority_cities": [
            "Berlin",
            "Helmstedt",
            "Braunschweig",
            "Wolfsburg",
            "Hannover",
            "Magdeburg",
        ],
        "secondary_cities": ["Hamburg", "Leipzig", "Dortmund", "Remote"],
        "skills_you_have": [
            "Python",
            "Pandas",
            "Scikit-learn",
            "SQL",
            "PostgreSQL",
            "MySQL",
            "SQLAlchemy",
            "Tableau",
            "Matplotlib",
            "Seaborn",
            "Plotly",
            "Streamlit",
            "Flask",
            "OpenAI API",
            "Pinecone",
            "SHAP",
            "BeautifulSoup",
            "Selenium",
            "GitHub Actions",
            "Excel",
            "AutoCAD",
            "Monday.com",
            "Notion API",
            "EDA",
            "Feature Engineering",
            "Random Forest",
            "NLP",
            "Predictive Maintenance",
            "KPI Monitoring",
            "Workforce Planning",
            "Stakeholder Communication",
            "Project Management",
        ],
        "skills_to_watch_for": [
            "Power BI",
            "Azure",
            "AWS",
            "dbt",
            "Airflow",
            "Spark",
            "Looker",
            "SAP",
            "ETL",
            "Data Warehousing",
            "MLOps",
            "LangChain",
            "Vector Databases",
            "A/B Testing",
            "Statistics",
            "Forecasting",
            "Time Series Analysis",
            "Six Sigma",
            "Lean Manufacturing",
        ],
        "sector_edge_keywords": [
            "manufacturing",
            "automotive",
            "logistics",
            "supply chain",
            "operations",
            "fulfilment",
            "predictive maintenance",
            "quality control",
            "production optimisation",
            "smart factory",
            "industrie 4.0",
            "fertigung",
            "maschinenbau",
            "lieferkette",
            "prozessoptimierung",
            "forschung",
            "wissenstransfer",
            "engineering",
            "fleet management",
            "warehouse",
        ],
        "language_tolerance": {"preferred": "B1", "stretch": "C1"},
        "weights": {
            "role": 0.35,
            "location": 0.25,
            "skills": 0.40,
            "watch_bonus": 0.15,
            "sector_edge": 0.15,
            "semantic": 0.20,
            "freshness": 0.10,
        },
    }


def _as_clean_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        t = " ".join(item.split()).strip()
        if not t:
            continue
        k = t.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def normalize_profile_dict(raw: dict | None) -> dict:
    default = _default_profile()
    if not isinstance(raw, dict):
        return copy.deepcopy(default)
    profile = copy.deepcopy(default)
    for key in [
        "target_roles",
        "priority_cities",
        "secondary_cities",
        "skills_you_have",
        "skills_to_watch_for",
        "sector_edge_keywords",
    ]:
        if key in raw:
            profile[key] = _as_clean_list(raw.get(key))
    if isinstance(raw.get("language_tolerance"), dict):
        lt = raw.get("language_tolerance") or {}
        profile["language_tolerance"] = {
            "preferred": str(lt.get("preferred", "B2")),
            "stretch": str(lt.get("stretch", "C1")),
        }
    if isinstance(raw.get("weights"), dict):
        merged = dict(profile["weights"])
        for k, v in raw["weights"].items():
            if k in merged:
                try:
                    merged[k] = float(v)
                except (TypeError, ValueError):
                    pass
        profile["weights"] = merged
    return profile


def build_profile() -> dict:
    return copy.deepcopy(_default_profile())


def load_profile_from_db(session) -> dict | None:
    row = session.query(UserProfileSettings).filter(UserProfileSettings.id == 1).first()
    if not row or not row.profile_json:
        return None
    try:
        parsed = json.loads(row.profile_json)
    except json.JSONDecodeError:
        return None
    # If the user explicitly selected "default", ignore stored settings.
    if isinstance(parsed, dict) and parsed.get(_PROFILE_MODE_KEY) == _PROFILE_MODE_DEFAULT:
        return None
    return normalize_profile_dict(parsed)

def load_profile_mode_from_db(session) -> str:
    """
    Return the selected profile mode.

    - default: always use code defaults, even if a custom profile is stored
    - custom: use stored profile_json (merged onto defaults)
    """
    row = session.query(UserProfileSettings).filter(UserProfileSettings.id == 1).first()
    if not row or not row.profile_json:
        return _PROFILE_MODE_DEFAULT
    try:
        parsed = json.loads(row.profile_json)
    except json.JSONDecodeError:
        return _PROFILE_MODE_DEFAULT
    if isinstance(parsed, dict) and parsed.get(_PROFILE_MODE_KEY) in {_PROFILE_MODE_DEFAULT, _PROFILE_MODE_CUSTOM}:
        return str(parsed.get(_PROFILE_MODE_KEY))
    # Back-compat: existing rows without a mode behave like "custom".
    return _PROFILE_MODE_CUSTOM


def save_profile_to_db(session, profile: dict) -> dict:
    normalized = normalize_profile_dict(profile)
    # Persist as "custom" whenever the user saves explicit settings.
    normalized[_PROFILE_MODE_KEY] = _PROFILE_MODE_CUSTOM
    payload = json.dumps(normalized, ensure_ascii=False)
    row = session.query(UserProfileSettings).filter(UserProfileSettings.id == 1).first()
    if row:
        row.profile_json = payload
    else:
        session.add(UserProfileSettings(id=1, profile_json=payload))
    return normalized


def save_profile_mode_to_db(session, mode: str) -> None:
    """
    Persist the profile mode without necessarily overwriting the custom profile.
    """
    m = str(mode).strip().lower()
    if m not in {_PROFILE_MODE_DEFAULT, _PROFILE_MODE_CUSTOM}:
        m = _PROFILE_MODE_DEFAULT

    row = session.query(UserProfileSettings).filter(UserProfileSettings.id == 1).first()
    if row and row.profile_json:
        try:
            parsed = json.loads(row.profile_json)
        except json.JSONDecodeError:
            parsed = {}
    else:
        parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    # Keep whatever custom fields exist; just toggle the mode.
    parsed[_PROFILE_MODE_KEY] = m
    if not row:
        row = UserProfileSettings(id=1, profile_json=json.dumps(parsed, ensure_ascii=False))
        session.add(row)
    else:
        row.profile_json = json.dumps(parsed, ensure_ascii=False)


def reset_profile_to_default_in_db(session) -> dict:
    """
    Overwrite stored profile_json to match code defaults and set mode=default.
    Returns the default profile dict.
    """
    default = build_profile()
    default[_PROFILE_MODE_KEY] = _PROFILE_MODE_DEFAULT
    payload = json.dumps(default, ensure_ascii=False)
    row = session.query(UserProfileSettings).filter(UserProfileSettings.id == 1).first()
    if row:
        row.profile_json = payload
    else:
        session.add(UserProfileSettings(id=1, profile_json=payload))
    return default


def get_effective_profile(session=None) -> dict:
    if session is None:
        return build_profile()
    persisted = load_profile_from_db(session)
    if persisted:
        return persisted
    return build_profile()

