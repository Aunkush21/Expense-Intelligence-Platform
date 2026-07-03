"""Auth tests: crypto units + cookie-based session / isolation via TestClient."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.security import (
    create_access_token,
    decode_subject,
    hash_password,
    verify_password,
)

# --- Unit: password hashing + JWT round-trip ---------------------------------


def test_password_hash_and_verify():
    hashed = hash_password("supersecret123")
    assert hashed != "supersecret123"
    assert verify_password("supersecret123", hashed)
    assert not verify_password("wrong", hashed)


def test_verify_rejects_garbage_hash():
    assert not verify_password("anything", "not-a-real-hash")


def test_token_round_trip():
    token = create_access_token("42")
    assert decode_subject(token) == "42"


# --- Integration: cookie sessions + per-user isolation -----------------------

engine = create_engine(
    "sqlite:///./test_auth.db", connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def app_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def _register(client, email, password="password123"):
    # TestClient persists the auth cookies the server sets, like a browser.
    res = client.post("/api/auth/register", json={"email": email, "password": password})
    assert res.status_code == 201, res.text
    assert "access_token" not in res.text  # token must never be in the body
    return res


def test_requires_auth(app_db):
    assert TestClient(app).get("/api/accounts").status_code == 401


def test_register_login_and_isolation(app_db):
    client_a = TestClient(app)
    _register(client_a, "a@example.com")

    acct = client_a.post("/api/accounts", json={"name": "A Checking"})
    assert acct.status_code == 201
    account_id = acct.json()["id"]

    # A separate client (separate cookie jar) is a different user.
    client_b = TestClient(app)
    _register(client_b, "b@example.com")
    assert client_b.get("/api/accounts").json() == []
    assert (
        client_b.get(f"/api/accounts/{account_id}/analytics/summary").status_code == 404
    )


def test_logout_revokes_session(app_db):
    client = TestClient(app)
    _register(client, "logout@example.com")
    assert client.get("/api/auth/me").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    # Cookies cleared + refresh revoked -> no more access.
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_email_rejected(app_db):
    client = TestClient(app)
    _register(client, "dup@example.com")
    res = client.post(
        "/api/auth/register",
        json={"email": "dup@example.com", "password": "password123"},
    )
    assert res.status_code == 409
