"""P2 — watchlists, schedules, alerts."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Alert, Run, Schedule, User, Watchlist
from ..security import get_current_user
from ..tickers import normalize_ticker

router = APIRouter(prefix="/api", tags=["automations"])


# ---------- schemas ----------

class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tickers: list[str] = Field(min_length=1, max_length=50)


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tickers: list[str] = Field(min_length=1, max_length=25)
    cadence: str = Field(pattern="^(daily|weekdays|weekly)$")
    weekday: int = Field(default=0, ge=0, le=6)
    hour: int = Field(default=7, ge=0, le=23)
    enabled: bool = True
    config: dict = Field(default_factory=dict)  # depth/mode/provider overrides


def _norm_tickers(raw: list[str]) -> list[str]:
    out: list[str] = []
    for t in raw:
        try:
            n = normalize_ticker(t)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if n not in out:
            out.append(n)
    return out


# ---------- watchlists (P2.1) ----------

def _watchlist_out(db: Session, w: Watchlist) -> dict:
    tickers = json.loads(w.tickers_json)
    latest: dict[str, dict] = {}
    if tickers:
        runs = (
            db.query(Run)
            .filter(Run.user_id == w.user_id, Run.ticker.in_(tickers), Run.status == "done")
            .order_by(Run.finished_at.desc())
            .all()
        )
        for r in runs:
            if r.ticker not in latest:
                latest[r.ticker] = {"run_id": r.id, "rating": r.rating, "trade_date": r.trade_date}
    return {
        "id": w.id, "name": w.name, "tickers": tickers,
        "latest": latest, "created_at": w.created_at,
    }


@router.get("/watchlists")
def list_watchlists(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.query(Watchlist).filter(Watchlist.user_id == user.id).order_by(Watchlist.created_at).all()
    return [_watchlist_out(db, w) for w in rows]


@router.post("/watchlists", status_code=201)
def save_watchlist(
    body: WatchlistIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    tickers = _norm_tickers(body.tickers)
    row = (
        db.query(Watchlist)
        .filter(Watchlist.user_id == user.id, Watchlist.name == body.name)
        .one_or_none()
    )
    if row is None:
        row = Watchlist(user_id=user.id, name=body.name)
        db.add(row)
    row.tickers_json = json.dumps(tickers)
    db.commit()
    return _watchlist_out(db, row)


@router.delete("/watchlists/{watchlist_id}", status_code=204)
def delete_watchlist(
    watchlist_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(Watchlist, watchlist_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    db.delete(row)
    db.commit()


@router.post("/watchlists/{watchlist_id}/run")
async def run_watchlist(
    watchlist_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    config: dict | None = None,
):
    """Run every ticker in the list; over-cap runs queue and start automatically."""
    from datetime import date

    from ..services import RunValidationError, create_run_for_user

    row = db.get(Watchlist, watchlist_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    base = {"trade_date": date.today().isoformat(), "mode": "demo", "research_depth": 1,
            **(config or {})}
    created, errors = [], []
    for ticker in json.loads(row.tickers_json):
        try:
            run = await create_run_for_user(db, user.id, {**base, "ticker": ticker})
            created.append({"run_id": run.id, "ticker": ticker, "status": run.status})
        except RunValidationError as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
    return {"created": created, "errors": errors}


# ---------- schedules (P2.2) ----------

def _schedule_out(s: Schedule) -> dict:
    return {
        "id": s.id, "name": s.name, "tickers": json.loads(s.tickers_json),
        "cadence": s.cadence, "weekday": s.weekday, "hour": s.hour,
        "enabled": s.enabled, "last_fired_date": s.last_fired_date,
        "config": json.loads(s.config_json or "{}"), "created_at": s.created_at,
    }


@router.get("/schedules")
def list_schedules(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.query(Schedule).filter(Schedule.user_id == user.id).order_by(Schedule.created_at).all()
    return [_schedule_out(s) for s in rows]


@router.post("/schedules", status_code=201)
def create_schedule(
    body: ScheduleIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = Schedule(
        user_id=user.id, name=body.name, tickers_json=json.dumps(_norm_tickers(body.tickers)),
        cadence=body.cadence, weekday=body.weekday, hour=body.hour,
        enabled=body.enabled, config_json=json.dumps(body.config),
    )
    db.add(row)
    db.commit()
    return _schedule_out(row)


@router.post("/schedules/{schedule_id}/toggle")
def toggle_schedule(
    schedule_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(Schedule, schedule_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Schedule not found")
    row.enabled = not row.enabled
    db.commit()
    return _schedule_out(row)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(Schedule, schedule_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Schedule not found")
    db.delete(row)
    db.commit()


# ---------- alerts (P2.3) ----------

@router.get("/alerts")
def list_alerts(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    unread_only: bool = False,
):
    q = db.query(Alert).filter(Alert.user_id == user.id)
    if unread_only:
        q = q.filter(Alert.read.is_(False))
    rows = q.order_by(Alert.created_at.desc()).limit(200).all()
    return [
        {"id": a.id, "run_id": a.run_id, "ticker": a.ticker, "type": a.type,
         "message": a.message, "read": a.read, "created_at": a.created_at}
        for a in rows
    ]


@router.post("/alerts/read")
def mark_alerts_read(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    ids: list[str] | None = None,
):
    q = db.query(Alert).filter(Alert.user_id == user.id, Alert.read.is_(False))
    if ids:
        q = q.filter(Alert.id.in_(ids))
    updated = q.update({"read": True}, synchronize_session=False)
    db.commit()
    return {"marked_read": updated}
