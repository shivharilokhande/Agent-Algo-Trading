"""F4/F5/F7/F9 — runs: create, list, stream, cancel/resume, reports, export."""
from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..config import DEMO_MODE_AVAILABLE, MAX_CONCURRENT_RUNS_PER_USER
from ..db import get_db
from ..models import ApiKey, Run, RunEvent, RunReport, User
from ..runner import SECTION_TITLES, manager, past_memory_context
from ..schemas import ReportOut, RunCreate, RunEventOut, RunOut
from ..security import create_stream_ticket, decode_stream_ticket, get_current_user
from ..tickers import (
    detect_asset_type,
    filter_analysts_for_asset_type,
    normalize_ticker,
    resolve_benchmark,
    validate_trade_date,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])

_REPORT_ORDER = [
    "market_report", "sentiment_report", "news_report", "fundamentals_report",
    "investment_plan", "trader_investment_plan", "final_trade_decision",
]


def _to_out(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        ticker=run.ticker,
        company_name=run.company_name,
        asset_type=run.asset_type,
        trade_date=run.trade_date,
        mode=run.mode,
        status=run.status,
        rating=run.rating,
        decision_summary=run.decision_summary,
        error=run.error,
        stats=json.loads(run.stats_json or "{}"),
        benchmark=run.benchmark,
        config=json.loads(run.config_json or "{}"),
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.post("", response_model=RunOut, status_code=201)
async def create_run(
    body: RunCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    # validation (F3.1–F3.4)
    try:
        ticker = normalize_ticker(body.ticker)
        trade_date = validate_trade_date(body.trade_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    asset_type = detect_asset_type(ticker)
    analysts = filter_analysts_for_asset_type(
        [a for a in body.analysts if a in ("market", "social", "news", "fundamentals")],
        asset_type,
    )
    if not analysts:
        raise HTTPException(status_code=422, detail="Select at least one applicable analyst")
    if body.research_depth not in (1, 3, 5):
        raise HTTPException(status_code=422, detail="research_depth must be 1, 3 or 5")

    mode = body.mode
    if mode == "demo" and not DEMO_MODE_AVAILABLE:
        raise HTTPException(status_code=422, detail="Demo mode is disabled on this deployment")
    if mode == "engine":
        has_key = (
            db.query(ApiKey)
            .filter(ApiKey.user_id == user.id, ApiKey.provider == body.llm_provider)
            .one_or_none()
        )
        if has_key is None:
            raise HTTPException(
                status_code=422,
                detail=f"No credential stored for provider '{body.llm_provider}'. Add it in Settings → API Keys.",
            )

    # C7: merge the user's saved defaults into this run's config
    from ..models import Setting

    setting_row = db.get(Setting, user.id)
    defaults = json.loads(setting_row.config_json) if setting_row else {}

    config = body.model_dump()
    config["ticker"] = ticker
    config["analysts"] = analysts
    config["asset_type"] = asset_type
    config["_memory_context"] = past_memory_context(user.id, ticker)
    if defaults.get("data_vendors") and not config.get("data_vendors"):
        config["data_vendors"] = defaults["data_vendors"]
    benchmark_override = defaults.get("benchmark_ticker") or None

    run = Run(
        user_id=user.id,
        ticker=ticker,
        asset_type=asset_type,
        trade_date=trade_date,
        config_json=json.dumps(config),
        mode=mode,
        benchmark=resolve_benchmark(ticker, override=benchmark_override),
    )
    db.add(run)
    db.commit()
    try:
        # NFR-S2/C6: cap enforced atomically inside the manager lock
        await manager.start(run.id, user_id=user.id, max_concurrent=MAX_CONCURRENT_RUNS_PER_USER)
    except PermissionError as exc:
        db.delete(run)
        db.commit()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return _to_out(run)


@router.get("", response_model=list[RunOut])
def list_runs(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    ticker: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 100,
):
    query = db.query(Run).filter(Run.user_id == user.id)
    if ticker:
        query = query.filter(Run.ticker == ticker.upper())
    if status:
        query = query.filter(Run.status == status)
    if q:  # F7.1 full-text search over decisions and report content
        needle = f"%{q[:100]}%"
        matching_run_ids = (
            db.query(RunReport.run_id)
            .join(Run, Run.id == RunReport.run_id)
            .filter(Run.user_id == user.id, RunReport.content_md.ilike(needle))
            .subquery()
        )
        query = query.filter(
            (Run.decision_summary.ilike(needle)) | (Run.id.in_(matching_run_ids))
        )
    runs = query.order_by(Run.created_at.desc()).limit(min(limit, 500)).all()
    return [_to_out(r) for r in runs]


def _owned_run(run_id: str, user: User, db: Session) -> Run:
    run = db.get(Run, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{run_id}", response_model=RunOut)
def get_run(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return _to_out(_owned_run(run_id, user, db))


@router.get("/{run_id}/events", response_model=list[RunEventOut])
def get_events(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    after_seq: int = 0,
    limit: int = 1000,
):
    """Replay from the event log (F4.4), paginated (P1)."""
    _owned_run(run_id, user, db)
    events = (
        db.query(RunEvent)
        .filter(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
        .order_by(RunEvent.seq)
        .limit(min(limit, 5000))
        .all()
    )
    return [
        RunEventOut(seq=e.seq, type=e.type, agent=e.agent, payload=json.loads(e.payload_json), ts=e.ts)
        for e in events
    ]


@router.get("/{run_id}/reports", response_model=list[ReportOut])
def get_reports(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _owned_run(run_id, user, db)
    rows = db.query(RunReport).filter(RunReport.run_id == run_id).all()
    by_section = {r.section: r for r in rows}
    return [
        ReportOut(section=s, content_md=by_section[s].content_md)
        for s in _REPORT_ORDER
        if s in by_section
    ]


@router.get("/{run_id}/report.md", response_class=PlainTextResponse)
def export_markdown(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """F7.3 — complete_report.md parity export."""
    run = _owned_run(run_id, user, db)
    rows = {r.section: r.content_md for r in db.query(RunReport).filter(RunReport.run_id == run_id)}
    parts = [f"# {run.ticker} — {run.trade_date}\n\nRating: **{run.rating or 'n/a'}** · Mode: {run.mode} · Benchmark: {run.benchmark}\n"]
    groups = [
        ("I. Analyst Team Reports", ["market_report", "sentiment_report", "news_report", "fundamentals_report"]),
        ("II. Research Team Decision", ["investment_plan"]),
        ("III. Trading Team Plan", ["trader_investment_plan"]),
        ("IV & V. Risk Management and Portfolio Manager Decision", ["final_trade_decision"]),
    ]
    for title, sections in groups:
        content = [rows[s] for s in sections if s in rows]
        if content:
            parts.append(f"## {title}\n\n" + "\n\n---\n\n".join(content))
    return "\n\n".join(parts)


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel_run(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    run = _owned_run(run_id, user, db)
    if not manager.is_active(run_id):
        raise HTTPException(status_code=409, detail="Run is not executing")
    await manager.cancel(run_id)
    for _ in range(50):
        await asyncio.sleep(0.1)
        db.refresh(run)
        if run.status in ("cancelled", "interrupted", "done", "failed"):
            break
    return _to_out(run)


@router.post("/{run_id}/resume", response_model=RunOut)
async def resume_run(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """F9.2 — resume an interrupted/cancelled run from its checkpoints."""
    run = _owned_run(run_id, user, db)
    if run.status not in ("interrupted", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Run is {run.status}; only interrupted or cancelled runs resume")
    if manager.is_active(run_id):
        raise HTTPException(status_code=409, detail="Run is already executing")
    run.status = "queued"
    db.commit()
    try:
        await manager.start(run_id, resume=True, user_id=user.id, max_concurrent=MAX_CONCURRENT_RUNS_PER_USER)
    except PermissionError as exc:
        run.status = "interrupted"
        db.commit()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return _to_out(run)


@router.delete("/{run_id}", status_code=204)
def delete_run(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    run = _owned_run(run_id, user, db)
    if manager.is_active(run_id):
        raise HTTPException(status_code=409, detail="Cancel the run before deleting it")
    # C1: detach memory entries referencing this run before delete (FK is enforced)
    from ..models import MemoryEntry

    db.query(MemoryEntry).filter(MemoryEntry.run_id == run_id).update({"run_id": None})
    db.delete(run)
    db.commit()


# ---------- live stream (F4.2) ----------

@router.post("/{run_id}/stream-ticket")
def stream_ticket(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """S3 — mint a 60s, run-scoped WS ticket so the JWT never rides a URL."""
    _owned_run(run_id, user, db)
    return {"ticket": create_stream_ticket(user.id, run_id)}


@router.websocket("/{run_id}/stream")
async def stream_run(websocket: WebSocket, run_id: str, ticket: str = ""):
    """WS: replays state then streams live events. Auth via short-lived ?ticket=."""
    from ..db import SessionLocal

    try:
        user_id = decode_stream_ticket(ticket, run_id)
    except HTTPException:
        await websocket.close(code=4401)
        return
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None or run.user_id != user_id:
            await websocket.close(code=4404)
            return
    await websocket.accept()

    # subscribe FIRST so no live event is lost during replay
    queue = manager.subscribe(run_id)
    try:
        last_seq = 0
        with SessionLocal() as db:
            # P1: report sections replay from run_reports (latest content only),
            # not from the event log's every-revision history.
            for r in db.query(RunReport).filter(RunReport.run_id == run_id).all():
                await websocket.send_json(
                    {"seq": 0, "type": "report_section", "agent": "",
                     "payload": {"section": r.section,
                                 "title": SECTION_TITLES.get(r.section, r.section),
                                 "content_md": r.content_md},
                     "ts": ""}
                )
            events = (
                db.query(RunEvent)
                .filter(RunEvent.run_id == run_id, RunEvent.type != "report_section")
                .order_by(RunEvent.seq)
                .all()
            )
            for e in events:
                last_seq = e.seq
                await websocket.send_json(
                    {"seq": e.seq, "type": e.type, "agent": e.agent,
                     "payload": json.loads(e.payload_json), "ts": e.ts.isoformat()}
                )
        if not manager.is_active(run_id):
            await websocket.send_json({"type": "stream_end"})
            await websocket.close()
            return
        while True:
            event = await queue.get()
            if event is None:  # run finished
                await websocket.send_json({"type": "stream_end"})
                break
            if event["seq"] <= last_seq:
                continue
            await websocket.send_json(event)
        await websocket.close()
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe(run_id, queue)


SECTION_TITLES_EXPORT = SECTION_TITLES  # re-export for tests
