"""
Streamlit entrypoint – StellenRadar Dashboard.

Windows note:
When running `streamlit run dashboard/streamlit_app.py`, Streamlit may not add the
repository root to the Python import path, so `import app...` can fail.
We bootstrap `sys.path` here to make imports stable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

try:
    import pandas as pd
    from openai import OpenAI
    from sqlalchemy import create_engine, func
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
except ImportError as e:
    st.error(
        "Missing required dependency for the dashboard.\n\n"
        f"Import error: `{e}`\n\n"
        "Fix: ensure your environment installed `requirements.txt`."
    )
    st.stop()

from app.config import settings
from app.db.models import Job, SavedJob, Skill
from app.db.session import SessionLocal as _SessionLocal
from app.profile import (
    get_effective_profile,
    load_profile_mode_from_db,
    reset_profile_to_default_in_db,
    save_profile_mode_to_db,
    save_profile_to_db,
)
from app.services.matcher import (
    load_active_jobs_with_skills,
    score_job_for_profile,
    semantic_scores_for_jobs,
)
from app.services.pinecone_store import embed_text, search_jobs_semantic
from app.services.notion_service import create_job_tracking_page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_setting(key: str) -> str:
    try:
        return str(st.secrets.get(key, "")) or ""
    except Exception:
        return ""


def _get_session_local():
    if _SessionLocal:
        return _SessionLocal
    db_url = _get_setting("DATABASE_URL") or settings.database_url
    if not db_url:
        return None
    engine = create_engine(db_url, echo=False, future=True)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


SessionLocal = _get_session_local()
OPENAI_API_KEY = _get_setting("OPENAI_API_KEY") or settings.openai_api_key


def _profile_hash(profile: dict) -> str:
    """Deterministic short hash so cache busts when the profile changes."""
    raw = json.dumps(profile, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="StellenRadar", layout="wide")
st.title("StellenRadar Dashboard")
st.caption("Scraping-first German job intelligence")


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner=False)
def _load_and_score_jobs(limit: int, profile_key: str) -> list[dict]:
    """Load jobs + skills from DB and compute match scores.

    ``profile_key`` is included so the cache invalidates when the user
    edits their matching profile.
    """
    if not SessionLocal:
        return []
    with SessionLocal() as session:
        profile = get_effective_profile(session=session)
        jobs = load_active_jobs_with_skills(session=session, limit=limit)
        semantic_map = semantic_scores_for_jobs(
            [int(j["id"]) for j in jobs], profile=profile
        )
        scored: list[dict] = []
        for j in jobs:
            j["semantic_score"] = semantic_map.get(int(j["id"]), 0.0)
            scoring = score_job_for_profile(j, profile=profile)
            scored.append({**j, **scoring})
        return scored


def _save_job_to_notion_and_db(
    job_id: int, match_score: float, notes: str = ""
) -> str:
    if not SessionLocal:
        raise RuntimeError("DB is not configured. Set DATABASE_URL.")
    with SessionLocal() as session:
        job: Job | None = (
            session.query(Job).filter(Job.id == int(job_id)).first()
        )
        if not job:
            raise RuntimeError(f"Job id={job_id} not found.")
        job_dict = {
            "id": job.id,
            "title": job.title or "",
            "company": job.company or "",
            "location": job.location or "",
            "description": job.description or "",
            "url": job.url or "",
        }
        notion_page_id = create_job_tracking_page(
            job=job_dict, match_score=float(match_score), notes=notes or ""
        )
        existing: SavedJob | None = (
            session.query(SavedJob).filter(SavedJob.job_id == int(job_id)).first()
        )
        if existing:
            existing.match_score = float(match_score)
            existing.notes = (notes or existing.notes or "").strip()
            existing.notion_page_id = notion_page_id
        else:
            saved = SavedJob(
                job_id=int(job_id),
                notion_page_id=notion_page_id,
                match_score=float(match_score),
                notes=(notes or "").strip(),
                status="considering",
            )
            session.add(saved)
        session.commit()
        return notion_page_id


@st.cache_data(ttl=3600)
def _load_skill_trends():
    if not SessionLocal:
        return (
            pd.DataFrame(columns=["week_start", "skill_name", "count"]),
            None,
            None,
        )
    with SessionLocal() as session:
        min_date, max_date = session.query(
            func.min(Job.date_scraped), func.max(Job.date_scraped)
        ).one()
        week_col = func.date_trunc("week", Job.date_scraped).label("week_start")
        q = (
            session.query(
                week_col,
                Skill.skill_name,
                func.count(Skill.id).label("count"),
            )
            .join(Job, Job.id == Skill.job_id)
            .group_by(week_col, Skill.skill_name)
        )
        rows = q.all()
    df = pd.DataFrame(rows, columns=["week_start", "skill_name", "count"])
    return df, min_date, max_date


# ---------------------------------------------------------------------------
# Sidebar – navigation & controls
# ---------------------------------------------------------------------------

st.sidebar.header("Navigation")
page = st.sidebar.radio(
    "Page",
    ["Profile Match", "Ask the market", "Skill trends", "Saved Jobs"],
    index=0,
)

st.sidebar.header("Controls")

ctrl_left, ctrl_right = st.sidebar.columns(2)
with ctrl_left:
    if st.button(
        "Refresh data",
        use_container_width=True,
        help="Clear cached scores and reload from the database",
    ):
        st.cache_data.clear()
        st.rerun()
with ctrl_right:
    run_scraper = st.button(
        "Run scraper",
        use_container_width=True,
        help="Execute the scraping pipeline (scripts/run_scrape.py) to fetch new jobs",
    )

if run_scraper:
    with st.sidebar.status("Running scraper...", expanded=True) as status:
        try:
            # Scraper runtime is highly variable; allow tuning to prevent Streamlit timeouts.
            scrape_timeout_sec = int(st.session_state.get("scrape_timeout_sec", 600))
            max_roles = int(st.session_state.get("scrape_max_roles", 3))
            max_cities = int(st.session_state.get("scrape_max_cities", 3))
            max_pages = int(st.session_state.get("scrape_max_pages", 2))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.run_scrape",
                    "--max-roles",
                    str(max_roles),
                    "--max-cities",
                    str(max_cities),
                    "--max-pages",
                    str(max_pages),
                ],
                capture_output=True,
                text=True,
                timeout=scrape_timeout_sec,
                cwd=str(REPO_ROOT),
            )
            if result.returncode == 0:
                for line in (result.stdout or "").strip().split("\n")[-8:]:
                    st.sidebar.text(line)
                st.cache_data.clear()
                status.update(label="Scraper finished!", state="complete")
            else:
                st.sidebar.error("Scraper failed.")
                st.sidebar.code(
                    (result.stderr or "")[-500:] or "No error output"
                )
                status.update(label="Scraper failed", state="error")
        except subprocess.TimeoutExpired:
            st.sidebar.error(f"Scraper timed out ({scrape_timeout_sec} s).")
            status.update(label="Timed out", state="error")
        except Exception as e:
            st.sidebar.error(f"Could not start scraper: {e}")
            status.update(label="Error", state="error")


# ---------------------------------------------------------------------------
# Sidebar – profile editor
# ---------------------------------------------------------------------------

if SessionLocal:
    st.sidebar.divider()
    st.sidebar.subheader("Profile settings")
    with SessionLocal() as session:
        current_profile = get_effective_profile(session=session)
        current_mode = load_profile_mode_from_db(session=session)

    # --- Scraper tuning controls (sidebar) ---
    with st.sidebar.expander("Scraper settings", expanded=False):
        st.slider(
            "Scraper timeout (seconds)",
            min_value=180,
            max_value=2400,
            value=600,
            step=60,
            key="scrape_timeout_sec",
            help="Increase if StepStone is slow or you have many queries.",
        )
        col_s1, col_s2, col_s3 = st.columns(3)
        with col_s1:
            st.number_input(
                "Max roles",
                min_value=1,
                max_value=10,
                value=3,
                step=1,
                key="scrape_max_roles",
                help="Only the first N target roles are used to scrape.",
            )
        with col_s2:
            st.number_input(
                "Max cities",
                min_value=1,
                max_value=10,
                value=3,
                step=1,
                key="scrape_max_cities",
                help="Only the first N priority cities are used to scrape.",
            )
        with col_s3:
            st.number_input(
                "Max pages",
                min_value=1,
                max_value=10,
                value=2,
                step=1,
                key="scrape_max_pages",
                help="How many StepStone result pages to read per query.",
            )

    with st.sidebar.expander("Edit matching profile", expanded=False):
        profile_mode = st.radio(
            "Profile source",
            options=["Default (use code defaults)", "Custom (use saved profile)"],
            index=0 if current_mode == "default" else 1,
            help=(
                "Default: always use the profile from app/profile.py.\n"
                "Custom: use the saved profile from the database."
            ),
            key="profile_mode_selector",
        )
        selected_mode = "default" if profile_mode.startswith("Default") else "custom"
        if selected_mode != current_mode:
            with SessionLocal() as session:
                save_profile_mode_to_db(session=session, mode=selected_mode)
                session.commit()
            st.cache_data.clear()
            st.rerun()

        col_p1, col_p2 = st.columns(2)
        with col_p1:
            if st.button(
                "Reset to default",
                use_container_width=True,
                help="Overwrite saved profile with code defaults and switch to Default mode.",
            ):
                with SessionLocal() as session:
                    reset_profile_to_default_in_db(session=session)
                    session.commit()
                st.cache_data.clear()
                st.success("Reset to default profile.")
                st.rerun()

        # Show the profile that will be used, based on selected mode.
        if selected_mode == "default":
            with SessionLocal() as session:
                # get_effective_profile(session) will return defaults when mode=default,
                # but we want the exact default values immediately even before rerun.
                current_profile = get_effective_profile(session=session)
        else:
            # Custom mode already loaded into current_profile above.
            pass

        roles = st.text_area(
            "Target roles (comma-separated)",
            value=", ".join(current_profile.get("target_roles", [])),
            key="profile_roles",
        )
        cities = st.text_area(
            "Priority cities (comma-separated)",
            value=", ".join(current_profile.get("priority_cities", [])),
            key="profile_priority_cities",
        )
        skills_have = st.text_area(
            "Skills you have (comma-separated)",
            value=", ".join(current_profile.get("skills_you_have", [])),
            key="profile_skills_have",
        )
        skills_watch = st.text_area(
            "Skills to watch (comma-separated)",
            value=", ".join(current_profile.get("skills_to_watch_for", [])),
            key="profile_skills_watch",
        )
        col_a, col_b = st.columns(2)
        with col_a:
            semantic_weight = st.slider(
                "Semantic weight",
                min_value=0.0,
                max_value=0.4,
                value=float(
                    current_profile.get("weights", {}).get("semantic", 0.2)
                ),
                step=0.01,
                key="profile_semantic_weight",
            )
        with col_b:
            freshness_weight = st.slider(
                "Freshness weight",
                min_value=0.0,
                max_value=0.2,
                value=float(
                    current_profile.get("weights", {}).get("freshness", 0.1)
                ),
                step=0.01,
                key="profile_freshness_weight",
            )
        if st.button("Save profile settings", key="save_profile_settings"):

            def _split_csv(raw: str) -> list[str]:
                return [item.strip() for item in raw.split(",") if item.strip()]

            updated = dict(current_profile)
            updated["target_roles"] = _split_csv(roles)
            updated["priority_cities"] = _split_csv(cities)
            updated["skills_you_have"] = _split_csv(skills_have)
            updated["skills_to_watch_for"] = _split_csv(skills_watch)
            updated.setdefault("weights", {})
            updated["weights"]["semantic"] = float(semantic_weight)
            updated["weights"]["freshness"] = float(freshness_weight)
            with SessionLocal() as session:
                save_profile_to_db(session=session, profile=updated)
                session.commit()
            st.cache_data.clear()
            st.success("Profile updated – scores will recalculate.")


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Profile Match
# ═══════════════════════════════════════════════════════════════════════════

if page == "Profile Match":
    st.header("Profile Match")

    limit = st.sidebar.slider(
        "Jobs to load from DB",
        min_value=10,
        max_value=200,
        value=50,
        step=10,
        help=(
            "How many of the most-recent active jobs to pull from the "
            "database for scoring. Higher = more coverage but slower to load."
        ),
    )
    top_n = st.sidebar.slider(
        "Show top N matches",
        min_value=5,
        max_value=min(limit, 100),
        value=min(20, limit),
        step=5,
        help="How many top-scored jobs to display in the results.",
    )

    if not (settings.database_url or _get_setting("DATABASE_URL")):
        st.warning(
            "Set `DATABASE_URL` (Neon Postgres) in your environment to enable scoring."
        )
    elif not SessionLocal:
        st.warning(
            "DB session is not configured. Check `DATABASE_URL` and your `.env`."
        )
    else:
        with SessionLocal() as session:
            profile = get_effective_profile(session=session)
        p_key = _profile_hash(profile)

        with st.spinner(f"Scoring {limit} jobs against your profile..."):
            scored_jobs = _load_and_score_jobs(limit=limit, profile_key=p_key)

        if not scored_jobs:
            st.info(
                "No jobs found (or no skills extracted yet). "
                "Click **Run scraper** in the sidebar to populate jobs."
            )
        else:
            top = sorted(
                scored_jobs,
                key=lambda x: x.get("match_score", 0),
                reverse=True,
            )[:top_n]

            # ── Summary metrics ───────────────────────────────────────
            scores = [j["match_score"] for j in top]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Jobs scored", len(scored_jobs))
            m2.metric("Showing top", len(top))
            m3.metric("Best match", f"{max(scores):.0%}")
            m4.metric("Avg match", f"{sum(scores) / len(scores):.0%}")

            # ── Filters ───────────────────────────────────────────────
            with st.container():
                f1, f2, f3 = st.columns([2, 2, 1])
                with f1:
                    all_locs = sorted(
                        {j.get("location", "") for j in top if j.get("location")}
                    )
                    sel_locs = st.multiselect(
                        "Filter by location",
                        options=all_locs,
                        default=[],
                        key="loc_filter",
                    )
                with f2:
                    min_score = st.slider(
                        "Min match score",
                        0.0,
                        1.0,
                        0.0,
                        0.05,
                        key="min_score",
                    )
                with f3:
                    st.write("")
                    expand_all = st.checkbox("Expand all", key="expand_all")

            filtered = top
            if sel_locs:
                filtered = [
                    j for j in filtered if j.get("location", "") in sel_locs
                ]
            if min_score > 0:
                filtered = [
                    j
                    for j in filtered
                    if j.get("match_score", 0) >= min_score
                ]

            if not filtered:
                st.info(
                    "No jobs match the current filters. Try relaxing them."
                )
            else:
                # ── Results table with clickable Apply links ──────────
                table_rows = []
                for j in filtered:
                    table_rows.append(
                        {
                            "Score": round(j["match_score"], 3),
                            "Title": j["title"],
                            "Company": j["company"],
                            "Location": j["location"],
                            "Posted": j.get("date_posted") or "",
                            "Apply": j.get("url") or "",
                        }
                    )
                df = pd.DataFrame(table_rows)
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Score": st.column_config.ProgressColumn(
                            "Score",
                            min_value=0.0,
                            max_value=1.0,
                            format="%.0%%",
                        ),
                        "Apply": st.column_config.LinkColumn(
                            "Apply", display_text="Open"
                        ),
                    },
                )

                # ── Job detail cards (ALL filtered jobs) ──────────────
                st.subheader(f"Job details  ({len(filtered)} matches)")

                for j in filtered:
                    score_val = j["match_score"]
                    icon = (
                        "+" if score_val >= 0.6 else ("~" if score_val >= 0.35 else "-")
                    )
                    label = (
                        f"[{score_val:.0%}] "
                        f'{j["title"]}  —  {j["company"]}  ({j["location"]})'
                    )
                    with st.expander(label, expanded=expand_all):
                        info_col, action_col = st.columns([3, 1])

                        with info_col:
                            st.markdown(f"**Match score:** {score_val:.1%}")
                            components = (
                                (j.get("debug") or {}).get("score_components")
                                or {}
                            )
                            if components:
                                parts = [
                                    f"**{k}** {float(v):.0%}"
                                    for k, v in components.items()
                                    if float(v) > 0
                                ]
                                if parts:
                                    st.caption(
                                        "Breakdown: " + " · ".join(parts)
                                    )
                            if j.get("reasons"):
                                for r in j["reasons"]:
                                    st.markdown(f"- {r}")
                            if j.get("skill_gaps"):
                                st.markdown("**Skill gaps to watch:**")
                                st.markdown(
                                    ", ".join(
                                        f"`{g}`" for g in j["skill_gaps"]
                                    )
                                )

                        with action_col:
                            if j.get("url"):
                                st.link_button(
                                    "Apply / View",
                                    j["url"],
                                    use_container_width=True,
                                )
                            if j.get("date_posted"):
                                st.caption(f"Posted: {j['date_posted']}")

                        st.divider()
                        n_key = f"save_notes_{j['id']}"
                        b_key = f"save_btn_{j['id']}"
                        notes = st.text_area(
                            "Notes for Notion (optional)",
                            value="",
                            key=n_key,
                            placeholder="e.g. Strong fit; follow up next week.",
                            height=68,
                        )
                        if st.button("Save to Notion", key=b_key):
                            try:
                                notion_id = _save_job_to_notion_and_db(
                                    job_id=int(j["id"]),
                                    match_score=float(j["match_score"]),
                                    notes=notes,
                                )
                                st.success(
                                    "Saved!"
                                    + (
                                        f" (Notion page: {notion_id})"
                                        if notion_id
                                        else ""
                                    )
                                )
                            except Exception as e:
                                st.error(f"Save failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Ask the market
# ═══════════════════════════════════════════════════════════════════════════

elif page == "Ask the market":
    st.header("Ask the market")
    st.caption(
        "Ask free-form questions about the German job market. "
        "Answers are generated by GPT-4o-mini and grounded in real job "
        "listings stored in your database (via Pinecone semantic search)."
    )

    with st.expander("How does this work?"):
        st.markdown(
            "1. Your question is embedded with OpenAI and used to find the "
            "**most semantically relevant jobs** in Pinecone.\n"
            "2. The top 5 matching job descriptions are retrieved from your "
            "Postgres database.\n"
            "3. These listings, plus your profile context (target roles & "
            "cities), are sent to **GPT-4o-mini** which answers based "
            "**only** on the retrieved data.\n\n"
            "The quality of answers depends on how many jobs you have "
            "scraped. More jobs = richer context = better answers."
        )

    st.markdown("**Try an example:**")
    example_questions = [
        "Which companies are hiring Data Analysts in Stuttgart right now?",
        "What are the most common skills required for BI roles?",
        "Are there any remote-friendly data positions available?",
        "Compare job requirements between Berlin and Munich postings.",
        "Which roles mention SAP or Power BI?",
        "What salary ranges are mentioned for analyst positions?",
    ]
    example_cols = st.columns(3)
    for idx, eq in enumerate(example_questions):
        col = example_cols[idx % 3]
        if col.button(eq, key=f"example_{idx}", use_container_width=True):
            st.session_state["market_question"] = eq

    user_question = st.text_input(
        "Your question",
        value=st.session_state.get("market_question", ""),
        placeholder="e.g. Which companies hire for Data Analyst roles in Stuttgart right now?",
        key="market_q_input",
    )
    if st.button("Ask"):
        if not user_question.strip():
            st.warning("Type a question first.")
        elif not OPENAI_API_KEY:
            st.error(
                "Missing `OPENAI_API_KEY`. Set it in your environment to enable Ask the market."
            )
        elif (
            not (settings.database_url or _get_setting("DATABASE_URL"))
            or not SessionLocal
        ):
            st.error(
                "Database is not configured. Set `DATABASE_URL` to enable Ask the market."
            )
        else:
            with st.spinner("Retrieving relevant jobs..."):
                qvec = embed_text(user_question)
                retrieved = (
                    search_jobs_semantic(user_question, top_k=5) if qvec else []
                )

            if not retrieved:
                st.info(
                    "No relevant jobs found in semantic search yet. Try scraping more jobs first."
                )
            else:
                job_ids = [
                    int(r["job_id"])
                    for r in retrieved
                    if r.get("job_id") is not None
                ]

                with SessionLocal() as session:
                    rows: list[Job] = (
                        session.query(Job).filter(Job.id.in_(job_ids)).all()
                    )
                    by_id = {int(j.id): j for j in rows}

                context_lines = [
                    "Here are relevant job listings from the database:\n"
                ]
                sources_used: list[str] = []
                for i, hit in enumerate(retrieved, start=1):
                    jid = hit.get("job_id")
                    if jid is None:
                        continue
                    row = by_id.get(int(jid))
                    if not row:
                        continue
                    title = row.title or hit.get("title") or ""
                    company = row.company or hit.get("company") or ""
                    location = row.location or hit.get("location") or ""
                    desc = (row.description or "").strip().replace("\n", " ")
                    snippet = desc[:400]
                    context_lines.append(
                        f"[Job {i}: {title} at {company}, {location}]"
                    )
                    context_lines.append(snippet)
                    context_lines.append("")
                    sources_used.append(f"{title} - {company}")

                context_string = "\n".join(context_lines).strip()

                with SessionLocal() as session:
                    profile = get_effective_profile(session=session)
                profile_hint = (
                    "User profile context: "
                    + ", ".join(profile.get("target_roles", [])[:4])
                    + " | cities: "
                    + ", ".join(profile.get("priority_cities", [])[:4])
                )

                client = OpenAI(api_key=OPENAI_API_KEY)
                with st.spinner("Analyzing the market..."):
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        temperature=0.2,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are a German job market analyst. "
                                    "Answer based only on the provided job listings. "
                                    "Be specific and cite companies or roles by name."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    profile_hint
                                    + "\n\n"
                                    + context_string
                                    + "\n\nQuestion: "
                                    + user_question
                                ),
                            },
                        ],
                    )

                answer = resp.choices[0].message.content or ""
                st.write(answer)

                with st.expander("Sources used"):
                    for s in sources_used:
                        st.write(f"- {s}")


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Saved Jobs
# ═══════════════════════════════════════════════════════════════════════════

elif page == "Saved Jobs":
    st.header("Saved Jobs")

    if not SessionLocal:
        st.warning("Database is not configured.")
    else:
        with SessionLocal() as session:
            saved_rows = (
                session.query(SavedJob, Job)
                .join(Job, Job.id == SavedJob.job_id)
                .order_by(SavedJob.saved_at.desc())
                .all()
            )

        if not saved_rows:
            st.info(
                "No saved jobs yet. Save jobs from the **Profile Match** page."
            )
        else:
            st.metric("Total saved", len(saved_rows))
            table = []
            for saved, job in saved_rows:
                notion_url = ""
                if saved.notion_page_id and not saved.notion_page_id.startswith("simulated-"):
                    clean_id = saved.notion_page_id.replace("-", "")
                    notion_url = f"https://www.notion.so/{clean_id}"
                table.append(
                    {
                        "Score": round(saved.match_score or 0, 3),
                        "Title": job.title or "",
                        "Company": job.company or "",
                        "Location": job.location or "",
                        "Status": saved.status or "",
                        "Notes": saved.notes or "",
                        "Saved": (
                            str(saved.saved_at.date()) if saved.saved_at else ""
                        ),
                        "Job Link": job.url or "",
                        "Notion": notion_url,
                    }
                )
            df = pd.DataFrame(table)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Score": st.column_config.ProgressColumn(
                        "Score", min_value=0.0, max_value=1.0, format="%.0%%"
                    ),
                    "Job Link": st.column_config.LinkColumn(
                        "Job Link", display_text="Open"
                    ),
                    "Notion": st.column_config.LinkColumn(
                        "Notion", display_text="Open"
                    ),
                },
            )


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Skill trends
# ═══════════════════════════════════════════════════════════════════════════

elif page == "Skill trends":
    st.header("Skill trends")

    if (
        not (settings.database_url or _get_setting("DATABASE_URL"))
        or not SessionLocal
    ):
        st.warning("Set `DATABASE_URL` to enable skill trend analytics.")
    else:
        # -- Re-extract skills button ---------------------------------
        # Useful after updating the keyword dictionary in skill_extractor.py
        # without having to re-scrape from the web.
        if st.button(
            "Re-extract skills from existing jobs",
            help=(
                "Re-run the skill extractor on every job already in the "
                "database. Use this after the keyword dictionary has been "
                "updated so new skills are detected without re-scraping."
            ),
        ):
            from app.services.skill_extractor import extract_skills

            with st.spinner("Re-extracting skills for all jobs..."):
                updated = 0
                with SessionLocal() as session:
                    all_jobs: list[Job] = session.query(Job).all()
                    for job in all_jobs:
                        if not job.description:
                            continue
                        session.query(Skill).filter(
                            Skill.job_id == job.id
                        ).delete()
                        for item in extract_skills(job.description):
                            session.add(
                                Skill(
                                    job_id=job.id,
                                    skill_name=item["skill_name"],
                                    category=item["category"],
                                )
                            )
                        updated += 1
                    session.commit()
            st.cache_data.clear()
            st.success(f"Re-extracted skills for {updated} jobs.")
            st.rerun()

        # -- Load data ------------------------------------------------
        df_weekly, min_date, max_date = _load_skill_trends()
        if min_date and max_date:
            st.caption(
                f"Scraped data range: **{min_date.date()}** to **{max_date.date()}**"
            )

        if df_weekly.empty:
            st.info(
                "No skills data found yet. Run the scraper to populate skills."
            )
        else:
            min_threshold = st.sidebar.slider(
                "Min occurrences to show",
                min_value=1,
                max_value=20,
                value=2,
                step=1,
                help="Only display skills that appear at least this many times across all jobs.",
            )
            category_filter = st.sidebar.multiselect(
                "Skill categories",
                options=["technical", "soft", "language"],
                default=["technical", "soft", "language"],
                help="Filter skills by category.",
            )

            totals = (
                df_weekly.groupby("skill_name")["count"]
                .sum()
                .reset_index(name="total_count")
            )
            totals = totals[totals["total_count"] >= min_threshold]
            df_filtered = df_weekly.merge(
                totals[["skill_name"]], on="skill_name", how="inner"
            )

            # Apply category filter if we have category info in the DB.
            if category_filter and SessionLocal:
                with SessionLocal() as session:
                    cat_rows = (
                        session.query(
                            Skill.skill_name, Skill.category
                        )
                        .distinct()
                        .all()
                    )
                cat_map = {r.skill_name: r.category for r in cat_rows}
                allowed_skills = {
                    name
                    for name, cat in cat_map.items()
                    if cat in category_filter
                }
                if allowed_skills:
                    df_filtered = df_filtered[
                        df_filtered["skill_name"].isin(allowed_skills)
                    ]

            if df_filtered.empty:
                st.info(
                    f"No skills meet the minimum threshold of {min_threshold}. "
                    "Try lowering it in the sidebar or re-extracting skills."
                )
            else:
                df_filtered["week_start"] = pd.to_datetime(
                    df_filtered["week_start"]
                )
                current_week = df_filtered["week_start"].max()
                last_week = current_week - pd.Timedelta(days=7)

                pivot = (
                    df_filtered.pivot_table(
                        index="skill_name",
                        columns="week_start",
                        values="count",
                        aggfunc="sum",
                        fill_value=0,
                    ).reset_index()
                )
                pivot["this_week_count"] = pivot.get(current_week, 0)
                pivot["last_week_count"] = pivot.get(last_week, 0)

                totals2 = (
                    df_filtered.groupby("skill_name")["count"]
                    .sum()
                    .reset_index(name="total_count")
                )
                merged = pivot.merge(totals2, on="skill_name", how="left")

                def _pct_change(row) -> float | None:
                    lw = float(row["last_week_count"])
                    tw = float(row["this_week_count"])
                    if lw == 0:
                        return None
                    return (tw - lw) / lw * 100.0

                merged["pct_change"] = merged.apply(_pct_change, axis=1)

                left, right = st.columns(2)

                with left:
                    st.subheader("Top skills (total)")
                    top20 = merged.sort_values(
                        "total_count", ascending=False
                    ).head(20)
                    chart_df = top20.set_index("skill_name")["total_count"]
                    st.bar_chart(chart_df)

                with right:
                    st.subheader("Trending week-over-week")
                    trend_df = merged.sort_values(
                        "pct_change", ascending=False
                    )[
                        [
                            "skill_name",
                            "this_week_count",
                            "last_week_count",
                            "pct_change",
                            "total_count",
                        ]
                    ]

                    def _color_trend(val):
                        if val is None or (
                            isinstance(val, float) and pd.isna(val)
                        ):
                            return ""
                        if val > 0:
                            return "color: green;"
                        if val < 0:
                            return "color: red;"
                        return ""

                    st.dataframe(
                        trend_df.style.applymap(
                            _color_trend, subset=["pct_change"]
                        ).format(
                            {
                                "pct_change": lambda x: (
                                    "" if pd.isna(x) else f"{x:.1f}%"
                                )
                            }
                        ),
                        use_container_width=True,
                    )

                # -- All skills summary table -------------------------
                with st.expander("All extracted skills"):
                    all_skills_df = merged[
                        ["skill_name", "total_count"]
                    ].sort_values("total_count", ascending=False)
                    st.dataframe(
                        all_skills_df,
                        use_container_width=True,
                        hide_index=True,
                    )
