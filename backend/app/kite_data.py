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


def make_state(user_id: str) -> str:
    """Short-lived signed state so the unauthenticated browser callback can be
    mapped to the right user (10-min expiry, kite-callback scope only)."""
    import time as _time

    import jwt

    from .config import SECRET_KEY

    return jwt.encode({"sub": user_id, "scope": "kite_cb",
                       "exp": int(_time.time()) + 600}, SECRET_KEY, algorithm="HS256")


def verify_state(state: str) -> str | None:
    import jwt

    from .config import SECRET_KEY

    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=["HS256"])
        return payload["sub"] if payload.get("scope") == "kite_cb" else None
    except jwt.PyJWTError:
        return None


def login_url(user_id: str) -> str | None:
    from urllib.parse import quote

    c = _creds(user_id)
    if not c:
        return None
    # redirect_params ride through Zerodha to our callback → auto-connect
    params = quote(f"state={make_state(user_id)}")
    return (f"https://kite.zerodha.com/connect/login?v=3&api_key={c['api_key']}"
            f"&redirect_params={params}")


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


def kite_option_runup(symbol: str, expiry_str: str, strike: int, direction: str,
                      minutes: int = 15, user_id: str | None = None) -> float | None:
    """% the option premium has run from its LOW of the last `minutes` 1-min
    candles to its current price — the 'already extended?' check a human makes
    on the strike's own chart before entering. None when it can't be computed
    (no Kite session, no historical add-on, fresh contract) — callers fail open.
    """
    uid = user_id or _any_connected_user()
    if uid is None:
        return None
    kite = _client(uid)
    if kite is None:
        return None
    from datetime import timedelta

    for ts in tradingsymbols(symbol, expiry_str, int(strike), direction):
        try:
            q = kite.quote([ts]).get(ts)
            if not q or not q.get("instrument_token"):
                continue
            cur = float(q.get("last_price") or 0)
            end = datetime.now(IST)
            candles = kite.historical_data(q["instrument_token"],
                                           end - timedelta(minutes=minutes + 2),
                                           end, "minute")
            lows = [float(c["low"]) for c in candles[-minutes:] if c.get("low")]
            if not lows or cur <= 0:
                return None
            low = min(lows)
            if low <= 0:
                return None
            return round((cur - low) / low * 100, 2)
        except Exception as exc:  # noqa: BLE001
            log.info("Kite runup miss %s: %s", ts, exc)
    return None


_INDEX_TOKEN = {"NIFTY": 256265, "BANKNIFTY": 260105, "FINNIFTY": 257801}


def kite_history_sessions(symbol: str, days: int) -> dict[str, list[dict]] | None:
    """1m session bars for the last `days` trading days from Kite historical
    (subscription add-on). Chunked ≤30 days per request (API limit is 60 for
    minute candles). None when unavailable → caller falls back to yfinance."""
    from datetime import timedelta

    token = _INDEX_TOKEN.get(symbol)
    uid = _any_connected_user()
    if token is None or uid is None:
        return None
    kite = _client(uid)
    if kite is None:
        return None
    sessions: dict[str, list[dict]] = {}
    try:
        end = datetime.now(IST)
        start = end - timedelta(days=int(days * 1.6) + 5)  # weekends/holidays margin
        cur = start
        while cur < end:
            chunk_end = min(cur + timedelta(days=30), end)
            for c in kite.historical_data(token, cur, chunk_end, "minute"):
                t = c["date"]  # tz-aware IST
                if (t.hour, t.minute) < (9, 15) or (t.hour, t.minute) > (15, 30):
                    continue
                sessions.setdefault(t.date().isoformat(), []).append(
                    {"t": t.isoformat(), "hm": (t.hour, t.minute),
                     "h": float(c["high"]), "l": float(c["low"]),
                     "c": float(c["close"]), "v": float(c.get("volume") or 0)})
            cur = chunk_end
    except Exception as exc:  # noqa: BLE001 — no add-on / expired session
        log.info("Kite historical unavailable for %s: %s", symbol, exc)
        return None
    out = dict(sorted(sessions.items())[-days:])
    return out or None


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
    # R6-3: the ride plan (policy G) and profit projection were computed from
    # the free-feed premium — recompute everything premium-derived from ep
    if sig.get("exit_policy") == "G":
        from .scalp import G_FLOOR_R, G_TRAIL_R

        sig["ride_floor"] = round(ep + G_FLOOR_R * risk, 2)
        sig["trail_gap"] = round(G_TRAIL_R * risk, 2)
    # re-size on the broker premium using the same capital basis
    if old_sizing.get("capital"):
        from .fno import DEFAULT_LOT_SIZES, size_position

        lot = old_sizing.get("lot_size") or DEFAULT_LOT_SIZES.get(sig["symbol"])
        sig["sizing"] = size_position(ep, sig["sl"], lot,
                                      old_sizing["capital"], old_sizing["risk_pct"])
        if sig["sizing"].get("lots") and lot:
            sig["sizing"]["profit_tp"] = round((sig["tp"] - ep) * lot * sig["sizing"]["lots"], 2)
    return sig
