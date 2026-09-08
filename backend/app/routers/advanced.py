"""P4 — ensembles & scoreboard (A3), triggers (A6), Agent Studio (A1), library (A4)."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import library
from ..db import get_db
from ..models import AgentProfile, Document, Ensemble, MemoryEntry, Run, Trigger, User
from ..security import get_current_user
from ..tickers import normalize_ticker

router = APIRouter(prefix="/api", tags=["advanced"])


# ==================== A3: ensembles ====================

class StackIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    mode: str = Field(default="demo", pattern="^(demo|engine)$")
    llm_provider: str = "demo"
    quick_think_llm: str = ""
    deep_think_llm: str = ""


class EnsembleIn(BaseModel):
    ticker: str
    trade_date: str
    research_depth: int = 1
    stacks: list[StackIn] = Field(min_length=2, max_length=3)


@router.post("/ensembles", status_code=201)
async def create_ensemble(
    body: EnsembleIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from ..services import RunValidationError, create_run_for_user

    run_ids = []
    for i, stack in enumerate(body.stacks):
        cfg = {
            "ticker": body.ticker, "trade_date": body.trade_date,
            "research_depth": body.research_depth, "mode": stack.mode,
            "llm_provider": stack.llm_provider, "quick_think_llm": stack.quick_think_llm,
            "deep_think_llm": stack.deep_think_llm,
            "_stack_tag": f"{i}:{stack.label}", "_stack_label": stack.label,
        }
        try:
            run = await create_run_for_user(db, user.id, cfg)
        except RunValidationError as exc:
            # R5-12: don't leave earlier stacks' runs orphaned on partial failure
            if run_ids:
                from ..services import detach_run_references

                for rid in run_ids:
                    orphan = db.get(Run, rid)
                    if orphan is not None and orphan.status == "queued":
                        detach_run_references(db, rid)
                        db.delete(orphan)
                db.commit()
            raise HTTPException(status_code=422, detail=f"Stack '{stack.label}': {exc}") from exc
        run_ids.append(run.id)
    ens = Ensemble(
        user_id=user.id, ticker=normalize_ticker(body.ticker),
        trade_date=body.trade_date, run_ids_json=json.dumps(run_ids),
    )
    db.add(ens)
    db.commit()
    return _ensemble_out(db, ens)


def _meta_judge(runs: list[Run]) -> dict:
    """Deterministic arbitration: agreement level + dissent surfacing."""
    ratings = [r.rating for r in runs if r.rating]
    if not ratings or len(ratings) < len(runs):  # R3-1: empty/partial sets never judge
        return {"status": "pending"}
    distinct = set(ratings)
    if len(distinct) == 1:
        verdict, headline = "unanimous", f"All {len(runs)} stacks agree: {ratings[0]}."
    else:
        counts = {x: ratings.count(x) for x in distinct}
        top = max(counts, key=counts.get)  # type: ignore[arg-type]
        if counts[top] > len(ratings) / 2:
            verdict = "majority"
            headline = f"Majority {top} ({counts[top]}/{len(ratings)}); dissent: " + ", ".join(
                r for r in distinct if r != top)
        else:
            verdict, headline = "split", "No majority — stacks disagree: " + " vs ".join(sorted(distinct))
    bullish = sum(1 for x in ratings if x in ("Buy", "Overweight"))
    bearish = sum(1 for x in ratings if x in ("Sell", "Underweight"))
    lean = "bullish" if bullish > bearish else "bearish" if bearish > bullish else "neutral"
    per_stack = [
        {"run_id": r.id, "label": json.loads(r.config_json).get("_stack_label", r.mode),
         "rating": r.rating, "summary": r.decision_summary[:200]}
        for r in runs
    ]
    return {"status": "done", "verdict": verdict, "headline": headline,
            "lean": lean, "ratings": ratings, "stacks": per_stack}


def _ensemble_out(db: Session, ens: Ensemble) -> dict:
    run_ids = json.loads(ens.run_ids_json)
    runs = {r.id: r for r in db.query(Run).filter(Run.id.in_(run_ids)).all()}
    ordered = [runs[i] for i in run_ids if i in runs]
    # R3-1: a consensus needs the FULL stack set — deleted member runs disqualify it
    all_done = bool(ordered) and len(ordered) == len(run_ids) and all(
        r.status == "done" for r in ordered
    )
    consensus = json.loads(ens.consensus_json) if ens.consensus_json else None
    if all_done and not consensus:
        consensus = _meta_judge(ordered)
        ens.consensus_json = json.dumps(consensus)
        db.commit()
    return {
        "id": ens.id, "ticker": ens.ticker, "trade_date": ens.trade_date,
        "created_at": ens.created_at, "consensus": consensus,
        "runs": [
            {"id": r.id, "status": r.status, "rating": r.rating,
             "label": json.loads(r.config_json).get("_stack_label", r.mode),
             "stats": json.loads(r.stats_json or "{}")}
            for r in ordered
        ],
    }


@router.get("/ensembles")
def list_ensembles(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = (db.query(Ensemble).filter(Ensemble.user_id == user.id)
            .order_by(Ensemble.created_at.desc()).limit(50).all())
    return [_ensemble_out(db, e) for e in rows]


@router.get("/ensembles/{ensemble_id}")
def get_ensemble(
    ensemble_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    ens = db.get(Ensemble, ensemble_id)
    if ens is None or ens.user_id != user.id:
        raise HTTPException(status_code=404, detail="Ensemble not found")
    return _ensemble_out(db, ens)


@router.get("/desk-review")
def get_desk_review(user: Annotated[User, Depends(get_current_user)]):
    """Borrow #2 — per-agent KPI table (hit rate vs resolved outcomes)."""
    from ..desk import desk_review

    return desk_review(user.id)


@router.get("/briefings/{ticker}")
def get_briefing(
    ticker: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from ..models import Briefing

    try:
        symbol = normalize_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = (db.query(Briefing)
           .filter(Briefing.user_id == user.id, Briefing.ticker == symbol)
           .one_or_none())
    return {"ticker": symbol, "content": row.content if row else "",
            "updated_at": row.updated_at if row else None}


# ==================== Zerodha Kite (data only — never orders) ====================

class KiteSessionIn(BaseModel):
    request_token: str = Field(min_length=8, max_length=128)


@router.get("/kite/login-url")
def kite_login_url(user: Annotated[User, Depends(get_current_user)]):
    from .. import kite_data

    url = kite_data.login_url(user.id)
    if url is None:
        raise HTTPException(status_code=400,
                            detail="Add your Kite credential first (Settings → API Keys → "
                                   "Zerodha Kite: secret = API secret, api_key field = API key)")
    return {"login_url": url,
            "hint": ("Log in, get redirected to your app's redirect URL, copy the "
                     "request_token query parameter and paste it here. Needed once per day.")}


@router.post("/kite/session")
async def kite_session(
    body: KiteSessionIn,
    user: Annotated[User, Depends(get_current_user)],
):
    import asyncio

    from .. import kite_data

    try:
        return await asyncio.to_thread(kite_data.exchange_session, user.id, body.request_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Kite session exchange failed: {exc}") from exc


@router.get("/kite/callback")
async def kite_callback(request_token: str = "", state: str = "", status: str = ""):
    """Zerodha redirect target (set this as the app's Redirect URL):
    completes the daily session automatically, then bounces to Settings.

    Unauthenticated by necessity (the browser arrives from Zerodha) — the
    signed short-lived `state` from our own login URL identifies the user.
    """
    import asyncio
    from urllib.parse import quote

    from fastapi.responses import RedirectResponse

    from .. import kite_data

    def bounce(result: str, detail: str = "") -> RedirectResponse:
        q = f"?kite={result}" + (f"&detail={quote(detail[:200])}" if detail else "")
        return RedirectResponse(url=f"/settings{q}", status_code=303)

    uid = kite_data.verify_state(state)
    if uid is None:
        return bounce("error", "Login link expired — click Kite login again")
    if status and status != "success" or not request_token:
        return bounce("error", "Zerodha reported a failed login")
    try:
        out = await asyncio.to_thread(kite_data.exchange_session, uid, request_token)
        return bounce("connected", out.get("kite_user", ""))
    except Exception as exc:  # noqa: BLE001
        return bounce("error", str(exc))


@router.get("/kite/status")
async def kite_status(user: Annotated[User, Depends(get_current_user)]):
    import asyncio

    from .. import kite_data

    return await asyncio.to_thread(kite_data.status, user.id)


# ==================== Scalp Mode ====================

@router.get("/scalp/signals")
def scalp_signals(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from ..models import ScalpSignal

    rows = (db.query(ScalpSignal).filter(ScalpSignal.user_id == user.id)
            .order_by(ScalpSignal.created_at.desc()).limit(50).all())
    return [{"id": r.id, "symbol": r.symbol, "rule": r.rule, "direction": r.direction,
             "instrument": r.instrument, "simulated": r.simulated,
             "created_at": r.created_at, **json.loads(r.payload_json or "{}")}
            for r in rows]


@router.post("/scalp/backtest")
async def scalp_backtest(
    user: Annotated[User, Depends(get_current_user)],
    symbol: str = "NIFTY",
    days: int = 7,
    capital: float = 100_000.0,
    risk_pct: float = 1.0,
    brokerage: float = 20.0,  # R6-9: match the calibrated Zerodha flat
    slippage_pct: float = 0.25,
    exit_policy: str = "A",
):
    """Replay the last ≤7 sessions through the live rule code (modeled premiums)."""
    import asyncio

    from ..backtest import backtest_symbol

    symbol = symbol.upper()
    if symbol not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "COMBINED"):
        raise HTTPException(status_code=422,
                            detail="symbol must be NIFTY/BANKNIFTY/FINNIFTY/COMBINED")
    days = max(1, min(days, 60))  # >7 needs a live Kite session (historical add-on)
    capital = max(10_000.0, min(capital, 100_000_000.0))
    risk_pct = max(0.1, min(risk_pct, 10.0))
    brokerage = max(0.0, min(brokerage, 500.0))
    slippage_pct = max(0.0, min(slippage_pct, 2.0))
    if exit_policy not in ("A", "G"):
        raise HTTPException(status_code=422, detail="exit_policy must be A or G")
    try:
        if symbol == "COMBINED":
            from ..backtest import backtest_combined

            return await asyncio.to_thread(backtest_combined, days, capital, risk_pct,
                                           brokerage, slippage_pct,
                                           ("NIFTY", "BANKNIFTY"), exit_policy)
        return await asyncio.to_thread(backtest_symbol, symbol, days, capital, risk_pct,
                                       brokerage, slippage_pct, exit_policy)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"History unavailable: {exc}") from exc


@router.post("/scalp/backtest/compare")
async def scalp_backtest_compare(
    user: Annotated[User, Depends(get_current_user)],
    symbol: str = "NIFTY",
    days: int = 7,
):
    """Same historical signals under three exit policies (fixed / trail / hybrid)."""
    import asyncio

    from ..backtest import compare_exit_policies

    symbol = symbol.upper()
    if symbol not in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
        raise HTTPException(status_code=422, detail="symbol must be NIFTY/BANKNIFTY/FINNIFTY")
    try:
        return await asyncio.to_thread(compare_exit_policies, symbol, max(1, min(days, 7)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"History unavailable: {exc}") from exc


@router.delete("/scalp/signals/simulated", status_code=204)
def clear_simulated_signals(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Wipe test rows so the table starts clean — real signals are never touched."""
    from ..models import ScalpSignal

    from ..models import Alert

    db.query(ScalpSignal).filter(
        ScalpSignal.user_id == user.id, ScalpSignal.simulated.is_(True)
    ).delete()
    # R5: also clear the matching [SIM] rows from the alert bell
    db.query(Alert).filter(
        Alert.user_id == user.id, Alert.type == "scalp",
        Alert.message.like("[SIM]%"),
    ).delete(synchronize_session=False)
    db.commit()


@router.get("/scalp/paper")
async def scalp_paper(
    user: Annotated[User, Depends(get_current_user)],
    days: int = 14,
    live: bool = True,
):
    """Paper scoreboard: real signals scored on actual candles, plus a
    provisional live view of today's not-yet-finalized signals."""
    import asyncio

    from ..paper_score import paper_summary, provisional_today

    if not 1 <= days <= 60:
        raise HTTPException(status_code=422, detail="days must be 1–60")
    out = await asyncio.to_thread(paper_summary, user.id, days)
    if live:
        try:
            out["today_live"] = await asyncio.to_thread(provisional_today, user.id)
        except Exception:  # noqa: BLE001 — live strip must never break the board
            out["today_live"] = []
    return out


@router.post("/scalp/paper/score")
async def scalp_paper_score(
    user: Annotated[User, Depends(get_current_user)],
    day: str | None = None,
):
    """Score now (default: today IST). Idempotent — already-scored rows skip."""
    import asyncio
    from datetime import date

    from ..paper_score import paper_summary, score_day

    if day is not None:
        try:
            date.fromisoformat(day)
        except ValueError:
            raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD") from None
    # R6-1: scores are FINAL (score_day skips already-scored rows), so scoring
    # today mid-session would freeze partial-day outcomes into the go-live
    # scoreboard — a TIME marked at 11:00 hides the SL that hits at 11:40.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ..paper_score import SCORE_AFTER_HM

    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    target = day or now_ist.date().isoformat()
    if target == now_ist.date().isoformat() and (now_ist.hour, now_ist.minute) < SCORE_AFTER_HM:
        raise HTTPException(status_code=409,
                            detail="Session not finished — today's signals are scored "
                                   "after 15:35 IST (partial scores would be final).")
    scored = await asyncio.to_thread(score_day, day, user.id)
    return {"scored": scored, "summary": paper_summary(user.id, 14)}


@router.get("/scalp/paper-trades")
def scalp_paper_trades(
    user: Annotated[User, Depends(get_current_user)],
    days: int = 35,
):
    """Live paper-trading desk: backtest-style rows + portfolio summary."""
    from ..paper_trade import paper_trades_summary

    if not 1 <= days <= 90:
        raise HTTPException(status_code=422, detail="days must be 1–90")
    return paper_trades_summary(user.id, days)


@router.get("/scalp/status")
async def scalp_status(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ..fno import is_market_hours_ist
    from ..models import Setting
    from ..scalp import SCALP_POLL_SECONDS, day_bias, theta_cutoff_passed

    import asyncio

    srow = db.get(Setting, user.id)
    cfg = json.loads(srow.config_json) if srow else {}
    symbols = cfg.get("scalp_symbols") or ["NIFTY"]
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    bias = {s: await asyncio.to_thread(day_bias, user.id, s, True) for s in symbols}
    return {
        "enabled": bool(cfg.get("scalp_enabled")),
        "symbols": symbols,
        "market_open": is_market_hours_ist(),
        "theta_cutoff": theta_cutoff_passed(now_ist),
        "poll_seconds": SCALP_POLL_SECONDS,
        # {sym: {rating, as_of, today}} — stale ratings shown with their date,
        # and the engine itself filters on TODAY's rating only (R5-7)
        "bias": bias,
    }


@router.post("/scalp/simulate", status_code=201)
async def scalp_simulate(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Emit one SIMULATED signal from the live chain so the UI/flow can be tested
    outside market hours. Clearly watermarked; never sends a desktop notification."""
    from ..fno import get_fno_snapshot
    from ..models import Setting
    from ..scalp import build_scalp_signal, emit_signal

    srow = db.get(Setting, user.id)
    cfg = json.loads(srow.config_json) if srow else {}
    symbol = (cfg.get("scalp_symbols") or ["NIFTY"])[0]
    try:
        snapshot = await get_fno_snapshot(symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Chain unavailable: {exc}") from exc
    hit = {"rule": "ORB", "direction": "CE",
           "why": "SIMULATED — sample signal for flow testing, not a market setup"}
    sig = build_scalp_signal(symbol, hit, snapshot, cfg)
    if sig is None:
        raise HTTPException(status_code=502, detail="No scalp-band strike in the chain right now")
    rid = emit_signal(user.id, sig, simulated=True)
    if rid is None:
        raise HTTPException(status_code=409, detail="Cooldown active — a recent signal exists")
    return {"id": rid, **sig, "simulated": True}


@router.get("/scoreboard")
def scoreboard(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """A3.3 — realized alpha by provider/depth from resolved decisions."""
    rows = (
        db.query(MemoryEntry, Run)
        .join(Run, Run.id == MemoryEntry.run_id)
        .filter(MemoryEntry.user_id == user.id, MemoryEntry.status == "resolved",
                MemoryEntry.alpha.isnot(None))
        .all()
    )
    buckets: dict[str, dict] = {}
    for mem, run in rows:
        cfg = json.loads(run.config_json or "{}")
        key = f"{cfg.get('llm_provider', run.mode)} · depth {cfg.get('research_depth', '?')}"
        b = buckets.setdefault(key, {"count": 0, "alpha_sum": 0.0, "hits": 0})
        b["count"] += 1
        b["alpha_sum"] += mem.alpha
        bullish = mem.rating in ("Buy", "Overweight")
        bearish = mem.rating in ("Sell", "Underweight")
        if (bullish and mem.alpha > 0) or (bearish and mem.alpha < 0) or (
            mem.rating == "Hold" and abs(mem.alpha) < 0.02
        ):
            b["hits"] += 1
    return [
        {"stack": k, "decisions": b["count"],
         "avg_alpha": round(b["alpha_sum"] / b["count"], 4),
         "hit_rate": round(b["hits"] / b["count"], 3)}
        for k, b in sorted(buckets.items(), key=lambda kv: -kv[1]["alpha_sum"] / kv[1]["count"])
    ]


# ==================== A6: triggers ====================

class TriggerIn(BaseModel):
    ticker: str
    threshold: float = Field(default=3.0, ge=0.5, le=50)
    config: dict = Field(default_factory=dict)


@router.get("/triggers")
def list_triggers(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.query(Trigger).filter(Trigger.user_id == user.id).order_by(Trigger.created_at).all()
    return [
        {"id": t.id, "ticker": t.ticker, "type": t.type, "threshold": t.threshold,
         "enabled": t.enabled, "last_fired_date": t.last_fired_date, "created_at": t.created_at}
        for t in rows
    ]


@router.post("/triggers", status_code=201)
def create_trigger(
    body: TriggerIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        ticker = normalize_ticker(body.ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = Trigger(user_id=user.id, ticker=ticker, threshold=body.threshold,
                  config_json=json.dumps(body.config))
    db.add(row)
    db.commit()
    return {"id": row.id, "ticker": ticker, "threshold": row.threshold, "enabled": True}


@router.post("/triggers/{trigger_id}/toggle")
def toggle_trigger(
    trigger_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(Trigger, trigger_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Trigger not found")
    row.enabled = not row.enabled
    db.commit()
    return {"id": row.id, "enabled": row.enabled}


@router.delete("/triggers/{trigger_id}", status_code=204)
def delete_trigger(
    trigger_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(Trigger, trigger_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Trigger not found")
    db.delete(row)
    db.commit()


# ==================== A1: Agent Studio ====================

STOCK_AGENTS = [
    "Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst",
    "Bull Researcher", "Bear Researcher", "Research Manager", "Trader",
    "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst", "Portfolio Manager",
]
CUSTOM_TOOLBOX = [
    "get_stock_data", "get_indicators", "get_news", "get_global_news", "get_fundamentals",
    "get_insider_transactions", "get_macro_indicators", "get_prediction_markets",
]


class ProfileIn(BaseModel):
    agent_key: str = Field(min_length=1, max_length=80)
    display_name: str = Field(default="", max_length=80)
    persona: str = Field(default="", max_length=4000)
    tools: list[str] = Field(default_factory=list, max_length=6)
    enabled: bool = True


@router.get("/studio")
def studio_state(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.query(AgentProfile).filter(AgentProfile.user_id == user.id).all()
    return {
        "stock_agents": STOCK_AGENTS,
        "toolbox": CUSTOM_TOOLBOX,
        "profiles": [
            {"id": p.id, "agent_key": p.agent_key, "display_name": p.display_name,
             "persona": p.persona, "tools": json.loads(p.tools_json or "[]"), "enabled": p.enabled}
            for p in rows
        ],
    }


@router.post("/studio")
def save_profile(
    body: ProfileIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    is_custom = body.agent_key.startswith("custom:")
    if not is_custom and body.agent_key not in STOCK_AGENTS:
        raise HTTPException(status_code=422, detail="agent_key must be a stock agent or 'custom:<name>'")
    bad_tools = [t for t in body.tools if t not in CUSTOM_TOOLBOX]
    if bad_tools:
        raise HTTPException(status_code=422, detail=f"Unknown tools: {bad_tools}")
    row = (db.query(AgentProfile)
           .filter(AgentProfile.user_id == user.id, AgentProfile.agent_key == body.agent_key)
           .one_or_none())
    if row is None:
        row = AgentProfile(user_id=user.id, agent_key=body.agent_key)
        db.add(row)
    row.display_name = body.display_name
    row.persona = body.persona
    row.tools_json = json.dumps(body.tools)
    row.enabled = body.enabled
    db.commit()
    return {"id": row.id, "agent_key": row.agent_key}


@router.delete("/studio/{profile_id}", status_code=204)
def delete_profile(
    profile_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(AgentProfile, profile_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete(row)
    db.commit()


# ==================== A4: research library ====================

@router.post("/library/ingest/{ticker}")
async def ingest(
    ticker: str,
    user: Annotated[User, Depends(get_current_user)],
):
    try:
        symbol = normalize_ticker(ticker)
        filings = await library.ingest_sec_filings(user.id, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"EDGAR unavailable: {exc}") from exc
    return {"ingested": filings, "count": len(filings)}


@router.post("/library/index-run/{run_id}")
def index_run(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    run = db.get(Run, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    library.index_run_reports(user.id, run_id)
    return {"indexed": True}


@router.get("/library/search")
def library_search(
    q: str,
    user: Annotated[User, Depends(get_current_user)],
    ticker: str | None = None,
    as_of: str | None = None,
):
    return library.search(user.id, q, ticker=ticker.upper() if ticker else None, as_of=as_of)


@router.get("/library")
def library_list(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = (db.query(Document).filter(Document.user_id == user.id)
            .order_by(Document.created_at.desc()).limit(200).all())
    return [
        {"id": d.id, "ticker": d.ticker, "source": d.source, "title": d.title,
         "doc_date": d.doc_date, "url": d.url, "chars": len(d.content)}
        for d in rows
    ]
