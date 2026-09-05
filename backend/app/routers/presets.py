"""F3.9 presets + F10 per-user settings."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Preset, Setting, User
from ..schemas import PresetIn, PresetOut, SettingsIn
from ..security import get_current_user

router = APIRouter(prefix="/api", tags=["presets"])

# Allowed per-user setting keys — mirrors TRADINGAGENTS_* env overrides (F10.1)
_SETTING_VALIDATORS: dict[str, type | tuple] = {
    "llm_provider": str,
    "deep_think_llm": str,
    "quick_think_llm": str,
    "output_language": str,
    "max_debate_rounds": int,
    "max_risk_discuss_rounds": int,
    "checkpoint_enabled": bool,
    "benchmark_ticker": str,
    "temperature": (int, float),
    "llm_max_retries": int,
    "max_tokens": int,
    "google_thinking_level": str,
    "openai_reasoning_effort": str,
    "anthropic_effort": str,
    "research_depth": int,
    "analysts": list,
    "data_vendors": dict,
    "memory_log_max_entries": int,
    "mode": str,
    "paper_trading": bool,
    "paper_notional": (int, float),
    "hitl": bool,
    "trading_capital": (int, float),
    "risk_per_trade_pct": (int, float),
    "fno_lot_sizes": dict,
    "scalp_enabled": bool,
    "scalp_symbols": list,
    "scalp_risk_pct": (int, float),
}


@router.get("/presets", response_model=list[PresetOut])
def list_presets(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.query(Preset).filter(Preset.user_id == user.id).order_by(Preset.created_at).all()
    return [
        PresetOut(id=p.id, name=p.name, config=json.loads(p.config_json), created_at=p.created_at)
        for p in rows
    ]


@router.post("/presets", response_model=PresetOut, status_code=201)
def save_preset(
    body: PresetIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(Preset)
        .filter(Preset.user_id == user.id, Preset.name == body.name)
        .one_or_none()
    )
    if row is None:
        row = Preset(user_id=user.id, name=body.name, config_json=json.dumps(body.config))
        db.add(row)
    else:
        row.config_json = json.dumps(body.config)
    db.commit()
    return PresetOut(id=row.id, name=row.name, config=body.config, created_at=row.created_at)


@router.delete("/presets/{preset_id}", status_code=204)
def delete_preset(
    preset_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(Preset, preset_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Preset not found")
    db.delete(row)
    db.commit()


@router.get("/settings")
def get_settings(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(Setting, user.id)
    return {"config": json.loads(row.config_json) if row else {}}


@router.put("/settings")
def put_settings(
    body: SettingsIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """F10.1 — validate loudly, mirroring the engine's _coerce semantics."""
    for key, value in body.config.items():
        expected = _SETTING_VALIDATORS.get(key)
        if expected is None:
            raise HTTPException(status_code=422, detail=f"Unknown setting: {key}")
        if value is not None and not isinstance(value, expected):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid value for {key}: expected {expected}, got {type(value).__name__}",
            )
    row = db.get(Setting, user.id)
    if row is None:
        row = Setting(user_id=user.id)
        db.add(row)
    row.config_json = json.dumps(body.config)
    db.commit()
    return {"config": body.config}
