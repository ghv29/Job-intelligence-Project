"""
Export recently scraped StepStone jobs to the career-ops pipeline.md inbox.

Only jobs that pass the profile match gate are exported: career-ops
evaluations cost real time and tokens per URL, so junk (forklift drivers,
receptionists) must never reach the queue. Jobs with an explicit German
C1+/verhandlungssicher requirement are still exported but visibly marked,
never silently dropped.

Usage:
    python scripts/export_to_career_ops.py
    python scripts/export_to_career_ops.py --days 3
    python scripts/export_to_career_ops.py --days 7 --limit 20 --min-score 0.5
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.models import Job, Skill
from app.db.session import SessionLocal
from app.profile import get_effective_profile
from app.services.language_gate import TIER_ENGLISH_FRIENDLY, TIER_REQUIRED_C1
from app.services.matcher import score_job_for_profile

CAREER_OPS_ROOT = Path(r"D:\Ironhack\Github\career-ops")
PIPELINE_FILE = CAREER_OPS_ROOT / "data" / "pipeline.md"
JDS_DIR = CAREER_OPS_ROOT / "jds"

DEFAULT_MIN_SCORE = 0.45
DEFAULT_LIMIT = 15

# career-ops modes/pipeline.md expects English section headers.
PIPELINE_TEMPLATE = """\
# Job Pipeline — Inbox

Add StepStone job URLs here, then run `/career-ops pipeline` to evaluate them.

## Pending

## Processed
"""


def _read_pipeline() -> str:
    if not PIPELINE_FILE.exists():
        return PIPELINE_TEMPLATE
    return PIPELINE_FILE.read_text(encoding="utf-8")


def _migrate_spanish_headers(content: str) -> str:
    """Older exports wrote Spanish headers; career-ops tooling expects English."""
    content = content.replace("## Pendientes", "## Pending")
    content = content.replace("## Procesadas", "## Processed")
    return content


def _extract_known_urls(content: str) -> set[str]:
    """
    Pull every URL already known to career-ops.

    Since jobs are exported as local JD files (the pipeline line carries a
    local: path, not the posting URL), the JD files themselves must also be
    scanned — otherwise re-runs would duplicate previously exported jobs.
    """
    urls = set(re.findall(r"https?://\S+", content))
    if JDS_DIR.exists():
        for jd_file in JDS_DIR.glob("*.md"):
            try:
                urls.update(re.findall(r"https?://\S+", jd_file.read_text(encoding="utf-8")))
            except OSError:
                continue
    return urls


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:max_len].rstrip("-") or "unknown"


def _write_jd_file(job: Job) -> Path:
    """
    Save the already-scraped description as jds/{company}-{role}-{id}.md so
    the career-ops pipeline reads it via the local: prefix instead of
    re-extracting the posting with Playwright (30-60s + tokens per job).
    """
    JDS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_slugify(job.company)}-{_slugify(job.title)}-{job.id}.md"
    path = JDS_DIR / filename
    body = (
        f"# {(job.title or 'Unknown role').strip()}\n\n"
        f"**Company:** {(job.company or 'Unknown').strip()}\n"
        f"**Location:** {(job.location or 'N/A').strip()}\n"
        f"**Posted:** {job.date_posted or 'N/A'}\n"
        f"**Source URL:** {job.url}\n"
        f"**Exported from StellenRadar:** {datetime.now(timezone.utc).date().isoformat()}\n\n"
        f"---\n\n"
        f"{job.description or '_No description stored — extract from the source URL._'}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def _language_marker(scored: dict) -> str:
    tier = (scored.get("language") or {}).get("tier")
    if tier == TIER_REQUIRED_C1:
        return " ⚠ Deutsch C1+ gefordert"
    if tier == TIER_ENGLISH_FRIENDLY:
        return " ✓ English-friendly"
    return ""


def _passes_gate(scored: dict, min_score: float) -> bool:
    """
    A job must look relevant on substance, not just on location/freshness.

    Location + freshness alone can add up to ~0.35, which is how gardening
    jobs in Berlin used to sneak through — so at least one role keyword or
    one overlapping skill is required in addition to the score threshold.
    """
    components = scored.get("debug", {}).get("score_components", {})
    has_substance = components.get("role", 0) > 0 or components.get("skills", 0) > 0
    return has_substance and scored["match_score"] >= min_score


def _append_jobs(content: str, entries: list[tuple[Job, dict]]) -> tuple[str, int]:
    """Insert new job lines directly under ## Pending."""
    lines = content.splitlines(keepends=True)
    insert_at = None
    for i, line in enumerate(lines):
        if line.strip() == "## Pending":
            insert_at = i + 1
            break

    if insert_at is None:
        content += "\n## Pending\n"
        lines = content.splitlines(keepends=True)
        insert_at = len(lines)

    new_lines = []
    for job, scored in entries:
        company = (job.company or "Unknown company").strip()
        title = (job.title or "Unknown role").strip() + _language_marker(scored)
        if job.description and job.description.strip():
            jd_path = _write_jd_file(job)
            ref = f"local:jds/{jd_path.name}"
        else:
            # No stored description — let the career-ops agent extract from the URL.
            ref = job.url
        new_lines.append(f"- [ ] {ref} | {company} | {title}\n")

    lines[insert_at:insert_at] = new_lines
    return "".join(lines), len(new_lines)


def _load_scored_jobs(days: int, min_score: float) -> tuple[list[tuple[Job, dict]], int]:
    """Load recent active jobs with skills and score them against the profile."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with SessionLocal() as session:
        jobs = (
            session.query(Job)
            .filter(Job.status == "active")
            .filter(Job.date_scraped >= cutoff)
            .order_by(Job.date_scraped.desc())
            .all()
        )
        job_ids = [j.id for j in jobs]
        skills_by_job: dict[int, list[str]] = {}
        if job_ids:
            for s in session.query(Skill).filter(Skill.job_id.in_(job_ids)).all():
                skills_by_job.setdefault(s.job_id, []).append(s.skill_name)
        profile = get_effective_profile(session=session)

    scored_entries: list[tuple[Job, dict]] = []
    rejected = 0
    for job in jobs:
        job_dict = {
            "title": job.title or "",
            "description": job.description or "",
            "location": job.location or "",
            "date_posted": job.date_posted,
            "skills": sorted(set(skills_by_job.get(job.id, []))),
        }
        scored = score_job_for_profile(job_dict, profile)
        if _passes_gate(scored, min_score):
            scored_entries.append((job, scored))
        else:
            rejected += 1

    scored_entries.sort(key=lambda pair: pair[1]["match_score"], reverse=True)
    return scored_entries, rejected


def run(days: int = 1, limit: int | None = DEFAULT_LIMIT, min_score: float = DEFAULT_MIN_SCORE) -> None:
    if not SessionLocal:
        raise RuntimeError("DATABASE_URL is missing. Set it in your .env file.")

    entries, rejected = _load_scored_jobs(days=days, min_score=min_score)
    print(f"Scored jobs from the last {days} day(s): {len(entries) + rejected} total, "
          f"{len(entries)} passed the gate (min_score={min_score}), {rejected} rejected.")

    if not entries:
        print("Nothing to export.")
        return

    content = _migrate_spanish_headers(_read_pipeline())
    known_urls = _extract_known_urls(content)

    new_entries = [(j, s) for j, s in entries if j.url and j.url.strip() not in known_urls]
    already_present = len(entries) - len(new_entries)

    if limit:
        new_entries = new_entries[:limit]

    if not new_entries:
        print(f"All {len(entries)} gated jobs are already in the pipeline. Nothing added.")
        return

    content, added = _append_jobs(content, new_entries)

    PIPELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PIPELINE_FILE.write_text(content, encoding="utf-8")

    print(f"Added   : {added} jobs to {PIPELINE_FILE}")
    for job, scored in new_entries:
        marker = _language_marker(scored) or ""
        print(f"  {scored['match_score']:.2f}  {job.company} — {job.title}{marker}")
    print(f"Skipped : {already_present} already present, {rejected} below gate")
    print("\nNext step: open career-ops in Claude Code and run /career-ops pipeline")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export scraped jobs to career-ops pipeline.md.")
    parser.add_argument("--days", type=int, default=1, help="Export jobs scraped in the last N days (default: 1).")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Cap at N best jobs per run (default: {DEFAULT_LIMIT}).")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE, help=f"Minimum match score (default: {DEFAULT_MIN_SCORE}).")
    args = parser.parse_args()
    run(days=max(1, args.days), limit=args.limit, min_score=args.min_score)
