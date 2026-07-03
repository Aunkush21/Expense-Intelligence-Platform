# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Single-image deploy: build the React frontend, then serve it AND the FastAPI
# API from one Python process (same origin → no CORS/cookie cross-site issues).
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: build the React/Vite frontend ──────────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # emits /build/dist

# ── Stage 2: Python backend that serves the API + the built SPA ─────────────
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    STATIC_DIR=/app/static

WORKDIR /app

# libgomp1 is required at runtime by scikit-learn (OpenMP).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ ./
# Bring in the compiled frontend from stage 1.
COPY --from=frontend /build/dist ./static

# Writable scratch dirs (ephemeral on the host; the DB lives in Postgres).
RUN mkdir -p uploads digest_outbox

# Render (and most PaaS) inject $PORT; default to 8000 for local `docker run`.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
