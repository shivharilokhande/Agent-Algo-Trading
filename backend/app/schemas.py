"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


# ---------- Auth ----------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    is_admin: bool = False
    created_at: datetime


# ---------- Keys ----------

class KeyUpsert(BaseModel):
    provider: str
    secret: str = Field(default="", max_length=4096)
    extra: dict[str, str] = Field(default_factory=dict)  # base_url, region, etc.


class KeyOut(BaseModel):
    provider: str
    mask: str
    status: str
    tested_at: datetime | None
    extra: dict[str, str]


# ---------- Runs ----------

class RunCreate(BaseModel):
    ticker: str
    trade_date: str
    analysts: list[str] = Field(default=["market", "social", "news", "fundamentals"])
    research_depth: int = 1
    llm_provider: str = "demo"
    quick_think_llm: str = ""
    deep_think_llm: str = ""
    output_language: str = "English"
    temperature: float | None = None
    max_tokens: int | None = None
    llm_max_retries: int | None = None
    google_thinking_level: str | None = None
    openai_reasoning_effort: str | None = None
    anthropic_effort: str | None = None
    checkpoint_enabled: bool = True
    data_vendors: dict[str, str] = Field(default_factory=dict)
    mode: Literal["demo", "engine"] = "demo"  # S7: Literal closes the demo-gate bypass
    hitl: bool = False  # P3-A5: pause before the Portfolio Manager
    fno_mode: bool = False  # F&O Desk: produce an option trade plan after the decision


class RunOut(BaseModel):
    id: str
    ticker: str
    company_name: str
    asset_type: str
    trade_date: str
    mode: str
    status: str
    rating: str | None
    decision_summary: str
    error: str
    stats: dict[str, Any]
    benchmark: str
    config: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunEventOut(BaseModel):
    seq: int
    type: str
    agent: str
    payload: dict[str, Any]
    ts: datetime


class ReportOut(BaseModel):
    section: str
    content_md: str


# ---------- Memory ----------

class MemoryOut(BaseModel):
    id: str
    ticker: str
    trade_date: str
    rating: str
    summary: str
    status: str
    raw_return: float | None
    alpha: float | None
    benchmark: str
    reflection: str
    resolved_at: datetime | None
    created_at: datetime


# ---------- Presets / settings ----------

class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any]


class PresetOut(BaseModel):
    id: str
    name: str
    config: dict[str, Any]
    created_at: datetime


class SettingsIn(BaseModel):
    config: dict[str, Any]
