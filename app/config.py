import os
from dataclasses import dataclass
from dotenv import load_dotenv


# Load local .env values into process environment.
load_dotenv()


@dataclass
class Settings:
    # Core app settings
    project_name: str = os.getenv("PROJECT_NAME", "StellenRadar")
    env: str = os.getenv("ENV", "dev")
    database_url: str = os.getenv("DATABASE_URL", "")

    # External service keys
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    pinecone_api_key: str = os.getenv("PINECONE_API_KEY", "")
    pinecone_index: str = os.getenv("PINECONE_INDEX", "")

    # Integration tokens
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    notion_api_key: str = os.getenv("NOTION_API_KEY", "")
    notion_database_id: str = os.getenv("NOTION_DATABASE_ID", "")


# Shared settings object imported across the app.
settings = Settings()
