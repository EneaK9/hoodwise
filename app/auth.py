"""Email/password auth with argon2id and DB-backed session cookies."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, HTTPException, Response

from app.config import settings
from app.db import fetch_one, get_conn

hasher = PasswordHasher()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COOKIE_NAME = "hoodwise_session"


def _token_hash(token: str) -> str:
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def validate_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=400, detail="Invalid email")
    return email


def validate_password(password: str) -> None:
    if len(password) < 10 or len(password) > 200:
        raise HTTPException(status_code=400, detail="Password must be 10–200 characters")


def create_user(email: str, password: str) -> dict:
    email = validate_email(email)
    validate_password(password)
    existing = fetch_one("SELECT id FROM users WHERE email = %s", (email,))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    password_hash = hasher.hash(password)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id, email, created_at",
                (email, password_hash),
            )
            user = cur.fetchone()
        conn.commit()
    return user


def verify_login(email: str, password: str) -> dict:
    email = validate_email(email)
    user = fetch_one(
        "SELECT id, email, password_hash, created_at FROM users WHERE email = %s",
        (email,),
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    try:
        hasher.verify(user["password_hash"], password)
    except VerifyMismatchError:
        raise HTTPException(status_code=401, detail="Invalid credentials") from None
    return user


def issue_session(response: Response, user_id: UUID, user_agent: str | None) -> None:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO auth_sessions (token_hash, user_id, expires_at, user_agent)
                VALUES (%s, %s, %s, %s)
                """,
                (_token_hash(token), user_id, expires, (user_agent or "")[:300]),
            )
        conn.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


def current_user(hoodwise_session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> dict | None:
    if not hoodwise_session:
        return None
    row = fetch_one(
        """
        SELECT u.id, u.email, u.created_at
          FROM auth_sessions s
          JOIN users u ON u.id = s.user_id
         WHERE s.token_hash = %s
           AND s.expires_at > now()
        """,
        (_token_hash(hoodwise_session),),
    )
    return row


def require_user(user: dict | None) -> dict:
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def logout(response: Response, token: str | None) -> None:
    if token:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = %s",
                    (_token_hash(token),),
                )
            conn.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
