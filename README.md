# Personal Finance Intelligence & Automation Platform

Turn raw bank / credit-card statement exports into something you can act on. Upload
a CSV and the platform **auto-detects the file's columns**, **categorizes every
transaction** (rules + a lightweight ML fallback), **detects recurring
subscriptions**, **flags spending anomalies**, and emails a **weekly digest** —
all behind multi-user authentication.

> A full-stack project spanning data engineering, applied ML, layered
> backend architecture, background automation, and a real analytics dashboard.

---

## Features

- **Smart ingestion** — upload *any* bank/credit CSV. A schema-inference engine
  reads the headers **and the cell contents** to map each column to the canonical
  `date / merchant / amount` model, including separate debit/credit or a
  direction column (e.g. ANZ's `movement`). Non-statement files (e.g. an ML
  feature dataset) are rejected with a clear explanation. Re-uploads are deduped.
- **Categorization** — a two-tier engine: deterministic keyword rules first, then
  a Naive Bayes classifier trained on your history. **Correcting a category in the
  UI retrains the model** (human-in-the-loop).
- **Recurring-subscription detection** — finds merchants billing on a regular
  cadence for a stable amount, and predicts the next charge date.
- **Anomaly flagging** — per-category spend spikes (IQR outliers) and notable
  first-time merchants, tuned for precision.
- **Weekly email digest** — a background scheduler independently emails a polished
  HTML summary with a **spend-by-category pie chart** and **personalized saving
  tips** (where you overspent vs. last week, upcoming subscriptions, anomalies).
- **Authentication** — multi-user with bcrypt-hashed passwords, short-lived JWT
  access tokens + rotating refresh tokens, all delivered as **httpOnly cookies**.
- **India-first** — ₹ formatting with lakh/crore grouping, Indian-bank CSV parsing
  (split debit/credit, DD/MM dates, UPI-narration cleanup), and Indian merchant
  rules (Swiggy, Zepto, Jio, UPI/NEFT, ATM cash, university fees…).
- **Analytics dashboard** — a cash-flow summary, spend-by-category, monthly trends,
  subscriptions, alerts, and a transactions table with inline category correction
  (React + Recharts).

## Architecture

A single ingestion path funnels data into one database, which then **forks into
two independent consumers** — the analytics API and the scheduler — that share
*only* the database and never call each other. That decoupling is deliberate.

```
              ┌──────────────────────────────────────────┐
   CSV  ─────▶│  Ingestion API                           │
  upload      │  schema inference → ETL → categorization  │
              └───────────────────────┬──────────────────┘
                                      ▼
                          ┌───────────────────────┐
                          │  Database (SQLite/PG)  │
                          └───────────┬───────────┘
                          ┌───────────┴────────────┐
                          ▼                         ▼
                 ┌─────────────────┐      ┌──────────────────┐
                 │  Analytics API  │      │  Scheduler (job) │
                 │  + subs/anomaly │      │  weekly digest   │
                 └────────┬────────┘      └────────┬─────────┘
                          ▼                         ▼
                 ┌─────────────────┐      ┌──────────────────┐
                 │ React dashboard │      │  Email (SMTP)    │
                 └─────────────────┘      └──────────────────┘
```

## Tech stack

| Layer    | Choice                                                            |
|----------|-------------------------------------------------------------------|
| Backend  | FastAPI, SQLAlchemy 2.0, Pydantic v2                               |
| Auth     | JWT (PyJWT) + bcrypt, httpOnly cookies, rotating refresh tokens    |
| ML / ETL | scikit-learn (TF-IDF + Naive Bayes), pandas                       |
| Scheduler| APScheduler · digest charts with matplotlib                       |
| Tooling  | ruff (lint + format), pytest                                      |
| Database | SQLite for local dev · **PostgreSQL** as the production target     |
| Frontend | React + TypeScript (Vite), Recharts                               |

The data layer is pure SQLAlchemy, so switching SQLite → PostgreSQL is a one-line
`DATABASE_URL` change with no query edits.

## Data model

`users`, `refresh_tokens`, `accounts`, `transactions`, `categories`,
`subscriptions` (derived), `anomalies` (derived). Every account is scoped to a
user; subscriptions and anomalies are recomputed from transactions on each upload.

## Getting started

**Prerequisites:** Python 3.11+, Node 18+.

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # defaults to SQLite — no DB install needed
uvicorn app.main:app --reload --port 8123
```

API runs at `http://localhost:8123` · interactive docs at `/docs`.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173 (proxies /api to the backend)
```

Open `http://localhost:5173`, sign up, then **Upload statement** — try
`backend/sample_data/sample_statement_india.csv` (an HDFC-style ₹ statement), or
`anz.csv` to see the column inference handle a completely different format.

### 3. Email digest (optional)

Delivery is picked automatically: **Brevo HTTP API** → SMTP → file. With no
credentials, digests are written to `backend/digest_outbox/` so the feature is
fully demoable. To send real email, set `BREVO_API_KEY` in `.env` (the API works
even on hosts that block outbound SMTP) with `DIGEST_FROM` set to a verified
Brevo sender. Set `DIGEST_INTERVAL_MINUTES=2` to watch the scheduler fire on a
short loop.

## Tests & code quality

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q          # 29 tests: ETL, categorization, subscriptions, anomalies,
                   # digest rendering, localization, and auth/isolation
ruff check .       # lint (config in pyproject.toml)
ruff format .      # format
```

## API overview

| Area      | Endpoints                                                          |
|-----------|-------------------------------------------------------------------|
| Auth      | `POST /api/auth/register` · `login` · `refresh` · `logout` · `GET /me` |
| Accounts  | `POST/GET /api/accounts` · `PATCH /api/accounts/{id}`             |
| Ingestion | `POST /api/statements/preview` · `POST /api/accounts/{id}/statements` |
| Analytics | `GET /api/accounts/{id}/analytics/{summary,by-category,trends}`    |
| Insights  | `GET /api/accounts/{id}/{subscriptions,anomalies}`                |
| Digest    | `GET .../digest/preview` · `POST .../digest/send` · `GET /api/automation/status` |

Every account-scoped endpoint verifies ownership and returns 404 for another
user's data (so it never leaks which IDs exist).

## Security notes

- Passwords: **bcrypt**. Tokens: **httpOnly cookies** (JS can't read them).
- **15-min access token + 7-day rotating refresh token**; refresh-token reuse is
  detected and revokes the whole session family.
- Login is **rate-limited**. Set a strong `JWT_SECRET_KEY` and `COOKIE_SECURE=true`
  (HTTPS) in production.

## Project structure

```
backend/
  app/
    routers/     auth · ingestion · analytics · automation
    services/    schema_inference · etl · categorization · pipeline
                 subscriptions · anomalies · digest · mailer · formatting
    models.py · schemas.py · security.py · scheduler.py · database.py · config.py
  tests/         pytest suite (7 modules)
  sample_data/   sample_statement_india.csv · anz.csv
  pyproject.toml requirements.txt · requirements-dev.txt
frontend/
  src/           App.tsx · AuthScreen.tsx · api.ts (+ css)
Dockerfile · render.yaml · DEPLOYMENT.md   # single-image deploy (see below)
```

## Deployment

Ships as **one Docker image**: the React app is built and served by FastAPI from
the same origin (no CORS/cookie cross-site issues). The reference setup is a
Docker web service on **Render** backed by **PostgreSQL on Neon**, driven by
`render.yaml`. Email sends via Brevo's HTTP API (Render blocks outbound SMTP).
Full walkthrough in [DEPLOYMENT.md](DEPLOYMENT.md).

## Roadmap

- [x] Smart ingestion + categorization + analytics dashboard
- [x] Subscription detection · anomaly flagging · weekly email digest
- [x] Multi-user JWT auth (httpOnly cookies, refresh rotation)
- [x] Deployment — single-image Docker on Render + PostgreSQL on Neon
- [ ] CSRF double-submit token (defense-in-depth beyond SameSite cookies)
