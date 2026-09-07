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
_STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}


def atm_instrument(symbol: str, spot: float, direction: str) -> str:
    """The ATM strike the model trades: nearest listed strike to spot at entry."""
    step = _STRIKE_STEP.get(symbol, 50)
    return f"{symbol} {round(spot / step) * step} {direction}"


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


HARD_CAP_MIN = 45  # trailing policies: absolute max holding time


def _simulate_policy(bars: list[dict], i: int, direction: str, policy: str) -> dict:
    """Simulate one trade under an exit policy. R units; conservative ordering.

    A fixed  : SL −1R / TP +1.5R / exit at 20 min (the live behavior today)
    B trail  : SL −1R; at +0.5R move stop to breakeven, then trail 0.5R off the
               peak; no target cap; hard cap 45 min
    C hybrid : B, plus 'loser time-out' — if +0.5R never reached by 20 min, exit
    """
    spot0 = bars[i]["c"]
    p0 = spot0 * ATM_PREMIUM_PCT / 100
    risk = p0 * SCALP_SL_PCT / 100
    sign = 1.0 if direction == "CE" else -1.0
    to_r = lambda px: sign * (px - spot0) * ASSUMED_DELTA / risk  # noqa: E731
    end = min(i + (SCALP_TIME_STOP_MIN if policy == "A" else HARD_CAP_MIN), len(bars) - 1)
    peak, be_armed = 0.0, False
    for j in range(i + 1, end + 1):
        hi, lo = bars[j]["h"], bars[j]["l"]
        fav = to_r(hi if direction == "CE" else lo)
        adv = to_r(lo if direction == "CE" else hi)  # most adverse close-equivalent
        close_r = to_r(bars[j]["c"])
        if policy == "A":
            if adv <= -1.0:
                return {"outcome": "SL", "r": -1.0, "bars_held": j - i}
            if fav >= SCALP_RR:
                return {"outcome": "TP", "r": SCALP_RR, "bars_held": j - i}
            continue
        # B / C — stops first (conservative), using state from BEFORE this bar
        if not be_armed and adv <= -1.0:
            return {"outcome": "SL", "r": -1.0, "bars_held": j - i}
        if be_armed and adv <= 0.0:
            return {"outcome": "BE", "r": 0.0, "bars_held": j - i}
        if be_armed and close_r <= peak - 0.5:
            return {"outcome": "TRAIL", "r": round(close_r, 2), "bars_held": j - i}
        peak = max(peak, fav)
        if peak >= 0.5:
            be_armed = True
        if policy == "C" and j - i >= SCALP_TIME_STOP_MIN and not be_armed:
            return {"outcome": "TIME", "r": round(close_r, 2), "bars_held": j - i}
    return {"outcome": "TIME", "r": round(to_r(bars[end]["c"]), 2), "bars_held": end - i}


def compare_exit_policies(symbol: str, days: int = 7) -> dict:
    """Same signals, three exit policies, side-by-side summaries."""
    sessions = fetch_history_sessions(symbol, days)
    signals: list[tuple[list[dict], int, str, str, str]] = []
    for day, bars in sessions.items():
        last_fire: dict[str, int] = {}
        for i in range(OPENING_RANGE_MIN + 5, len(bars)):
            if bars[i]["hm"] >= THETA_CUTOFF:
                break
            for hit in evaluate_rules(bars[: i + 1]):
                if i - last_fire.get(hit["rule"], -10_000) < COOLDOWN_MIN:
                    continue
                last_fire[hit["rule"]] = i
                signals.append((bars, i, hit["direction"], hit["rule"], day))
    policies = {"A_fixed_20m": "A", "B_trail": "B", "C_hybrid": "C"}
    out: dict = {"symbol": symbol, "sessions": list(sessions.keys()),
                 "n_signals": len(signals), "policies": {}}
    for name, p in policies.items():
        rs = [_simulate_policy(bars, i, d, p) for bars, i, d, _, _ in signals]
        total = round(sum(x["r"] for x in rs), 2)
        winners = [x["r"] for x in rs if x["r"] > 0.05]
        losers = [x["r"] for x in rs if x["r"] < -0.05]
        out["policies"][name] = {
            "net_r": total,
            "expectancy_r": round(total / len(rs), 3) if rs else None,
            "winners": len(winners), "losers": len(losers),
            "flat": len(rs) - len(winners) - len(losers),
            "avg_winner_r": round(sum(winners) / len(winners), 2) if winners else None,
            "avg_loser_r": round(sum(losers) / len(losers), 2) if losers else None,
            "best_r": round(max((x["r"] for x in rs), default=0), 2),
            "avg_hold_min": round(sum(x["bars_held"] for x in rs) / len(rs), 1) if rs else None,
        }
    return out


def backtest_symbol(symbol: str, days: int = 7, capital: float = 100_000.0,
                    risk_pct: float = 1.0) -> dict:
    """Walk each session bar-by-bar through evaluate_rules; simulate every signal.

    Rupee simulation: live sizing rules applied on RUNNING equity (compounding) —
    lots = risk budget ÷ risk per lot, premium outlay capped at 30% of equity.
    Trades the equity can't afford (0 lots) are recorded as skipped.
    """
    from .fno import DEFAULT_LOT_SIZES, size_position

    lot = DEFAULT_LOT_SIZES.get(symbol)
    sessions = fetch_history_sessions(symbol, days)
    trades: list[dict] = []
    equity = capital
    peak_equity, max_dd = capital, 0.0
    skipped = 0
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
                spot0 = bars[i]["c"]
                ep = round(spot0 * ATM_PREMIUM_PCT / 100, 2)
                risk = round(ep * SCALP_SL_PCT / 100, 2)
                exit_p = round(ep + sim["r"] * risk, 2)
                sizing = size_position(ep, round(ep - risk, 2), lot, equity, risk_pct)
                lots = sizing.get("lots") or 0
                pnl = round(sim["r"] * risk * lot * lots, 2) if lots else 0.0
                if lots:
                    equity = round(equity + pnl, 2)
                    peak_equity = max(peak_equity, equity)
                    max_dd = max(max_dd, peak_equity - equity)
                else:
                    skipped += 1
                trades.append({
                    "day": day, "time": bars[i]["t"][11:16], "rule": hit["rule"],
                    "direction": hit["direction"], "spot": round(spot0, 1),
                    "instrument": atm_instrument(symbol, spot0, hit["direction"]),
                    "entry": ep, "exit": exit_p, "lots": lots,
                    "outlay": round(ep * lot * lots, 2) if lots else 0.0,
                    "pnl": pnl, "equity": equity,
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
            "capital_start": capital, "capital_end": equity,
            "net_pnl": round(equity - capital, 2),
            "return_pct": round((equity - capital) / capital * 100, 2) if capital else None,
            "max_drawdown": round(max_dd, 2),
            "risk_pct": risk_pct, "lot_size": lot,
            "skipped_unaffordable": skipped,
        },
    }
