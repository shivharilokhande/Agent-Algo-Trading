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
