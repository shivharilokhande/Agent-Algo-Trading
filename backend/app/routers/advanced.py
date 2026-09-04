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

RATINGS_ORDER = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]


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
