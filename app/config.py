import os
import re
from dataclasses import dataclass
from dotenv import load_dotenv


# Load local .env values into process environment.
load_dotenv()

def _clean_env(value: str) -> str:
    """
    Make env var loading more forgiving.

    `.env` files often end up with accidental surrounding quotes or a stray
    trailing quote (common when copy/pasting keys). This normalizes those.
    """
    v = (value or "").strip()
    if not v:
        return ""
    # Strip surrounding quotes: "abc" -> abc
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1].strip()
    # Strip a single trailing quote: abc" -> abc
    if v.endswith('"') or v.endswith("'"):
        v = v[:-1].strip()
    return v


def _normalize_notion_database_id(value: str) -> str:
    """
    Accept either:
      - a raw Notion database id (usually 32 hex chars, e.g. 32fd...)
      - a full Notion URL containing that id
    and return the raw id string.
    """
    v = _clean_env(value)
    if not v:
        return ""

    # If already looks like a 32-char hex id, keep it.
    if re.fullmatch(r"[0-9a-fA-F]{32}", v):
        return v

    # Extract from common Notion URL formats.
    # Example: https://www.notion.so/<db_id>?v=...
    m = re.search(r"notion\.so/([0-9a-fA-F]{32})(?:[/?]|$)", v)
    if m:
        return m.group(1)

    # Sometimes the value may include a hyphenated UUID.
    m = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        v,
    )
    if m:
        return m.group(1)

    # Fall back to cleaned value; this may still fail at runtime, but we tried.
    return v


@dataclass
class Settings:
    # Core app settings
    project_name: str = _clean_env(os.getenv("PROJECT_NAME", "StellenRadar"))
    env: str = _clean_env(os.getenv("ENV", "dev"))
    database_url: str = _clean_env(os.getenv("DATABASE_URL", ""))

    # External service keys
    openai_api_key: str = _clean_env(os.getenv("OPENAI_API_KEY", ""))
    pinecone_api_key: str = _clean_env(os.getenv("PINECONE_API_KEY", ""))
    pinecone_index: str = _clean_env(os.getenv("PINECONE_INDEX", ""))

    # Integration tokens
    telegram_bot_token: str = _clean_env(os.getenv("TELEGRAM_BOT_TOKEN", ""))
    notion_api_key: str = _clean_env(os.getenv("NOTION_API_KEY", ""))
    notion_database_id: str = _normalize_notion_database_id(os.getenv("NOTION_DATABASE_ID", ""))


# Shared settings object imported across the app.
settings = Settings()
