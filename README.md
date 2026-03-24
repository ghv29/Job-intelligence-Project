# StellenRadar

StellenRadar is a scraping-first job intelligence assistant for the German data analytics market.
It is designed for one primary user profile and focuses on actionable insights for manufacturing,
automotive, logistics, and supply chain opportunities.

## Why this project

- Tracks real German job postings instead of static datasets
- Matches jobs to a profile with engineering + analytics background
- Supports German and English job terminology
- Provides dashboard insights and conversational workflows

## Current repository structure

- `app/` core application code
  - `main.py` FastAPI entrypoint
  - `db/` SQLAlchemy models + DB session setup
  - `scrapers/` source-specific scrapers (StepStone, Indeed)
  - `services/` skill extraction, matching, Pinecone, Notion
  - `agent/` tool orchestration layer
  - `telegram_bot/` Telegram interface
- `dashboard/` Streamlit dashboard app
- `scripts/` utility scripts such as scraping runner
- `.github/workflows/` GitHub Actions automation

## Quick start

### 1) Create and activate virtual environment

Windows (PowerShell):

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure environment variables

```bash
copy .env.example .env
```

Then fill `.env` values for:

- `DATABASE_URL` (Neon PostgreSQL)
- `OPENAI_API_KEY`
- `PINECONE_API_KEY`, `PINECONE_INDEX`
- `TELEGRAM_BOT_TOKEN`
- `NOTION_API_KEY`, `NOTION_DATABASE_ID`

### 4) Run the API

```bash
uvicorn app.main:app --reload
```

### 5) Run the dashboard

```bash
streamlit run dashboard/streamlit_app.py
```

### 6) Run scraping script (placeholder)

```bash
python scripts/run_scrape.py
```

## GitHub automation included

- `CI` workflow for install + import smoke checks on push/PR
- `Daily Scrape (placeholder)` workflow with schedule + manual trigger

The daily scrape workflow is intentionally a placeholder until production credentials
and full scraper execution logic are ready.

## Next implementation milestone

Complete Phase 1 foundation:

- fully load `.env` in runtime
- finalize Neon DB engine/session behavior
- create migration setup (Alembic)
- persist scraped jobs into the `jobs` table with deduplication by URL
