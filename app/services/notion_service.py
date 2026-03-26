from __future__ import annotations

from app.config import settings
import uuid
import logging

from notion_client import Client
from notion_client.errors import APIResponseError


def create_job_tracking_page(job: dict, match_score: float, notes: str = "") -> str:
    """
    Create a job tracking page in Notion.

    For the class demo, Notion is optional. If credentials are missing,
    we simulate a page id so the rest of the app still works.
    """
    if not settings.notion_api_key or not settings.notion_database_id:
        _ = (job, match_score, notes)
        return f"simulated-{uuid.uuid4()}"

    logger = logging.getLogger("notion")

    client = Client(auth=settings.notion_api_key)

    # Property names in your Notion database (as created by you):
    # The Title property label differs across Notion templates, so we try a few.
    # Your database Title property is called "All Jobs" per your description.
    title_property_candidates = ["Job", "Job Applications", "All Jobs"]

    company = (job.get("company") or "").strip()
    location = (job.get("location") or "").strip()
    job_title = (job.get("title") or "").strip()
    job_url = (job.get("url") or "").strip()
    notes_text = (notes or "").strip()

    # Status select options (must match Notion Select labels exactly).
    status_label = "Considering"

    property_payload_base = {
        "Company": {"rich_text": [{"text": {"content": company}}]},
        "Location": {"rich_text": [{"text": {"content": location}}]},
        "Job URL": {"url": job_url},
        "Match Score": {"number": float(match_score)},
        "Status": {"select": {"name": status_label}},
        "Notes": {"rich_text": [{"text": {"content": notes_text}}]},
    }

    # Try multiple title-property names so the integration works even if the
    # Title property label differs slightly.
    last_error: Exception | None = None
    for title_prop in title_property_candidates:
        properties = {**property_payload_base}
        properties[title_prop] = {"title": [{"text": {"content": job_title}}]}

        try:
            page = client.pages.create(
                parent={"database_id": settings.notion_database_id},
                properties=properties,
            )
            return page["id"]
        except APIResponseError as e:
            last_error = e
            # Notion 400 errors are usually caused by property-name/type mismatch.
            # Logging the exception helps you see exactly what went wrong.
            logger.error(
                "Notion create failed for title_prop=%s (status=%s): %s",
                title_prop,
                getattr(e, "status", None),
                str(e),
            )
            continue
        except Exception as e:
            last_error = e
            break

    # If all attempts fail, fall back to simulated id so the bot doesn't crash.
    _ = (job, match_score, notes, last_error)
    return f"simulated-{uuid.uuid4()}"
