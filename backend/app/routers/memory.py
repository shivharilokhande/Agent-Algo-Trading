"""F8 — decision-log memory with outcome resolution."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import MemoryEntry, User
from ..schemas import MemoryOut
from ..security import get_current_user

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _to_out(e: MemoryEntry) -> MemoryOut:
    return MemoryOut(
        id=e.id, ticker=e.ticker, trade_date=e.trade_date, rating=e.rating,
        summary=e.summary, status=e.status, raw_return=e.raw_return, alpha=e.alpha,
        benchmark=e.benchmark, reflection=e.reflection, resolved_at=e.resolved_at,
        created_at=e.created_at,
    )


@router.get("", response_model=list[MemoryOut])
def list_memory(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    ticker: str | None = None,
    status: str | None = None,
):
    q = db.query(MemoryEntry).filter(MemoryEntry.user_id == user.id)
    if ticker:
        q = q.filter(MemoryEntry.ticker == ticker.upper())
    if status:
        q = q.filter(MemoryEntry.status == status)
    return [_to_out(e) for e in q.order_by(MemoryEntry.created_at.desc()).limit(500)]


@router.get("/stats")
def memory_stats(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """F8.4 aggregate stats: hit rate by tier, average alpha."""
    entries = (
        db.query(MemoryEntry)
        .filter(MemoryEntry.user_id == user.id, MemoryEntry.status == "resolved")
        .all()
    )
    by_rating: dict[str, dict] = {}
    for e in entries:
        b = by_rating.setdefault(e.rating, {"count": 0, "hits": 0, "alpha_sum": 0.0})
        b["count"] += 1
        if e.alpha is not None:
            b["alpha_sum"] += e.alpha
            bullish = e.rating in ("Buy", "Overweight")
            bearish = e.rating in ("Sell", "Underweight")
            if (bullish and e.alpha > 0) or (bearish and e.alpha < 0) or (e.rating == "Hold" and abs(e.alpha) < 0.02):
                b["hits"] += 1
    return {
        "resolved": len(entries),
        "pending": db.query(MemoryEntry)
        .filter(MemoryEntry.user_id == user.id, MemoryEntry.status == "pending")
        .count(),
        "by_rating": {
            r: {
                "count": b["count"],
                "hit_rate": round(b["hits"] / b["count"], 3) if b["count"] else None,
                "avg_alpha": round(b["alpha_sum"] / b["count"], 4) if b["count"] else None,
            }
            for r, b in by_rating.items()
        },
    }


# C8: minimum days between trade date and resolution (fresh Holds resolve as noise)
MIN_RESOLVE_DAYS = 2


async def _fetch_returns(ticker: str, benchmark: str, start: str) -> tuple[float, float]:
    """Realized returns of ticker and benchmark over the SAME calendar window.

    C8: the two series are aligned on their common trading dates so a 24/7
    crypto ticker isn't compared against a benchmark window that includes
    closed-market days.
    """

    def _both() -> tuple[float, float]:
        import yfinance as yf

        end = (date.today() + timedelta(days=1)).isoformat()
        t = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)["Close"].dropna()
        b = yf.Ticker(benchmark).history(start=start, end=end, auto_adjust=True)["Close"].dropna()
        if len(t) < 2 or len(b) < 2:
            raise ValueError(f"Not enough price history for {ticker}/{benchmark} since {start}")
        t.index = t.index.date
        b.index = b.index.date
        common = t.index.intersection(b.index)
        if len(common) >= 2:
            t, b = t.loc[common], b.loc[common]
        return (
            float(t.iloc[-1] / t.iloc[0] - 1.0),
            float(b.iloc[-1] / b.iloc[0] - 1.0),
        )

    return await asyncio.to_thread(_both)


async def resolve_entry_core(db, entry: MemoryEntry, min_days: int = MIN_RESOLVE_DAYS) -> MemoryEntry:
    """Shared resolution logic for the endpoint and the scheduled job (F8.2)."""
    if entry.status == "resolved":
        return entry
    age_days = (date.today() - date.fromisoformat(entry.trade_date)).days
    if age_days < min_days:
        raise HTTPException(
            status_code=409,
            detail=f"Outcome window too short — resolvable {min_days} days after the trade date",
        )
    raw, bench = await _fetch_returns(entry.ticker, entry.benchmark, entry.trade_date)
    db.refresh(entry)  # C8: guard against a concurrent resolve
    if entry.status == "resolved":
        return entry
    entry.raw_return = round(raw, 6)
    entry.alpha = round(raw - bench, 6)
    entry.reflection = _reflection(entry, raw, entry.alpha)
    entry.status = "resolved"
    entry.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return entry


def _reflection(entry: MemoryEntry, raw: float, alpha: float) -> str:
    """Deterministic reflection (2–4 sentences, engine-log parity in spirit).

    Engine mode also writes an LLM reflection into the per-user engine memory
    file; this platform-level reflection never spends user tokens.
    """
    bullish = entry.rating in ("Buy", "Overweight")
    bearish = entry.rating in ("Sell", "Underweight")
    correct = (bullish and alpha > 0) or (bearish and alpha < 0) or (
        entry.rating == "Hold" and abs(alpha) < 0.02
    )
    verdict = "correct" if correct else "incorrect"
    return (
        f"The {entry.rating} call on {entry.ticker} ({entry.trade_date}) was {verdict}: "
        f"raw return {raw:+.1%}, alpha vs {entry.benchmark} {alpha:+.1%}. "
        f"{'The directional thesis held; maintain the sizing discipline that limited drawdown.' if correct else 'The thesis did not play out; next time weight the opposing debate case more heavily and tighten the invalidation level.'}"
    )


@router.post("/{entry_id}/resolve", response_model=MemoryOut)
async def resolve_entry(
    entry_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """F8.2 — fetch realized return + alpha, write reflection, mark resolved."""
    entry = db.get(MemoryEntry, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    try:
        entry = await resolve_entry_core(db, entry)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Outcome data unavailable: {exc}") from exc
    return _to_out(entry)


@router.delete("/{entry_id}", status_code=204)
def delete_entry(
    entry_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    entry = db.get(MemoryEntry, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    db.delete(entry)
    db.commit()
