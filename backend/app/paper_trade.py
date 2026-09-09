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
POLL_SECONDS = 30           # monitor cadence during market hours


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


def _equity(db, user_id: str, base: float) -> tuple[float, float]:
    """(equity from closed trades, capital reserved in open positions)."""
    from .models import ScalpPaperTrade as T

    closed_pnl = sum(t.pnl or 0.0 for t in db.query(T)
                     .filter(T.user_id == user_id, T.status == "closed").all())
    reserved = sum(t.capital_used for t in db.query(T)
                   .filter(T.user_id == user_id, T.status == "open").all())
    return round(base + closed_pnl, 2), round(reserved, 2)


def open_paper_trade(user_id: str, sig: dict, signal_id: str | None,
                     expiry: str | None, cfg: dict) -> str | None:
    """Open a portfolio paper trade for a just-emitted REAL signal.

    Returns the trade id, or None when skipped (concurrency / unaffordable).
    """
    from .db import SessionLocal
    from .fno import DEFAULT_LOT_SIZES, size_position
    from .models import ScalpPaperTrade as T

    ep, sl, tp = float(sig["ep"]), float(sig["sl"]), float(sig["tp"])
    lot = ((sig.get("sizing") or {}).get("lot_size")
           or DEFAULT_LOT_SIZES.get(sig["symbol"]))
    if not (ep and sl and lot):
        return None
    base = _base_capital(cfg)
    risk_pct = _paper_risk_pct(cfg)
    with SessionLocal() as db:
        open_n = (db.query(T).filter(T.user_id == user_id, T.status == "open").count())
        if open_n >= MAX_CONCURRENT:
            log.info("Paper trade skipped (concurrency %s): %s", open_n, sig["instrument"])
            return None
        equity, reserved = _equity(db, user_id, base)
        avail = equity - reserved
        sizing = size_position(ep, sl, int(lot), avail, risk_pct)
        lots = sizing.get("lots") or 0
        if lots < 1:
            log.info("Paper trade skipped (unaffordable at avail ₹%.0f): %s",
                     avail, sig["instrument"])
            return None
        row = T(user_id=user_id, signal_id=signal_id,
                day=datetime.now(IST).date().isoformat(),
                symbol=sig["symbol"], rule=sig["rule"], direction=sig["direction"],
                instrument=sig["instrument"], expiry=expiry,
                spot_entry=sig.get("spot"), delta=sig.get("delta"),
                entry_p=ep, sl=sl, tp=tp, lot_size=int(lot), lots=int(lots),
                capital_used=round(ep * lot * lots, 2))
        db.add(row)
        db.commit()
        log.info("Paper trade OPEN %s ×%s lots @ ₹%s (avail ₹%.0f)",
                 sig["instrument"], lots, ep, avail)
        return row.id


def _close(db, t, exit_p: float, outcome: str, source: str) -> None:
    from .backtest import trade_cost

    exit_p = max(round(exit_p, 2), 0.05)
    risk = max(t.entry_p - t.sl, 0.01)
    slip = 0.0 if source == "kite" else 0.25  # real bid/ask already pays the spread
    t.exit_p = exit_p
    t.exit_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
    return exit_p, res["outcome"]


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
            if bid:
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
                _close(db, t, modeled[0], modeled[1], "modeled")
            t.status = "closed"
            closed += 1
        if closed:
            # equity_after: chain in close order for a readable running column.
            # SessionLocal runs autoflush=False — flush the closes first or the
            # closed-pnl query below reads PRE-close rows (live bug, day 1: the
            # first trade stored equity_after = base, ignoring its own +₹2.5K).
            db.flush()
            base_by_user: dict[str, float] = {}
            from .models import Setting

            for t in open_rows:
                if t.status != "closed" or t.equity_after is not None:
                    continue
                if t.user_id not in base_by_user:
                    srow = db.get(Setting, t.user_id)
                    cfg = json.loads(srow.config_json or "{}") if srow else {}
                    base_by_user[t.user_id] = _base_capital(cfg)
                closed_pnl = sum(x.pnl or 0.0 for x in db.query(T)
                                 .filter(T.user_id == t.user_id, T.status == "closed").all())
                t.equity_after = round(base_by_user[t.user_id] + closed_pnl, 2)
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


def paper_trades_summary(user_id: str, days: int = 35) -> dict:
    """Backtest-style payload: rows (newest first) + portfolio summary."""
    from .db import SessionLocal
    from .models import ScalpPaperTrade as T, Setting

    cutoff = (datetime.now(IST).date() - timedelta(days=days)).isoformat()
    with SessionLocal() as db:
        srow = db.get(Setting, user_id)
        cfg = json.loads(srow.config_json or "{}") if srow else {}
        base = _base_capital(cfg)
        rows = (db.query(T).filter(T.user_id == user_id, T.day >= cutoff)
                .order_by(T.created_at.desc()).limit(500).all())
        equity, reserved = _equity(db, user_id, base)
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
                "equity_after": t.equity_after,
            })
    closed = [r for r in out_rows if r["status"] == "closed"]
    wins = sum(1 for r in closed if (r["pnl"] or 0) > 0)
    return {
        "rows": out_rows,
        "summary": {
            "base_capital": base, "risk_pct": _paper_risk_pct(cfg),
            "equity": equity, "reserved": reserved,
            "open": sum(1 for r in out_rows if r["status"] == "open"),
            "n_closed": len(closed), "wins": wins,
            "win_pct": round(wins / len(closed) * 100, 1) if closed else None,
            "net_pnl": round(sum(r["pnl"] or 0 for r in closed), 2),
            "total_charges": round(sum(r["charges"] or 0 for r in closed), 2),
            "sum_r": round(sum(r["r"] or 0 for r in closed), 2),
            "max_concurrent": MAX_CONCURRENT,
            "note": ("One paper account (base = trading_capital). Exits on real "
                     "Kite bid when connected (no slippage added — the spread is "
                     "in the fills); 'modeled' exits use spot-replay + 0.25%/side."),
        },
    }
