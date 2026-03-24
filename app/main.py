from fastapi import FastAPI

from app.config import settings

app = FastAPI(title=settings.project_name)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "project": settings.project_name}
