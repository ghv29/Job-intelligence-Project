from __future__ import annotations

from app.db.session import SessionLocal
from app.profile import get_effective_profile
from app.services.matcher import load_active_jobs_with_skills, score_job_for_profile, semantic_scores_for_jobs


def run(limit: int = 80, top_k: int = 10) -> None:
    if not SessionLocal:
        raise RuntimeError("DATABASE_URL is missing. Set it in your .env file.")

    with SessionLocal() as session:
        profile = get_effective_profile(session=session)
        jobs = load_active_jobs_with_skills(session=session, limit=limit)
        semantic_map = semantic_scores_for_jobs([int(j["id"]) for j in jobs], profile=profile)

        scored = []
        for job in jobs:
            job["semantic_score"] = semantic_map.get(int(job["id"]), 0.0)
            scoring = score_job_for_profile(job=job, profile=profile)
            scored.append({**job, **scoring})

    scored.sort(key=lambda x: x.get("match_score", 0.0), reverse=True)
    top = scored[:top_k]
    if not top:
        print("No jobs found. Run scripts/run_scrape.py first.")
        return

    print(f"Top {len(top)} jobs by hybrid match score")
    print("-" * 72)
    for idx, job in enumerate(top, start=1):
        c = (job.get("debug") or {}).get("score_components") or {}
        print(
            f"{idx:02d}. id={job['id']} score={job['match_score']:.3f} "
            f"semantic={c.get('semantic', 0)} freshness={c.get('freshness', 0)} "
            f"{job['title']} @ {job['company']} ({job['location']})"
        )
    print("-" * 72)
    avg_score = sum(float(j.get("match_score") or 0.0) for j in top) / len(top)
    avg_semantic = sum(float(((j.get("debug") or {}).get("score_components") or {}).get("semantic", 0.0)) for j in top) / len(top)
    print(f"Average top-{len(top)} score: {avg_score:.3f}")
    print(f"Average semantic contribution: {avg_semantic:.3f}")


if __name__ == "__main__":
    run()
