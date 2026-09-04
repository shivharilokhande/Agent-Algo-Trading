"""P3-A2 — paper-trading simulator: simulated fills at real market prices.

Long-only ruleset (v1):
  Buy        → open a full-notional position (if none open for the ticker)
  Overweight → open a half-notional position (if none open)
  Underweight/Sell → close any open position
  Hold / REVIEW    → no action
No real broker orders are ever placed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from .db import SessionLocal
from .models import PaperPosition, Run, Setting

log = logging.getLogger("agentalgo.paper")

DEFAULT_NOTIONAL = 10_000.0  # USD-equivalent per full unit


def _settings(db, user_id: str) -> dict:
    row = db.get(Setting, user_id)
    return json.loads(row.config_json) if row else {}


def _last_price_sync(ticker: str) -> float:
    import yfinance as yf

    hist = yf.Ticker(ticker).history(period="5d", auto_adjust=True)["Close"].dropna()
    if hist.empty:
        raise ValueError(f"No price data for {ticker}")
    return float(hist.iloc[-1])


async def last_price(ticker: str) -> float:
    return await asyncio.to_thread(_last_price_sync, ticker)


async def execute_decision(run_id: str) -> PaperPosition | None:
    """Apply a completed run's rating to the user's paper book (if enabled)."""
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None or run.status != "done" or not run.rating:
            return None
        settings = _settings(db, run.user_id)
        if not settings.get("paper_trading"):
            return None
        notional = float(settings.get("paper_notional") or DEFAULT_NOTIONAL)
        open_pos = (
            db.query(PaperPosition)
            .filter(PaperPosition.user_id == run.user_id,
                    PaperPosition.ticker == run.ticker,
                    PaperPosition.status == "open")
            .first()
        )
        rating = run.rating

    if rating in ("Buy", "Overweight") and open_pos is None:
        size = notional if rating == "Buy" else notional / 2
        price = await last_price(run.ticker)
        with SessionLocal() as db:
            pos = PaperPosition(
                user_id=run.user_id, run_id=run.id, ticker=run.ticker, rating=rating,
                qty=round(size / price, 6), entry_price=round(price, 4), notional=size,
            )
            db.add(pos)
            db.commit()
            log.info("Paper OPEN %s %s @ %.2f (%s)", run.ticker, rating, price, run.user_id)
            return pos

    if rating in ("Underweight", "Sell") and open_pos is not None:
        price = await last_price(run.ticker)
        with SessionLocal() as db:
            pos = db.get(PaperPosition, open_pos.id)
            if pos is None or pos.status != "open":
                return None
            pos.status = "closed"
            pos.exit_price = round(price, 4)
            pos.realized_pnl = round((price - pos.entry_price) * pos.qty, 2)
            pos.close_reason = f"{rating} rating from run {run.id[:8]}"
            pos.closed_at = datetime.now(timezone.utc)
            db.commit()
            log.info("Paper CLOSE %s @ %.2f pnl %.2f", pos.ticker, price, pos.realized_pnl)
            return pos
    return None


async def portfolio_snapshot(user_id: str) -> dict:
    """Open + closed positions with live unrealized P&L."""
    with SessionLocal() as db:
        positions = (
            db.query(PaperPosition)
            .filter(PaperPosition.user_id == user_id)
            .order_by(PaperPosition.opened_at.desc())
            .limit(200)
            .all()
        )
        rows = [
            {
                "id": p.id, "run_id": p.run_id, "ticker": p.ticker, "rating": p.rating,
                "qty": p.qty, "entry_price": p.entry_price, "notional": p.notional,
                "status": p.status, "exit_price": p.exit_price, "realized_pnl": p.realized_pnl,
                "close_reason": p.close_reason,
                "opened_at": p.opened_at.isoformat(),
                "closed_at": p.closed_at.isoformat() if p.closed_at else None,
            }
            for p in positions
        ]

    open_rows = [r for r in rows if r["status"] == "open"]
    prices: dict[str, float | None] = {}
    for ticker in {r["ticker"] for r in open_rows}:
        try:
            prices[ticker] = await last_price(ticker)
        except Exception:  # noqa: BLE001 — price feed hiccup must not 500 the page
            prices[ticker] = None
    unrealized_total = 0.0
    for r in open_rows:
        price = prices.get(r["ticker"])
        r["last_price"] = round(price, 4) if price else None
        r["unrealized_pnl"] = round((price - r["entry_price"]) * r["qty"], 2) if price else None
        if r["unrealized_pnl"]:
            unrealized_total += r["unrealized_pnl"]
    realized_total = sum(r["realized_pnl"] or 0 for r in rows if r["status"] == "closed")
    return {
        "positions": rows,
        "open_count": len(open_rows),
        "unrealized_pnl": round(unrealized_total, 2),
        "realized_pnl": round(realized_total, 2),
    }
