"""P3 — paper portfolio + human-in-the-loop run controls."""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import PaperPosition, Run, User
from ..paper import last_price, portfolio_snapshot
from ..runner import manager
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["portfolio"])


# ---------- paper portfolio (A2) ----------

@router.get("/portfolio")
async def get_portfolio(user: Annotated[User, Depends(get_current_user)]):
    return await portfolio_snapshot(user.id)


@router.post("/portfolio/{position_id}/close")
async def close_position(
    position_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    pos = db.get(PaperPosition, position_id)
    if pos is None or pos.user_id != user.id:
        raise HTTPException(status_code=404, detail="Position not found")
    if pos.status != "open":
        raise HTTPException(status_code=409, detail="Position already closed")
    try:
        price = await last_price(pos.ticker)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Price unavailable: {exc}") from exc
    pos.status = "closed"
    pos.exit_price = round(price, 4)
    pos.realized_pnl = round((price - pos.entry_price) * pos.qty, 2)
    pos.close_reason = "closed manually"
    pos.closed_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": pos.id, "exit_price": pos.exit_price, "realized_pnl": pos.realized_pnl}


# ---------- human-in-the-loop (A5) ----------

class ProceedIn(BaseModel):
    user_view: str = Field(default="", max_length=4000)


class AskIn(BaseModel):
    agent: str = Field(min_length=1, max_length=48)
    question: str = Field(min_length=1, max_length=1000)


def _owned_paused_run(run_id: str, user: User, db: Session) -> Run:
    run = db.get(Run, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    if not manager.is_paused(run_id):
        raise HTTPException(status_code=409, detail="Run is not paused at the decision breakpoint")
    return run


@router.post("/runs/{run_id}/proceed")
async def proceed_run(
    run_id: str,
    body: ProceedIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _owned_paused_run(run_id, user, db)
    ok = await manager.proceed(run_id, body.user_view)
    if not ok:
        raise HTTPException(status_code=409, detail="Run is not paused")
    return {"status": "resuming", "user_view_recorded": bool(body.user_view.strip())}


_ANSWER_TEMPLATES = {
    "Bull Researcher": [
        "What breaks my thesis is {risk}: if that materialises before the catalyst lands, the upside case degrades to neutral at best.",
        "The strongest counter to the bear is positioning — {positioning}. I'd still respect the invalidation level.",
    ],
    "Bear Researcher": [
        "My conviction rests on {risk}; the bull case requires flawless execution the market is already paying for.",
        "If price reclaims the breakout level on volume, I would concede the short-term momentum argument.",
    ],
    "Trader": [
        "Sizing assumes the stop holds; a gap through it costs roughly 1.5x the planned risk, so I'd stage entries.",
    ],
    "Conservative Analyst": [
        "Correlation to your existing exposure is my worry — a single macro print moves the whole book, so cap the unit size.",
    ],
    "Aggressive Analyst": [
        "Waiting for confirmation costs more than being early here; the asymmetric payoff justifies the full unit.",
    ],
    "Neutral Analyst": [
        "Both sides overstate. Expected value at the stated stop/target is positive but thin — the user's own horizon should decide.",
    ],
}
_RISKS = ["a macro repricing of rate expectations", "earnings-guidance disappointment",
          "sector rotation away from momentum names", "liquidity thinning into the event"]
_POSITIONING = ["retail crowding is high but institutional exposure is not",
                "short interest provides squeeze fuel", "options skew shows hedged, not euphoric, positioning"]


@router.post("/runs/{run_id}/ask")
async def ask_agent(
    run_id: str,
    body: AskIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """A5.2 — interrogate an agent while paused (demo mode: deterministic persona answers)."""
    run = _owned_paused_run(run_id, user, db)
    templates = _ANSWER_TEMPLATES.get(body.agent)
    if templates is None:
        raise HTTPException(status_code=422, detail=f"Unknown agent: {body.agent}")
    seed = int(hashlib.sha256(f"{run.ticker}|{body.question}".encode()).hexdigest()[:10], 16)
    rng = random.Random(seed)
    answer = rng.choice(templates).format(risk=rng.choice(_RISKS), positioning=rng.choice(_POSITIONING))
    answer = f"[demo persona] {answer}"
    ok = await manager.ask_paused(run_id, body.agent, body.question, answer)
    if not ok:
        raise HTTPException(status_code=409, detail="Run is not paused")
    return {"agent": body.agent, "answer": answer}
