"""Desk intelligence: per-agent call extraction/grading (#2) and briefing books (#3).

Inspired by ai-fund's KPI-review and briefing-book patterns, adapted to
AgentAlgo's pipeline and outcome-resolution loop.
"""
from __future__ import annotations

import logging
import re

from .db import SessionLocal
from .models import AgentCall, Briefing, Run, RunReport

log = logging.getLogger("agentalgo.desk")

BRIEFING_MAX_CHARS = 8_000
BRIEFING_CONTEXT_CHARS = 2_000

_RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
_PROPOSAL_RE = re.compile(
    r"(?:FINAL\s+TRANSACTION\s+PROPOSAL|Recommendation|Rating)\s*[:\-]?\s*\**\s*"
    r"(Buy|Overweight|Hold|Underweight|Sell)\b",
    re.IGNORECASE,
)

_SECTION_AGENT = {
    "market_report": "Market Analyst",
    "sentiment_report": "Sentiment Analyst",
    "news_report": "News Analyst",
    "fundamentals_report": "Fundamentals Analyst",
    "investment_plan": "Research Manager",
    "trader_investment_plan": "Trader",
    "final_trade_decision": "Portfolio Manager",
}


def extract_call(text: str) -> str | None:
    m = _PROPOSAL_RE.search(text or "")
    if not m:
        return None
    call = m.group(1).capitalize()
    return call if call in _RATINGS else None


def record_agent_calls(run_id: str) -> int:
    """Parse each report section's directional proposal into AgentCall rows."""
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None:
            return 0
        existing = db.query(AgentCall).filter(AgentCall.run_id == run_id).count()
        if existing:
            return 0
        reports = db.query(RunReport).filter(RunReport.run_id == run_id).all()
        n = 0
        for r in reports:
            agent = _SECTION_AGENT.get(r.section)
            if agent is None:
                continue
            call = run.rating if r.section == "final_trade_decision" else extract_call(r.content_md)
            if call is None:
                continue
            db.add(AgentCall(user_id=run.user_id, run_id=run_id, ticker=run.ticker,
                             agent=agent, call=call))
            n += 1
        db.commit()
        return n


def grade_agent_calls(run_id: str | None, alpha: float) -> int:
    """Grade every agent's call for a resolved run against realized alpha."""
    if run_id is None:
        return 0
    with SessionLocal() as db:
        calls = db.query(AgentCall).filter(AgentCall.run_id == run_id,
                                           AgentCall.correct.is_(None)).all()
        for c in calls:
            bullish = c.call in ("Buy", "Overweight")
            bearish = c.call in ("Sell", "Underweight")
            c.correct = ((bullish and alpha > 0) or (bearish and alpha < 0)
                         or (c.call == "Hold" and abs(alpha) < 0.02))
            c.alpha = alpha
        db.commit()
        return len(calls)


def desk_review(user_id: str) -> list[dict]:
    """Per-agent KPI table from graded calls."""
    with SessionLocal() as db:
        calls = (db.query(AgentCall)
                 .filter(AgentCall.user_id == user_id, AgentCall.correct.isnot(None))
                 .all())
    agents: dict[str, dict] = {}
    for c in calls:
        a = agents.setdefault(c.agent, {"graded": 0, "hits": 0, "alpha_sum": 0.0,
                                        "bullish": 0, "bearish": 0, "hold": 0})
        a["graded"] += 1
        a["hits"] += 1 if c.correct else 0
        a["alpha_sum"] += c.alpha or 0
        key = ("bullish" if c.call in ("Buy", "Overweight")
               else "bearish" if c.call in ("Sell", "Underweight") else "hold")
        a[key] += 1
    out = []
    for agent, a in agents.items():
        hit_rate = a["hits"] / a["graded"] if a["graded"] else None
        out.append({
            "agent": agent, "graded": a["graded"], "hit_rate": round(hit_rate, 3),
            "avg_alpha_when_called": round(a["alpha_sum"] / a["graded"], 4),
            "bias": {"bullish": a["bullish"], "bearish": a["bearish"], "hold": a["hold"]},
            "verdict": ("bench — review persona" if a["graded"] >= 5 and hit_rate < 0.4
                        else "watch" if a["graded"] >= 5 and hit_rate < 0.5 else "ok"),
        })
    return sorted(out, key=lambda x: -(x["hit_rate"] or 0))


# ---------- briefing books (#3) ----------

def append_briefing(user_id: str, ticker: str, entry: str) -> None:
    """Append a dated entry; trim oldest content beyond the cap."""
    with SessionLocal() as db:
        row = (db.query(Briefing)
               .filter(Briefing.user_id == user_id, Briefing.ticker == ticker)
               .one_or_none())
        if row is None:
            row = Briefing(user_id=user_id, ticker=ticker, content="")
            db.add(row)
        content = (row.content + "\n\n" + entry).strip()
        if len(content) > BRIEFING_MAX_CHARS:  # keep the newest
            content = content[-BRIEFING_MAX_CHARS:]
            cut = content.find("\n\n")
            if 0 < cut < 500:
                content = content[cut + 2:]
        row.content = content
        db.commit()


def briefing_context(user_id: str, ticker: str) -> str:
    """Most recent briefing content for injection into a new run."""
    with SessionLocal() as db:
        row = (db.query(Briefing)
               .filter(Briefing.user_id == user_id, Briefing.ticker == ticker)
               .one_or_none())
        if row is None or not row.content:
            return ""
        return row.content[-BRIEFING_CONTEXT_CHARS:]


def briefing_entry_for_run(run: Run, card: dict | None) -> str:
    """One compact dated paragraph capturing this run's takeaways."""
    from datetime import date

    parts = [f"[{date.today().isoformat()}] {run.mode} run → {run.rating or 'n/a'}."]
    if run.decision_summary:
        parts.append(run.decision_summary[:200])
    if card:
        for row in card.get("rows", [])[:2]:
            parts.append(f"Level: {row.get('condition')} → {row.get('instrument')} "
                         f"(ep ≈{row.get('ep')}).")
        if card.get("verdict"):
            parts.append(f"Card verdict: {card['verdict']}.")
    return " ".join(parts)
