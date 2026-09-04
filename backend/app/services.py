"""Run-creation service shared by the API, watchlist runs, and the scheduler."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .config import DEMO_MODE_AVAILABLE, MAX_CONCURRENT_RUNS_PER_USER
from .models import ApiKey, Run, Setting
from .runner import manager, past_memory_context
from .tickers import (
    detect_asset_type,
    filter_analysts_for_asset_type,
    normalize_ticker,
    resolve_benchmark,
    validate_trade_date,
)


class RunValidationError(ValueError):
    pass


def build_run(db: Session, user_id: str, cfg: dict) -> Run:
    """Validate config and persist a Run row (status=queued). Raises RunValidationError."""
    try:
        ticker = normalize_ticker(cfg.get("ticker", ""))
        trade_date = validate_trade_date(cfg.get("trade_date", ""))
    except ValueError as exc:
        raise RunValidationError(str(exc)) from exc
    asset_type = detect_asset_type(ticker)
    analysts = filter_analysts_for_asset_type(
        [a for a in cfg.get("analysts", ["market", "social", "news", "fundamentals"])
         if a in ("market", "social", "news", "fundamentals")],
        asset_type,
    )
    if not analysts:
        raise RunValidationError("Select at least one applicable analyst")
    if cfg.get("research_depth", 1) not in (1, 3, 5):
        raise RunValidationError("research_depth must be 1, 3 or 5")

    mode = cfg.get("mode", "demo")
    if mode not in ("demo", "engine"):
        raise RunValidationError("mode must be demo or engine")
    if cfg.get("hitl") and mode != "demo":
        raise RunValidationError(
            "Human-in-the-loop pause is demo-mode only for now (engine support is on the roadmap)"
        )
    if mode == "demo" and not DEMO_MODE_AVAILABLE:
        raise RunValidationError("Demo mode is disabled on this deployment")
    if mode == "engine":
        provider = cfg.get("llm_provider", "openai")
        has_key = (
            db.query(ApiKey)
            .filter(ApiKey.user_id == user_id, ApiKey.provider == provider)
            .one_or_none()
        )
        if has_key is None:
            raise RunValidationError(
                f"No credential stored for provider '{provider}'. Add it in Settings → API Keys."
            )

    setting_row = db.get(Setting, user_id)
    defaults = json.loads(setting_row.config_json) if setting_row else {}

    config = dict(cfg)
    config["ticker"] = ticker
    config["trade_date"] = trade_date
    config["analysts"] = analysts
    config["asset_type"] = asset_type
    config["_memory_context"] = past_memory_context(user_id, ticker)
    if defaults.get("data_vendors") and not config.get("data_vendors"):
        config["data_vendors"] = defaults["data_vendors"]

    run = Run(
        user_id=user_id,
        ticker=ticker,
        asset_type=asset_type,
        trade_date=trade_date,
        config_json=json.dumps(config),
        mode=mode,
        benchmark=resolve_benchmark(ticker, override=defaults.get("benchmark_ticker") or None),
    )
    db.add(run)
    db.commit()
    return run


async def start_or_queue(run: Run, user_id: str) -> bool:
    """Start immediately if the user has a free slot; else leave queued (P2 pump).

    Returns True when started now.
    """
    try:
        await manager.start(run.id, user_id=user_id, max_concurrent=MAX_CONCURRENT_RUNS_PER_USER)
        return True
    except PermissionError:
        return False  # stays 'queued'; the pump starts it when a slot frees
    except ValueError:
        return False  # already executing


async def create_run_for_user(db: Session, user_id: str, cfg: dict) -> Run:
    run = build_run(db, user_id, cfg)
    await start_or_queue(run, user_id)
    db.refresh(run)
    return run
