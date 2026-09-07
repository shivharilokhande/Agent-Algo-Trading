"""Scalp Mode — fast rule-based intraday signal engine (no LLM in the loop).

The morning engine run sets the directional bias; this module computes 1-minute
indicators locally (VWAP, EMA 9/20, RSI, opening range) and emits tight-bracket
option-buying scalp signals in seconds. Research signals only — never orders.

Rules (long-premium only):
  ORB        — spot breaks the 09:15–09:30 opening range with trend alignment
  VWAP_RECLAIM — spot crosses and holds VWAP with EMA9>EMA20 momentum
  WALL_REJECT  — spot rejects a heavy OI wall (fade back toward VWAP)

Safety rails: bias filter from the day's latest engine run, theta cutoff (no new
long-premium signals after 14:30 IST), per-rule cooldown, one-shot dedupe.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("agentalgo.scalp")

IST = ZoneInfo("Asia/Kolkata")

# --- tunables -----------------------------------------------------------------
SCALP_SL_PCT = 18.0          # stop loss: −18% of entry premium
SCALP_RR = 1.5               # target = entry + 1.5 × risk
SCALP_TIME_STOP_MIN = 20     # exit if nothing happened in 20 minutes
THETA_CUTOFF = (14, 30)      # no new long-premium signals after 14:30 IST
OPENING_RANGE_MIN = 15       # ORB window: 09:15–09:30
COOLDOWN_MIN = 30            # min gap between signals per (symbol, rule)
DELTA_LO, DELTA_HI = 0.40, 0.60  # scalp strike: fast-moving near-ATM delta
RSI_LEN = 14
# Quality filters (60d fit/validation tested — improved BOTH halves; apply only
# to ORB/VWAP_RECLAIM, the rules they were validated on):
MAX_OR_WIDTH_BP = 55   # skip hyper-volatile opens: breakouts whipsaw (37% win, −0.10R avg)
MAX_EMA_GAP_BP = 4     # don't chase extended moves (EMA9−EMA20 gap > 4bp of spot)
DEFAULT_SCALP_RISK_FRACTION = 0.5  # scalp risk = half the swing risk % by default


# --- indicators (pure functions — unit-testable) --------------------------------

def ema(values: list[float], span: int) -> float | None:
    if len(values) < span:
        return None
    k = 2 / (span + 1)
    e = sum(values[:span]) / span
    for v in values[span:]:
        e = v * k + e * (1 - k)
    return round(e, 2)


def rsi(closes: list[float], length: int = RSI_LEN) -> float | None:
    if len(closes) < length + 1:
        return None
    gains, losses = 0.0, 0.0
    for prev, cur in zip(closes[-length - 1:-1], closes[-length:]):
        d = cur - prev
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return round(100 - 100 / (1 + rs), 1)


def vwap(bars: list[dict]) -> float | None:
    """bars: [{h,l,c,v}] intraday session bars.

    NSE index feeds (yfinance ^NSEI/^NSEBANK) report zero volume, so when the
    session has no volume we fall back to the equal-weighted typical-price mean
    — same anchor concept, no volume weighting.
    """
    if not bars:
        return None
    vol = sum(b["v"] for b in bars)
    if vol:
        pv = sum(((b["h"] + b["l"] + b["c"]) / 3) * b["v"] for b in bars)
        return round(pv / vol, 2)
    tp = [(b["h"] + b["l"] + b["c"]) / 3 for b in bars]
    return round(sum(tp) / len(tp), 2)


def opening_range(bars: list[dict]) -> tuple[float, float] | None:
    """(high, low) of the first OPENING_RANGE_MIN minutes; None until complete."""
    if len(bars) < OPENING_RANGE_MIN:
        return None
    window = bars[:OPENING_RANGE_MIN]
    return (max(b["h"] for b in window), min(b["l"] for b in window))


def theta_cutoff_passed(now_ist: datetime) -> bool:
    return (now_ist.hour, now_ist.minute) >= THETA_CUTOFF


# --- rule evaluation ------------------------------------------------------------

def evaluate_rules(bars: list[dict], oi_walls: dict | None = None) -> list[dict]:
    """Evaluate scalp rules on session 1m bars. Returns [{rule, direction, why}].

    direction: "CE" (bullish) or "PE" (bearish). Pure function on bar data.
    """
    if len(bars) < OPENING_RANGE_MIN + 5:
        return []  # not enough session data yet
    closes = [b["c"] for b in bars]
    spot = closes[-1]
    vw = vwap(bars)
    e9, e20 = ema(closes, 9), ema(closes, 20)
    r = rsi(closes)
    orng = opening_range(bars)
    if not all(x is not None for x in (vw, e9, e20, r, orng)):
        return []
    or_high, or_low = orng
    out: list[dict] = []

    trend_up = e9 > e20 and spot > vw
    trend_dn = e9 < e20 and spot < vw

    # Quality gate for the trend rules (validated on 60d in/out-of-sample):
    # wide opening range = chop day, big EMA gap = late entry into a spent move.
    quality_ok = ((or_high - or_low) / spot * 1e4 <= MAX_OR_WIDTH_BP
                  and abs(e9 - e20) / spot * 1e4 <= MAX_EMA_GAP_BP)

    # ORB — FRESH breakout of the opening range with alignment. "Fresh" = one of
    # the last 3 closes was still inside the range; without this the condition
    # stays true all day in a trend and re-fires stale mid-trend entries every
    # cooldown (backtest: dominant loss source).
    prev3_orb = closes[-4:-1]
    if (quality_ok and spot > or_high and any(c <= or_high for c in prev3_orb)
            and trend_up and r < 75):
        out.append({"rule": "ORB", "direction": "CE",
                    "why": f"spot {spot:.1f} broke OR high {or_high:.1f}; EMA9>EMA20, above VWAP {vw:.1f}, RSI {r}"})
    elif (quality_ok and spot < or_low and any(c >= or_low for c in prev3_orb)
            and trend_dn and r > 25):
        out.append({"rule": "ORB", "direction": "PE",
                    "why": f"spot {spot:.1f} broke OR low {or_low:.1f}; EMA9<EMA20, below VWAP {vw:.1f}, RSI {r}"})

    # VWAP reclaim / reject — cross in the last 3 bars, holding CLEAR of VWAP
    # (≥0.05% beyond it, so 1–2 point chop around VWAP can never signal)
    clearance = vw * 0.0005
    prev3 = closes[-4:-1]
    if not quality_ok:
        prev3 = []  # quality gate also covers VWAP_RECLAIM (validated together)
    if any(c < vw for c in prev3) and spot > vw + clearance and e9 > e20 and 45 < r < 70:
        out.append({"rule": "VWAP_RECLAIM", "direction": "CE",
                    "why": f"reclaimed VWAP {vw:.1f} (spot {spot:.1f}) with EMA9>EMA20, RSI {r}"})
    elif any(c > vw for c in prev3) and spot < vw - clearance and e9 < e20 and 30 < r < 55:
        out.append({"rule": "VWAP_RECLAIM", "direction": "PE",
                    "why": f"lost VWAP {vw:.1f} (spot {spot:.1f}) with EMA9<EMA20, RSI {r}"})

    # OI wall reject — approach within 0.15% of a heavy wall and stall/reverse
    if oi_walls:
        res, sup = oi_walls.get("resistance"), oi_walls.get("support")
        band = spot * 0.0015
        recent_high = max(b["h"] for b in bars[-5:])
        recent_low = min(b["l"] for b in bars[-5:])
        if res and abs(recent_high - res) <= band and spot < recent_high and not trend_up:
            out.append({"rule": "WALL_REJECT", "direction": "PE",
                        "why": f"rejected {res:.0f} CE OI wall (high {recent_high:.1f}, spot {spot:.1f})"})
        if sup and abs(recent_low - sup) <= band and spot > recent_low and not trend_dn:
            out.append({"rule": "WALL_REJECT", "direction": "CE",
                        "why": f"bounced off {sup:.0f} PE OI wall (low {recent_low:.1f}, spot {spot:.1f})"})
    return out


def bias_allows(direction: str, bias_rating: str | None) -> bool:
    """Directional filter from the day's engine run. Hold/None allows both."""
    if bias_rating in ("Buy", "Overweight"):
        return direction == "CE"
    if bias_rating in ("Sell", "Underweight"):
        return direction == "PE"
    return True


def pick_scalp_strike(ladder: list[dict], direction: str) -> dict | None:
    """Nearest strike with |delta| in the scalp band and a live premium."""
    dkey, pkey = ("ce_delta", "ce_ltp") if direction == "CE" else ("pe_delta", "pe_ltp")
    best, best_dist = None, 9e9
    for row in ladder or []:
        d, ltp = row.get(dkey), row.get(pkey)
        if not d or not ltp or ltp <= 0:
            continue
        ad = abs(d)
        if DELTA_LO <= ad <= DELTA_HI:
            dist = abs(ad - 0.5)
            if dist < best_dist:
                best, best_dist = row, dist
    return best


def build_scalp_signal(symbol: str, rule_hit: dict, snapshot: dict,
                       settings_cfg: dict) -> dict | None:
    """Assemble a full scalp card from a rule hit + live chain snapshot."""
    from .fno import DEFAULT_CAPITAL, DEFAULT_LOT_SIZES, DEFAULT_RISK_PCT, size_position

    direction = rule_hit["direction"]
    row = pick_scalp_strike(snapshot.get("atm_ladder") or [], direction)
    if row is None:
        return None
    ltp = row["ce_ltp"] if direction == "CE" else row["pe_ltp"]
    delta = row["ce_delta"] if direction == "CE" else row["pe_delta"]
    theta = row.get("ce_theta") if direction == "CE" else row.get("pe_theta")
    ep = round(float(ltp), 2)
    risk = round(ep * SCALP_SL_PCT / 100, 2)
    sl = round(ep - risk, 2)
    tp = round(ep + risk * SCALP_RR, 2)
    capital = float(settings_cfg.get("trading_capital") or DEFAULT_CAPITAL)
    swing_risk = float(settings_cfg.get("risk_per_trade_pct") or DEFAULT_RISK_PCT)
    scalp_risk = float(settings_cfg.get("scalp_risk_pct") or swing_risk * DEFAULT_SCALP_RISK_FRACTION)
    # hard safety clamp: a typo like "50" must never size 50%-risk scalps
    scalp_risk = max(0.1, min(scalp_risk, 5.0))
    lot = (settings_cfg.get("fno_lot_sizes") or {}).get(symbol) or DEFAULT_LOT_SIZES.get(symbol)
    sizing = size_position(ep, sl, lot, capital, scalp_risk)
    if sizing.get("lots"):
        sizing["profit_tp"] = round((tp - ep) * lot * sizing["lots"], 2)
    return {
        "symbol": symbol,
        "rule": rule_hit["rule"],
        "direction": direction,
        "instrument": f"{symbol} {row['strike']:.0f} {direction}",
        "strike": row["strike"],
        "ep": ep, "sl": sl, "tp": tp, "rr": SCALP_RR,
        "delta": delta, "theta_day": theta,
        "time_stop_min": SCALP_TIME_STOP_MIN,
        "spot": snapshot.get("spot"),
        "why": rule_hit["why"],
        "sizing": sizing,
    }


# --- data: 1m session bars ------------------------------------------------------

_YF_SYMBOL = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS"}


def fetch_session_bars(symbol: str) -> list[dict]:
    """Today's 1m bars from yfinance for an index symbol (session only)."""
    import yfinance as yf

    yf_sym = _YF_SYMBOL.get(symbol, symbol)
    df = yf.Ticker(yf_sym).history(period="1d", interval="1m", auto_adjust=False)
    bars = []
    for ts, row in df.iterrows():
        t = ts.tz_convert(IST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(IST)
        if (t.hour, t.minute) < (9, 15) or (t.hour, t.minute) > (15, 30):
            continue
        bars.append({"t": t.isoformat(), "h": float(row["High"]), "l": float(row["Low"]),
                     "c": float(row["Close"]), "v": float(row["Volume"] or 0)})
    return bars


def day_bias(user_id: str, symbol: str, with_age: bool = False):
    """Rating of the user's latest completed engine run for this instrument.

    R5-7: a stale (non-today) rating no longer silently drives the filter —
    callers get None unless the run finished today; `with_age=True` returns
    {rating, as_of, today} so the UI can show the age explicitly.
    """
    from .db import SessionLocal
    from .models import Run

    # R5-6: FINNIFTY was missing — Run.ticker stores the normalized symbol
    ticker_map = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK",
                  "FINNIFTY": "NIFTY_FIN_SERVICE.NS"}
    ticker = ticker_map.get(symbol, symbol)
    with SessionLocal() as db:
        run = (db.query(Run)
               .filter(Run.user_id == user_id, Run.ticker == ticker,
                       Run.mode == "engine", Run.status == "done")
               .order_by(Run.finished_at.desc()).first())
        is_today = bool(run and run.finished_at
                        and run.finished_at.date() == datetime.now(IST).date())
        rating = run.rating if (run and is_today) else None
        if with_age:
            return {"rating": run.rating if run else None,
                    "as_of": run.finished_at.date().isoformat() if run and run.finished_at else None,
                    "today": is_today}
        return rating


# --- signal persistence + notification ------------------------------------------

def _notify_mac(title: str, message: str) -> None:
    """macOS desktop notification (best effort — backend runs on the user's Mac)."""
    import os

    if os.environ.get("AGENTALGO_DISABLE_NOTIFY"):
        return  # tests emit 'real' signals into an isolated DB — never notify
    try:
        script = f'display notification "{message}" with title "{title}" sound name "Glass"'
        # R5: fire-and-forget — a blocking run(timeout=5) could stall the event loop
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def emit_signal(user_id: str, sig: dict, simulated: bool = False) -> str | None:
    """Persist a scalp signal + alert; dedupe on (user, symbol, rule) cooldown."""
    from .db import SessionLocal
    from .models import Alert, ScalpSignal

    with SessionLocal() as db:
        from datetime import timezone

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=COOLDOWN_MIN)
        # R5-3: simulated and real signals keep SEPARATE cooldowns — a test
        # signal must never suppress (or be suppressed by) a real one
        dup = (db.query(ScalpSignal)
               .filter(ScalpSignal.user_id == user_id, ScalpSignal.symbol == sig["symbol"],
                       ScalpSignal.rule == sig["rule"],
                       ScalpSignal.simulated.is_(simulated),
                       ScalpSignal.created_at >= cutoff)
               .first())
        if dup:
            return None
        row = ScalpSignal(
            user_id=user_id, symbol=sig["symbol"], rule=sig["rule"],
            direction=sig["direction"], instrument=sig["instrument"],
            payload_json=json.dumps({**sig, "simulated": simulated}),
            simulated=simulated,
        )
        db.add(row)
        db.add(Alert(
            user_id=user_id, ticker=sig["symbol"], type="scalp",
            message=(("[SIM] " if simulated else "")
                     + f"SCALP {sig['rule']}: {sig['instrument']} @ ₹{sig['ep']} "
                       f"SL ₹{sig['sl']} TP ₹{sig['tp']} ({sig['why']})"),
        ))
        db.commit()
        rid = row.id
    if not simulated:
        _notify_mac(f"AgentAlgo scalp — {sig['instrument']}",
                    f"{sig['rule']}: entry ₹{sig['ep']} SL ₹{sig['sl']} TP ₹{sig['tp']}")
    return rid


# --- the loop --------------------------------------------------------------------

SCALP_POLL_SECONDS = 45
BARS_CACHE_TTL = 60  # R5: must exceed the poll interval or the cache never hits
_last_bars_fetch: dict[str, tuple[float, list[dict]]] = {}


def usable_session_bars(bars: list[dict], now_ist: datetime) -> list[dict]:
    """Guard the live feed (R5-2, R5-8):

    - HOLIDAY/STALE GUARD: on an NSE holiday yfinance returns the previous
      session's bars — reject any bar set whose last bar isn't from today
      or is more than 10 minutes old.
    - FORMING-BAR PARITY: the last 1m bar is still forming intraday; rules
      must see only COMPLETED bars (the backtest replays completed bars), so
      drop the bar for the current minute.
    """
    if not bars:
        return []
    last = datetime.fromisoformat(bars[-1]["t"])
    if last.date() != now_ist.date():
        return []  # previous session (holiday / feed outage) — never signal on it
    if (now_ist - last).total_seconds() > 600:
        return []  # feed stalled mid-session
    if (last.hour, last.minute) == (now_ist.hour, now_ist.minute):
        return bars[:-1]  # drop the in-progress bar
    return bars


async def scalp_sweep() -> int:
    """One sweep across all scalp-enabled users. Returns signals emitted."""
    from .db import SessionLocal
    from .fno import get_fno_snapshot, is_market_hours_ist
    from .models import Setting

    now_ist = datetime.now(IST)
    if not is_market_hours_ist() or theta_cutoff_passed(now_ist):
        return 0
    with SessionLocal() as db:
        rows = db.query(Setting).all()
        users = []
        for srow in rows:
            cfg = json.loads(srow.config_json or "{}")
            if cfg.get("scalp_enabled"):
                users.append((srow.user_id, cfg))
    if not users:
        return 0
    emitted = 0
    symbols = sorted({sym for _, cfg in users
                      for sym in (cfg.get("scalp_symbols") or ["NIFTY"])})
    for symbol in symbols:
        try:
            now = time.time()
            cached = _last_bars_fetch.get(symbol)
            if cached and now - cached[0] < BARS_CACHE_TTL:
                bars = cached[1]
            else:
                bars = await asyncio.to_thread(fetch_session_bars, symbol)
                _last_bars_fetch[symbol] = (now, bars)
            bars = usable_session_bars(bars, datetime.now(IST))
            if not bars:
                log.info("Scalp: no usable session bars for %s (holiday/stale feed?)", symbol)
                continue
            snapshot = await get_fno_snapshot(symbol)
            walls = {"resistance": (snapshot.get("resistance_strikes") or [{}])[0].get("strike"),
                     "support": (snapshot.get("support_strikes") or [{}])[0].get("strike")}
            hits = evaluate_rules(bars, walls)
        except Exception as exc:  # noqa: BLE001
            log.info("Scalp data unavailable for %s: %s", symbol, exc)
            continue
        if not hits:
            continue
        for user_id, cfg in users:
            if symbol not in (cfg.get("scalp_symbols") or ["NIFTY"]):
                continue
            # R5: keep blocking DB work off the event loop
            bias = await asyncio.to_thread(day_bias, user_id, symbol)
            for hit in hits:
                if not bias_allows(hit["direction"], bias):
                    continue
                sig = build_scalp_signal(symbol, hit, snapshot, cfg)
                if sig:
                    try:  # broker-grade premium when a Kite session is live
                        from .kite_data import refine_signal_with_kite

                        sig = await asyncio.to_thread(
                            refine_signal_with_kite, sig, snapshot.get("expiry"))
                    except Exception:  # noqa: BLE001
                        pass
                    if await asyncio.to_thread(emit_signal, user_id, sig):
                        emitted += 1
    return emitted


async def scalp_loop() -> None:
    while True:
        try:
            n = await scalp_sweep()
            if n:
                log.info("Scalp sweep emitted %s signal(s)", n)
        except Exception:  # pragma: no cover
            log.exception("Scalp sweep failed")
        await asyncio.sleep(SCALP_POLL_SECONDS)
