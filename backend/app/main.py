"""FastAPI application entry point.

Wires together the ingestion and analytics routers, initializes the database,
and seeds the default category taxonomy on startup.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import Category
from app.routers import analytics, auth, automation, ingestion
from app.scheduler import create_scheduler
from app.services.categorization import DEFAULT_CATEGORIES

settings = get_settings()


def seed_categories() -> None:
    with SessionLocal() as db:
        existing = set(db.execute(select(Category.name)).scalars())
        new = [
            Category(name=name, is_user_defined=False)
            for name in DEFAULT_CATEGORIES
            if name not in existing
        ]
        if new:
            db.add_all(new)
            db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_categories()
    if settings.using_default_secret:
        logging.warning(
            "JWT_SECRET_KEY is the built-in dev default — set a strong secret "
            "(e.g. `openssl rand -hex 32`) in production."
        )
    scheduler = create_scheduler()
    scheduler.start()
    # Expose scheduler + human-readable status on app.state for the status endpoint.
    app.state.scheduler = scheduler
    app.state.digest_cadence = (
        f"every {settings.digest_interval_minutes} min"
        if settings.digest_interval_minutes > 0
        else "weekly (Mon 08:00)"
    )
    if settings.brevo_api_key:
        app.state.digest_delivery_mode = "email (Brevo API)"
    elif settings.smtp_configured:
        app.state.digest_delivery_mode = "email (SMTP)"
    else:
        app.state.digest_delivery_mode = "file (digest_outbox/)"
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Personal Finance Intelligence & Automation Platform",
    description="Ingests statements, categorizes transactions, and surfaces "
    "spending analytics, subscriptions, and anomalies.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(ingestion.router)
app.include_router(analytics.router)
app.include_router(automation.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Serve the built React app (single-origin production) ──────────────────────
# When STATIC_DIR points at frontend/dist, the API also serves the SPA: hashed
# assets are served directly, and any other path falls back to index.html so
# client-side routing works. Registered last, so /api and /health win first.
_static_dir = Path(settings.static_dir) if settings.static_dir else None
if _static_dir and _static_dir.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=_static_dir / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        # Never let the SPA fallback swallow unmatched API/health requests.
        if full_path.startswith("api/") or full_path == "health":
            raise HTTPException(status_code=404)
        candidate = _static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_static_dir / "index.html")
