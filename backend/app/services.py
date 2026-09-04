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


def detach_run_references(db: Session, run_id: str) -> None:
    """Null out soft references so a run can be deleted under FK enforcement."""
    import json as _json

    from .models import Alert, Ensemble, MemoryEntry, PaperPosition

    for model in (MemoryEntry, Alert, PaperPosition):
        db.query(model).filter(model.run_id == run_id).update({"run_id": None})
    # R3-1: invalidate cached consensus for ensembles that referenced this run.
    # run_ids_json keeps the original id (it's a soft JSON reference, no FK) so the
    # member count stays honest and a partial set can never be judged "complete".
    for ens in db.query(Ensemble).filter(Ensemble.run_ids_json.contains(run_id)).all():
        ens.consensus_json = ""
    _ = _json  # id list intentionally left untouched


def purge_user_data(db: Session, user_id: str) -> None:
    """Delete every row belonging to a user that lacks an ORM cascade.

    Runs/events/reports/keys cascade from User; everything else is explicit so
    account deletion can never 500 on a foreign key (C1 family).
    """
    from sqlalchemy import bindparam, text as sql

    from .models import (
        AgentProfile, Alert, Document, Ensemble, MemoryEntry, PaperPosition,
        Preset, Schedule, Setting, Trigger, Watchlist,
    )

    # drop FTS rows for the user's documents first (R3-10: parameterized, chunked)
    doc_ids = [d[0] for d in db.query(Document.id).filter(Document.user_id == user_id).all()]
    stmt = sql("DELETE FROM documents_fts WHERE doc_id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    for i in range(0, len(doc_ids), 200):
        db.execute(stmt, {"ids": doc_ids[i:i + 200]})
    for model in (Alert, PaperPosition, MemoryEntry, Preset, Setting, Watchlist,
                  Schedule, Trigger, AgentProfile, Document, Ensemble):
        db.query(model).filter(model.user_id == user_id).delete()


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

    # P4-A1: Agent Studio personas + custom analysts (demo runner consumes these)
    import json as _json

    from .models import AgentProfile

    profiles = db.query(AgentProfile).filter(
        AgentProfile.user_id == user_id, AgentProfile.enabled.is_(True)
    ).all()
    overrides = {p.agent_key: p.persona for p in profiles if not p.agent_key.startswith("custom:") and p.persona}
    customs = [
        {"name": p.display_name or p.agent_key.removeprefix("custom:"),
         "persona": p.persona, "tools": _json.loads(p.tools_json or "[]")}
        for p in profiles if p.agent_key.startswith("custom:")
    ]
    if overrides:
        config["_agent_profiles"] = overrides
    if customs:
        config["_custom_analysts"] = customs

    # P4-A4: grounded citations from the research library (as-of filtered)
    try:
        from .library import grounding_context

        grounding = grounding_context(user_id, ticker, trade_date)
        if grounding:
            config["_grounding"] = grounding
    except Exception:  # pragma: no cover — library must never block a run
        pass

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
