"""FastAPI application entry point.

Wires together the ingestion and analytics routers, initializes the database,
and seeds the default category taxonomy on startup.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import Category
from app.routers import analytics, ingestion
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
    yield


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

app.include_router(ingestion.router)
app.include_router(analytics.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
