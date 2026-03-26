"""
Streamlit entrypoint.

Windows note:
When running `streamlit run dashboard/streamlit_app.py`, Streamlit may not add the
repository root to the Python import path, so `import app...` can fail.
We bootstrap `sys.path` here to make imports stable.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is on sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st
import pandas as pd

from app.config import settings
from app.db.session import SessionLocal
from app.profile import build_profile
from app.services.matcher import load_active_jobs_with_skills, score_job_for_profile

# Optional: agent-based "Ask the market" section (implemented next).
from app.agent.agent_core import handle_user_query

st.set_page_config(page_title="StellenRadar", layout="wide")
st.title("StellenRadar Dashboard")
st.caption("Scraping-first German job intelligence")


def _load_and_score_jobs(limit: int) -> list[dict]:
    """
    Load jobs+skills from the DB and compute match scores for the current profile.
    """
    profile = build_profile()

    if not SessionLocal:
        return []

    with SessionLocal() as session:
        jobs = load_active_jobs_with_skills(session=session, limit=limit)
        scored: list[dict] = []
        for j in jobs:
            scoring = score_job_for_profile(j, profile=profile)
            scored.append({**j, **scoring})
        return scored


st.sidebar.header("Controls")
limit = st.sidebar.slider("Jobs to score", min_value=10, max_value=200, value=50, step=10)
refresh = st.sidebar.button("Refresh now")

if refresh:
    st.rerun()


st.header("Profile Match")
if not settings.database_url:
    st.warning("Set `DATABASE_URL` (Neon Postgres) in your environment to enable scoring.")
elif not SessionLocal:
    st.warning("DB session is not configured. Check `DATABASE_URL` and your `.env`.")
else:
    scored_jobs = _load_and_score_jobs(limit=limit)

    if not scored_jobs:
        st.info("No jobs found (or no skills extracted yet). Run `python scripts/run_scrape.py`.")
    else:
        top = sorted(scored_jobs, key=lambda x: x.get("match_score", 0), reverse=True)[:20]
        df = pd.DataFrame(
            [
                {
                    "id": j["id"],
                    "match_score": j["match_score"],
                    "title": j["title"],
                    "company": j["company"],
                    "location": j["location"],
                    "date_posted": j.get("date_posted"),
                }
                for j in top
            ]
        )

        st.dataframe(df, use_container_width=True)

        st.subheader("Why these matches?")
        for j in top[:8]:
            with st.expander(f'{j["title"]} — {j["company"]} ({j["location"]})'):
                # Direct application link (opens the job posting).
                if j.get("url"):
                    st.link_button("Apply", j["url"])
                st.write(f"Match score: **{j['match_score']}**")
                if j.get("reasons"):
                    st.write("Reasons:")
                    for r in j["reasons"]:
                        st.write(f"- {r}")
                if j.get("skill_gaps"):
                    st.write("Skill gaps to watch:")
                    for gap in j["skill_gaps"]:
                        st.write(f"- {gap}")


st.header("Ask the market (free-text)")
st.caption("Use natural language. The agent will convert it into an action and then answer.")

user_question = st.text_input("Your question", placeholder="e.g. Top manufacturing analytics roles in Stuttgart, including match explanations")
if st.button("Ask"):
    if not user_question.strip():
        st.warning("Type a question first.")
    else:
        # The agent will (eventually) call matcher/tools.
        result = handle_user_query(user_question)
        st.write(result.get("reply", ""))
        # If the agent returns structured data, show it lightly.
        if "top_jobs" in result and result["top_jobs"]:
            st.write("Top jobs:")
            for j in result["top_jobs"][:5]:
                st.write(f'- {j["id"]}: {j["title"]} ({j["company"]}, {j["location"]}) score={j["match_score"]}')
