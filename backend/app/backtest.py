"""Scalp backtester — replays history through the SAME rule code that fires live.

Uses yfinance 1-minute bars (max ~7 trading days back). Trades are simulated in
premium terms with an explicit model: entry premium = 0.4·S·σ·√T (time-scaled ATM),
premium path = delta × spot move (theta over ≤20 min treated as negligible),
brackets identical to live (SL −18%, target 1:1.5, 20-min time stop). Bar-touch
detection uses highs/lows; when SL and TP are touched in the same bar the SL is
counted first (conservative). WALL_REJECT is excluded — historical OI walls are
not available for free.
"""
from __future__ import annotations

import logging

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
from .scalp import IST, _YF_SYMBOL  # shared: one tz + one symbol map (R5 dead-code sweep)

ASSUMED_IV = 13.0        # fallback ATM IV (%) when India VIX is unavailable
ASSUMED_DELTA = 0.50     # scalp strikes are picked at |Δ|≈0.5 live
DEFAULT_BROKERAGE = 20.0  # ₹ per executed order (Zerodha F&O flat)
DEFAULT_SLIP_PCT = 0.25   # % of premium lost to the spread on EACH side

# Statutory rates (Zerodha schedule, Sep 2026): STT 0.15% on sell-side premium
# (raised from 0.10% on 01-Apr-2026), NSE txn 0.03553% of premium turnover,
# SEBI ₹10/crore, stamp 0.003% buy-side, GST 18% on brokerage+txn+SEBI.
_STT_SELL = 0.0015
_NSE_TXN = 0.0003553
_SEBI_PER_RUPEE = 10 / 1e7
_STAMP_BUY = 0.00003
_GST = 0.18


def trade_cost(entry_p: float, exit_p: float, lot: int, lots: int,
               brokerage: float = DEFAULT_BROKERAGE,
               slip_pct: float = DEFAULT_SLIP_PCT) -> float:
    """Exact Zerodha option round-trip cost + slippage.

    Verified against the worked example (75 qty, buy ₹100 → sell ₹110):
    brokerage ₹40 + STT ₹12.38 + txn ₹5.60 + stamp ₹0.23 + SEBI ₹0.02
    + GST ₹8.21 ≈ ₹66.4 (before slippage).
    """
    units = lot * lots
    buy_turn = entry_p * units
    sell_turn = max(exit_p, 0.05) * units
    brok = 2 * brokerage
    stt = _STT_SELL * sell_turn
    txn = _NSE_TXN * (buy_turn + sell_turn)
    sebi = _SEBI_PER_RUPEE * (buy_turn + sell_turn)
    stamp = _STAMP_BUY * buy_turn
    gst = _GST * (brok + txn + sebi)
    slip = slip_pct / 100 * (entry_p + exit_p) * units
    return round(brok + stt + txn + sebi + stamp + gst + slip, 2)
# index vol vs India VIX (VIX tracks NIFTY; bank/fin indices run hotter)
_VIX_MULT = {"NIFTY": 1.0, "BANKNIFTY": 1.25, "FINNIFTY": 1.1}


def fetch_vix_map(days: int = 12) -> dict[str, float]:
    """ISO date -> India VIX close, for calibrating the premium model per day."""
    import yfinance as yf

    try:
        closes = yf.Ticker("^INDIAVIX").history(period=f"{days + 5}d")["Close"].dropna()
        return {ts.date().isoformat(): float(v) for ts, v in closes.items()}
    except Exception:  # noqa: BLE001 — model falls back to ASSUMED_IV
        return {}


def model_premium(spot: float, day_iso: str, symbol: str,
                  iv: float | None = None) -> float:
    """ATM premium via Brenner–Subrahmanyam: 0.4 · S · σ · √T to the nearest expiry.

    σ comes from that day's India VIX (× index multiplier) when available.
    Verified against reality (both user-caught): 02-Sep, 6 days to expiry,
    VIX-calibrated ≈ real ₹165; 07-Sep expiry-eve, VIX 10.68 → ₹53.2 vs the
    real 23800 PE at ₹53.50 (a flat 13% IV had said ₹64.8)."""
    from datetime import date, datetime

    sigma = (iv if iv else ASSUMED_IV) * _VIX_MULT.get(symbol, 1.0)
    exp = datetime.strptime(assumed_expiry(symbol, day_iso), "%d-%b-%Y").date()
    dte = max((exp - date.fromisoformat(day_iso)).days, 0.5)
    return round(0.4 * spot * (sigma / 100) * (dte / 365) ** 0.5, 2)
_YF = _YF_SYMBOL  # alias — single source of truth lives in scalp.py
_STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}


def atm_instrument(symbol: str, spot: float, direction: str) -> str:
    """The ATM strike the model trades: nearest listed strike to spot at entry."""
    step = _STRIKE_STEP.get(symbol, 50)
    return f"{symbol} {round(spot / step) * step} {direction}"


def assumed_expiry(symbol: str, day_iso: str) -> str:
    """Nearest expiry the model assumes: NIFTY = next weekly (Tuesday);
    BANKNIFTY/FINNIFTY = monthly (last Tuesday of the month)."""
    from datetime import date, timedelta

    d = date.fromisoformat(day_iso)
    if symbol == "NIFTY":
        exp = d + timedelta(days=(1 - d.weekday()) % 7)  # next Tuesday (incl. today)
    else:
        def last_tuesday(y: int, m: int) -> date:
            nxt = date(y + (m == 12), m % 12 + 1, 1)
            e = nxt - timedelta(days=1)
            return e - timedelta(days=(e.weekday() - 1) % 7)

        exp = last_tuesday(d.year, d.month)
        if exp < d:
            y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
            exp = last_tuesday(y, m)
    return exp.strftime("%d-%b-%Y")


def fetch_history_sessions(symbol: str, days: int = 7) -> dict[str, list[dict]]:
    """1m bars for the last `days` trading sessions, keyed by ISO date.

    Kite historical first when a broker session is live (up to ~60 days of
    exchange-grade candles); yfinance fallback caps at ~7 days.
    """
    try:
        from .kite_data import kite_history_sessions

        kite_sessions = kite_history_sessions(symbol, days)
        if kite_sessions:
            return kite_sessions
    except Exception:  # noqa: BLE001
        pass
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


def _simulate_trade(bars: list[dict], i: int, direction: str, p0: float) -> dict:
    """Simulate one trade entered at bar i's close. Returns outcome + R multiple."""
    spot0 = bars[i]["c"]
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


def _simulate_policy(bars: list[dict], i: int, direction: str, policy: str,
                     p0: float | None = None) -> dict:
    """Simulate one trade under an exit policy. R units; conservative ordering.

    A fixed  : SL −1R / TP +1.5R / exit at 20 min (the live behavior today)
    B trail  : SL −1R; at +0.5R move stop to breakeven, then trail 0.5R off the
               peak; no target cap; hard cap 45 min
    C hybrid : B, plus 'loser time-out' — if +0.5R never reached by 20 min, exit
    E extend : A for 20 min; at the bell, a trade holding ≥ +0.5R earns 10 more
               minutes chasing the full TP with a +0.5R profit floor (exit the
               moment it slips below); everything else exits like A (user idea)
    G run-TP : A until the +1.5R target is TOUCHED — then instead of banking,
               ride with a ratcheting trail (floor starts +1.2R, tracks
               peak−0.3R) chasing 2R/3R; hard cap 45 min (user idea #2)
    """
    spot0 = bars[i]["c"]
    if p0 is None:
        p0 = 0.4 * spot0 * (ASSUMED_IV / 100) * (3 / 365) ** 0.5  # generic mid-week
    risk = p0 * SCALP_SL_PCT / 100
    sign = 1.0 if direction == "CE" else -1.0
    to_r = lambda px: sign * (px - spot0) * ASSUMED_DELTA / risk  # noqa: E731
    end = min(i + (SCALP_TIME_STOP_MIN if policy == "A" else
                   SCALP_TIME_STOP_MIN + 10 if policy in ("E", "F") else HARD_CAP_MIN),
              len(bars) - 1)
    peak, be_armed = 0.0, False
    extended = False
    tp_riding = False  # policy G: TP touched, now trailing for more
    for j in range(i + 1, end + 1):
        hi, lo = bars[j]["h"], bars[j]["l"]
        fav = to_r(hi if direction == "CE" else lo)
        adv = to_r(lo if direction == "CE" else hi)  # most adverse close-equivalent
        close_r = to_r(bars[j]["c"])
        if policy == "G":
            if tp_riding:
                peak = max(peak, fav)
                floor = max(1.2, peak - 0.3)
                if close_r <= floor:
                    return {"outcome": "RUN", "r": round(max(close_r, 1.2), 2),
                            "exit_t": bars[j]["t"], "bars_held": j - i}
                continue
            if adv <= -1.0:
                return {"outcome": "SL", "r": -1.0, "exit_t": bars[j]["t"], "bars_held": j - i}
            if fav >= SCALP_RR:
                tp_riding, peak = True, fav  # don't bank — ride the winner
                continue
            if j - i >= SCALP_TIME_STOP_MIN:
                return {"outcome": "TIME", "r": round(close_r, 2), "exit_t": bars[j]["t"], "bars_held": j - i}
            continue
        if policy in ("A", "E", "F"):
            if adv <= -1.0:
                return {"outcome": "SL", "r": -1.0, "exit_t": bars[j]["t"], "bars_held": j - i}
            if fav >= SCALP_RR:
                return {"outcome": "TP", "r": SCALP_RR, "exit_t": bars[j]["t"], "bars_held": j - i}
            if policy in ("E", "F"):
                if j - i == SCALP_TIME_STOP_MIN:
                    if close_r >= 0.5:
                        extended = True  # earned 10 extra minutes chasing TP
                        peak = max(peak, close_r)
                    else:
                        return {"outcome": "TIME", "r": round(close_r, 2), "exit_t": bars[j]["t"], "bars_held": j - i}
                elif extended:
                    peak = max(peak, close_r)
                    # E: static +0.5R floor; F: floor ratchets up with the peak
                    floor = 0.5 if policy == "E" else max(0.5, peak - 0.25)
                    if close_r < floor:
                        return {"outcome": "FLOOR", "r": round(close_r, 2), "exit_t": bars[j]["t"], "bars_held": j - i}
            continue
        # B / C — stops first (conservative), using state from BEFORE this bar
        if not be_armed and adv <= -1.0:
            return {"outcome": "SL", "r": -1.0, "exit_t": bars[j]["t"], "bars_held": j - i}
        if be_armed and adv <= 0.0:
            return {"outcome": "BE", "r": 0.0, "exit_t": bars[j]["t"], "bars_held": j - i}
        if be_armed and close_r <= peak - 0.5:
            return {"outcome": "TRAIL", "r": round(close_r, 2), "exit_t": bars[j]["t"], "bars_held": j - i}
        peak = max(peak, fav)
        if peak >= 0.5:
            be_armed = True
        if policy == "C" and j - i >= SCALP_TIME_STOP_MIN and not be_armed:
            return {"outcome": "TIME", "r": round(close_r, 2), "exit_t": bars[j]["t"], "bars_held": j - i}
    return {"outcome": "TIME", "r": round(to_r(bars[end]["c"]), 2),
            "exit_t": bars[end]["t"], "bars_held": end - i}


MAX_CONCURRENT = 2  # combined portfolio: at most this many open positions


def backtest_combined(days: int = 7, capital: float = 100_000.0,
                      risk_pct: float = 1.0,
                      brokerage: float = DEFAULT_BROKERAGE,
                      slip_pct: float = DEFAULT_SLIP_PCT,
                      symbols: tuple[str, ...] = ("NIFTY", "BANKNIFTY"),
                      exit_policy: str = "A") -> dict:
    """ONE account trading all `symbols` chronologically — the realistic setup.

    Signals from every index are merged in time order; open positions reserve
    their premium outlay, at most MAX_CONCURRENT positions are held at once,
    and P&L settles at each trade's exit bar (no look-ahead).
    """
    from .fno import DEFAULT_LOT_SIZES, size_position

    # 1) collect candidates per symbol (same rules/cooldowns as the live engine)
    cands: list[dict] = []
    all_days: set[str] = set()
    for sym in symbols:
        sessions = fetch_history_sessions(sym, days)
        vix = fetch_vix_map(days)
        all_days |= set(sessions)
        for day, bars in sessions.items():
            last: dict[str, int] = {}
            for i in range(OPENING_RANGE_MIN + 5, len(bars)):
                if bars[i]["hm"] >= THETA_CUTOFF:
                    break
                for hit in evaluate_rules(bars[: i + 1]):
                    m = bars[i]["hm"][0] * 60 + bars[i]["hm"][1]
                    if m - last.get(hit["rule"], -10_000) < COOLDOWN_MIN:
                        continue
                    last[hit["rule"]] = m
                    spot0 = bars[i]["c"]
                    ep = model_premium(spot0, day, sym, vix.get(day))
                    sim = (_simulate_trade(bars, i, hit["direction"], ep)
                           if exit_policy == "A"
                           else _simulate_policy(bars, i, hit["direction"], "G", ep))
                    cands.append({
                        "day": day, "em": m, "xm": m + sim["bars_held"], "sym": sym,
                        "time": bars[i]["t"][11:16], "rule": hit["rule"],
                        "direction": hit["direction"], "spot": round(spot0, 1),
                        "instrument": atm_instrument(sym, spot0, hit["direction"]),
                        "expiry": assumed_expiry(sym, day), "entry": ep,
                        "risk": round(ep * SCALP_SL_PCT / 100, 2),
                        "why": hit["why"], **sim,
                    })
    cands.sort(key=lambda c: (c["day"], c["em"]))

    # 2) portfolio walk: settle at exit, reserve outlay, cap concurrency
    trades: list[dict] = []
    equity, peak, max_dd = capital, capital, 0.0
    skipped_conc = skipped_size = 0
    for day in sorted(all_days):
        open_pos: list[dict] = []

        def _settle(upto: int) -> None:
            nonlocal equity, peak, max_dd
            for p in sorted([p for p in open_pos if p["xm"] <= upto], key=lambda p: p["xm"]):
                equity = round(equity + p["pnl"], 2)
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                p["rec"]["equity"] = equity
                open_pos.remove(p)

        for c in [c for c in cands if c["day"] == day]:
            _settle(c["em"])
            if len(open_pos) >= MAX_CONCURRENT:
                skipped_conc += 1
                continue
            lot = DEFAULT_LOT_SIZES.get(c["sym"])
            reserved = sum(p["outlay"] for p in open_pos)
            sizing = size_position(c["entry"], round(c["entry"] - c["risk"], 2), lot,
                                   max(equity - reserved, 0), risk_pct)
            lots = sizing.get("lots") or 0
            if not lots:
                skipped_size += 1
                continue
            exit_p = round(c["entry"] + c["r"] * c["risk"], 2)
            cost = trade_cost(c["entry"], exit_p, lot, lots, brokerage, slip_pct)
            pnl = round(c["r"] * c["risk"] * lot * lots - cost, 2)
            outlay = round(c["entry"] * lot * lots, 2)
            rec = {k: c[k] for k in ("day", "time", "rule", "direction", "spot",
                                     "instrument", "expiry", "why", "r", "outcome",
                                     "bars_held", "exit_t")}
            rec.update({"entry": c["entry"], "exit": exit_p, "cost": cost,
                        "lots": lots, "outlay": outlay, "pnl": pnl, "equity": equity})
            trades.append(rec)
            open_pos.append({"xm": c["xm"], "pnl": pnl, "outlay": outlay, "rec": rec})
        _settle(10**9)

    wins = [t for t in trades if t["outcome"] == "TP"]
    losses = [t for t in trades if t["outcome"] == "SL"]
    profitable = sum(1 for t in trades if t["pnl"] > 0)  # net of charges
    total_r = round(sum(t["r"] for t in trades), 2)
    return {
        "symbol": "COMBINED (" + "+".join(symbols) + f", max {MAX_CONCURRENT} open)",
        "sessions": sorted(all_days),
        "assumptions": {
            "premium_model": ("0.4·S·σ·√T — σ from each day's India VIX close"
                              f" (fallback {ASSUMED_IV}%), T to nearest expiry"),
            "delta": ASSUMED_DELTA, "sl_pct": SCALP_SL_PCT, "rr": SCALP_RR,
            "time_stop_min": SCALP_TIME_STOP_MIN,
            "note": (f"ONE shared account across {'+'.join(symbols)} — outlay reserved "
                     f"while positions are open, max {MAX_CONCURRENT} concurrent; "
                     f"{skipped_conc} signals skipped for concurrency, {skipped_size} "
                     "unaffordable. Premium P&L modeled; WALL_REJECT excluded."),
        },
        "trades": trades,
        "summary": {
            "n": len(trades) + skipped_conc + skipped_size, "n_taken": len(trades),
            "tp": len(wins), "sl": len(losses),
            "time_exits": len(trades) - len(wins) - len(losses),
            "win_rate": round(len(wins) / len(trades), 3) if trades else None,
            "profit_rate": round(profitable / len(trades), 3) if trades else None,
            "profitable": profitable,
            "total_r": total_r,
            "expectancy_r": round(total_r / len(trades), 3) if trades else None,
            "capital_start": capital, "capital_end": equity,
            "net_pnl": round(equity - capital, 2),
            "return_pct": round((equity - capital) / capital * 100, 2) if capital else None,
            "max_drawdown": round(max_dd, 2),
            "risk_pct": risk_pct, "lot_size": None,
            "skipped_unaffordable": skipped_size,
            "total_costs": round(sum(t.get("cost", 0) for t in trades), 2),
            "cost_model": (f"Zerodha exact: ₹{brokerage}/order + STT 0.15% sell + "
                           f"txn 0.03553% + stamp + GST + {slip_pct}%/side slippage"),
        },
    }


def compare_exit_policies(symbol: str, days: int = 7) -> dict:
    """Same signals, three exit policies, side-by-side summaries."""
    sessions = fetch_history_sessions(symbol, days)
    vix = fetch_vix_map(days)
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
    policies = {"A_fixed_20m": "A", "B_trail": "B", "C_hybrid": "C",
                "E_extend_floor": "E", "F_extend_ratchet": "F",
                "G_run_after_tp": "G"}
    out: dict = {"symbol": symbol, "sessions": list(sessions.keys()),
                 "n_signals": len(signals), "policies": {}}
    for name, p in policies.items():
        rs = [_simulate_policy(bars, i, d, p, model_premium(bars[i]["c"], day, symbol, vix.get(day)))
              for bars, i, d, _, day in signals]
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
                    risk_pct: float = 1.0, brokerage: float = DEFAULT_BROKERAGE,
                    slip_pct: float = DEFAULT_SLIP_PCT,
                    exit_policy: str = "A") -> dict:
    """Walk each session bar-by-bar through evaluate_rules; simulate every signal.

    Rupee simulation (R5-5, no look-ahead): P&L settles at the trade's EXIT bar,
    never at entry, and while a trade is open its premium outlay is reserved —
    a second overlapping signal is sized on equity minus open outlay, so future
    profits can never fund a position and concurrent outlay can't stack past
    the cap. Trades the free equity can't afford (0 lots) are recorded as skipped.
    Cooldown compares bar TIMESTAMPS (minutes), matching the live 30-min clock
    even when the feed has missing bars (R5-13).
    """
    from .fno import DEFAULT_LOT_SIZES, size_position

    lot = DEFAULT_LOT_SIZES.get(symbol)
    sessions = fetch_history_sessions(symbol, days)
    vix = fetch_vix_map(days)
    trades: list[dict] = []
    equity = capital
    peak_equity, max_dd = capital, 0.0
    skipped = 0

    def _mins(hm: tuple[int, int]) -> int:
        return hm[0] * 60 + hm[1]

    for day, bars in sessions.items():
        last_fire: dict[str, int] = {}  # rule -> minutes-of-day (cooldown)
        open_trades: list[dict] = []    # [{exit_i, pnl, outlay, rec}]
        open_outlay = 0.0

        def _settle_until(bar_i: int) -> None:
            nonlocal equity, peak_equity, max_dd, open_outlay
            for ot in sorted([o for o in open_trades if o["exit_i"] <= bar_i],
                             key=lambda o: o["exit_i"]):
                equity = round(equity + ot["pnl"], 2)
                open_outlay = round(open_outlay - ot["outlay"], 2)
                peak_equity = max(peak_equity, equity)
                max_dd = max(max_dd, peak_equity - equity)
                ot["rec"]["equity"] = equity
                open_trades.remove(ot)

        for i in range(OPENING_RANGE_MIN + 5, len(bars)):
            if bars[i]["hm"] >= THETA_CUTOFF:
                break
            hits = evaluate_rules(bars[: i + 1])  # no OI walls in history
            if not hits:
                continue
            _settle_until(i)  # realize anything that exited before this bar
            for hit in hits:
                now_min = _mins(bars[i]["hm"])
                if now_min - last_fire.get(hit["rule"], -10_000) < COOLDOWN_MIN:
                    continue
                last_fire[hit["rule"]] = now_min
                spot0 = bars[i]["c"]
                ep = model_premium(spot0, day, symbol, vix.get(day))
                sim = (_simulate_trade(bars, i, hit["direction"], ep)
                       if exit_policy == "A"
                       else _simulate_policy(bars, i, hit["direction"], "G", ep))
                risk = round(ep * SCALP_SL_PCT / 100, 2)
                exit_p = round(ep + sim["r"] * risk, 2)
                free_equity = max(equity - open_outlay, 0.0)
                sizing = size_position(ep, round(ep - risk, 2), lot, free_equity, risk_pct)
                lots = sizing.get("lots") or 0
                cost = trade_cost(ep, exit_p, lot, lots, brokerage, slip_pct) if lots else 0.0
                pnl = round(sim["r"] * risk * lot * lots - cost, 2) if lots else 0.0
                outlay = round(ep * lot * lots, 2) if lots else 0.0
                rec = {
                    "day": day, "time": bars[i]["t"][11:16], "rule": hit["rule"],
                    "direction": hit["direction"], "spot": round(spot0, 1),
                    "instrument": atm_instrument(symbol, spot0, hit["direction"]),
                    "expiry": assumed_expiry(symbol, day),
                    "entry": ep, "exit": exit_p, "lots": lots,
                    "outlay": outlay, "pnl": pnl, "cost": cost, "equity": equity,
                    "why": hit["why"], **sim,
                }
                trades.append(rec)
                if lots:
                    open_trades.append({"exit_i": i + sim["bars_held"], "pnl": pnl,
                                        "outlay": outlay, "rec": rec})
                    open_outlay = round(open_outlay + outlay, 2)
                else:
                    skipped += 1
        _settle_until(10**9)  # end of session: realize everything still open

    # R5-19: R statistics over TAKEN trades only; skipped rows carry no result
    taken = [t for t in trades if t["lots"]]
    wins = [t for t in taken if t["outcome"] == "TP"]
    losses = [t for t in taken if t["outcome"] == "SL"]
    profitable = sum(1 for t in taken if t["pnl"] > 0)  # net of charges
    total_r = round(sum(t["r"] for t in taken), 2)
    by_rule: dict[str, dict] = {}
    for t in taken:
        b = by_rule.setdefault(t["rule"], {"n": 0, "tp": 0, "sl": 0, "time": 0, "r": 0.0})
        b["n"] += 1
        b["r"] = round(b["r"] + t["r"], 2)
        b["tp" if t["outcome"] == "TP" else "sl" if t["outcome"] == "SL" else "time"] += 1
    return {
        "symbol": symbol,
        "sessions": list(sessions.keys()),
        "assumptions": {
            "premium_model": ("0.4·S·σ·√T — σ from each day's India VIX close"
                              f" (fallback {ASSUMED_IV}%), T to nearest expiry"),
            "delta": ASSUMED_DELTA,
            "sl_pct": SCALP_SL_PCT, "rr": SCALP_RR, "time_stop_min": SCALP_TIME_STOP_MIN,
            "note": ("Premium P&L modeled (Δ×spot move); WALL_REJECT excluded — no "
                     "historical OI. Same-bar SL+TP counted as SL. No bias filter in "
                     "backtest (bias runs didn't exist historically)."),
        },
        "trades": trades,
        "by_rule": by_rule,
        "summary": {
            "n": len(trades), "n_taken": len(taken),
            "tp": len(wins), "sl": len(losses),
            "time_exits": len(taken) - len(wins) - len(losses),
            "win_rate": round(len(wins) / len(taken), 3) if taken else None,
            "profit_rate": round(profitable / len(taken), 3) if taken else None,
            "profitable": profitable,
            "total_r": total_r,
            "expectancy_r": round(total_r / len(taken), 3) if taken else None,
            "capital_start": capital, "capital_end": equity,
            "net_pnl": round(equity - capital, 2),
            "return_pct": round((equity - capital) / capital * 100, 2) if capital else None,
            "max_drawdown": round(max_dd, 2),
            "risk_pct": risk_pct, "lot_size": lot,
            "skipped_unaffordable": skipped,
            "total_costs": round(sum(t.get("cost", 0) for t in taken), 2),
            "cost_model": (f"Zerodha exact: ₹{brokerage}/order + STT 0.15% sell + "
                           f"txn 0.03553% + stamp + GST + {slip_pct}%/side slippage"),
        },
    }
