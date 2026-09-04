"""Run execution manager (F4/F5/F9).

Event-sourced: every event is persisted to run_events (source of truth for
replay) and broadcast to live WebSocket subscribers via per-run asyncio queues.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..db import SessionLocal
from ..models import Alert, MemoryEntry, Run, RunEvent, RunReport

log = logging.getLogger("agentalgo.runner")

# Engine-parity agent roster (graph node names)
AGENTS = [
    "Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst",
    "Bull Researcher", "Bear Researcher", "Research Manager", "Trader",
    "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst", "Portfolio Manager",
]

ANALYST_AGENT = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

SECTION_TITLES = {
    "market_report": "Market Analysis",
    "sentiment_report": "Social Sentiment",
    "news_report": "News Analysis",
    "fundamentals_report": "Fundamentals Analysis",
    "investment_plan": "Research Team Decision",
    "trader_investment_plan": "Trading Team Plan",
    "final_trade_decision": "Portfolio Manager Decision",
}


class RunCancelled(Exception):
    pass


class RunHandle:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.cancel_event = asyncio.Event()
        self.subscribers: list[asyncio.Queue] = []
        self.seq = 0
        self.task: asyncio.Task | None = None


class RunManager:
    """Singleton owning all in-flight runs in this process."""

    def __init__(self) -> None:
        self._handles: dict[str, RunHandle] = {}
        self._lock = asyncio.Lock()

    # ---------- lifecycle ----------

    async def start(
        self, run_id: str, resume: bool = False,
        user_id: str | None = None, max_concurrent: int | None = None,
    ) -> None:
        async with self._lock:
            if run_id in self._handles:
                raise ValueError("Run already executing")
            # C6: enforce the per-user cap atomically under the manager lock
            if user_id is not None and max_concurrent is not None:
                if self.active_count_for_user(user_id) >= max_concurrent:
                    raise PermissionError("Concurrent run limit reached")
            handle = RunHandle(run_id)
            if resume:
                handle.seq = self._max_seq(run_id)
            self._handles[run_id] = handle
        handle.task = asyncio.create_task(self._execute(handle, resume=resume))

    async def cancel(self, run_id: str) -> bool:
        handle = self._handles.get(run_id)
        if handle is None:
            return False
        handle.cancel_event.set()
        return True

    def is_active(self, run_id: str) -> bool:
        return run_id in self._handles

    def active_count_for_user(self, user_id: str) -> int:
        if not self._handles:
            return 0
        with SessionLocal() as db:
            ids = list(self._handles.keys())
            rows = db.query(Run.id).filter(Run.user_id == user_id, Run.id.in_(ids)).all()
            return len(rows)

    # ---------- pub/sub ----------

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        handle = self._handles.get(run_id)
        if handle is not None:
            handle.subscribers.append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        handle = self._handles.get(run_id)
        if handle is not None and q in handle.subscribers:
            handle.subscribers.remove(q)

    # ---------- event emission ----------

    def _max_seq(self, run_id: str) -> int:
        with SessionLocal() as db:
            row = (
                db.query(RunEvent.seq)
                .filter(RunEvent.run_id == run_id)
                .order_by(RunEvent.seq.desc())
                .first()
            )
            return row[0] if row else 0

    async def emit(
        self, handle: RunHandle, type_: str, agent: str = "", payload: dict | None = None
    ) -> None:
        if handle.cancel_event.is_set():
            raise RunCancelled()
        handle.seq += 1
        payload = payload or {}
        event = {
            "seq": handle.seq,
            "type": type_,
            "agent": agent,
            "payload": payload,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        def _persist() -> None:  # C4: keep WAL fsyncs off the event loop
            with SessionLocal() as db:
                db.add(
                    RunEvent(
                        run_id=handle.run_id,
                        seq=handle.seq,
                        type=type_,
                        agent=agent,
                        payload_json=json.dumps(payload),
                    )
                )
                db.commit()

        await asyncio.to_thread(_persist)
        for q in list(handle.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow consumer: drop oldest
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass

    async def save_report(self, handle: RunHandle, section: str, content_md: str) -> None:
        with SessionLocal() as db:
            existing = (
                db.query(RunReport)
                .filter(RunReport.run_id == handle.run_id, RunReport.section == section)
                .one_or_none()
            )
            if existing:
                existing.content_md = content_md
            else:
                db.add(RunReport(run_id=handle.run_id, section=section, content_md=content_md))
            db.commit()
        await self.emit(
            handle,
            "report_section",
            payload={"section": section, "title": SECTION_TITLES.get(section, section), "content_md": content_md},
        )

    # ---------- execution ----------

    async def _execute(self, handle: RunHandle, resume: bool) -> None:
        run_id = handle.run_id
        try:
            with SessionLocal() as db:
                run = db.get(Run, run_id)
                if run is None:
                    return
                run.status = "running"
                run.started_at = run.started_at or datetime.now(timezone.utc)
                run.error = ""
                db.commit()
                mode = run.mode
                config = json.loads(run.config_json)
                ticker = run.ticker
                trade_date = run.trade_date
                user_id = run.user_id
                benchmark = run.benchmark

            await self.emit(handle, "run_status", payload={"status": "running", "resumed": resume})

            if mode == "engine":
                from .engine import run_engine

                result = await run_engine(self, handle, run_id, config, resume=resume)
            else:
                from .demo import run_demo

                result = await run_demo(
                    self, handle, ticker, trade_date, config, run_id=run_id, resume=resume
                )

            with SessionLocal() as db:
                run = db.get(Run, run_id)
                run.status = "done"
                run.rating = result.get("rating")
                run.decision_summary = result.get("decision_summary", "")
                run.stats_json = json.dumps(result.get("stats", {}))
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                # F8.1 — append pending decision-log entry
                db.add(
                    MemoryEntry(
                        user_id=user_id,
                        run_id=run_id,
                        ticker=ticker,
                        trade_date=trade_date,
                        rating=result.get("rating") or "REVIEW",
                        summary=(result.get("decision_summary", "") or "")[:2000],
                        benchmark=benchmark,
                    )
                )
                db.commit()
                maybe_create_alert(db, run)  # P2.3
            await self.emit(
                handle,
                "run_status",
                payload={
                    "status": "done",
                    "rating": result.get("rating"),
                    "decision_summary": result.get("decision_summary", ""),
                    "stats": result.get("stats", {}),
                },
            )
        except RunCancelled:
            self._finalize(run_id, "cancelled")
            await self._broadcast_final(handle, "cancelled")
        except Exception as exc:  # noqa: BLE001 — any failure leaves a resumable run
            log.exception("Run %s failed", run_id)
            self._finalize(run_id, "interrupted", error=str(exc))
            await self._broadcast_final(handle, "interrupted", error=str(exc))
        finally:
            for q in list(handle.subscribers):
                try:
                    q.put_nowait(None)  # sentinel: stream over
                except asyncio.QueueFull:  # C5: never drop the sentinel
                    try:
                        q.get_nowait()
                        q.put_nowait(None)
                    except asyncio.QueueEmpty:
                        pass
            async with self._lock:
                self._handles.pop(run_id, None)
            await pump_queued_runs()  # P2: a slot freed — start the oldest queued run

    def _finalize(self, run_id: str, status: str, error: str = "") -> None:
        with SessionLocal() as db:
            run = db.get(Run, run_id)
            if run is not None:
                run.status = status
                run.error = error
                run.finished_at = datetime.now(timezone.utc)
                db.commit()

    async def _broadcast_final(self, handle: RunHandle, status: str, error: str = "") -> None:
        handle.seq += 1
        event = {
            "seq": handle.seq,
            "type": "run_status",
            "agent": "",
            "payload": {"status": status, "error": error},
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with SessionLocal() as db:
            db.add(
                RunEvent(
                    run_id=handle.run_id,
                    seq=handle.seq,
                    type="run_status",
                    payload_json=json.dumps(event["payload"]),
                )
            )
            db.commit()
        for q in list(handle.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


manager = RunManager()


def maybe_create_alert(db, run: Run) -> None:
    """P2.3 — alert on REVIEW or a rating-tier change vs. the previous done run."""
    if run.rating == "REVIEW":
        db.add(Alert(
            user_id=run.user_id, run_id=run.id, ticker=run.ticker, type="review",
            message=f"{run.ticker} ({run.trade_date}): decision needs REVIEW — no parseable rating.",
        ))
        db.commit()
        return
    prev = (
        db.query(Run)
        .filter(Run.user_id == run.user_id, Run.ticker == run.ticker,
                Run.status == "done", Run.id != run.id, Run.rating.isnot(None))
        .order_by(Run.finished_at.desc())
        .first()
    )
    if prev is not None and run.rating and prev.rating != run.rating:
        db.add(Alert(
            user_id=run.user_id, run_id=run.id, ticker=run.ticker, type="rating_change",
            message=f"{run.ticker}: rating changed {prev.rating} → {run.rating} "
                    f"(previous run {prev.trade_date}, new run {run.trade_date}).",
        ))
        db.commit()


async def pump_queued_runs(user_id: str | None = None) -> int:
    """P2 — start queued runs while their owners have free slots. Returns starts."""
    from ..config import MAX_CONCURRENT_RUNS_PER_USER

    started = 0
    with SessionLocal() as db:
        q = db.query(Run).filter(Run.status == "queued").order_by(Run.created_at)
        if user_id:
            q = q.filter(Run.user_id == user_id)
        queued = q.limit(50).all()
        queued_info = [(r.id, r.user_id) for r in queued]
    for rid, uid in queued_info:
        if manager.is_active(rid):
            continue
        try:
            await manager.start(rid, user_id=uid, max_concurrent=MAX_CONCURRENT_RUNS_PER_USER)
            started += 1
        except (PermissionError, ValueError):
            continue
    return started


async def cancel_all_for_user(user_id: str, timeout: float = 6.0) -> None:
    """M5: stop a user's in-flight runs (and their engine subprocesses) before
    the account or its runs are deleted."""
    with SessionLocal() as db:
        ids = [r[0] for r in db.query(Run.id).filter(Run.user_id == user_id).all()]
    active = [rid for rid in ids if manager.is_active(rid)]
    for rid in active:
        await manager.cancel(rid)
    deadline = asyncio.get_event_loop().time() + timeout
    while active and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.2)
        active = [rid for rid in active if manager.is_active(rid)]


def completed_sections(run_id: str) -> set[str]:
    """Which report sections already exist (checkpoint semantics for resume)."""
    with SessionLocal() as db:
        rows = db.query(RunReport.section).filter(RunReport.run_id == run_id).all()
        return {r[0] for r in rows}


def past_memory_context(user_id: str, ticker: str, limit: int = 3) -> list[dict[str, Any]]:
    """F8.3 — recent resolved same-ticker decisions + cross-ticker lessons."""
    with SessionLocal() as db:
        same = (
            db.query(MemoryEntry)
            .filter(
                MemoryEntry.user_id == user_id,
                MemoryEntry.ticker == ticker,
                MemoryEntry.status == "resolved",
            )
            .order_by(MemoryEntry.resolved_at.desc())
            .limit(limit)
            .all()
        )
        cross = (
            db.query(MemoryEntry)
            .filter(
                MemoryEntry.user_id == user_id,
                MemoryEntry.ticker != ticker,
                MemoryEntry.status == "resolved",
            )
            .order_by(MemoryEntry.resolved_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "ticker": e.ticker,
                "trade_date": e.trade_date,
                "rating": e.rating,
                "alpha": e.alpha,
                "reflection": e.reflection,
                "scope": "same" if e.ticker == ticker else "cross",
            }
            for e in list(same) + list(cross)
        ]
