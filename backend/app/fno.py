"""NSE F&O intelligence: option-chain analytics for indices and stock derivatives.

Free data straight from nseindia.com (session-warmed requests) plus India VIX
via yfinance. Produces a compact markdown snapshot — PCR, max pain, OI-implied
support/resistance, ATM IV, futures basis — that is injected into analyses
(all agents see it) and stored as the run's `derivatives_report` section.

Analysis only. AgentAlgo never places orders, and derivatives are leveraged
instruments — nothing here is trading advice.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger("agentalgo.fno")

NSE_HOME = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}

# Index option-chain symbols on NSE
INDEX_FNO = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 600  # 10 min


def compute_max_pain(strikes: list[dict]) -> float | None:
    """Strike with minimum total option-writer payout (classic max pain)."""
    if not strikes:
        return None
    points = [(s["strike"], s.get("ce_oi", 0), s.get("pe_oi", 0)) for s in strikes]
    best_strike, best_pain = None, None
    for candidate, _, _ in points:
        pain = 0.0
        for k, ce_oi, pe_oi in points:
            pain += ce_oi * max(candidate - k, 0) + pe_oi * max(k - candidate, 0)
        if best_pain is None or pain < best_pain:
            best_strike, best_pain = candidate, pain
    return best_strike


def analyze_chain(records: dict, symbol: str) -> dict:
    """Reduce NSE option-chain JSON to decision-relevant analytics.

    Handles both the legacy shape (multi-expiry rows keyed ``expiryDate``) and
    the 2026 v3 shape (single-expiry rows keyed ``expiryDates``).
    """
    spot = records.get("underlyingValue")
    expiries = records.get("expiryDates") or []
    expiry = expiries[0] if expiries else None
    strikes: list[dict] = []
    for row in records.get("data", []):
        row_expiry = row.get("expiryDate") or row.get("expiryDates")
        if expiry and row_expiry and row_expiry != expiry:
            continue
        ce, pe = row.get("CE") or {}, row.get("PE") or {}
        strikes.append({
            "strike": row.get("strikePrice"),
            "ce_oi": ce.get("openInterest", 0), "pe_oi": pe.get("openInterest", 0),
            "ce_chg": ce.get("changeinOpenInterest", 0), "pe_chg": pe.get("changeinOpenInterest", 0),
            "ce_iv": ce.get("impliedVolatility", 0), "pe_iv": pe.get("impliedVolatility", 0),
        })
    strikes = [s for s in strikes if s["strike"]]
    total_ce = sum(s["ce_oi"] for s in strikes)
    total_pe = sum(s["pe_oi"] for s in strikes)
    pcr = round(total_pe / total_ce, 3) if total_ce else None
    resistance = sorted(strikes, key=lambda s: -s["ce_oi"])[:3]
    support = sorted(strikes, key=lambda s: -s["pe_oi"])[:3]
    atm = min(strikes, key=lambda s: abs(s["strike"] - spot)) if (spot and strikes) else None
    return {
        "symbol": symbol,
        "spot": spot,
        "expiry": expiry,
        "pcr": pcr,
        "total_ce_oi": total_ce,
        "total_pe_oi": total_pe,
        "max_pain": compute_max_pain(strikes),
        "resistance_strikes": [{"strike": s["strike"], "ce_oi": s["ce_oi"]} for s in resistance],
        "support_strikes": [{"strike": s["strike"], "pe_oi": s["pe_oi"]} for s in support],
        "atm_iv_ce": atm["ce_iv"] if atm else None,
        "atm_iv_pe": atm["pe_iv"] if atm else None,
        "strike_count": len(strikes),
    }


def _fetch_chain_sync(symbol: str, is_index: bool) -> dict:
    """NSE option-chain v3 (2026 API): contract-info for expiries, then the chain."""
    kind = "Indices" if is_index else "Equity"
    with httpx.Client(headers=_HEADERS, timeout=25, follow_redirects=True) as client:
        client.get(NSE_HOME)  # session warm-up: NSE requires cookies
        client.get(f"{NSE_HOME}/option-chain")
        info = client.get(f"{NSE_HOME}/api/option-chain-contract-info?symbol={symbol}")
        info.raise_for_status()
        expiries = (info.json() or {}).get("expiryDates") or []
        if not expiries:
            raise ValueError(f"No F&O expiries listed for {symbol}")
        expiry = expiries[0]
        r = client.get(
            f"{NSE_HOME}/api/option-chain-v3?type={kind}&symbol={symbol}&expiry={expiry}"
        )
        r.raise_for_status()
        records = (r.json() or {}).get("records") or {}
        records.setdefault("expiryDates", [expiry])
    return records


def _india_vix_sync() -> float | None:
    try:
        import yfinance as yf

        h = yf.Ticker("^INDIAVIX").history(period="5d")["Close"].dropna()
        return round(float(h.iloc[-1]), 2) if len(h) else None
    except Exception:  # noqa: BLE001
        return None


async def get_fno_snapshot(fno_symbol: str) -> dict:
    """Cached derivatives snapshot for an NSE index or stock symbol."""
    key = fno_symbol.upper()
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]
    is_index = key in INDEX_FNO
    records = await asyncio.to_thread(_fetch_chain_sync, key, is_index)
    snapshot = analyze_chain(records, key)
    if is_index:
        snapshot["india_vix"] = await asyncio.to_thread(_india_vix_sync)
    _cache[key] = (now, snapshot)
    return snapshot


def snapshot_to_md(s: dict) -> str:
    """Markdown block for reports / agent context."""
    res = ", ".join(f"{x['strike']} ({x['ce_oi']:,} CE OI)" for x in s["resistance_strikes"])
    sup = ", ".join(f"{x['strike']} ({x['pe_oi']:,} PE OI)" for x in s["support_strikes"])
    pcr = s.get("pcr")
    pcr_read = ("bullish-leaning (put writers confident)" if pcr and pcr > 1.2
                else "bearish-leaning (call writers dominate)" if pcr and pcr < 0.8
                else "neutral")
    vix = s.get("india_vix")
    lines = [
        f"### NSE F&O Snapshot — {s['symbol']} (nearest expiry {s['expiry']})",
        "",
        f"- **Spot:** {s['spot']}",
        f"- **Put/Call Ratio (OI):** {pcr} — {pcr_read}",
        f"- **Max Pain:** {s['max_pain']}",
        f"- **OI resistance (top CE):** {res}",
        f"- **OI support (top PE):** {sup}",
        f"- **ATM IV:** CE {s['atm_iv_ce']}% / PE {s['atm_iv_pe']}%",
    ]
    if vix is not None:
        lines.append(f"- **India VIX:** {vix}")
    lines += [
        "",
        f"_Total OI: {s['total_pe_oi']:,} PE vs {s['total_ce_oi']:,} CE across "
        f"{s['strike_count']} strikes. Derivatives are leveraged instruments — "
        "this is research context, not trading advice._",
    ]
    return "\n".join(lines)


def fno_symbol_for(ticker: str) -> str | None:
    """Map an AgentAlgo ticker to its NSE F&O chain symbol, if derivatives exist."""
    t = ticker.upper()
    index_map = {
        "^NSEI": "NIFTY", "NIFTY": "NIFTY",
        "^NSEBANK": "BANKNIFTY", "BANKNIFTY": "BANKNIFTY",
        "FINNIFTY": "FINNIFTY", "MIDCPNIFTY": "MIDCPNIFTY",
    }
    if t in index_map:
        return index_map[t]
    if t.endswith(".NS"):
        return t[:-3]  # stock derivatives chain uses the plain NSE symbol
    return None
