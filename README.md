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

Ingest factory PDFs or any owner-manual catalog in `ingestion/catalogs/`:

```bash
python -m ingestion.run_ingest --mvp
python -m ingestion.run_ingest --catalog --embed
# add --vision if ANTHROPIC_API_KEY is set
# add --embed if OPENAI_API_KEY is set
```

A catalog is just JSON: `make`, `folder`, and a `manuals` list. Add another brand by dropping another file in `ingestion/catalogs/` — no code change.

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

- VIN is optional. Paste a VIN in chat or pick a variant. No VIN means no vehicle filter, never a substitute car.
- Numbers must appear in retrieved specs/chunks or the answer is refused.
- Guest chat works without an account; signup claims the current session.

## How a question is answered

There are no keyword lists or regex interpreters in this path. The model reads; code verifies and enforces the allowlist.

1. **Pin the car from the VIN.** vPIC, then Vincario / CarsXE / API Ninjas. The decode is passed to the model as evidence (displacement, raw fuel field, CO2 vs consumption physics hint), not turned into conclusions in code. The owner's confirmation (`POST /api/vin/fuel`) is the only fuel code ever sets.
2. **Understand.** `app/understand.py`: one structured-output call (`UNDERSTAND_MODEL`) with the message, the last three user turns, the VIN decode, and the catalog of manuals we hold with year ranges. Returns the part in plain words, the car and engine and fuel (from the VIN data or the user's words, merged across turns), what is still missing, the one question to ask back with tap options, a manual search phrase, and a web query.
3. **Ask before guessing.** If anything is missing the app asks instead of answering. "the model is hyundai santa fe 2016" and "2.0 diesel" are recognised as replies. Definitions skip this.
4. **Retrieve.** Spec rows plus hybrid vector and full-text search over the pinned car's manual for the model's phrase. Hit pages are returned whole, so a table row and its header are never split.
5. **Draft with the web on.** `CHAT_MODEL` gets the pinned car, the whole pages, and Claude's web search tool. Manual first, web for what the manual does not print, each attributed separately; disagreements shown side by side.
6. **Verify.** `app/verify.py`: a second structured-output call (`VERIFY_MODEL`) checks every figure, grade and code in the draft against the pages and the web passages, for this exact engine and fuel. Plus one deterministic backstop: every digit string in the answer must appear verbatim in a source, the question, or the car. Failures are fed back once; then the answer is refused.
7. **Shop links.** From the verifier's search-ready item name, URL-encoded into allowlisted retailer search pages for `SHOP_REGION` (eu / uk / us). Hosts are checked once per six hours; dead hosts and 404 search paths are dropped. Nothing is parsed out of text by code.

Accuracy eval on hard questions (needs DB + API key): `python -m evals.run_hard_eval` prints precision, recall and F1 over `evals/hard_questions.yaml` and writes `evals/hard_eval_report.json`.
