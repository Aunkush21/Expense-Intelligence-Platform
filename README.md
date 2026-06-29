# Personal Finance Intelligence & Automation Platform

Turns raw bank / credit-card statement exports into something you can act on:
it ingests a CSV statement, automatically **categorizes** every transaction
(rules + a lightweight ML fallback), and surfaces **spending analytics** through
a real dashboard. Recurring-subscription detection, anomaly flagging, and a
scheduled email digest are the Week-2 roadmap below.

> Built as a focused portfolio project spanning data engineering, applied ML,
> backend architecture, and full-stack delivery.

## Architecture

A single ingestion endpoint funnels statements through an ETL + categorization
stage into one database. From there the data forks into **two independent
consumers** — an analytics API and (roadmap) a scheduler — that share only the
database and never call each other.

```
Statement upload (CSV)
   │
   ▼
Ingestion API ──► ETL + categorization ──► Database (SQLite dev / PostgreSQL prod)
                                              │
                        ┌─────────────────────┴─────────────────────┐
                        ▼                                            ▼
                 Analytics API                              Scheduler job  (roadmap)
                        │                                            │
                        ▼                                            ▼
                 React dashboard                              Email digest  (roadmap)
```

## Tech stack

| Layer      | Choice                                                   |
|------------|----------------------------------------------------------|
| Backend    | FastAPI, SQLAlchemy 2.0, Pydantic v2                      |
| ML         | scikit-learn (TF-IDF char n-grams + Multinomial NB)      |
| ETL        | pandas                                                    |
| Database   | SQLite for local dev, PostgreSQL as the production target |
| Frontend   | React + TypeScript (Vite), Recharts                      |

The data layer is written against SQLAlchemy, so switching from SQLite to
PostgreSQL is a one-line `DATABASE_URL` change — no query changes.

## Data model

- **accounts** — one row per bank / credit account tracked
- **transactions** — normalized line items (date, merchant, amount, description,
  category, recurring flag) with a dedupe hash so re-uploaded statements don't
  double-count
- **categories** — system defaults plus user overrides
- **subscriptions** — derived recurring-merchant cadences *(roadmap)*
- **anomalies** — flagged transactions with a reason code *(roadmap)*

## Categorization

Two-tier, as proposed:

1. **Rule-based** keyword matching on merchant text — deterministic and useful
   from the very first upload.
2. **ML fallback** — a Naive Bayes classifier trained on the labelled history
   (rule hits + user corrections). Consulted only when no rule fires.

When a user corrects a category in the dashboard, that transaction is stored
with `category_source = "user"` and becomes a training example for every later
upload — the human-in-the-loop feedback path.

## Running it locally

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # defaults to SQLite — no DB install needed
uvicorn app.main:app --reload --port 8123
```

API docs: <http://localhost:8123/docs>

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 (proxies /api to the backend)
```

Open the dashboard and click **Upload statement** — try
`backend/sample_data/sample_statement.csv`.

### Tests

```bash
cd backend && pytest -q
```

## API overview

| Method & path                                          | Purpose                                  |
|--------------------------------------------------------|------------------------------------------|
| `POST /api/accounts`                                   | Create an account                        |
| `GET  /api/accounts`                                   | List accounts                            |
| `POST /api/accounts/{id}/statements`                   | Upload a CSV statement (ingestion)       |
| `GET  /api/accounts/{id}/transactions`                 | List transactions                        |
| `PATCH /api/transactions/{id}/category`                | Correct a category (trains the model)    |
| `GET  /api/accounts/{id}/analytics/summary`            | Spend / income / net / top category      |
| `GET  /api/accounts/{id}/analytics/by-category`        | Spend grouped by category                |
| `GET  /api/accounts/{id}/analytics/trends`             | Monthly spend vs income                  |

## Supported statement formats

The ETL parser maps common header spellings onto canonical fields, so most
exports work without manual mapping:

- A unified **`Amount`** column (negative = spend), **or** separate
  **`Debit` / `Credit`** columns
- Date headers like `Date`, `Posted Date`, `Transaction Date`
- Merchant headers like `Description`, `Merchant`, `Payee`, `Narrative`
- Currency formatting (`$`, thousands separators, accounting `(123.45)` negatives)

## Roadmap (Week 2 — "industry-grade")

- [ ] Recurring-subscription detection
- [ ] Anomaly flagging (spend spikes, new merchants)
- [ ] Scheduled weekly email digest
- [ ] JWT auth for multi-user support
- [ ] Deployment (Render/Railway + Vercel)
```
