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
import math
import time
from datetime import datetime

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


# ---------- Black-Scholes Greeks (NSE publishes IV; Delta/Theta/Vega we compute) ----------

RISK_FREE_RATE = 0.07  # ~India 91-day T-bill; Greeks are insensitive to small changes


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_greeks(spot: float, strike: float, iv_pct: float, t_years: float,
              is_call: bool, r: float = RISK_FREE_RATE) -> dict:
    """Delta, per-day theta, and vega (per 1 IV point) for a European option."""
    if not (spot and strike and iv_pct and iv_pct > 0):
        return {"delta": None, "theta_day": None, "vega": None}
    t = max(t_years, 0.5 / 365)  # floor at half a day to avoid expiry-day blowups
    sigma = iv_pct / 100.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    delta = _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0
    # call: −S·φ(d1)·σ/(2√t) − r·K·e^(−rt)·N(d2);  put: … + r·K·e^(−rt)·N(−d2)
    # (R5-4: the rate term's sign was inverted for both legs)
    theta_year = (-(spot * _norm_pdf(d1) * sigma) / (2 * sqrt_t)
                  + (-1 if is_call else 1) * r * strike * math.exp(-r * t)
                  * _norm_cdf(d2 if is_call else -d2))
    vega = spot * _norm_pdf(d1) * sqrt_t / 100.0  # per 1 IV point
    return {"delta": round(delta, 3), "theta_day": round(theta_year / 365.0, 2),
            "vega": round(vega, 2)}


def years_to_expiry(expiry: str | None) -> float:
    """'08-Sep-2026' → year fraction from now (floored at half a day)."""
    if not expiry:
        return 7 / 365
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.strptime(expiry, "%d-%b-%Y")
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
        days = (dt - now_ist).total_seconds() / 86400 + 0.65  # expiry ~15:30 IST
        return max(days, 0.5) / 365
    except ValueError:
        return 7 / 365


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


WALL_BAND_PCT = 1.5   # walls must be within ±1.5% of spot to matter intraday


def pick_walls(strikes: list[dict], spot: float | None) -> tuple[list[dict], list[dict]]:
    """Top-3 CE-OI strikes AT/ABOVE spot (resistance) and top-3 PE-OI strikes
    AT/BELOW spot (support), each within ±WALL_BAND_PCT of spot.

    18-Sep fix: the old whole-chain top-OI pick returned BANKNIFTY walls of
    58000/57500 with spot 56330 (monthly far-OTM writing) and NIFTY
    resistance == support == 23300 (max-pain strike carrying both books) —
    neither is a level price can reject intraday. Falls back to the whole
    side, then the whole chain, when the band is empty (thin / test chains).
    """
    if not strikes:
        return [], []
    if not spot:
        return (sorted(strikes, key=lambda s: -s["ce_oi"])[:3],
                sorted(strikes, key=lambda s: -s["pe_oi"])[:3])
    band = spot * WALL_BAND_PCT / 100
    above = [s for s in strikes if s["strike"] >= spot]
    below = [s for s in strikes if s["strike"] <= spot]
    near_above = [s for s in above if s["strike"] - spot <= band] or above or strikes
    near_below = [s for s in below if spot - s["strike"] <= band] or below or strikes
    return (sorted(near_above, key=lambda s: -s["ce_oi"])[:3],
            sorted(near_below, key=lambda s: -s["pe_oi"])[:3])


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
            "ce_ltp": ce.get("lastPrice", 0), "pe_ltp": pe.get("lastPrice", 0),
        })
    strikes = [s for s in strikes if s["strike"]]
    total_ce = sum(s["ce_oi"] for s in strikes)
    total_pe = sum(s["pe_oi"] for s in strikes)
    pcr = round(total_pe / total_ce, 3) if total_ce else None
    resistance, support = pick_walls(strikes, spot)
    atm = min(strikes, key=lambda s: abs(s["strike"] - spot)) if (spot and strikes) else None
    # nearest-ATM ladder with live premiums + computed Greeks
    near_atm = sorted(strikes, key=lambda s: abs(s["strike"] - (spot or 0)))[:7]
    near_atm = sorted(near_atm, key=lambda s: s["strike"])
    t_years = years_to_expiry(expiry)
    ladder = []
    for s in near_atm:
        ce_g = bs_greeks(spot, s["strike"], s["ce_iv"], t_years, is_call=True)
        pe_g = bs_greeks(spot, s["strike"], s["pe_iv"], t_years, is_call=False)
        ladder.append({
            "strike": s["strike"], "ce_ltp": s["ce_ltp"], "pe_ltp": s["pe_ltp"],
            "ce_oi": s["ce_oi"], "pe_oi": s["pe_oi"], "ce_iv": s["ce_iv"], "pe_iv": s["pe_iv"],
            "ce_delta": ce_g["delta"], "pe_delta": pe_g["delta"],
            "ce_theta": ce_g["theta_day"], "pe_theta": pe_g["theta_day"],
            "ce_vega": ce_g["vega"], "pe_vega": pe_g["vega"],
        })
    atm_iv = ((atm["ce_iv"] or 0) + (atm["pe_iv"] or 0)) / 2 if atm else None
    return {
        "atm_ladder": ladder,
        "days_to_expiry": round(t_years * 365, 1),
        "iv_skew": round((atm["pe_iv"] or 0) - (atm["ce_iv"] or 0), 2) if atm else None,
        "atm_iv": round(atm_iv, 2) if atm_iv else None,
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
    if s.get("days_to_expiry") is not None:
        lines.append(f"- **Days to expiry:** {s['days_to_expiry']}")
    if s.get("iv_skew") is not None:
        skew = s["iv_skew"]
        skew_read = ("puts bid up — downside fear" if skew > 1.5
                     else "calls bid up — upside chase" if skew < -1.5 else "balanced")
        lines.append(f"- **IV skew (PE−CE at ATM):** {skew:+.2f} — {skew_read}")
    ladder = s.get("atm_ladder") or []
    if ladder:
        lines += ["", "| Strike | CE ₹ (Δ / θ/day / IV) | PE ₹ (Δ / θ/day / IV) | CE OI | PE OI |",
                  "|---|---|---|---|---|"]
        for row in ladder:
            ce = (f"₹{row['ce_ltp']} ({row.get('ce_delta')} / {row.get('ce_theta')} / {row['ce_iv']}%)")
            pe = (f"₹{row['pe_ltp']} ({row.get('pe_delta')} / {row.get('pe_theta')} / {row['pe_iv']}%)")
            lines.append(f"| {row['strike']} | {ce} | {pe} | {row['ce_oi']:,} | {row['pe_oi']:,} |")
    lines += [
        "",
        f"_Total OI: {s['total_pe_oi']:,} PE vs {s['total_ce_oi']:,} CE across "
        f"{s['strike_count']} strikes. Derivatives are leveraged instruments — "
        "this is research context, not trading advice._",
    ]
    return "\n".join(lines)


# ---------- live spot (lightweight, for level watching) ----------

_spot_cache: dict[str, tuple[float, float]] = {}
SPOT_TTL = 60  # seconds

_INDEX_NAMES = {  # NSE allIndices display names
    "NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FINANCIAL SERVICES", "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
}


def _live_spot_sync(fno_symbol: str) -> float:
    key = fno_symbol.upper()
    with httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
        client.get(NSE_HOME)
        if key in _INDEX_NAMES:
            r = client.get(f"{NSE_HOME}/api/allIndices")
            r.raise_for_status()
            for row in (r.json() or {}).get("data", []):
                if row.get("index") == _INDEX_NAMES[key]:
                    return float(row["last"])
            raise ValueError(f"{key} not in allIndices")
        r = client.get(f"{NSE_HOME}/api/quote-equity?symbol={key}")
        r.raise_for_status()
        return float(((r.json() or {}).get("priceInfo") or {}).get("lastPrice"))


async def get_live_spot(fno_symbol: str) -> float:
    """Live spot: Zerodha Kite when a session is connected (real-time, uncached),
    else the NSE endpoint (≈1 min cache)."""
    key = fno_symbol.upper()
    try:  # broker feed first — best freshness for Level Watch / scalp premiums
        from .kite_data import kite_spot

        spot = await asyncio.to_thread(kite_spot, key)
        if spot:
            _spot_cache[key] = (time.time(), spot)
            return spot
    except Exception:  # noqa: BLE001 — never let the broker path break the free path
        pass
    now = time.time()
    if key in _spot_cache and now - _spot_cache[key][0] < SPOT_TTL:
        return _spot_cache[key][1]
    spot = await asyncio.to_thread(_live_spot_sync, key)
    _spot_cache[key] = (now, spot)
    return spot


def is_market_hours_ist(now=None) -> bool:
    """NSE cash/F&O session: Mon–Fri 09:15–15:30 IST."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ist = now or datetime.now(ZoneInfo("Asia/Kolkata"))
    if ist.weekday() >= 5:
        return False
    minutes = ist.hour * 60 + ist.minute
    return (9 * 60 + 15) <= minutes <= (15 * 60 + 30)


def levels_from_card(card: dict) -> list[dict]:
    """Extract watchable trigger levels from a trade card's conditional rows."""
    import re as _re

    out = []
    for row in card.get("rows", []):
        cond = str(row.get("condition", ""))
        below = _re.search(r"(?:below|under|breaks?\s+down\s+(?:past|to))\s+(?:the\s+)?([\d,]{4,})", cond, _re.I)
        above = _re.search(r"(?:above|through|reclaims?|crosses)\s+(?:the\s+)?([\d,]{4,})", cond, _re.I)
        m = below or above
        if not m:
            continue
        out.append({
            "direction": "below" if below else "above",
            "level": float(m.group(1).replace(",", "")),
            "instrument": row.get("instrument", ""),
            "ep": row.get("ep"),
            "sl": row.get("sl"),
            "tp1": row.get("tp1"),
            "primary": bool(row.get("primary")),
        })
    return out


def fno_symbol_for(ticker: str) -> str | None:
    """Map an AgentAlgo ticker to its NSE F&O chain symbol, if derivatives exist."""
    t = ticker.upper()
    index_map = {
        "^NSEI": "NIFTY", "NIFTY": "NIFTY",
        "^NSEBANK": "BANKNIFTY", "BANKNIFTY": "BANKNIFTY",
        "NIFTY_FIN_SERVICE.NS": "FINNIFTY", "FINNIFTY": "FINNIFTY",
        "MIDCPNIFTY": "MIDCPNIFTY",
    }
    if t in index_map:
        return index_map[t]
    if t.endswith(".NS"):
        return t[:-3]  # stock derivatives chain uses the plain NSE symbol
    return None


# ---------- trade card: the one simple table (structured, UI-rendered) ----------

def _ladder_leg(ladder: list[dict], strike: float) -> dict | None:
    for r in ladder:
        if r["strike"] == strike:
            return r
    return None


def _nearest_strike(ladder: list[dict], target: float) -> float | None:
    if not ladder:
        return None
    return min((r["strike"] for r in ladder), key=lambda k: abs(k - target))


def _card_row(scenario: str, condition: str, instrument: str, ep: float,
              note: str, primary: bool, sl_spot: str) -> dict:
    sl = round(ep * 0.60, 2)
    tp1 = round(ep * 1.60, 2)
    tp2 = round(ep * 2.20, 2)
    risk = ep - sl
    return {
        "scenario": scenario, "condition": condition, "instrument": instrument,
        "ep": round(ep, 2), "sl": sl, "sl_spot": sl_spot, "tp1": tp1, "tp2": tp2,
        "rr1": round((tp1 - ep) / risk, 1) if risk else None,
        "rr2": round((tp2 - ep) / risk, 1) if risk else None,
        "primary": primary, "note": note,
    }


def build_trade_card(snap: dict, rating: str, ticker: str) -> dict:
    """Structured card: verdict + at-most-two plain rows. The UI renders this
    as the simple table; the prose plan stays in its own tab for the why."""
    spot = snap.get("spot")
    ladder = snap.get("atm_ladder") or []
    symbol = snap.get("symbol", ticker)
    expiry = snap.get("expiry")
    bullish = rating in ("Buy", "Overweight")
    bearish = rating in ("Sell", "Underweight")
    card: dict = {"symbol": symbol, "expiry": expiry, "spot": spot, "rating": rating,
                  "rows": [], "generated_note": ""}
    if not (spot and ladder):
        card["verdict"] = "NO DATA"
        return card

    step = round(min(abs(ladder[i + 1]["strike"] - ladder[i]["strike"])
                     for i in range(len(ladder) - 1))) if len(ladder) > 1 else 50
    support = [s["strike"] for s in (snap.get("support_strikes") or [])][:2]
    resistance = [s["strike"] for s in (snap.get("resistance_strikes") or [])][:2]
    # bear trigger: below BOTH nearby put walls; bull trigger: reclaim of the
    # single heaviest call wall plus one strike step (the report's 24,000→24,050 logic)
    bear_trigger = min(support) if support else spot - 2 * step
    bull_trigger = (resistance[0] if resistance else spot) + step

    def estimate_ep(strike: float, side: str, trigger: float) -> float | None:
        leg = _ladder_leg(ladder, strike)
        if leg is None:
            return None
        ltp = leg["ce_ltp" if side == "CE" else "pe_ltp"]
        delta = leg.get("ce_delta" if side == "CE" else "pe_delta") or 0.35
        if not ltp:
            return None
        return max(ltp + abs(delta) * abs(trigger - spot), ltp)

    if bullish or bearish:
        # directional verdict → trade in that direction now, on strength
        target_delta = 0.40
        dkey, pkey = ("ce_delta", "ce_ltp") if bullish else ("pe_delta", "pe_ltp")
        candidates = [r for r in ladder if (r.get(pkey) or 0) > 0 and r.get(dkey) is not None]
        leg = min(candidates, key=lambda r: abs(abs(r[dkey]) - target_delta), default=None)
        if leg:
            side = "CE" if bullish else "PE"
            wall = support[0] if bullish and support else (resistance[0] if resistance else None)
            card["verdict"] = "TRADE"
            card["rows"].append(_card_row(
                "NOW", f"Pipeline is {rating} — enter on strength, not into a fade",
                f"{symbol} {leg['strike']} {side}", leg[pkey],
                f"Δ {leg[dkey]} · IV {leg['ce_iv' if bullish else 'pe_iv']}%", True,
                f"close {'below' if bullish else 'above'} {wall}" if wall else "—",
            ))
        else:
            card["verdict"] = "NO TRADE NOW"
        return card

    # Hold / REVIEW → the two-trigger bracket
    card["verdict"] = "NO TRADE NOW"
    card["generated_note"] = (
        f"{symbol} is boxed between the {min(support) if support else '—'} put wall and the "
        f"{max(resistance) if resistance else '—'} call wall. Take AT MOST ONE of the rows "
        "below — whichever level breaks first on a closing basis. Until then, sit out."
    )
    pe_strike = _nearest_strike(ladder, bear_trigger)
    ce_strike = _nearest_strike(ladder, bull_trigger)
    if pe_strike:
        ep = estimate_ep(pe_strike, "PE", bear_trigger)
        if ep:
            card["rows"].append(_card_row(
                "IF BREAKS DOWN", f"Closes below {bear_trigger}",
                f"{symbol} {pe_strike} PE", ep,
                "Primary — goes with the trend; puts are cheaper (IV)", True,
                f"close back above {support[0] if support else bear_trigger + step}",
            ))
    if ce_strike:
        ep = estimate_ep(ce_strike, "CE", bull_trigger)
        if ep:
            card["rows"].append(_card_row(
                "IF BREAKS UP", f"Closes above {bull_trigger}",
                f"{symbol} {ce_strike} CE", ep,
                "Counter-trend — defensive branch", False,
                f"close back below {resistance[0] if resistance else bull_trigger - step}",
            ))
    return card


# ---------- position sizing (fixed-fractional, ScalpX/ai-fund inspired) ----------

# NSE F&O lot sizes — REVISED PERIODICALLY BY NSE; users must verify with their
# broker (surfaced in the UI). Override via settings key `fno_lot_sizes`.
# NSE circular NSE/FAOP/70616 — revised lots effective the Jan-2026 contract series.
DEFAULT_LOT_SIZES = {
    "NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120, "NIFTYNXT50": 25,
}
DEFAULT_CAPITAL = 100_000.0
DEFAULT_RISK_PCT = 1.0        # % of capital risked per trade (entry→SL)
MAX_OUTLAY_PCT = 30.0         # ScalpX-style allocation cap on total premium


def size_position(ep: float, sl: float, lot_size: int | None,
                  capital: float, risk_pct: float) -> dict:
    """Fixed-fractional sizing: lots = risk budget ÷ risk per lot, capped by outlay."""
    risk_budget = round(capital * risk_pct / 100, 2)
    per_unit_risk = max(ep - sl, 0.01)
    out: dict = {"capital": capital, "risk_pct": risk_pct, "risk_budget": risk_budget}
    if not lot_size:
        out.update({"lots": None,
                    "note": f"risk ₹{per_unit_risk:.2f}/unit — set the lot size to get lot count"})
        return out
    risk_per_lot = round(per_unit_risk * lot_size, 2)
    lots = int(risk_budget // risk_per_lot) if risk_per_lot else 0
    # outlay cap: never deploy more than MAX_OUTLAY_PCT of capital in premium
    max_outlay = capital * MAX_OUTLAY_PCT / 100
    while lots > 0 and ep * lot_size * lots > max_outlay:
        lots -= 1
    out.update({
        "lot_size": lot_size,
        "lots": lots,
        "risk_per_lot": risk_per_lot,
        "max_loss": round(risk_per_lot * lots, 2),
        "premium_outlay": round(ep * lot_size * lots, 2),
        "outlay_pct": round(ep * lot_size * lots / capital * 100, 1) if capital else None,
    })
    if lots == 0:
        out["note"] = (f"risk/lot ₹{risk_per_lot:,.0f} exceeds the ₹{risk_budget:,.0f} "
                       "budget — 0 lots; raise risk % or skip")
    return out


def enrich_card_sizing(card: dict, settings_cfg: dict) -> dict:
    """Attach a sizing block to every card row from the user's capital settings."""
    capital = float(settings_cfg.get("trading_capital") or DEFAULT_CAPITAL)
    risk_pct = float(settings_cfg.get("risk_per_trade_pct") or DEFAULT_RISK_PCT)
    overrides = settings_cfg.get("fno_lot_sizes") or {}
    symbol = str(card.get("symbol", "")).upper()
    lot = overrides.get(symbol) or DEFAULT_LOT_SIZES.get(symbol)
    for row in card.get("rows", []):
        ep, sl = row.get("ep"), row.get("sl")
        if ep and sl and ep > sl:
            sizing = size_position(float(ep), float(sl), lot, capital, risk_pct)
            # ₹ profit at each target for the sized position (long option: (target−entry)×lot×lots)
            lots = sizing.get("lots") or 0
            if lots and lot:
                for key, tgt in (("profit_t1", row.get("tp1")), ("profit_t2", row.get("tp2"))):
                    if tgt and float(tgt) > float(ep):
                        sizing[key] = round((float(tgt) - float(ep)) * lot * lots, 2)
            row["sizing"] = sizing
    card["sizing_basis"] = {"capital": capital, "risk_pct": risk_pct, "lot_size": lot,
                            "lot_note": "verify current NSE lot size with your broker"}
    return card


def sanitize_card_rows(card: dict, snap: dict) -> dict:
    """Make conditional rows honest: a row that triggers at a different spot level
    must quote the DELTA-ADJUSTED premium at that level, never today's LTP.

    Parses the trigger level from the condition text, finds the instrument's
    strike/side in the ladder, recomputes ep = ltp + |Δ|·|trigger − spot| and
    rescales SL/TP/R:R accordingly. Rows it cannot parse are left untouched.
    """
    import re as _re

    spot = snap.get("spot")
    ladder = snap.get("atm_ladder") or []
    if not (spot and ladder):
        return card
    for row in card.get("rows", []):
        if not str(row.get("scenario", "")).upper().startswith("IF"):
            continue  # 'NOW' rows quote live prices — correct as-is
        m = _re.search(r"(\d[\d,]{2,})", str(row.get("condition", "")))
        inst = _re.search(r"(\d[\d,]{2,})\s*(CE|PE)", str(row.get("instrument", "")))
        if not (m and inst):
            continue
        trigger = float(m.group(1).replace(",", ""))
        strike = float(inst.group(1).replace(",", ""))
        side = inst.group(2)
        leg = next((r for r in ladder if r["strike"] == strike), None)
        if leg is None:
            continue
        ltp = leg["ce_ltp" if side == "CE" else "pe_ltp"]
        delta = abs(leg.get("ce_delta" if side == "CE" else "pe_delta") or 0.35)
        if not ltp:
            continue
        ep = round(ltp + delta * abs(trigger - spot), 2)
        row["ep"] = ep
        row["sl"] = round(ep * 0.60, 2)
        row["tp1"] = round(ep * 1.60, 2)
        row["tp2"] = round(ep * 2.20, 2)
        row["rr1"], row["rr2"] = 1.5, 3.0
        note = str(row.get("note", ""))
        row["note"] = (f"₹{ltp} today at spot {spot} → est. ₹{ep} at trigger (Δ {delta}). " + note)[:200]
    return card


_CARD_JSON_RE = None  # compiled lazily


def extract_card_json(text: str) -> tuple[str, dict | None]:
    """Split an LLM trade plan into (markdown, structured card).

    The engine prompt asks for a fenced ```json card at the END of the plan.
    Returns the plan with the JSON block removed, plus the parsed card (or None).
    """
    import json as _json
    import re as _re

    global _CARD_JSON_RE
    if _CARD_JSON_RE is None:
        _CARD_JSON_RE = _re.compile(r"```json\s*(\{.*?\})\s*```", _re.DOTALL)
    matches = list(_CARD_JSON_RE.finditer(text))
    if not matches:
        return text, None
    m = matches[-1]
    try:
        card = _json.loads(m.group(1))
    except _json.JSONDecodeError:
        return text, None
    if not isinstance(card, dict) or card.get("verdict") not in ("TRADE", "NO TRADE NOW"):
        return text, None
    rows = card.get("rows")
    card["rows"] = [
        r for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and r.get("instrument") and r.get("ep") is not None
    ][:3]
    clean = (text[:m.start()] + text[m.end():]).strip()
    return clean, card


# ---------- option trade plan (deterministic; engine mode refines via LLM) ----------

def build_trade_plan_md(snap: dict, rating: str, ticker: str) -> str:
    """Translate the pipeline's rating + live chain into a concrete option plan.

    Heuristics (documented in the output): first-OTM strike for direction,
    premium stop at −40%, targets at +60% / +120% of premium, spot-level
    invalidation at the opposing OI wall.
    """
    spot = snap.get("spot")
    ladder = snap.get("atm_ladder") or []
    expiry = snap.get("expiry")
    bullish = rating in ("Buy", "Overweight")
    bearish = rating in ("Sell", "Underweight")
    header = f"## Option Trade Plan — {snap.get('symbol', ticker)} (expiry {expiry})\n"
    disclaimer = (
        "\n\n---\n_Derived from the agent pipeline's final rating plus live NSE chain data. "
        "Options are leveraged and can go to zero — position-size for a 100% premium loss, "
        "confirm the current NSE lot size, and treat this as research, not advice._"
    )

    if not (spot and ladder):
        return header + "\nInsufficient chain data for a trade plan." + disclaimer

    if not (bullish or bearish):
        support = (snap.get("support_strikes") or [{}])[0].get("strike")
        resistance = (snap.get("resistance_strikes") or [{}])[0].get("strike")
        return (
            header
            + f"\n**Pipeline verdict: {rating} — no directional edge.**\n\n"
            f"Spot {spot} sits between the {support} put wall and the {resistance} call wall "
            f"(max pain {snap.get('max_pain')}). Buying premium here fights time decay without "
            "a directional thesis; the plan is **no trade**. Re-run after a close beyond either "
            "OI wall. (Range-selling structures exist for this regime but are for experienced, "
            "margin-aware traders only.)" + disclaimer
        )

    # Delta-targeted strike selection: for long options the 0.35–0.55 |Δ| band
    # balances directional exposure against theta bleed. Pick the strike whose
    # |Δ| is closest to 0.40 on the pipeline's side.
    TARGET_DELTA = 0.40
    if bullish:
        side, dkey, pkey, ivkey, tkey = "CE", "ce_delta", "ce_ltp", "ce_iv", "ce_theta"
        wall = (snap.get("support_strikes") or [{}])[0].get("strike")
        invalidation = f"a close below the {wall} put wall"
    else:
        side, dkey, pkey, ivkey, tkey = "PE", "pe_delta", "pe_ltp", "pe_iv", "pe_theta"
        wall = (snap.get("resistance_strikes") or [{}])[0].get("strike")
        invalidation = f"a close above the {wall} call wall"

    candidates = [r for r in ladder if (r.get(pkey) or 0) > 0 and r.get(dkey) is not None]
    scored = sorted(candidates, key=lambda r: abs(abs(r[dkey]) - TARGET_DELTA))
    leg = scored[0] if scored else None
    if leg is None:
        # fall back to first-OTM when Greeks are unavailable (missing IV on chain)
        fallback = ([r for r in ladder if r["strike"] >= spot and r["ce_ltp"] > 0] if bullish
                    else [r for r in reversed(ladder) if r["strike"] <= spot and r["pe_ltp"] > 0])
        leg = fallback[0] if fallback else None
    if leg is None or not leg.get(pkey):
        return header + "\nNo liquid near-ATM strike found for the direction." + disclaimer

    strike, premium = leg["strike"], leg[pkey]
    delta, iv, theta = leg.get(dkey), leg.get(ivkey), leg.get(tkey)
    sl = round(premium * 0.60, 2)
    t1 = round(premium * 1.60, 2)
    t2 = round(premium * 2.20, 2)
    risk = round(premium - sl, 2)
    rr1 = round((t1 - premium) / risk, 2) if risk else None
    rr2 = round((t2 - premium) / risk, 2) if risk else None
    breakeven = round(strike + premium, 2) if side == "CE" else round(strike - premium, 2)
    move_t1 = round((t1 - premium) / abs(delta), 1) if delta else None

    # IV richness vs India VIX: paying 30%+ over VIX means the move must be fast
    vix = snap.get("india_vix")
    iv_note = ""
    if iv and vix:
        ratio = iv / vix
        if ratio >= 1.3:
            iv_note = (f"⚠️ IV {iv}% is rich vs India VIX {vix} ({ratio:.1f}×) — premium is "
                       "expensive; consider a debit spread instead of a naked long option.")
        elif ratio <= 0.9:
            iv_note = f"IV {iv}% is cheap vs India VIX {vix} — favorable premium buying conditions."
        else:
            iv_note = f"IV {iv}% is fair vs India VIX {vix}."

    return (
        header
        + f"\n**Pipeline verdict: {rating} → {'bullish' if bullish else 'bearish'} — "
        f"buy the {strike} {side}** (selected for Δ ≈ {TARGET_DELTA}: best "
        "exposure-per-theta among near-ATM strikes).\n\n"
        f"| Parameter | Level |\n|---|---|\n"
        f"| Instrument | {snap.get('symbol', ticker)} {strike} {side}, expiry {expiry} |\n"
        f"| Entry (last traded premium) | **₹{premium}** |\n"
        f"| Δ (delta) | {delta} — gains ≈ ₹{abs(delta or 0):.2f} per point of spot move |\n"
        f"| θ (theta/day) | ₹{theta} — daily decay cost held flat |\n"
        f"| IV | {iv}% |\n"
        f"| Breakeven at expiry | {breakeven} |\n"
        f"| Stop loss (premium) | ₹{sl} (−40%) |\n"
        f"| Target 1 | ₹{t1} (+60%) — R:R **1:{rr1}**"
        + (f" — needs ≈ {move_t1} pts of spot move |" if move_t1 else " |") + "\n"
        f"| Target 2 | ₹{t2} (+120%) — R:R **1:{rr2}** |\n"
        f"| Spot reference | {spot} · max pain {snap.get('max_pain')} |\n"
        f"| Invalidation (spot) | Exit on {invalidation} |\n\n"
        + (iv_note + "\n\n" if iv_note else "")
        + "Execution notes: enter on strength in the pipeline's direction, not into a fade; "
        "book half at Target 1 and trail the rest; theta accelerates into expiry — avoid "
        "holding a losing long option overnight in the final week."
        + disclaimer
    )
