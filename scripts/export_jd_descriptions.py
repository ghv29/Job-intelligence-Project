"""
Export full job descriptions for specific URLs to a markdown file.
Run: python scripts/export_jd_descriptions.py
Output: scripts/exported_jds.md
"""

from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.models import Job
from app.db.session import SessionLocal

TARGET_URLS = [
    "https://www.stepstone.de/jobs--Data-Analyst-m-w-d-Munchehofe-Glaserne-Molkerei-GmbH--14056774-inline.html",
    "https://www.stepstone.de/jobs--Data-Analyst-w-m-d-Bocholt-Gescher-Kiel-Meppen-Munster-Osnabruck-Salem-Schoppingen-d-velop-AG--14053241-inline.html",
    "https://www.stepstone.de/jobs--Senior-Data-Business-Analyst-m-w-d-Pforzheim-BRUNO-BADER-GmbH-Co-KG--14053686-inline.html",
    "https://www.stepstone.de/jobs--Sales-Data-Analyst-w-m-d-Koln-Stroer-Media-Deutschland-GmbH--14001039-inline.html",
    "https://www.stepstone.de/jobs--Data-Consultant-Data-Analyst-Financial-Services-m-w-d-remote-deutschlandweit-Berlin-Munchen-Hamburg-Koln-Frankfurt-am-Main-Stuttgart-Dusseldorf-Nurnberg-Karslruhe-Dresden-Bremen-23DATA-GmbH--14005163-inline.html",
    "https://www.stepstone.de/jobs--Fully-Funded-PhD-Positions-m-w-d-in-Data-Science-HEIBRiDS-Berlin-Berlin-MAX-DELBRUCK-CENTRUM-FUR-MOLEKULARE-MEDIZIN--14060088-inline.html",
    "https://www.stepstone.de/jobs--Data-BI-Analyst-Business-Application-m-w-d-Sulzbach-Michael-Page--14053247-inline.html",
]

OUTPUT_FILE = REPO_ROOT / "scripts" / "exported_jds.md"


def run() -> None:
    if not SessionLocal:
        raise RuntimeError("DATABASE_URL is missing. Set it in your .env file.")

    with SessionLocal() as session:
        jobs = session.query(Job).filter(Job.url.in_(TARGET_URLS)).all()

    found_urls = {j.url for j in jobs}
    missing = [u for u in TARGET_URLS if u not in found_urls]

    lines = ["# Exported Job Descriptions\n"]
    for job in jobs:
        lines.append(f"## {job.title} — {job.company}")
        lines.append(f"**Location:** {job.location or 'N/A'}")
        lines.append(f"**Posted:** {job.date_posted or 'N/A'}")
        lines.append(f"**URL:** {job.url}")
        lines.append("")
        lines.append(job.description or "_No description stored._")
        lines.append("\n---\n")

    if missing:
        lines.append("## Not found in DB")
        for u in missing:
            lines.append(f"- {u}")

    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported {len(jobs)} jobs to {OUTPUT_FILE}")
    if missing:
        print(f"Not found in DB: {len(missing)} URLs")


if __name__ == "__main__":
    run()
