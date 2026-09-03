"""F3 — provider/model/config catalog + ticker preview."""
from __future__ import annotations

import json
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import catalog as cat
from ..config import DEMO_MODE_AVAILABLE
from ..db import get_db
from ..models import ApiKey, User
from ..security import decrypt_secret, get_current_user
from ..tickers import detect_asset_type, normalize_ticker, resolve_benchmark

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/providers")
def providers(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """All providers with per-user availability (F2.4 gating)."""
    keys = {
        k.provider: k for k in db.query(ApiKey).filter(ApiKey.user_id == user.id).all()
    }
    out = []
    for p in cat.PROVIDERS:
        k = keys.get(p["id"])
        out.append({**p, "configured": k is not None, "status": k.status if k else None})
    return {
        "llm_providers": out,
        "data_providers": [
            {**p, "configured": p["id"] in keys, "status": keys[p["id"]].status if p["id"] in keys else None}
            for p in cat.DATA_PROVIDERS
        ],
        "demo_mode": DEMO_MODE_AVAILABLE,
    }


@router.get("/models/{provider}")
async def models(
    provider: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Model options; OpenRouter list is fetched live (F3.6)."""
    if provider == "openrouter":
        row = (
            db.query(ApiKey)
            .filter(ApiKey.user_id == user.id, ApiKey.provider == "openrouter")
            .one_or_none()
        )
        if row is not None and row.ciphertext:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(
                        "https://openrouter.ai/api/v1/models",
                        headers={"Authorization": f"Bearer {decrypt_secret(row.ciphertext)}"},
                    )
                if r.status_code < 300:
                    data = r.json().get("data", [])[:30]
                    opts = [{"label": m.get("name", m["id"]), "id": m["id"]} for m in data]
                    opts.append({"label": "Custom model ID", "id": "custom"})
                    return {"quick": opts, "deep": opts}
            except httpx.HTTPError:
                pass
    return cat.get_model_options(provider)


@router.get("/options")
def options():
    return {
        "analysts": cat.ANALYSTS,
        "depths": cat.DEPTHS,
        "languages": cat.LANGUAGES,
        "thinking_knobs": cat.THINKING_KNOBS,
        "ratings": cat.RATINGS,
        "data_vendor_categories": {
            "core_stock_apis": ["yfinance", "alpha_vantage"],
            "technical_indicators": ["yfinance", "alpha_vantage"],
            "fundamental_data": ["yfinance", "alpha_vantage"],
            "news_data": ["yfinance", "alpha_vantage"],
            "macro_data": ["fred"],
            "prediction_markets": ["polymarket"],
        },
    }


@router.get("/ticker/{raw}")
def ticker_preview(raw: str, user: Annotated[User, Depends(get_current_user)]):
    """F3.1 — normalize + instrument identity preview."""
    try:
        symbol = normalize_ticker(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    asset_type = detect_asset_type(symbol)
    name, exchange, currency = _identity(symbol, asset_type)
    return {
        "symbol": symbol,
        "asset_type": asset_type,
        "benchmark": resolve_benchmark(symbol),
        "name": name,
        "exchange": exchange,
        "currency": currency,
    }


def _identity(symbol: str, asset_type: str) -> tuple[str, str, str]:
    """Deterministic instrument identity via yfinance when available (F5.5)."""
    try:
        import yfinance as yf  # optional dependency

        info = yf.Ticker(symbol).get_info()
        return (
            info.get("shortName") or info.get("longName") or symbol,
            info.get("fullExchangeName") or info.get("exchange") or "",
            info.get("currency") or ("USD" if asset_type == "crypto" else ""),
        )
    except Exception:
        return symbol, "", "USD" if asset_type == "crypto" else ""
