"""Ticker normalization, asset-type detection, benchmark resolution (F3.1, F6.4).

Mirrors tradingagents.dataflows.symbol_utils and default_config.benchmark_map
so validation works without the engine installed; purely syntactic.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_CRYPTO_BASES = frozenset(
    {
        "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "DOT", "LTC", "LINK", "AVAX",
        "MATIC", "BNB", "UNI", "ATOM", "XLM", "TRX", "SHIB", "NEAR", "APT", "ARB",
    }
)
_CRYPTO_QUOTES = ("USDT", "USDC", "USD")

BENCHMARK_MAP = {
    ".NS": "^NSEI",     # NSE India (Nifty 50)
    ".BO": "^BSESN",    # BSE India (Sensex)
    ".T": "^N225",      # Tokyo (Nikkei 225)
    ".HK": "^HSI",      # Hong Kong (Hang Seng)
    ".L": "^FTSE",      # London (FTSE 100)
    ".TO": "^GSPTSE",   # Toronto (TSX)
    ".AX": "^AXJO",     # Australia (ASX 200)
    ".SS": "000001.SS", # Shanghai
    ".SZ": "399001.SZ", # Shenzhen
    "": "SPY",          # US default
}

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-\^]{0,15}$")
# path-traversal hardening (engine parity: safe_ticker_component)
_SAFE_COMPONENT_RE = re.compile(r"^[A-Z0-9.\-\^]+$")


def crypto_base(raw: str) -> str | None:
    s = raw.upper().replace("-", "")
    for quote in _CRYPTO_QUOTES:
        if s.endswith(quote):
            base = s[: -len(quote)]
            if base in _CRYPTO_BASES:
                return base
    return None


def normalize_ticker(raw: str) -> str:
    """BTCUSD/btc-usdt → BTC-USD; aapl → AAPL. Raises ValueError when invalid."""
    s = raw.strip().upper()
    if not s:
        raise ValueError("Ticker is required")
    base = crypto_base(s)
    if base:
        return f"{base}-USD"
    if not _TICKER_RE.match(s) or not _SAFE_COMPONENT_RE.match(s):
        raise ValueError(f"Invalid ticker symbol: {raw!r}")
    return s


def detect_asset_type(ticker: str) -> str:
    return "crypto" if crypto_base(ticker) else "stock"


def resolve_benchmark(ticker: str, override: str | None = None) -> str:
    if override:
        return override
    for suffix, bench in BENCHMARK_MAP.items():
        if suffix and ticker.endswith(suffix):
            return bench
    return BENCHMARK_MAP[""]


def filter_analysts_for_asset_type(analysts: list[str], asset_type: str) -> list[str]:
    """Fundamentals doesn't apply to crypto (engine parity)."""
    if asset_type == "crypto":
        return [a for a in analysts if a != "fundamentals"]
    return analysts


def validate_trade_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Date must be YYYY-MM-DD") from exc
    # C9: one day of slack for clients ahead of the server's timezone
    if parsed > date.today() + timedelta(days=1):
        raise ValueError("Analysis date cannot be in the future")
    if parsed.year < 2000:
        raise ValueError("Analysis date is too far in the past")
    return value
