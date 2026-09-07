"""Zerodha Kite Connect live-data adapter — DATA ONLY, never orders.

Broker-grade upgrades over the free feeds:
  - real-time index spot (vs ~1-min cached NSE / delayed yfinance)
  - the actual bid/ask of the exact option strike (executable premiums)

Credentials live in the encrypted vault (provider "kite"): ciphertext = API
secret, extra_json.api_key = API key. Zerodha invalidates sessions daily, so
each morning the user opens the login URL and pastes the request_token back;
the exchanged access_token is stored Fernet-encrypted with its date and is
considered valid only for that IST day.

Every function degrades to None on any failure — callers keep their free-data
fallback. No order/GTT/margin endpoint is ever called from this module.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from .scalp import IST

log = logging.getLogger("agentalgo.kite")

_SPOT_SYMBOL = {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK",
                "FINNIFTY": "NSE:NIFTY FIN SERVICE"}
_MONTH_CODE = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8",
               9: "9", 10: "O", 11: "N", 12: "D"}


def _creds(user_id: str) -> dict | None:
    """{api_key, api_secret, access_token?} — token only if exchanged TODAY (IST)."""
    from .db import SessionLocal
    from .models import ApiKey
    from .security import decrypt_secret

    with SessionLocal() as db:
        row = (db.query(ApiKey)
               .filter(ApiKey.user_id == user_id, ApiKey.provider == "kite")
               .one_or_none())
        if row is None:
            return None
        extra = json.loads(row.extra_json or "{}")
        out = {"api_key": extra.get("api_key"), "api_secret": decrypt_secret(row.ciphertext)}
        if not out["api_key"]:
            return None
        today = datetime.now(IST).date().isoformat()
        if extra.get("access_token_date") == today and extra.get("access_token_enc"):
            out["access_token"] = decrypt_secret(extra["access_token_enc"])
        return out


def login_url(user_id: str) -> str | None:
    c = _creds(user_id)
    if not c:
        return None
    return f"https://kite.zerodha.com/connect/login?v=3&api_key={c['api_key']}"


def exchange_session(user_id: str, request_token: str) -> dict:
    """Daily handshake: request_token -> access_token, stored encrypted."""
    from kiteconnect import KiteConnect

    from .db import SessionLocal
    from .models import ApiKey
    from .security import encrypt_secret

    c = _creds(user_id)
    if not c:
        raise ValueError("Add your Kite API key + secret in Settings → API Keys first")
    kite = KiteConnect(api_key=c["api_key"])
    data = kite.generate_session(request_token, api_secret=c["api_secret"])
    with SessionLocal() as db:
        row = (db.query(ApiKey)
               .filter(ApiKey.user_id == user_id, ApiKey.provider == "kite").one())
        extra = json.loads(row.extra_json or "{}")
        extra["access_token_enc"] = encrypt_secret(data["access_token"])
        extra["access_token_date"] = datetime.now(IST).date().isoformat()
        extra["kite_user"] = data.get("user_id", "")
        row.extra_json = json.dumps(extra)
        row.status = "valid"
        db.commit()
    return {"connected": True, "kite_user": data.get("user_id", "")}


def _client(user_id: str):
    from kiteconnect import KiteConnect

    c = _creds(user_id)
    if not c or "access_token" not in c:
        return None
    kite = KiteConnect(api_key=c["api_key"])
    kite.set_access_token(c["access_token"])
    return kite


def _any_connected_user() -> str | None:
    """Single-operator convenience: the loops (level watch/scalp) are global, so
    any user with a live Kite session supplies the market data for everyone."""
    from .db import SessionLocal
    from .models import ApiKey

    today = datetime.now(IST).date().isoformat()
    with SessionLocal() as db:
        for row in db.query(ApiKey).filter(ApiKey.provider == "kite").all():
            extra = json.loads(row.extra_json or "{}")
            if extra.get("access_token_date") == today and extra.get("access_token_enc"):
                return row.user_id
    return None


def status(user_id: str) -> dict:
    c = _creds(user_id)
    if not c:
        return {"configured": False, "connected": False}
    out = {"configured": True, "connected": "access_token" in c}
    if out["connected"]:
        try:
            spot = kite_spot("NIFTY", user_id)
            out["nifty_ltp"] = spot
            out["connected"] = spot is not None
        except Exception:  # noqa: BLE001
            out["connected"] = False
    return out


def kite_spot(symbol: str, user_id: str | None = None) -> float | None:
    """Real-time index LTP; None when not configured/connected/known."""
    sym = _SPOT_SYMBOL.get(symbol)
    if sym is None:
        return None
    uid = user_id or _any_connected_user()
    if uid is None:
        return None
    kite = _client(uid)
    if kite is None:
        return None
    try:
        data = kite.ltp([sym])
        return float(data[sym]["last_price"])
    except Exception as exc:  # noqa: BLE001
        log.info("Kite spot failed %s: %s", symbol, exc)
        return None


def tradingsymbols(symbol: str, expiry_str: str, strike: int, direction: str) -> list[str]:
    """Candidate NFO tradingsymbols for '08-Sep-2026' style expiry.

    Weekly format: NIFTY{YY}{M}{DD}{strike}{CE} (M: 1-9, O, N, D);
    monthly format: NIFTY{YY}{MMM}{strike}{CE}. We return both and let the
    quote call decide — Zerodha only lists the one that exists.
    """
    try:
        exp = datetime.strptime(expiry_str, "%d-%b-%Y")
    except (ValueError, TypeError):
        return []
    yy = exp.strftime("%y")
    weekly = f"{symbol}{yy}{_MONTH_CODE[exp.month]}{exp.day:02d}{strike}{direction}"
    monthly = f"{symbol}{yy}{exp.strftime('%b').upper()}{strike}{direction}"
    return [f"NFO:{monthly}", f"NFO:{weekly}"] if _is_monthly(exp) else [f"NFO:{weekly}", f"NFO:{monthly}"]


def _is_monthly(exp: datetime) -> bool:
    """Last Tuesday of its month → monthly contract naming."""
    from datetime import timedelta

    nxt = exp + timedelta(days=7)
    return nxt.month != exp.month


def kite_option_quote(symbol: str, expiry_str: str, strike: int, direction: str,
                      user_id: str | None = None) -> dict | None:
    """{last_price, bid, ask, tradingsymbol} for the exact strike, or None."""
    uid = user_id or _any_connected_user()
    if uid is None:
        return None
    kite = _client(uid)
    if kite is None:
        return None
    for ts in tradingsymbols(symbol, expiry_str, int(strike), direction):
        try:
            q = kite.quote([ts]).get(ts)
            if not q:
                continue
            depth = q.get("depth") or {}
            bid = (depth.get("buy") or [{}])[0].get("price")
            ask = (depth.get("sell") or [{}])[0].get("price")
            return {"last_price": q.get("last_price"), "bid": bid, "ask": ask,
                    "tradingsymbol": ts.removeprefix("NFO:")}
        except Exception as exc:  # noqa: BLE001
            log.info("Kite quote miss %s: %s", ts, exc)
    return None


def refine_signal_with_kite(sig: dict, expiry_str: str) -> dict:
    """Upgrade a scalp signal's premium to the broker's live quote (if connected).

    Entry becomes the ASK (what a buyer actually pays); SL/TP/sizing are
    recomputed from it. Free-data numbers are kept when Kite is unavailable.
    """
    from .scalp import SCALP_RR, SCALP_SL_PCT

    try:
        q = kite_option_quote(sig["symbol"], expiry_str, int(sig["strike"]), sig["direction"])
    except Exception:  # noqa: BLE001
        q = None
    if not q or not (q.get("ask") or q.get("last_price")):
        return sig
    ep = round(float(q.get("ask") or q["last_price"]), 2)
    risk = round(ep * SCALP_SL_PCT / 100, 2)
    old_sizing = sig.get("sizing") or {}
    sig.update({
        "ep": ep, "sl": round(ep - risk, 2), "tp": round(ep + risk * SCALP_RR, 2),
        "quote_source": "kite", "bid": q.get("bid"), "ask": q.get("ask"),
        "instrument": f"{sig['symbol']} {int(sig['strike'])} {sig['direction']}",
    })
    # re-size on the broker premium using the same capital basis
    if old_sizing.get("capital"):
        from .fno import DEFAULT_LOT_SIZES, size_position

        lot = old_sizing.get("lot_size") or DEFAULT_LOT_SIZES.get(sig["symbol"])
        sig["sizing"] = size_position(ep, sig["sl"], lot,
                                      old_sizing["capital"], old_sizing["risk_pct"])
    return sig
