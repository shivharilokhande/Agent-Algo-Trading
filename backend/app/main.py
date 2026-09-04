"""AgentAlgo backend — FastAPI application."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .config import CORS_ORIGINS, DEMO_MODE_AVAILABLE
from .db import SessionLocal, init_db
from .models import Run
from .routers import admin, auth, automations, catalog, keys, memory, presets, runs

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agentalgo")

RESOLUTION_INTERVAL_SECONDS = 6 * 3600
RESOLUTION_MIN_AGE_DAYS = 3


async def _auto_resolution_loop() -> None:
    """F8.2 — scheduled outcome resolution + F8.4 retention, every 6 hours."""
    from .models import MemoryEntry, Setting
    from .routers.memory import resolve_entry_core

    while True:
        try:
            cutoff = (date.today() - timedelta(days=RESOLUTION_MIN_AGE_DAYS)).isoformat()
            with SessionLocal() as db:
                pending = (
                    db.query(MemoryEntry)
                    .filter(MemoryEntry.status == "pending", MemoryEntry.trade_date <= cutoff)
                    .limit(50)
                    .all()
                )
                for entry in pending:
                    try:
                        await resolve_entry_core(db, entry, min_days=RESOLUTION_MIN_AGE_DAYS)
                        log.info("Auto-resolved %s %s", entry.ticker, entry.trade_date)
                    except Exception as exc:  # noqa: BLE001 — one bad ticker must not stop the sweep
                        log.info("Auto-resolve skipped %s: %s", entry.ticker, exc)
                # retention: prune oldest resolved entries beyond each user's cap
                for setting in db.query(Setting).all():
                    cap = (json.loads(setting.config_json or "{}") or {}).get("memory_log_max_entries")
                    if not cap:
                        continue
                    resolved = (
                        db.query(MemoryEntry)
                        .filter(MemoryEntry.user_id == setting.user_id, MemoryEntry.status == "resolved")
                        .order_by(MemoryEntry.resolved_at.desc())
                        .all()
                    )
                    for extra in resolved[int(cap):]:
                        db.delete(extra)
                db.commit()
        except Exception:  # pragma: no cover — the loop itself must never die
            log.exception("Auto-resolution sweep failed")
        await asyncio.sleep(RESOLUTION_INTERVAL_SECONDS)

DISCLAIMER = (
    "AgentAlgo is a research tool built on the TradingAgents framework. "
    "Nothing it produces is financial, investment, or trading advice."
)


SCHEDULE_POLL_SECONDS = 60


def schedule_is_due(cadence: str, weekday: int, hour: int, last_fired: str, now) -> bool:
    """P2.2 due check (server-local time). Fires once per due day at/after `hour`."""
    today = now.date().isoformat()
    if last_fired == today or now.hour < hour:
        return False
    if cadence == "daily":
        return True
    if cadence == "weekdays":
        return now.weekday() < 5
    return now.weekday() == weekday  # weekly


async def _schedule_loop() -> None:
    """P2.2 — fire due schedules; runs queue through the pump."""
    from datetime import datetime as dt

    from .models import Schedule
    from .services import RunValidationError, create_run_for_user

    while True:
        try:
            now = dt.now()
            with SessionLocal() as db:
                due = [
                    s for s in db.query(Schedule).filter(Schedule.enabled.is_(True)).all()
                    if schedule_is_due(s.cadence, s.weekday, s.hour, s.last_fired_date, now)
                ]
                for s in due:
                    base = {"trade_date": now.date().isoformat(), "mode": "demo",
                            "research_depth": 1, **json.loads(s.config_json or "{}")}
                    for ticker in json.loads(s.tickers_json):
                        try:
                            await create_run_for_user(db, s.user_id, {**base, "ticker": ticker})
                        except RunValidationError as exc:
                            log.info("Schedule %s skipped %s: %s", s.name, ticker, exc)
                    s.last_fired_date = now.date().isoformat()
                    log.info("Schedule fired: %s (%s tickers)", s.name, len(json.loads(s.tickers_json)))
                db.commit()
        except Exception:  # pragma: no cover
            log.exception("Schedule sweep failed")
        await asyncio.sleep(SCHEDULE_POLL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        # F9 — runs mid-execution at shutdown are resumable; queued runs restart via the pump
        stale = db.query(Run).filter(Run.status == "running").all()
        for run in stale:
            run.status = "interrupted"
            run.error = "Server restarted mid-run — resume to continue from checkpoint."
        # Super-admin bootstrap (create or promote; password only set on create)
        from .config import ADMIN_EMAIL, ADMIN_PASSWORD
        from .models import User
        from .security import hash_password

        if ADMIN_PASSWORD == "admin12345":  # H2: never ship the documented default
            log.critical(
                "SECURITY: AGENTALGO_ADMIN_PASSWORD is the documented default. "
                "Set a real password before exposing this deployment."
            )
        admin_user = db.query(User).filter(User.email == ADMIN_EMAIL.lower()).first()
        if admin_user is None:
            db.add(User(email=ADMIN_EMAIL.lower(),
                        password_hash=hash_password(ADMIN_PASSWORD), is_admin=True))
        else:
            admin_user.is_admin = True
        db.commit()
    from .runner import pump_queued_runs

    await pump_queued_runs()  # P2: restart anything that was waiting for a slot
    jobs = [
        asyncio.create_task(_auto_resolution_loop()),
        asyncio.create_task(_schedule_loop()),
    ]
    yield
    for job in jobs:
        job.cancel()


app = FastAPI(
    title="AgentAlgo",
    version="1.0.0",
    description=f"Multi-agent LLM trading research platform. {DISCLAIMER}",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    return response


# F11.2 — in-app announcements (edit this list per release)
ANNOUNCEMENTS: list[dict] = [
    {
        "id": "v1-launch",
        "level": "info",
        "text": "AgentAlgo 1.0 — full TradingAgents parity in the browser. Add an LLM key in Settings to switch from demo to live engine runs.",
    },
]


@app.get("/api/announcements")
def announcements():
    return ANNOUNCEMENTS

app.include_router(auth.router)
app.include_router(keys.router)
app.include_router(catalog.router)
app.include_router(runs.router)
app.include_router(memory.router)
app.include_router(presets.router)
app.include_router(admin.router)
app.include_router(automations.router)


@app.get("/api/health")
def health():
    engine_available = True
    try:
        import tradingagents  # noqa: F401
    except ImportError:
        engine_available = False
    return {
        "status": "ok",
        "app": "AgentAlgo",
        "version": "1.0.0",
        "engine_available": engine_available,
        "demo_mode": DEMO_MODE_AVAILABLE,
        "disclaimer": DISCLAIMER,
    }
