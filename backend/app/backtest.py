"""Scalp backtester — replays history through the SAME rule code that fires live.

Uses yfinance 1-minute bars (max ~7 trading days back). Trades are simulated in
premium terms with an explicit model: entry premium = ATM_PREMIUM_PCT of spot,
premium path = delta × spot move (theta over ≤20 min treated as negligible),
brackets identical to live (SL −18%, target 1:1.5, 20-min time stop). Bar-touch
detection uses highs/lows; when SL and TP are touched in the same bar the SL is
counted first (conservative). WALL_REJECT is excluded — historical OI walls are
not available for free.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .scalp import (
    COOLDOWN_MIN,
    OPENING_RANGE_MIN,
    SCALP_RR,
    SCALP_SL_PCT,
    SCALP_TIME_STOP_MIN,
    THETA_CUTOFF,
    evaluate_rules,
)

log = logging.getLogger("agentalgo.backtest")
IST = ZoneInfo("Asia/Kolkata")

ATM_PREMIUM_PCT = 0.28   # entry premium ≈ 0.28% of spot (near-expiry ATM weekly)
ASSUMED_DELTA = 0.50     # scalp strikes are picked at |Δ|≈0.5 live
_YF = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS"}


def fetch_history_sessions(symbol: str, days: int = 7) -> dict[str, list[dict]]:
    """1m bars for the last `days` trading sessions, keyed by ISO date."""
    import yfinance as yf

    df = yf.Ticker(_YF.get(symbol, symbol)).history(period="8d", interval="1m",
                                                    auto_adjust=False)
    sessions: dict[str, list[dict]] = {}
    for ts, row in df.iterrows():
        t = ts.tz_convert(IST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(IST)
        if (t.hour, t.minute) < (9, 15) or (t.hour, t.minute) > (15, 30):
            continue
        sessions.setdefault(t.date().isoformat(), []).append(
            {"t": t.isoformat(), "hm": (t.hour, t.minute),
             "h": float(row["High"]), "l": float(row["Low"]),
             "c": float(row["Close"]), "v": float(row["Volume"] or 0)})
    return dict(sorted(sessions.items())[-days:])


def _simulate_trade(bars: list[dict], i: int, direction: str) -> dict:
    """Simulate one trade entered at bar i's close. Returns outcome + R multiple."""
    spot0 = bars[i]["c"]
    p0 = round(spot0 * ATM_PREMIUM_PCT / 100, 2)
    risk = round(p0 * SCALP_SL_PCT / 100, 2)
    sign = 1.0 if direction == "CE" else -1.0
    # premium touch levels translated to spot moves: Δp = delta × Δspot(signed)
    sl_move = risk / ASSUMED_DELTA          # adverse spot move that hits SL
    tp_move = risk * SCALP_RR / ASSUMED_DELTA
    end = min(i + SCALP_TIME_STOP_MIN, len(bars) - 1)
    for j in range(i + 1, end + 1):
        fav = sign * (bars[j]["h"] if direction == "CE" else bars[j]["l"]) - sign * spot0
        adv = sign * spot0 - sign * (bars[j]["l"] if direction == "CE" else bars[j]["h"])
        hit_sl, hit_tp = adv >= sl_move, fav >= tp_move
        if hit_sl:  # conservative: same-bar double touch counts as a loss
            return {"outcome": "SL", "r": -1.0, "exit_t": bars[j]["t"], "bars_held": j - i}
        if hit_tp:
            return {"outcome": "TP", "r": SCALP_RR, "exit_t": bars[j]["t"], "bars_held": j - i}
    # time stop: mark at close of the last bar
    dp = sign * (bars[end]["c"] - spot0) * ASSUMED_DELTA
    return {"outcome": "TIME", "r": round(dp / risk, 2), "exit_t": bars[end]["t"],
            "bars_held": end - i}


def backtest_symbol(symbol: str, days: int = 7) -> dict:
    """Walk each session bar-by-bar through evaluate_rules; simulate every signal."""
    sessions = fetch_history_sessions(symbol, days)
    trades: list[dict] = []
    for day, bars in sessions.items():
        last_fire: dict[str, int] = {}  # rule -> bar index (cooldown)
        for i in range(OPENING_RANGE_MIN + 5, len(bars)):
            if bars[i]["hm"] >= THETA_CUTOFF:
                break
            hits = evaluate_rules(bars[: i + 1])  # no OI walls in history
            for hit in hits:
                if i - last_fire.get(hit["rule"], -10_000) < COOLDOWN_MIN:
                    continue
                last_fire[hit["rule"]] = i
                sim = _simulate_trade(bars, i, hit["direction"])
                trades.append({
                    "day": day, "time": bars[i]["t"][11:16], "rule": hit["rule"],
                    "direction": hit["direction"], "spot": round(bars[i]["c"], 1),
                    "why": hit["why"], **sim,
                })
    wins = [t for t in trades if t["outcome"] == "TP"]
    losses = [t for t in trades if t["outcome"] == "SL"]
    total_r = round(sum(t["r"] for t in trades), 2)
    by_rule: dict[str, dict] = {}
    for t in trades:
        b = by_rule.setdefault(t["rule"], {"n": 0, "tp": 0, "sl": 0, "time": 0, "r": 0.0})
        b["n"] += 1
        b["r"] = round(b["r"] + t["r"], 2)
        b["tp" if t["outcome"] == "TP" else "sl" if t["outcome"] == "SL" else "time"] += 1
    return {
        "symbol": symbol,
        "sessions": list(sessions.keys()),
        "assumptions": {
            "entry_premium_pct_of_spot": ATM_PREMIUM_PCT, "delta": ASSUMED_DELTA,
            "sl_pct": SCALP_SL_PCT, "rr": SCALP_RR, "time_stop_min": SCALP_TIME_STOP_MIN,
            "note": ("Premium P&L modeled (Δ×spot move); WALL_REJECT excluded — no "
                     "historical OI. Same-bar SL+TP counted as SL. No bias filter in "
                     "backtest (bias runs didn't exist historically)."),
        },
        "trades": trades,
        "summary": {
            "n": len(trades), "tp": len(wins), "sl": len(losses),
            "time_exits": len(trades) - len(wins) - len(losses),
            "win_rate": round(len(wins) / len(trades), 3) if trades else None,
            "total_r": total_r,
            "expectancy_r": round(total_r / len(trades), 3) if trades else None,
        },
    }
