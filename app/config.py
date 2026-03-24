import os
from dataclasses import dataclass


@dataclass
class Settings:
    project_name: str = os.getenv("PROJECT_NAME", "StellenRadar")
    env: str = os.getenv("ENV", "dev")
    database_url: str = os.getenv("DATABASE_URL", "")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    pinecone_api_key: str = os.getenv("PINECONE_API_KEY", "")
    pinecone_index: str = os.getenv("PINECONE_INDEX", "")

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    notion_api_key: str = os.getenv("NOTION_API_KEY", "")
    notion_database_id: str = os.getenv("NOTION_DATABASE_ID", "")


settings = Settings()
