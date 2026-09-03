"""Password hashing, JWT sessions, and Fernet key-vault crypto (F1/F2, NFR-S1)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Annotated

import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import ACCESS_TOKEN_EXPIRE_MINUTES, FERNET_KEY, SECRET_KEY
from .db import get_db

_PBKDF2_ITERATIONS = 210_000
_fernet = Fernet(FERNET_KEY.encode())
_bearer = HTTPBearer(auto_error=False)


# ---------- Passwords ----------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_b64, digest_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------

def create_access_token(user_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> str:
    """Return the user id or raise 401."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload["sub"]
    except (jwt.PyJWTError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
):
    from .models import User

    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = decode_token(credentials.credentials)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


# ---------- WebSocket stream tickets (S3) ----------
# Short-lived, run-scoped tokens so the long-lived JWT never rides a query string.

STREAM_TICKET_TTL_SECONDS = 60


def create_stream_ticket(user_id: str, run_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "run": run_id, "typ": "stream", "iat": now,
         "exp": now + STREAM_TICKET_TTL_SECONDS},
        SECRET_KEY, algorithm="HS256",
    )


def decode_stream_ticket(token: str, run_id: str) -> str:
    """Return the user id for a valid, matching stream ticket, or raise 401."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        if payload.get("typ") != "stream" or payload.get("run") != run_id:
            raise HTTPException(status_code=401, detail="Ticket does not match this run")
        return payload["sub"]
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired stream ticket") from exc


# ---------- Auth rate limiting (S6) ----------
# In-memory sliding window — per-process, suits the single-worker deployment.

import collections
import threading

_RATE_BUCKETS: dict[str, collections.deque] = {}
_RATE_LOCK = threading.Lock()


def check_rate_limit(key: str, limit: int = 10, window_seconds: int = 60) -> None:
    """Raise 429 when `key` exceeds `limit` events per window."""
    now = time.time()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS.setdefault(key, collections.deque())
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts — wait a minute and try again",
            )
        bucket.append(now)


# A constant dummy hash so login timing doesn't reveal whether an email exists (S6).
DUMMY_PASSWORD_HASH = None  # initialised lazily below


def dummy_verify() -> None:
    global DUMMY_PASSWORD_HASH
    if DUMMY_PASSWORD_HASH is None:
        DUMMY_PASSWORD_HASH = hash_password("timing-equalizer")
    verify_password("wrong-password", DUMMY_PASSWORD_HASH)


# ---------- Key vault crypto (F2.2) ----------

def encrypt_secret(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:  # master key rotated without migration
        raise HTTPException(
            status_code=500, detail="Stored credential cannot be decrypted"
        ) from exc


def mask_secret(secret: str) -> str:
    """Display mask, e.g. 'sk-…a4f2'. Never reveals more than 7 chars."""
    if len(secret) <= 8:
        return "…" + secret[-2:]
    return f"{secret[:3]}…{secret[-4:]}"
