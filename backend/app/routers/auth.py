"""Auth API: register, login, refresh, logout, and current-user lookup.

Tokens are delivered as httpOnly cookies, never in the response body, so the
browser's JavaScript never touches them.
"""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserOut
from app.security import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    create_access_token,
    get_current_user,
    hash_password,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
    set_auth_cookies,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class _RateLimiter:
    """Tiny in-memory fixed-window limiter (per key). For multi-process prod this
    would live in Redis; here it slows brute-force on a single instance."""

    def __init__(self, max_hits: int, window_seconds: int) -> None:
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._hits[key] if now - t < self.window]
        recent.append(now)
        self._hits[key] = recent
        return len(recent) <= self.max_hits


_login_limiter = _RateLimiter(max_hits=10, window_seconds=300)  # 10 / 5 min per IP


def _issue_session(response: Response, db: Session, user: User) -> None:
    access = create_access_token(str(user.id))
    refresh = issue_refresh_token(db, user.id)
    set_auth_cookies(response, access, refresh)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: UserCreate, response: Response, db: Session = Depends(get_db)
) -> User:
    exists = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists."
        )

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    _issue_session(response, db, user)
    return user


@router.post("/login", response_model=UserOut)
def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> User:
    """OAuth2 password flow: `username` is the email, `password` the password."""
    client_ip = request.client.host if request.client else "unknown"
    if not _login_limiter.check(client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts. Please wait a few minutes and try again.",
        )

    user = db.execute(
        select(User).where(User.email == form.username)
    ).scalar_one_or_none()
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _issue_session(response, db, user)
    return user


@router.post("/refresh", response_model=UserOut)
def refresh(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> User:
    """Rotate the refresh cookie and mint a fresh access token."""
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    new_refresh, user_id = rotate_refresh_token(db, raw)
    set_auth_cookies(response, create_access_token(str(user_id)), new_refresh)

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    revoke_refresh_token(db, request.cookies.get(REFRESH_COOKIE))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response)
    return response


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
