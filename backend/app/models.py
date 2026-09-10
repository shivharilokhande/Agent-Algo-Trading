"""ORM models — the PRD §10 core data model."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    runs: Mapped[list["Run"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ApiKey(Base):
    """Encrypted BYO credential for one provider (F2)."""

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_provider"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64))  # e.g. "openai", "alpha_vantage"
    ciphertext: Mapped[str] = mapped_column(Text)  # Fernet-encrypted secret
    # Non-secret extras (endpoint URLs etc.) stored as JSON text
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    mask: Mapped[str] = mapped_column(String(32), default="")  # e.g. "sk-…a4f2"
    status: Mapped[str] = mapped_column(String(16), default="untested")  # untested|valid|invalid
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="api_keys")


class Preset(Base):
    """Named analysis configuration (F3.9)."""

    __tablename__ = "presets"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_preset"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    config_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """Per-user default settings (F10) as a JSON document."""

    __tablename__ = "settings"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Run(Base):
    """One analysis run (F4/F5)."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    company_name: Mapped[str] = mapped_column(String(255), default="")
    asset_type: Mapped[str] = mapped_column(String(16), default="stock")
    trade_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    config_json: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(8), default="demo")  # demo|engine
    # queued | running | paused | interrupted | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    rating: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 5-tier or REVIEW
    decision_summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    stats_json: Mapped[str] = mapped_column(Text, default="{}")
    benchmark: Mapped[str] = mapped_column(String(16), default="SPY")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="runs")
    events: Mapped[list["RunEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunEvent.seq"
    )
    reports: Mapped[list["RunReport"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunEvent(Base):
    """Event-sourced run log — the source of truth for live view + replay."""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    # type: agent_status | message | tool_call | report_section | stats | run_status
    type: Mapped[str] = mapped_column(String(24))
    agent: Mapped[str] = mapped_column(String(48), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="events")


class RunReport(Base):
    """Final report sections, keyed like the engine's report tree (F7)."""

    __tablename__ = "run_reports"
    __table_args__ = (UniqueConstraint("run_id", "section", name="uq_run_section"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    section: Mapped[str] = mapped_column(String(48))
    content_md: Mapped[str] = mapped_column(Text, default="")

    run: Mapped[Run] = relationship(back_populates="reports")


class Watchlist(Base):
    """P2.1 — named ticker list."""

    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_watchlist"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    tickers_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Schedule(Base):
    """P2.2 — recurring analysis schedule (server-local time)."""

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    tickers_json: Mapped[str] = mapped_column(Text, default="[]")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    cadence: Mapped[str] = mapped_column(String(12), default="weekdays")  # daily|weekdays|weekly
    weekday: Mapped[int] = mapped_column(Integer, default=0)  # weekly cadence: 0=Mon
    hour: Mapped[int] = mapped_column(Integer, default=7)  # server-local hour 0-23
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fired_date: Mapped[str] = mapped_column(String(10), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    """P2.3 — rating-change / REVIEW notifications."""

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    ticker: Mapped[str] = mapped_column(String(24))
    type: Mapped[str] = mapped_column(String(20))  # rating_change | review
    message: Mapped[str] = mapped_column(Text, default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Ensemble(Base):
    """P4-A3 — one analysis fanned out across N model stacks."""

    __tablename__ = "ensembles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(24))
    trade_date: Mapped[str] = mapped_column(String(10))
    run_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    consensus_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Trigger(Base):
    """P4-A6 — event trigger that fires an analysis run."""

    __tablename__ = "triggers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(24))
    type: Mapped[str] = mapped_column(String(24), default="price_move_pct")
    threshold: Mapped[float] = mapped_column(Float, default=3.0)  # abs % day move
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fired_date: Mapped[str] = mapped_column(String(10), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentProfile(Base):
    """P4-A1 — persona override for a stock agent, or a custom analyst."""

    __tablename__ = "agent_profiles"
    __table_args__ = (UniqueConstraint("user_id", "agent_key", name="uq_user_agent"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # stock agent key ("Market Analyst", …) or "custom:<name>" for custom analysts
    agent_key: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(80), default="")
    persona: Mapped[str] = mapped_column(Text, default="")
    tools_json: Mapped[str] = mapped_column(Text, default="[]")  # custom analysts only
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    """P4-A4 — research library document (SEC filing or AgentAlgo report)."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    source: Mapped[str] = mapped_column(String(24))  # sec_filing | report
    title: Mapped[str] = mapped_column(String(255))
    doc_date: Mapped[str] = mapped_column(String(10), default="")  # filing/trade date
    url: Mapped[str] = mapped_column(String(512), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperPosition(Base):
    """P3-A2 — simulated position opened from a run's decision (paper trading)."""

    __tablename__ = "paper_positions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    rating: Mapped[str] = mapped_column(String(16))  # rating that opened it
    qty: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    notional: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_reason: Mapped[str] = mapped_column(String(120), default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Briefing(Base):
    """Borrow #3 — per-instrument briefing book (persistent desk memory)."""

    __tablename__ = "briefings"
    __table_args__ = (UniqueConstraint("user_id", "ticker", name="uq_user_briefing"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ScalpSignal(Base):
    """Scalp Mode — one fast-lane intraday signal (research only, never an order)."""

    __tablename__ = "scalp_signals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)   # NIFTY / BANKNIFTY / ...
    rule: Mapped[str] = mapped_column(String(24))                 # ORB / VWAP_RECLAIM / WALL_REJECT
    direction: Mapped[str] = mapped_column(String(4))             # CE / PE
    instrument: Mapped[str] = mapped_column(String(48))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScalpPaperTrade(Base):
    """Live paper trade — one row per real scalp signal, resolved on real quotes.

    Portfolio semantics mirror the COMBINED backtest: one paper account
    (base = the trading_capital setting), equity compounds on close, at most
    MAX_CONCURRENT open, unaffordable signals skipped. Exits prefer the real
    Kite bid at the moment SL/TP/time-stop triggers; falls back to the modeled
    spot-replay price when the broker session is down (exit_source says which).
    """

    __tablename__ = "scalp_paper_trades"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # A = baseline (every signal, go-live evidence); B = shadow portfolio with
    # the premium run-up gate (skips entries whose option already ran >15% in
    # the prior 15 min). Same signals, same exits — month-end A/B comparison.
    account: Mapped[str] = mapped_column(String(1), default="A", index=True)
    # % the option premium ran (window low → price) in the 15 min before entry;
    # logged on BOTH accounts when computable, None when Kite couldn't say
    runup_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    day: Mapped[str] = mapped_column(String(10), index=True)      # IST date
    symbol: Mapped[str] = mapped_column(String(24))
    rule: Mapped[str] = mapped_column(String(24))
    direction: Mapped[str] = mapped_column(String(4))
    instrument: Mapped[str] = mapped_column(String(48))
    expiry: Mapped[str | None] = mapped_column(String(16), nullable=True)
    spot_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_p: Mapped[float] = mapped_column(Float)                 # real quoted premium
    sl: Mapped[float] = mapped_column(Float)
    tp: Mapped[float] = mapped_column(Float)
    lot_size: Mapped[int] = mapped_column(Integer)
    lots: Mapped[int] = mapped_column(Integer)
    capital_used: Mapped[float] = mapped_column(Float)            # ep × lot × lots
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)  # open|closed|skipped
    exit_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_source: Mapped[str | None] = mapped_column(String(8), nullable=True)   # kite|modeled
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)       # SL|TP|TIME
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    charges: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)             # net, after charges
    equity_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentCall(Base):
    """Borrow #2 — one agent's directional proposal in one run, graded on resolution."""

    __tablename__ = "agent_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(24))
    agent: Mapped[str] = mapped_column(String(48))
    call: Mapped[str] = mapped_column(String(16))  # Buy/Overweight/Hold/Underweight/Sell
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None until graded
    alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MemoryEntry(Base):
    """Decision-log entry with pending → resolved lifecycle (F8)."""

    __tablename__ = "memory_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    trade_date: Mapped[str] = mapped_column(String(10))
    rating: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|resolved
    raw_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark: Mapped[str] = mapped_column(String(16), default="SPY")
    reflection: Mapped[str] = mapped_column(Text, default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
