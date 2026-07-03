# Deployment

The app deploys as **one Docker web service** (Render) backed by **one Postgres
database** (Neon). FastAPI serves both the REST API and the built React SPA from
the same origin, so there are no cross-site cookie/CORS problems.

```
  Browser ──▶ Render web service (Docker) ──▶ Neon PostgreSQL
             ├─ /            React SPA
             ├─ /assets/*    hashed JS/CSS
             └─ /api/*        FastAPI
```

## 1. Database — Neon

1. In the Neon console, create a project (any name, region closest to your Render
   region).
2. Open **Connection Details** → copy the **connection string**. It looks like:
   ```
   postgresql://USER:PASSWORD@ep-xxxx.aws.neon.tech/neondb?sslmode=require
   ```
3. Keep it handy — it's the `DATABASE_URL` for Render. (SQLAlchemy uses psycopg2
   by default for `postgresql://`; `sslmode=require` is already in the URL.)

Tables are created automatically on first boot (`init_db()` runs `create_all`),
so there is no migration step for the initial deploy.

## 2. Web service — Render

**Option A — Blueprint (uses `render.yaml`, recommended):**

1. Render dashboard → **New** → **Blueprint**.
2. Connect the GitHub repo → Render detects `render.yaml`.
3. It creates the service and prompts for the secret env vars (`sync: false`):
   fill them in per the table below. `JWT_SECRET_KEY` is auto-generated.
4. **Apply** → first build takes a few minutes (Docker builds the frontend, then
   the backend).

**Option B — Manual web service:**

1. **New** → **Web Service** → connect the repo.
2. Runtime **Docker** (Render finds the `Dockerfile`). Instance type **Free**.
3. Add the env vars below → **Create Web Service**.

### Environment variables

| Key | Value |
|-----|-------|
| `DATABASE_URL` | the Neon connection string from step 1 |
| `JWT_SECRET_KEY` | a strong random value (Blueprint generates it; else `openssl rand -hex 32`) |
| `COOKIE_SECURE` | `true` |
| `COOKIE_SAMESITE` | `lax` |
| `SMTP_HOST` | `smtp-relay.brevo.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your Brevo SMTP login |
| `SMTP_PASSWORD` | your Brevo SMTP key |
| `DIGEST_FROM` | `expenseintelligence@gmail.com` |
| `DIGEST_TO` | `expenseintelligence@gmail.com` |

`STATIC_DIR` is set inside the Dockerfile — don't add it manually. `PORT` is
provided by Render automatically.

## 3. Verify

1. Open the Render URL (e.g. `https://expense-intelligence.onrender.com`) — the
   app loads.
2. `…/health` returns `{"status":"ok"}`.
3. Register, upload `backend/sample_data/sample_statement_india.csv`, and send a
   digest — it arrives at your registered email.

## Notes

- **Free tier sleeps** after ~15 min idle; the first request afterward cold-starts
  in ~50s. Expected on the free plan.
- **Filesystem is ephemeral** (uploads/ and digest_outbox/ reset on redeploy). All
  durable data lives in Neon Postgres — that's by design.
- To send digests to real inboxes at scale, confirm your Brevo sender (so mail is
  `From: expenseintelligence@gmail.com`) and stay within the 300/day free tier.
