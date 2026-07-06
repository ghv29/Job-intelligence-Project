from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# Base class used by all ORM models.
Base = declarative_base()

# Engine is created only when DATABASE_URL is available.
engine = (
    create_engine(
        settings.database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
    )
    if settings.database_url
    else None
)
# SessionLocal provides DB sessions for scripts/services.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True) if engine else None
