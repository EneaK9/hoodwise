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

1. **Pin the car.** VIN decode (vPIC, then Vincario / CarsXE / API Ninjas). Fuel comes from, in order: the owner's confirmation, the decoder's fuel field, a make-scoped engine-size table (1995 cc Hyundai = 2.0 CRDi diesel), or type-approval CO2 vs consumption (diesel emits ~2640 g CO2 per litre, petrol ~2330). Every fuel carries its source. If none applies the UI asks petrol or diesel once and stores it on the VIN (`POST /api/vin/fuel`).
2. **Understand the question.** `app/intent.py` decides the part category (bulb, engine oil, tyre, ATF...) from the question, or from the previous turn on follow-ups like "and where do I buy them".
   With no VIN, the car is read from the question or earlier turns ("hyundai santa fe 2016 2.0 diesel"), labeled as text-sourced.
3. **Clarify before guessing.** `app/clarify.py` asks one question when the answer would otherwise be a guess: which car, which year (listing the generations we hold), petrol or diesel for fluid questions, which lamp for bulbs, or which part when the message is too vague. Replies like "2016–2018" or "Diesel" merge into the pinned car. Definitions ("what does DPF mean") skip this.
4. **Retrieve.** Specs table plus hybrid chunk search scoped to the pinned car, boosted toward the part category. Capacity questions pull the fluid table pages whole.
5. **Select rows, then write.** Every litre figure on the retrieved pages is tagged with its row label (fuel and fluid kind). The model is told which rows belong to this car's fuel and which do not. A validator then rejects any litre figure or oil grade the manual prints for the other fuel: one corrected retry, then wrong-fuel grade sentences are removed and a wrong-fuel litre figure is a refusal.
6. **Shop links.** Built only from the decided part category, so a lamp question can never carry an oil grade. Fluids search without the car name (eBay otherwise shows cars). Retailer hosts come from `SHOP_REGION` (eu / uk / us) and are checked once per six hours; a dead host or 404 search path is dropped. Bot walls pass but the note says listings are not verified.
