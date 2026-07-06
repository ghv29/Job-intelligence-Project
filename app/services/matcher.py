from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Job, Skill
from app.profile import build_profile
from app.services.language_gate import (
    TIER_ENGLISH_FRIENDLY,
    TIER_REQUIRED_C1,
    detect_german_requirement,
)
from app.services.pinecone_store import search_jobs_semantic


def _normalize_text(text: str | None) -> str:
    """
    Normalize text for robust keyword matching.

    - lowercases
    - strips accents (München -> munchen)
    - collapses whitespace
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"\s+", " ", ascii_text).strip()
    return ascii_text


def _to_iso_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _parse_iso_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def load_active_jobs_with_skills(session: Session, limit: int = 50) -> list[dict]:
    """
    Load the latest active jobs plus their extracted skills from Neon DB.

    This avoids N+1 queries by fetching all skills in one go.
    """
    jobs: list[Job] = (
        session.query(Job)
        .filter(Job.status == "active")
        .order_by(Job.date_posted.desc().nullslast())
        .limit(limit)
        .all()
    )

    job_ids = [j.id for j in jobs]
    if not job_ids:
        return []

    skills_rows: list[Skill] = session.query(Skill).filter(Skill.job_id.in_(job_ids)).all()

    skills_by_job_id: dict[int, list[str]] = {}
    for s in skills_rows:
        skills_by_job_id.setdefault(s.job_id, []).append(s.skill_name)

    out: list[dict] = []
    for j in jobs:
        out.append(
            {
                "id": j.id,
                "title": j.title or "",
                "company": j.company or "",
                "location": j.location or "",
                "salary_min": j.salary_min,
                "salary_max": j.salary_max,
                "description": j.description or "",
                "url": j.url or "",
                "date_posted": _to_iso_date(j.date_posted),
                "skills": sorted(set(skills_by_job_id.get(j.id, []))),
            }
        )
    return out


def build_profile_query(profile: dict) -> str:
    role_text = ", ".join(profile.get("target_roles", [])[:5])
    skill_text = ", ".join(profile.get("skills_you_have", [])[:8])
    watch_text = ", ".join(profile.get("skills_to_watch_for", [])[:6])
    city_text = ", ".join(profile.get("priority_cities", [])[:5])
    return (
        f"Relevant jobs for roles: {role_text}. "
        f"Skills: {skill_text}. "
        f"Bonus skills: {watch_text}. "
        f"Cities: {city_text}."
    )


def semantic_scores_for_jobs(job_ids: list[int], profile: dict) -> dict[int, float]:
    if not job_ids:
        return {}
    try:
        query = build_profile_query(profile)
        hits = search_jobs_semantic(query, top_k=min(50, max(20, len(job_ids))))
    except Exception:
        return {}
    max_score = max((float(h.get("score") or 0.0) for h in hits), default=0.0)
    if max_score <= 0:
        return {}
    out: dict[int, float] = {}
    wanted = set(int(i) for i in job_ids)
    for hit in hits:
        job_id = hit.get("job_id")
        if job_id is None:
            continue
        jid = int(job_id)
        if jid not in wanted:
            continue
        normalized = max(0.0, min(1.0, float(hit.get("score") or 0.0) / max_score))
        existing = out.get(jid, 0.0)
        if normalized > existing:
            out[jid] = normalized
    return out


def _role_keyword_map() -> list[tuple[str, list[str]]]:
    """
    Role-to-keywords map (English + common German variants).

    Keep keywords relatively explicit to reduce false positives.
    """
    return [
        (
            "Data Analyst",
            [
                "data analyst",
                "datenanalyst",
                "data analytics",
                "data reporting",
                "reporting analyst",
                "datenanalyse",
            ],
        ),
        (
            "Business Intelligence",
            [
                "business intelligence",
                "bi analyst",
                "bi-analyst",
                "bi entwickler",
                "bi-entwickler",
                "kennzahlen",
                "kpi",
                "dashboard",
                "dashboards",
                "reporting",
            ],
        ),
        (
            "Manufacturing Analytics",
            [
                "manufacturing analytics",
                "production analytics",
                "fertigungsanalyst",
                "fertigungsanalysten",
                "fertigungsanalytics",
                "fertigungsanalyt",
                "automotive",
                "automobil",
                "werk",
            ],
        ),
        (
            "Supply Chain Analytics",
            [
                "supply chain analytics",
                "supply chain",
                "lieferkette",
                "logistics",
                "logistik",
                "demand forecasting",
                "forecasting",
                "warehouse",
                "lager",
            ],
        ),
        (
            "Predictive Maintenance",
            [
                "predictive maintenance",
                "vorhersage",
                "instandhaltung",
                "maintenance",
                "anomal",
                "zustand",
            ],
        ),
    ]


def score_job_for_profile(job: dict, profile: dict | None = None) -> dict:
    """
    Score one job for the given profile.

    Returns:
      - match_score: float in [0, 1]
      - reasons: short strings
      - skill_gaps: watchlist skills missing from the job
    """
    profile = profile or build_profile()
    weights = profile.get("weights", {})
    w_role = float(weights.get("role", 0.35))
    w_location = float(weights.get("location", 0.25))
    w_skills = float(weights.get("skills", 0.40))
    w_watch = float(weights.get("watch_bonus", 0.15))
    w_edge = float(weights.get("sector_edge", 0.15))
    w_semantic = float(weights.get("semantic", 0.20))
    w_freshness = float(weights.get("freshness", 0.10))
    job_title = job.get("title", "")
    job_desc = job.get("description", "")
    job_location = job.get("location", "")
    job_skills = set(job.get("skills", []) or [])

    # We match keywords in a normalized text representation for robustness.
    combined_text = _normalize_text(f"{job_title} {job_desc}")

    priority_cities = {_normalize_text(c) for c in profile["priority_cities"]}
    secondary_cities = {_normalize_text(c) for c in profile["secondary_cities"]}
    normalized_location = _normalize_text(job_location)

    reasons: list[str] = []

    # 1) Role/title keyword match
    role_map = _role_keyword_map()
    role_hits: list[str] = []
    for role_name, keywords in role_map:
        if any(k in combined_text for k in keywords):
            role_hits.append(role_name)

    role_score = 0.0
    if role_hits:
        # More role hits => higher score, capped.
        role_score = min(w_role, 0.12 * len(role_hits))
        reasons.append(f"Role keywords matched: {', '.join(role_hits[:2])}")

    # 2) Location match (explicit city priority)
    location_score = 0.0
    top_city = None
    for c in priority_cities:
        if c and c in normalized_location:
            location_score = w_location
            top_city = c
            break
    if location_score <= 0.0:
        for c in secondary_cities:
            if c and c in normalized_location:
                location_score = min(w_location, 0.12)
                top_city = c
                break
    if top_city:
        reasons.append(f"Location preference matched: {top_city}")

    # 3) Skill overlap
    skills_you_have = set(profile["skills_you_have"])
    skills_to_watch_for = set(profile["skills_to_watch_for"])

    overlap = skills_you_have.intersection(job_skills)
    overlap_ratio = (len(overlap) / max(1, len(skills_you_have)))
    skill_score = min(w_skills, w_skills * overlap_ratio)
    if overlap:
        # Keep it short and readable for a demo.
        reasons.append(f"Skills overlap: {', '.join(sorted(overlap)[:3])}")

    watch_present = skills_to_watch_for.intersection(job_skills)
    watch_bonus = 0.0
    if watch_present:
        watch_bonus = min(w_watch, 0.03 * len(watch_present))
        reasons.append(f"Nice-to-have present: {', '.join(sorted(watch_present)[:3])}")

    # 4) Engineering/sector edge weighting
    # We don’t want this to dominate the score, but it should give the project
    # the "engineering background advantage" you can explain to the class.
    edge_hits = 0
    for kw in profile["sector_edge_keywords"]:
        if _normalize_text(kw) and _normalize_text(kw) in combined_text:
            edge_hits += 1
    edge_score = min(w_edge, 0.02 * edge_hits)
    if edge_hits:
        reasons.append("Engineering/industry signals found")

    posted_date = _parse_iso_date(job.get("date_posted"))
    freshness_score = 0.0
    if posted_date:
        age_days = max(0, (date.today() - posted_date).days)
        if age_days <= 3:
            freshness_score = w_freshness
        elif age_days <= 7:
            freshness_score = w_freshness * 0.65
        elif age_days <= 14:
            freshness_score = w_freshness * 0.35

    semantic_raw = float(job.get("semantic_score") or 0.0)
    semantic_score = max(0.0, min(w_semantic, semantic_raw * w_semantic))

    # 4b) German-language requirement (tiered — see language_gate.py).
    # Only an explicit C1/C2/verhandlungssicher demand is penalised; the user
    # has worked in German-only companies at B1, so boilerplate is a note only.
    language = detect_german_requirement(f"{job_title} {job_desc}")
    language_penalty = 0.0
    language_bonus = 0.0
    if language["tier"] == TIER_REQUIRED_C1:
        language_penalty = 0.15
        reasons.append("German C1+/verhandlungssicher explicitly required")
    elif language["tier"] == TIER_ENGLISH_FRIENDLY:
        language_bonus = 0.05
        reasons.append("English-friendly posting")

    # Combine into final score
    match_score = role_score + location_score + skill_score + watch_bonus + edge_score + freshness_score + semantic_score
    match_score = match_score - language_penalty + language_bonus
    match_score = max(0.0, min(1.0, match_score))

    # 5) Skill gaps (from skills_to_watch_for)
    skill_gaps: list[str] = []
    for s in sorted(skills_to_watch_for):
        if s not in job_skills:
            skill_gaps.append(s)
        if len(skill_gaps) >= 5:
            break

    return {
        "match_score": round(match_score, 3),
        "reasons": reasons[:5],
        "skill_gaps": skill_gaps,
        # These fields are helpful for debugging/UI; they’re not required.
        "language": language,
        "debug": {
            "score_components": {
                "role": round(role_score, 3),
                "location": round(location_score, 3),
                "skills": round(skill_score, 3),
                "watch_bonus": round(watch_bonus, 3),
                "sector_edge": round(edge_score, 3),
                "freshness": round(freshness_score, 3),
                "semantic": round(semantic_score, 3),
                "language_penalty": round(-language_penalty, 3),
                "language_bonus": round(language_bonus, 3),
            },
            "role_hits": role_hits,
            "overlap_skills": sorted(overlap),
            "watch_present": sorted(watch_present),
        },
    }

