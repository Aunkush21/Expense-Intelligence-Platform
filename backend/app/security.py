"""Authentication: password hashing, short-lived access JWTs, server-side
rotating refresh tokens, httpOnly auth cookies, and the request dependencies.

Design (close to how real apps do it):
  * Access token  - a 15-minute JWT. Sent as an httpOnly cookie (JS can't read
                    it, so XSS can't steal it). An Authorization: Bearer header
                    is also accepted, for curl/automated clients.
  * Refresh token - an opaque random string in a separate httpOnly cookie. Only
                    its SHA-256 hash is stored. Each use rotates it (old one is
                    revoked); reuse of a revoked token revokes the whole family
                    (theft detection). This is what makes logout real.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Account, RefreshToken, User

settings = get_settings()

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def _utcnow() -> datetime:
    # Naive UTC throughout, so DB-stored and computed times compare cleanly.
    return datetime.now(UTC).replace(tzinfo=None)


# --- Passwords ---------------------------------------------------------------


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


# --- Access tokens (JWT) -----------------------------------------------------


def create_access_token(subject: str) -> str:
    expire = _utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "exp": expire, "type": "access"}
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def decode_subject(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None
    return payload.get("sub")


# --- Refresh tokens (opaque, hashed, rotating) -------------------------------


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_refresh_token(db: Session, user_id: int) -> str:
    raw = secrets.token_urlsafe(48)
    record = RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(raw),
        expires_at=_utcnow() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(record)
    db.commit()
    return raw


def rotate_refresh_token(db: Session, raw: str) -> tuple[str, int]:
    """Validate a refresh token, revoke it, and issue a fresh one.

    Returns (new_raw_token, user_id). Raises 401 on any problem. If a *revoked*
    token is replayed, every token for that user is revoked (theft response).
    """
    record = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw))
    ).scalar_one_or_none()

    if record is None or record.expires_at < _utcnow():
        raise _CREDENTIALS_EXC
    if record.revoked:
        # Reuse of an already-rotated token => likely theft. Burn the family.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == record.user_id)
            .values(revoked=True)
        )
        db.commit()
        raise _CREDENTIALS_EXC

    record.revoked = True
    db.commit()
    return issue_refresh_token(db, record.user_id), record.user_id


def revoke_refresh_token(db: Session, raw: str | None) -> None:
    if not raw:
        return
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == _hash_token(raw))
        .values(revoked=True)
    )
    db.commit()


# --- Cookies -----------------------------------------------------------------


def set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": "/",
    }
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_expire_minutes * 60,
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_expire_days * 86400,
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


# --- Request dependencies ----------------------------------------------------


def _access_token_from_request(request: Request) -> str | None:
    cookie = request.cookies.get(ACCESS_COOKIE)
    if cookie:
        return cookie
    authz = request.headers.get("Authorization", "")
    if authz.lower().startswith("bearer "):
        return authz[7:]
    return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _access_token_from_request(request)
    if token is None:
        raise _CREDENTIALS_EXC
    user_id = decode_subject(token)
    if user_id is None:
        raise _CREDENTIALS_EXC
    user = db.get(User, int(user_id))
    if user is None:
        raise _CREDENTIALS_EXC
    return user


def get_owned_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Account:
    """Resolve an account and assert the current user owns it.

    Returns 404 (not 403) for someone else's account so we don't leak which
    account ids exist.
    """
    account = db.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return account
