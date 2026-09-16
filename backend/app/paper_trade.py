"""Live paper-trading desk for Scalp Mode — the backtest, run forward.

Every REAL scalp signal auto-opens a paper trade in one portfolio account
(base capital = the trading_capital setting). A monitor loop resolves each
trade the way a disciplined human would: SL / TP on the touch, time stop at
20 minutes — using the REAL Kite bid at that moment when a broker session is
live, else the modeled spot-replay price (exit_source records which).

Charges are the exact Zerodha model. When both entry (ask) and exit (bid) are
real quotes the spread is already paid in the fills, so no slippage is added;
modeled exits carry the standard 0.25%/side assumption.

Never places orders. This is the 30-day evidence base for the go-live gate.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("agentalgo.paper_trade")

MAX_CONCURRENT = 2          # mirror the COMBINED backtest portfolio
POLL_SECONDS = 10           # monitor cadence during market hours
# Portfolio B (shadow, 10-Sep, user's idea): before entering, look at the
# strike's OWN chart — if the premium already ran >15% off its low in the
# last 15 min, the signal is late; B skips it (recorded as status=skipped).
# A takes every signal regardless and stays the go-live evidence base.
# PRE-REGISTERED: threshold/window locked for the month — tuned only at
# month-end from the runup_pct logged on every trade.
B_RUNUP_MAX_PCT = 15.0
B_RUNUP_WINDOW_MIN = 15
# 10-Sep outage lesson: if quotes only come back AFTER the 20-min window
# (network/DNS down), the first stale bid must not be stamped as the fill —
# a trade this late is settled by spot replay instead (grace covers normal
# poll jitter). The 11:42 trade got +4.11R from a 20-min-late quote; the
# rule-faithful result was a −0.76R time stop.
LATE_SETTLE_GRACE_MIN = 2
#   (30 → 10 on day 1: a PE waterfall crossed the SL between two 30s polls and
#   filled −1.19R instead of ~−1R. 10s watches like an attentive human; a real
#   resting SL-M order would still be a touch faster.)


def _base_capital(cfg: dict) -> float:
    """Paper account base: its own setting, falling back to trading_capital."""
    return float(cfg.get("paper_capital") or cfg.get("trading_capital") or 100_000.0)


def _paper_risk_pct(cfg: dict) -> float:
    """Risk %/trade for the paper account (own setting; paper money may run
    hotter than the live 5% clamp — capped at 10)."""
    from .scalp import DEFAULT_SCALP_RISK_FRACTION

    r = cfg.get("paper_risk_pct")
    if r is None:
        r = cfg.get("scalp_risk_pct")
    if r is None:
        r = float(cfg.get("risk_per_trade_pct") or 1.0) * DEFAULT_SCALP_RISK_FRACTION
    return min(max(float(r), 0.1), 10.0)


def _equity(db, user_id: str, base: float, account: str = "A") -> tuple[float, float]:
    """(equity from closed trades, capital reserved in open positions)."""
    from .models import ScalpPaperTrade as T

    closed_pnl = sum(t.pnl or 0.0 for t in db.query(T)
                     .filter(T.user_id == user_id, T.account == account,
                             T.status == "closed").all())
    reserved = sum(t.capital_used for t in db.query(T)
                   .filter(T.user_id == user_id, T.account == account,
                           T.status == "open").all())
    return round(base + closed_pnl, 2), round(reserved, 2)


def _signal_runup(sig: dict, expiry: str | None) -> float | None:
    """Premium run-up on the strike's own chart at signal time (None = unknown)."""
    from .kite_data import kite_option_runup

    strike = sig.get("strike")
    if not (strike and expiry):
        return None
    try:
        return kite_option_runup(sig["symbol"], expiry, int(strike),
                                 sig["direction"], minutes=B_RUNUP_WINDOW_MIN)
    except Exception:  # noqa: BLE001
        return None


def _open_account_trade(db, user_id: str, sig: dict, signal_id: str | None,
                        expiry: str | None, cfg: dict, account: str,
                        runup: float | None) -> str | None:
    """One account's entry decision for a signal (A: always; B: run-up gate)."""
    from .fno import DEFAULT_LOT_SIZES, size_position
    from .models import ScalpPaperTrade as T

    ep, sl, tp = float(sig["ep"]), float(sig["sl"]), float(sig["tp"])
    lot = ((sig.get("sizing") or {}).get("lot_size")
           or DEFAULT_LOT_SIZES.get(sig["symbol"]))
    if not (ep and sl and lot):
        return None
    common = dict(user_id=user_id, signal_id=signal_id, account=account,
                  day=datetime.now(IST).date().isoformat(), runup_pct=runup,
                  symbol=sig["symbol"], rule=sig["rule"], direction=sig["direction"],
                  instrument=sig["instrument"], expiry=expiry,
                  spot_entry=sig.get("spot"), delta=sig.get("delta"),
                  entry_p=ep, sl=sl, tp=tp, lot_size=int(lot))
    if account == "B" and runup is not None and runup > B_RUNUP_MAX_PCT:
        # too extended — B stands aside, and records WHY for the month-end audit
        row = T(**common, lots=0, capital_used=0.0, status="skipped",
                outcome="EXT", exit_source=None)
        db.add(row)
        db.commit()
        log.info("Paper B SKIP (runup %.1f%% > %s%%): %s",
                 runup, B_RUNUP_MAX_PCT, sig["instrument"])
        return None
    open_n = (db.query(T).filter(T.user_id == user_id, T.account == account,
                                 T.status == "open").count())
    if open_n >= MAX_CONCURRENT:
        log.info("Paper %s skipped (concurrency %s): %s", account, open_n,
                 sig["instrument"])
        return None
    equity, reserved = _equity(db, user_id, _base_capital(cfg), account)
    avail = equity - reserved
    sizing = size_position(ep, sl, int(lot), avail, _paper_risk_pct(cfg))
    lots = sizing.get("lots") or 0
    if lots < 1:
        log.info("Paper %s skipped (unaffordable at avail ₹%.0f): %s",
                 account, avail, sig["instrument"])
        return None
    row = T(**common, lots=int(lots), capital_used=round(ep * lot * lots, 2))
    db.add(row)
    db.commit()
    log.info("Paper %s OPEN %s ×%s lots @ ₹%s (avail ₹%.0f, runup %s)",
             account, sig["instrument"], lots, ep, avail, runup)
    return row.id


def open_paper_trade(user_id: str, sig: dict, signal_id: str | None,
                     expiry: str | None, cfg: dict) -> str | None:
    """Open the paper trades for a just-emitted REAL signal: portfolio A
    (every signal — unchanged baseline) and shadow portfolio B (premium
    run-up gate). Returns A's trade id (None when A skipped)."""
    from .db import SessionLocal

    runup = _signal_runup(sig, expiry)  # one Kite lookup, logged on both
    with SessionLocal() as db:
        rid = _open_account_trade(db, user_id, sig, signal_id, expiry, cfg, "A", runup)
        try:
            _open_account_trade(db, user_id, sig, signal_id, expiry, cfg, "B", runup)
        except Exception:  # noqa: BLE001 — B must never break A
            log.exception("Paper B open failed")
        return rid


def _close(db, t, exit_p: float, outcome: str, source: str,
           exit_at: datetime | None = None) -> None:
    from .backtest import trade_cost

    exit_p = max(round(exit_p, 2), 0.05)
    risk = max(t.entry_p - t.sl, 0.01)
    slip = 0.0 if source == "kite" else 0.25  # real bid/ask already pays the spread
    t.exit_p = exit_p
    t.exit_at = (exit_at.astimezone(timezone.utc).replace(tzinfo=None) if exit_at
                 else datetime.now(timezone.utc).replace(tzinfo=None))
    t.exit_source = source
    t.outcome = outcome
    t.r_multiple = round((exit_p - t.entry_p) / risk, 2)
    t.charges = trade_cost(t.entry_p, exit_p, t.lot_size, t.lots, slip_pct=slip)
    t.pnl = round((exit_p - t.entry_p) * t.lot_size * t.lots - t.charges, 2)


def _entry_ist(t) -> datetime:
    created = t.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.astimezone(IST)


def _modeled_exit(t, now_ist: datetime) -> tuple[float, str] | None:
    """Replay the trade on actual spot candles (paper-score math) — recovery
    path when Kite is down or the app missed the exit moment."""
    from .backtest import _simulate_trade
    from .paper_score import _bar_index_at
    from .scalp import fetch_session_bars

    try:
        bars = fetch_session_bars(t.symbol)
    except Exception:  # noqa: BLE001
        return None
    if not bars:
        return None
    i = _bar_index_at(bars, _entry_ist(t))
    if i is None or i >= len(bars) - 1:
        return None
    res = _simulate_trade(bars, i, t.direction, t.entry_p,
                          delta=abs(t.delta) if t.delta else None)
    if res["outcome"] == "TIME" and res["bars_held"] < 20:
        return None  # window not complete yet — keep it open
    risk = max(t.entry_p - t.sl, 0.01)
    exit_p = round(t.entry_p + res["r"] * risk, 2)
    # the RULE's exit moment (16-Sep lesson: a delayed settle must not stamp
    # the sweep's wall-clock as the exit time — it reads as a longer hold)
    exit_at = _entry_ist(t) + timedelta(minutes=res["bars_held"])
    return exit_p, res["outcome"], exit_at


def _chain_equity_after(db, rows) -> None:
    """Fill equity_after on freshly-closed rows (per user+account chain)."""
    import json as _json

    from .models import ScalpPaperTrade as T, Setting

    db.flush()
    base_by_user: dict[str, float] = {}
    for t in rows:
        if t.status != "closed" or t.equity_after is not None:
            continue
        if t.user_id not in base_by_user:
            srow = db.get(Setting, t.user_id)
            cfg = _json.loads(srow.config_json or "{}") if srow else {}
            base_by_user[t.user_id] = _base_capital(cfg)
        closed_pnl = sum(x.pnl or 0.0 for x in db.query(T)
                         .filter(T.user_id == t.user_id, T.account == t.account,
                                 T.status == "closed").all())
        t.equity_after = round(base_by_user[t.user_id] + closed_pnl, 2)


def manual_exit(user_id: str, trade_id: str) -> dict:
    """Close an open paper trade NOW at the live Kite bid, outcome MANUAL.

    The human's exit button — mirrors real trading, where the operator can
    always flatten. Requires a live quote (no guessing a fill when the feed
    is down). Raises ValueError (not found / not open) or RuntimeError (no
    live quote).
    """
    from .db import SessionLocal
    from .kite_data import kite_option_quote
    from .models import ScalpPaperTrade as T

    with SessionLocal() as db:
        t = db.get(T, trade_id)
        if t is None or t.user_id != user_id:
            raise ValueError("Paper trade not found")
        if t.status != "open":
            raise ValueError("Trade is not open")
        try:
            strike = int(t.instrument.split()[1])
            quote = kite_option_quote(t.symbol, t.expiry, strike, t.direction,
                                      t.user_id)
        except Exception:  # noqa: BLE001
            quote = None
        bid = (quote.get("bid") or quote.get("last_price")) if quote else None
        if not bid:
            raise RuntimeError("No live quote — manual exit needs a connected "
                               "Kite session during market hours")
        _close(db, t, float(bid), "MANUAL", "kite")
        t.status = "closed"
        _chain_equity_after(db, [t])
        db.commit()
        log.info("Paper %s MANUAL exit %s @ ₹%s (%.2fR)", t.account,
                 t.instrument, t.exit_p, t.r_multiple)
        return {"id": t.id, "exit_p": t.exit_p, "pnl": t.pnl,
                "r": t.r_multiple, "outcome": t.outcome}


async def paper_trade_sweep() -> int:
    """One monitor pass: resolve open paper trades. Returns closes made."""
    from .db import SessionLocal
    from .fno import is_market_hours_ist
    from .kite_data import kite_option_quote
    from .models import ScalpPaperTrade as T
    from .scalp import SCALP_TIME_STOP_MIN

    now_ist = datetime.now(IST)
    closed = 0
    with SessionLocal() as db:
        open_rows = db.query(T).filter(T.status == "open").all()
        for t in open_rows:
            age_min = (now_ist - _entry_ist(t)).total_seconds() / 60
            quote = None
            if is_market_hours_ist():
                try:
                    strike = int(t.instrument.split()[1])
                    quote = await asyncio.to_thread(
                        kite_option_quote, t.symbol, t.expiry, strike,
                        t.direction, t.user_id)
                except Exception:  # noqa: BLE001
                    quote = None
            bid = quote.get("bid") or quote.get("last_price") if quote else None
            if bid and age_min >= SCALP_TIME_STOP_MIN + LATE_SETTLE_GRACE_MIN:
                # quote arrived well past the window (feed was down): the live
                # bid is NOT the fill the rule would have gotten — settle by
                # replay; only if replay is impossible fall back to the bid
                modeled = await asyncio.to_thread(_modeled_exit, t, now_ist)
                if modeled is not None:
                    _close(db, t, modeled[0], modeled[1], "modeled", modeled[2])
                else:
                    _close(db, t, bid, "TIME", "kite")
            elif bid:
                if bid <= t.sl:
                    _close(db, t, bid, "SL", "kite")
                elif bid >= t.tp:
                    _close(db, t, bid, "TP", "kite")
                elif age_min >= SCALP_TIME_STOP_MIN:
                    _close(db, t, bid, "TIME", "kite")
                else:
                    continue
            else:
                # Kite down / after hours: once the window is over (or the
                # session ended), settle from actual spot candles
                if age_min < SCALP_TIME_STOP_MIN and is_market_hours_ist():
                    continue
                modeled = await asyncio.to_thread(_modeled_exit, t, now_ist)
                if modeled is None:
                    continue
                _close(db, t, modeled[0], modeled[1], "modeled", modeled[2])
            t.status = "closed"
            closed += 1
        if closed:
            # equity_after: chain in close order for a readable running column.
            # (helper flushes first — autoflush=False sessions otherwise read
            # PRE-close rows; live bug, day 1)
            _chain_equity_after(db, open_rows)
        db.commit()
    if closed:
        log.info("Paper trade sweep closed %s position(s)", closed)
    return closed


async def paper_trade_loop() -> None:
    while True:
        try:
            await paper_trade_sweep()
        except Exception:  # pragma: no cover
            log.exception("Paper trade sweep failed")
        await asyncio.sleep(POLL_SECONDS)


def paper_trades_summary(user_id: str, days: int = 35, account: str = "A") -> dict:
    """Backtest-style payload: rows (newest first) + portfolio summary."""
    from .db import SessionLocal
    from .models import ScalpPaperTrade as T, Setting

    cutoff = (datetime.now(IST).date() - timedelta(days=days)).isoformat()
    with SessionLocal() as db:
        srow = db.get(Setting, user_id)
        cfg = json.loads(srow.config_json or "{}") if srow else {}
        base = _base_capital(cfg)
        rows = (db.query(T).filter(T.user_id == user_id, T.account == account,
                                   T.day >= cutoff)
                .order_by(T.created_at.desc()).limit(500).all())
        equity, reserved = _equity(db, user_id, base, account)
        out_rows = []
        for t in rows:
            out_rows.append({
                "id": t.id, "day": t.day,
                "entry_t": _entry_ist(t).strftime("%H:%M"),
                "exit_t": (t.exit_at.replace(tzinfo=timezone.utc).astimezone(IST)
                           .strftime("%H:%M") if t.exit_at else None),
                "rule": t.rule, "instrument": t.instrument, "expiry": t.expiry,
                "spot": t.spot_entry, "direction": t.direction,
                "entry_p": t.entry_p, "exit_p": t.exit_p, "sl": t.sl, "tp": t.tp,
                "lots": t.lots, "lot_size": t.lot_size,
                "capital_used": t.capital_used, "charges": t.charges,
                "pnl": t.pnl, "outcome": t.outcome, "r": t.r_multiple,
                "status": t.status, "exit_source": t.exit_source,
                "equity_after": t.equity_after, "runup_pct": t.runup_pct,
            })
    closed = [r for r in out_rows if r["status"] == "closed"]
    wins = sum(1 for r in closed if (r["pnl"] or 0) > 0)
    note_a = ("One paper account (base = trading_capital). Exits on real "
              "Kite bid when connected (no slippage added — the spread is "
              "in the fills); 'modeled' exits use spot-replay + 0.25%/side.")
    note_b = (f"Shadow portfolio B: identical signals, but skips entries whose "
              f"option premium already ran >{B_RUNUP_MAX_PCT:.0f}% off its "
              f"{B_RUNUP_WINDOW_MIN}-min low ('EXT' rows). Same capital, risk "
              f"and exits as A — the month-end A/B comparison decides whether "
              f"the run-up gate goes live.")
    return {
        "rows": out_rows,
        "summary": {
            "account": account,
            "base_capital": base, "risk_pct": _paper_risk_pct(cfg),
            "equity": equity, "reserved": reserved,
            "open": sum(1 for r in out_rows if r["status"] == "open"),
            "skipped": sum(1 for r in out_rows if r["status"] == "skipped"),
            "n_closed": len(closed), "wins": wins,
            "win_pct": round(wins / len(closed) * 100, 1) if closed else None,
            "net_pnl": round(sum(r["pnl"] or 0 for r in closed), 2),
            "total_charges": round(sum(r["charges"] or 0 for r in closed), 2),
            "sum_r": round(sum(r["r"] or 0 for r in closed), 2),
            "max_concurrent": MAX_CONCURRENT,
            "note": note_b if account == "B" else note_a,
        },
    }
