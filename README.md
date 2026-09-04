# Hoodwise

AI car-repair assistant for any type of car. Answers are grounded in the factory service manual: numeric specs come from extracted rows, never free generation.

## Stack

- Python 3.11+, FastAPI, PyMuPDF, Anthropic + OpenAI
- Postgres 16 + pgvector (Docker)
- Next.js UI

## Setup

```bash
cp .env.example .env
# Option A — Docker
docker compose up -d
# Option B — local Homebrew Postgres 16 + pgvector
# createdb hoodwise && psql hoodwise -f db/schema.sql -f db/seed.sql
# set DATABASE_URL=postgresql:///hoodwise in .env

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Repair damaged PDFs (optional, needs `qpdf`):

```bash
python -m ingestion.run_ingest --repair
```

Ingest the MVP manuals (Maintenance, Battery, Brakes):

```bash
python -m ingestion.run_ingest --mvp
# add --vision if ANTHROPIC_API_KEY is set
# add --embed if OPENAI_API_KEY is set
```

API + UI:

```bash
uvicorn app.main:app --reload --port 8000
cd web && npm install && npm run dev
```

## Eval

```bash
python -m evals.load_questions
pytest evals/test_golden.py -q
python -m evals.run_eval
```

## Policies

- VIN is optional. Paste a VIN in chat or pick a variant.
- Numbers must appear in retrieved specs/chunks or the answer is refused.
- Guest chat works without an account; signup claims the current session.
