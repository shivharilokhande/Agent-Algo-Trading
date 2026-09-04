"""Super-admin SaaS dashboard: platform stats + user management."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ApiKey, MemoryEntry, Preset, Run, Setting, User
from ..security import get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Super-admin access required")
    return user


@router.get("/stats")
def platform_stats(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    runs_by_status = dict(db.query(Run.status, func.count()).group_by(Run.status).all())
    ratings = dict(
        db.query(Run.rating, func.count()).filter(Run.rating.isnot(None)).group_by(Run.rating).all()
    )
    modes = dict(db.query(Run.mode, func.count()).group_by(Run.mode).all())
    tokens_in = tokens_out = llm_calls = 0
    for (stats_json,) in db.query(Run.stats_json).filter(Run.stats_json != "{}"):
        s = json.loads(stats_json or "{}")
        tokens_in += s.get("tokens_in", 0)
        tokens_out += s.get("tokens_out", 0)
        llm_calls += s.get("llm_calls", 0)
    top = (
        db.query(Run.ticker, func.count().label("n"))
        .group_by(Run.ticker).order_by(func.count().desc()).limit(8).all()
    )
    return {
        "users": db.query(func.count(User.id)).scalar(),
        "admins": db.query(func.count(User.id)).filter(User.is_admin).scalar(),
        "runs_total": db.query(func.count(Run.id)).scalar(),
        "runs_by_status": runs_by_status,
        "runs_by_mode": modes,
        "ratings": ratings,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llm_calls": llm_calls,
        "keys_stored": db.query(func.count(ApiKey.id)).scalar(),
        "memory_entries": db.query(func.count(MemoryEntry.id)).scalar(),
        "memory_resolved": db.query(func.count(MemoryEntry.id))
        .filter(MemoryEntry.status == "resolved").scalar(),
        "top_tickers": [{"ticker": t, "runs": n} for t, n in top],
    }


@router.get("/users")
def list_users(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = (
        db.query(
            User,
            func.count(func.distinct(Run.id)).label("runs"),
            func.max(Run.created_at).label("last_run"),
        )
        .outerjoin(Run, Run.user_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at)
        .all()
    )
    key_counts = dict(
        db.query(ApiKey.user_id, func.count()).group_by(ApiKey.user_id).all()
    )
    return [
        {
            "id": u.id,
            "email": u.email,
            "is_admin": u.is_admin,
            "created_at": u.created_at,
            "runs": runs,
            "last_run": last_run,
            "keys": key_counts.get(u.id, 0),
        }
        for u, runs, last_run in rows
    ]


@router.post("/users/{user_id}/toggle-admin")
def toggle_admin(
    user_id: str,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="You cannot demote yourself")
    target.is_admin = not target.is_admin
    db.commit()
    return {"id": target.id, "is_admin": target.is_admin}


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="You cannot delete your own account here")
    from ..runner import cancel_all_for_user
    from ..services import purge_user_data

    await cancel_all_for_user(user_id)  # M5: no orphaned engine subprocesses
    purge_user_data(db, user_id)
    db.delete(target)
    db.commit()
