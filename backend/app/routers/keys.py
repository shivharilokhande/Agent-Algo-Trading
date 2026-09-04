"""F2 — encrypted BYO API-key vault."""
from __future__ import annotations

import ipaddress
import json
import socket
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import ALLOW_PRIVATE_URLS

from ..catalog import DATA_PROVIDERS, PROVIDERS
from ..db import get_db
from ..models import ApiKey, User
from ..schemas import KeyOut, KeyUpsert
from ..security import decrypt_secret, encrypt_secret, get_current_user, mask_secret

router = APIRouter(prefix="/api/keys", tags=["keys"])

_KNOWN_PROVIDERS = {p["id"] for p in PROVIDERS} | {p["id"] for p in DATA_PROVIDERS}

# H1: extras that are secrets — Fernet-encrypted at rest, never returned to clients
SECRET_EXTRA_KEYS = {"secret_access_key"}


def _to_out(k: ApiKey) -> KeyOut:
    extra = json.loads(k.extra_json or "{}")
    for secret_key in SECRET_EXTRA_KEYS:
        if secret_key in extra:
            extra[secret_key] = "•••set•••"  # presence only, never the value
    return KeyOut(
        provider=k.provider,
        mask=k.mask,
        status=k.status,
        tested_at=k.tested_at,
        extra=extra,
    )


@router.get("", response_model=list[KeyOut])
def list_keys(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    keys = db.query(ApiKey).filter(ApiKey.user_id == user.id).all()
    return [_to_out(k) for k in keys]


@router.put("", response_model=KeyOut)
def upsert_key(
    body: KeyUpsert,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if body.provider not in _KNOWN_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"Unknown provider: {body.provider}")
    if not body.secret and not body.extra:
        raise HTTPException(status_code=422, detail="Provide a secret and/or endpoint details")
    row = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user.id, ApiKey.provider == body.provider)
        .one_or_none()
    )
    if row is None:
        row = ApiKey(user_id=user.id, provider=body.provider, ciphertext="")
        db.add(row)
    if body.secret:
        row.ciphertext = encrypt_secret(body.secret)
        row.mask = mask_secret(body.secret)
    row.ciphertext = row.ciphertext or ""  # C2: URL-only providers (Ollama) store no secret
    for url_field in ("base_url", "endpoint"):
        if body.extra.get(url_field):
            validate_outbound_url(body.extra[url_field])  # S4
    incoming = dict(body.extra)
    for secret_key in SECRET_EXTRA_KEYS:  # H1: encrypt secret extras like the main secret
        if incoming.get(secret_key):
            incoming[secret_key] = encrypt_secret(incoming[secret_key])
    row.extra_json = json.dumps({**json.loads(row.extra_json or "{}"), **incoming})
    row.status = "untested"
    row.tested_at = None
    db.commit()
    return _to_out(row)


@router.delete("/{provider}", status_code=204)
def delete_key(
    provider: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user.id, ApiKey.provider == provider)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No key stored for this provider")
    db.delete(row)
    db.commit()


# ---------- S4: outbound URL guard ----------

def validate_outbound_url(raw: str) -> str:
    """Reject non-http(s) schemes and (unless allowed) private/loopback hosts."""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Endpoint must be an http(s) URL")
    if ALLOW_PRIVATE_URLS:
        return raw
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=422, detail="Endpoint host cannot be resolved") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise HTTPException(
                status_code=422,
                detail="Private/internal endpoint addresses are blocked on this deployment",
            )
    return raw


# ---------- F2.3 key validation ----------

async def _probe(provider: str, secret: str, extra: dict) -> tuple[bool, str]:
    """Cheap provider-specific validation call. Returns (ok, detail)."""
    for url_field in ("base_url", "endpoint"):
        if extra.get(url_field):
            validate_outbound_url(extra[url_field])  # S4 (re-check at probe time)
    async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
        try:
            if provider == "openai":
                r = await client.get("https://api.openai.com/v1/models",
                                     headers={"Authorization": f"Bearer {secret}"})
            elif provider == "anthropic":
                r = await client.get("https://api.anthropic.com/v1/models",
                                     headers={"x-api-key": secret, "anthropic-version": "2023-06-01"})
            elif provider == "google":
                r = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={secret}")
            elif provider == "xai":
                r = await client.get("https://api.x.ai/v1/models",
                                     headers={"Authorization": f"Bearer {secret}"})
            elif provider == "deepseek":
                r = await client.get("https://api.deepseek.com/models",
                                     headers={"Authorization": f"Bearer {secret}"})
            elif provider == "openrouter":
                r = await client.get("https://openrouter.ai/api/v1/models",
                                     headers={"Authorization": f"Bearer {secret}"})
            elif provider == "groq":
                r = await client.get("https://api.groq.com/openai/v1/models",
                                     headers={"Authorization": f"Bearer {secret}"})
            elif provider == "mistral":
                r = await client.get("https://api.mistral.ai/v1/models",
                                     headers={"Authorization": f"Bearer {secret}"})
            elif provider == "alpha_vantage":
                r = await client.get(
                    f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=IBM&apikey={secret}")
                body = r.json()
                if "Error Message" in body or "Information" in body and "Invalid" in str(body):
                    return False, "Alpha Vantage rejected the key"
            elif provider == "fred":
                r = await client.get(
                    f"https://api.stlouisfed.org/fred/series?series_id=GDP&api_key={secret}&file_type=json")
            elif provider == "ollama":
                base = (extra.get("base_url") or "http://localhost:11434/v1").rstrip("/")
                root = base[: -len("/v1")] if base.endswith("/v1") else base
                r = await client.get(f"{root}/api/tags")
            elif provider == "openai_compatible":
                base = (extra.get("base_url") or "").rstrip("/")
                if not base:
                    return False, "base_url is required"
                headers = {"Authorization": f"Bearer {secret}"} if secret else {}
                r = await client.get(f"{base}/models", headers=headers)
            else:
                return True, "No automated probe for this provider — saved as-is"
            if r.status_code < 300:
                return True, "OK"
            return False, f"HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            return False, f"Connection failed: {exc.__class__.__name__}"


@router.post("/{provider}/test", response_model=KeyOut)
async def test_key(
    provider: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user.id, ApiKey.provider == provider)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No key stored for this provider")
    secret = decrypt_secret(row.ciphertext) if row.ciphertext else ""
    ok, detail = await _probe(provider, secret, json.loads(row.extra_json or "{}"))
    row.status = "valid" if ok else "invalid"
    row.tested_at = datetime.now(timezone.utc)
    db.commit()
    result = _to_out(row)
    if not ok:
        result.extra = {**result.extra, "_test_detail": detail}
    return result
