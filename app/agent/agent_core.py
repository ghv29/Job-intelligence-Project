from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI
from openai import AuthenticationError, OpenAIError
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.models import AgentMemory, Job, SavedJob
from app.db.session import SessionLocal
from app.profile import get_effective_profile
from app.services.matcher import load_active_jobs_with_skills, score_job_for_profile, semantic_scores_for_jobs
from app.services.notion_service import create_job_tracking_page
from app.services.pinecone_store import retrieve_memories, save_memory


def _extract_first_int(text: str) -> int | None:
    m = re.search(r"\b(\d{1,8})\b", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _is_greeting(text: str) -> bool:
    msg = (text or "").strip().lower()
    if not msg:
        return False
    greetings = {"hi", "hello", "hey", "hallo", "guten tag", "servus", "moin"}
    if msg in greetings:
        return True
    return msg.startswith(("hi ", "hello ", "hey ", "hallo "))


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """
    Best-effort extraction of a JSON object from an LLM response.
    """
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def build_system_prompt(profile: dict[str, Any], memories: list[str] | None = None) -> str:
    """
    Build the system prompt for intent parsing, optionally injecting remembered preferences.

    Returns a system prompt string used to convert free text into a single JSON action.
    """
    memories = memories or []
    memories_section = ""
    if memories:
        bullets = "\n".join([f"- {m}" for m in memories])
        memories_section = "\n\n## Your remembered preferences\n" + bullets + "\n"

    return (
        "You are an assistant that helps with German job intelligence.\n"
        "Convert the user's message into a single JSON object.\n"
        "You must follow the schema exactly.\n"
        + memories_section
        + "\nSchema:\n"
        "{\n"
        '  "action": one of ["top_matches","save_job","help","explain_profile_match","save_memory"],\n'
        '  "top_k": integer (only for top_matches, default 5),\n'
        '  "city": string or null (optional filter),\n'
        '  "job_id": integer or null (only for save_job),\n'
        '  "notes": string (optional notes),\n'
        '  "content": string or null (only for save_memory),\n'
        '  "memory_type": string or null (only for save_memory; use "preference"),\n'
        '  "language": "en" (always)\n'
        "}\n"
        "Rules:\n"
        "- If the user greets (hello/hi/hallo), use action=help.\n"
        "- If the message asks for top matches, use action=top_matches.\n"
        "- If the message asks to save a job, use action=save_job and extract job_id.\n"
        "- If job_id is not present for save_job, set job_id=null.\n"
        "- If the user states a preference, shares feedback on a job, or says anything starting with "
        '"remember that", use action=save_memory.\n'
        "- For save_memory, set memory_type=preference and put the memory text in content.\n"
        "- If unsure, default to top_matches.\n"
        "- Output JSON only, no markdown.\n"
    )


def _deterministic_action(user_message: str) -> dict[str, Any]:
    """
    Fallback action parsing without an LLM.
    Keeps the app usable even without `OPENAI_API_KEY`.
    """
    msg = user_message.strip().lower()

    # Greetings: treat as "help/intro" instead of spamming top matches.
    if _is_greeting(user_message):
        return {"action": "help"}

    if msg.startswith("/help") or "help" in msg:
        return {"action": "help"}

    if msg.startswith("/top") or msg.startswith("top "):
        top_k = _extract_first_int(msg) or 5
        return {"action": "top_matches", "top_k": top_k}

    if msg.startswith("/save") or msg.startswith("save "):
        job_id = _extract_first_int(msg)
        return {"action": "save_job", "job_id": job_id}

    if "save" in msg:
        # e.g. "save 12"
        job_id = _extract_first_int(msg)
        return {"action": "save_job", "job_id": job_id}

    if msg.startswith("remember that"):
        content = user_message.strip()[len("remember that") :].strip()
        return {"action": "save_memory", "content": content or user_message.strip(), "memory_type": "preference"}

    # Default: interpret as "show top matches"
    top_k = _extract_first_int(msg) or 5
    return {"action": "top_matches", "top_k": top_k}


def _llm_action(
    user_message: str, profile: dict[str, Any], memories: list[str] | None = None
) -> dict[str, Any] | None:
    """
    Ask the LLM to convert the user's message into a structured action.

    We only use the LLM for intent parsing/explanations. The actual matching
    is still deterministic (DB + matcher), so behavior stays reliable.
    """
    if not settings.openai_api_key:
        return None

    client = OpenAI(api_key=settings.openai_api_key)

    system_prompt = build_system_prompt(profile=profile, memories=memories or [])

    user_prompt = (
        f"User message: {user_message}\n\n"
        f"Profile context (for intent): {json.dumps(profile, ensure_ascii=False)}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        action_text = resp.choices[0].message.content or ""
        return _extract_json_object(action_text)
    except AuthenticationError:
        # If the key is invalid/expired, don't crash the bot—fallback to deterministic.
        return None
    except OpenAIError:
        # Any transient OpenAI error: fallback to deterministic parsing.
        return None


def _load_job_by_id(session, job_id: int) -> dict[str, Any] | None:
    job: Job | None = session.query(Job).filter(Job.id == job_id).first()
    if not job:
        return None
    # Load skills for this job.
    skills = [s.skill_name for s in job.skills] if getattr(job, "skills", None) else []
    # If relationship isn't populated, fall back to explicit fetch.
    if not skills:
        from app.db.models import Skill

        skills = [s.skill_name for s in session.query(Skill).filter(Skill.job_id == job_id).all()]
    return {
        "id": job.id,
        "title": job.title or "",
        "company": job.company or "",
        "location": job.location or "",
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "description": job.description or "",
        "url": job.url or "",
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "skills": sorted(set(skills)),
    }


def _apply_filters_to_scored_jobs(
    scored_jobs: list[dict[str, Any]], action: dict[str, Any]
) -> list[dict[str, Any]]:
    city = action.get("city")
    if city:
        city_norm = str(city).strip().lower()
        scored_jobs = [
            j for j in scored_jobs if city_norm in str(j.get("location", "")).lower()
        ]
    return scored_jobs


def _save_job(session, job: dict[str, Any], match_score: float, notes: str = "") -> int:
    """
    Insert/update a row in saved_jobs for the selected job.
    """
    existing: SavedJob | None = (
        session.query(SavedJob).filter(SavedJob.job_id == job["id"]).first()
    )

    notion_page_id = create_job_tracking_page(job=job, match_score=match_score, notes=notes or "")

    if existing:
        existing.match_score = match_score
        existing.notes = notes or existing.notes
        existing.notion_page_id = notion_page_id
        return existing.id

    saved = SavedJob(
        job_id=job["id"],
        notion_page_id=notion_page_id,
        match_score=match_score,
        notes=notes or "",
        status="considering",
    )
    session.add(saved)
    session.flush()  # make saved.id available without waiting for commit
    return saved.id


def handle_user_query(
    user_message: str, conversation_history: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """
    Main entrypoint used by Streamlit and (later) Telegram.

    It returns a dict with:
      - reply: text for the UI
      - top_jobs: list (only for top_matches)
      - saved_job_id: int (only for save_job)
    """
    user_message = user_message or ""
    memories: list[str] = []
    try:
        memories = retrieve_memories(user_message, top_k=5)
    except Exception:
        memories = []

    # Make greeting behavior consistent even if LLM is enabled.
    if _is_greeting(user_message):
        action = {"action": "help"}
    else:
        action = _llm_action(user_message, profile=profile, memories=memories) or _deterministic_action(
            user_message
        )
    action_type = action.get("action")

    if not SessionLocal:
        return {"reply": "DB is not configured. Set `DATABASE_URL` in your environment.", "actions": []}

    try:
        with SessionLocal() as session:
            profile = get_effective_profile(session=session)
            if action_type == "help":
                return {
                    "reply": (
                        "Commands:\n"
                        "- top / /top <k>: show top matched jobs\n"
                        "- save <job_id>: save a job (Notion optional/simulated)\n"
                        "\nOr ask in free text like: "
                        "'Top manufacturing analytics roles in Stuttgart'."
                    )
                }

            if action_type == "save_job":
                job_id = action.get("job_id")
                if not job_id:
                    return {"reply": "I couldn't find the job id to save. Try: `save <id>`.", "actions": []}

                job = _load_job_by_id(session=session, job_id=int(job_id))
                if not job:
                    return {"reply": f"No job found with id={job_id}. Try `/top` first.", "actions": []}

                scored = score_job_for_profile(job, profile=profile)
                saved_id = _save_job(
                    session=session, job=job, match_score=scored["match_score"], notes=action.get("notes", "")
                )
                # Persist the save to the DB (SessionLocal uses autocommit=False).
                session.commit()
                apply_url = job.get("url") or ""
                return {
                    "reply": (
                        f"Saved: {job['title']} at {job['company']} (id={job['id']})."
                        + (f"\nApply: {apply_url}" if apply_url else "")
                    ),
                    "saved_job_id": saved_id,
                    "actions": [],
                }

            if action_type == "save_memory":
                content = (action.get("content") or "").strip()
                memory_type = (action.get("memory_type") or "preference").strip() or "preference"
                if not content:
                    content = user_message.strip()

                pinecone_id = ""
                try:
                    pinecone_id = save_memory(content=content, memory_type=memory_type)
                except Exception as e:
                    return {"reply": f"I couldn't save that memory right now (Pinecone error: {e}).", "actions": []}

                session.add(
                    AgentMemory(
                        memory_type=memory_type,
                        content=content,
                        pinecone_id=pinecone_id,
                    )
                )
                session.commit()
                return {"reply": "Got it — I’ll remember that preference.", "actions": []}

            # Default: top matches
            if action_type in {"top_matches", None}:
                top_k = int(action.get("top_k") or 5)
                scored_jobs = []
                jobs = load_active_jobs_with_skills(session=session, limit=max(50, top_k * 5))
                semantic_map = semantic_scores_for_jobs([int(j["id"]) for j in jobs], profile=profile)
                for j in jobs:
                    j["semantic_score"] = semantic_map.get(int(j["id"]), 0.0)
                    scoring = score_job_for_profile(j, profile=profile)
                    scored_jobs.append({**j, **scoring})

                scored_jobs = _apply_filters_to_scored_jobs(scored_jobs, action=action)
                scored_jobs.sort(key=lambda x: x.get("match_score", 0), reverse=True)

                top_jobs = scored_jobs[:top_k]
                if not top_jobs:
                    return {"reply": "No jobs matched your request.", "top_jobs": [], "actions": []}

                lines = ["Top matched jobs:"]
                for j in top_jobs:
                    lines.append(
                        f"- id={j['id']} score={j['match_score']}: {j['title']} @ {j['location']}"
                    )

                # Keep reply readable: include only the top reason snippets.
                lines.append("")
                lines.append("Highlights:")
                for j in top_jobs[:3]:
                    if j.get("reasons"):
                        lines.append(f"- id={j['id']}: {', '.join(j['reasons'][:3])}")

                return {"reply": "\n".join(lines), "top_jobs": top_jobs, "actions": []}

            # If LLM chose an unknown action, recover.
            return {"reply": "I didn't understand that. Try 'top' or 'save <id>'.", "actions": []}

    except SQLAlchemyError as e:
        return {"reply": f"DB error: {e}", "actions": []}
