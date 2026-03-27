"""
User profile used for scoring job matches.

This file keeps the "business logic" (what you care about) separate from the
scoring implementation so you can tune weights without touching the matcher.
"""

from __future__ import annotations

import copy
import json

from app.db.models import UserProfileSettings


def _default_profile() -> dict:
    return {
        "target_roles": [
            "Data Analyst",
            "Business Intelligence Analyst",
            "BI Analyst",
            "Manufacturing Analytics",
            "Supply Chain Analytics",
            "Predictive Maintenance Engineer",
            "Fertigungsanalyst",
        ],
        "priority_cities": ["Berlin", "Hamburg", "Munchen"],
        "secondary_cities": [
            "Stuttgart",
            "Frankfurt",
            "Dresden",
            "Leipzig",
            "Chemnitz",
            "Zwickau",
            "Dortmund",
            "Bonn",
            "Koln",
            "Essen",
            "Duisburg",
            "Bochum",
            "Wuppertal",
            "Monchengladbach",
        ],
        "skills_you_have": [
            "Python",
            "SQL",
            "Tableau",
            "Power BI",
            "Excel",
            "Pandas",
            "NumPy",
            "ETL",
            "Forecasting",
            "Dashboarding",
        ],
        "skills_to_watch_for": ["Power BI", "SAP", "dbt", "Azure", "AWS"],
        "sector_edge_keywords": [
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
        ],
        "language_tolerance": {"preferred": "B2", "stretch": "C1"},
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
    return normalize_profile_dict(parsed)


def save_profile_to_db(session, profile: dict) -> dict:
    normalized = normalize_profile_dict(profile)
    payload = json.dumps(normalized, ensure_ascii=False)
    row = session.query(UserProfileSettings).filter(UserProfileSettings.id == 1).first()
    if row:
        row.profile_json = payload
    else:
        session.add(UserProfileSettings(id=1, profile_json=payload))
    return normalized


def get_effective_profile(session=None) -> dict:
    if session is None:
        return build_profile()
    persisted = load_profile_from_db(session)
    if persisted:
        return persisted
    return build_profile()

