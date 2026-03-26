"""
Small scoring runner for validating the matcher logic.

Usage:
  python scripts/score_profile.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is on sys.path when running as a file.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.db.session import SessionLocal
from app.profile import build_profile
from app.services.matcher import load_active_jobs_with_skills, score_job_for_profile


def main() -> None:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is missing. Fill it in your .env.")
    if not SessionLocal:
        raise RuntimeError("DB session not configured. Check DATABASE_URL.")

    profile = build_profile()

    with SessionLocal() as session:
        jobs = load_active_jobs_with_skills(session=session, limit=50)

    scored = []
    for j in jobs:
        s = score_job_for_profile(j, profile=profile)
        scored.append({**j, **s})

    scored.sort(key=lambda x: x.get("match_score", 0), reverse=True)

    print(f"Found {len(scored)} jobs. Top 10 matches:\n")
    for row in scored[:10]:
        print(f"- id={row['id']} score={row['match_score']} :: {row['title']} @ {row['location']}")
        if row.get("reasons"):
            for r in row["reasons"][:3]:
                print(f"    * {r}")
        if row.get("skill_gaps"):
            print(f"    gaps: {', '.join(row['skill_gaps'])}")
        print()


if __name__ == "__main__":
    main()

