"""Demo-mode runner: full pipeline UX with simulated agents (no LLM keys needed).

Replays the exact TradingAgents stage sequence — analyst tool loops, bull/bear
debate, research manager, trader, three-way risk debate, portfolio manager —
with deterministic (ticker+date seeded) content so users can exercise every
platform feature before adding keys. Every output is labeled as simulated.
"""
from __future__ import annotations

import asyncio
import hashlib
import random

from . import ANALYST_AGENT, RunManager, RunHandle, completed_sections, past_memory_context

TOOLS_BY_ANALYST = {
    "market": [("get_verified_market_snapshot", {}), ("get_stock_data", {}), ("get_indicators", {"indicators": "rsi,macd,boll"})],
    "social": [("get_news", {"kind": "ticker"}), ("get_stocktwits", {}), ("get_reddit", {})],
    "news": [("get_global_news", {}), ("get_macro_indicators", {"vendor": "fred"}), ("get_prediction_markets", {"vendor": "polymarket"})],
    "fundamentals": [("get_fundamentals", {}), ("get_income_statement", {}), ("get_balance_sheet", {}), ("get_insider_transactions", {})],
}

RATINGS = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]


def _rng(ticker: str, trade_date: str) -> random.Random:
    seed = int(hashlib.sha256(f"{ticker}|{trade_date}".encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def _fake_price(rng: random.Random) -> float:
    return round(rng.uniform(12, 780), 2)


async def run_demo(
    mgr: RunManager,
    handle: RunHandle,
    ticker: str,
    trade_date: str,
    config: dict,
    run_id: str,
    resume: bool = False,
) -> dict:
    rng = _rng(ticker, trade_date)
    price = _fake_price(rng)
    rsi = round(rng.uniform(22, 82), 1)
    macd_sig = rng.choice(["bullish crossover", "bearish divergence", "flat"])
    trend = "uptrend" if rsi < 65 and "bull" in macd_sig else ("downtrend" if rsi > 70 else "consolidation")
    sentiment = round(rng.uniform(-0.8, 0.8), 2)
    depth = int(config.get("research_depth", 1))
    analysts = config.get("analysts", list(ANALYST_AGENT.keys()))
    done_sections = completed_sections(run_id) if resume else set()
    delay = 0.9

    stats = {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0}

    async def llm(agent: str, text: str) -> None:
        stats["llm_calls"] += 1
        stats["tokens_in"] += rng.randint(900, 2400)
        stats["tokens_out"] += rng.randint(250, 900)
        await mgr.emit(handle, "message", agent=agent, payload={"kind": "reasoning", "text": text})
        await mgr.emit(handle, "stats", payload=dict(stats))
        await asyncio.sleep(delay)

    async def tool(agent: str, name: str, args: dict) -> None:
        stats["tool_calls"] += 1
        await mgr.emit(handle, "tool_call", agent=agent, payload={"tool": name, "args": args})
        await asyncio.sleep(delay * 0.6)

    async def status(agent: str, st: str) -> None:
        await mgr.emit(handle, "agent_status", agent=agent, payload={"status": st})

    # inject past memory context (F8.3 parity) — loaded by the router into config
    memory_ctx = config.get("_memory_context", [])
    if memory_ctx:
        await mgr.emit(
            handle,
            "message",
            agent="Portfolio Manager",
            payload={
                "kind": "memory",
                "text": f"Loaded {len(memory_ctx)} past decision(s) from the decision log for context.",
                "entries": memory_ctx,
            },
        )

    # ---------- Stage 1: analysts ----------
    section_by_analyst = {
        "market": "market_report",
        "social": "sentiment_report",
        "news": "news_report",
        "fundamentals": "fundamentals_report",
    }
    reports: dict[str, str] = {}
    for key in analysts:
        agent = ANALYST_AGENT[key]
        section = section_by_analyst[key]
        if section in done_sections:
            await status(agent, "done")
            continue
        await status(agent, "in_progress")
        for tname, targs in TOOLS_BY_ANALYST[key]:
            await tool(agent, tname, {"symbol": ticker, "curr_date": trade_date, **targs})
        content = _analyst_report(key, ticker, trade_date, price, rsi, macd_sig, trend, sentiment, rng)
        await llm(agent, f"{agent} synthesized findings for {ticker}.")
        reports[section] = content
        await mgr.save_report(handle, section, content)
        await status(agent, "done")

    # ---------- Stage 2: bull/bear debate ----------
    bull_points, bear_points = _debate_points(ticker, trend, sentiment, rng)
    for round_i in range(depth):
        await status("Bull Researcher", "in_progress")
        await llm("Bull Researcher", f"[Round {round_i+1}] Bull case: {bull_points[round_i % len(bull_points)]}")
        await status("Bull Researcher", "done")
        await status("Bear Researcher", "in_progress")
        await llm("Bear Researcher", f"[Round {round_i+1}] Bear rebuttal: {bear_points[round_i % len(bear_points)]}")
        await status("Bear Researcher", "done")

    if "investment_plan" not in done_sections:
        await status("Research Manager", "in_progress")
        stance = rng.choice(["bull", "bear", "balanced"])
        plan = _investment_plan(ticker, stance, trend, sentiment, rng)
        await llm("Research Manager", "Research Manager weighed the debate and issued an investment plan.")
        await mgr.save_report(handle, "investment_plan", plan)
        await status("Research Manager", "done")

    # ---------- Stage 3: trader ----------
    if "trader_investment_plan" not in done_sections:
        await status("Trader", "in_progress")
        tplan = _trader_plan(ticker, price, trend, rng)
        await llm("Trader", "Trader grounded the plan in the verified price snapshot.")
        await mgr.save_report(handle, "trader_investment_plan", tplan)
        await status("Trader", "done")

    # ---------- Stage 4: risk debate ----------
    risk_agents = ["Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"]
    risk_lines = _risk_lines(ticker, rng)
    for round_i in range(depth):
        for idx, agent in enumerate(risk_agents):
            await status(agent, "in_progress")
            await llm(agent, f"[Risk round {round_i+1}] {risk_lines[(round_i * 3 + idx) % len(risk_lines)]}")
            await status(agent, "done")

    # ---------- Stage 5: portfolio manager ----------
    rating = _final_rating(rsi, sentiment, trend, rng)
    decision = _final_decision(ticker, trade_date, rating, price, rsi, sentiment, trend, memory_ctx)
    if "final_trade_decision" not in done_sections:
        await status("Portfolio Manager", "in_progress")
        await llm("Portfolio Manager", "Portfolio Manager reviewed the risk debate and issued the final decision.")
        await mgr.save_report(handle, "final_trade_decision", decision)
        await status("Portfolio Manager", "done")

    summary = f"{rating} — demo-mode analysis of {ticker} on {trade_date} (simulated data)."
    return {"rating": rating, "decision_summary": summary, "stats": stats}


# ---------- content builders ----------

def _analyst_report(key, ticker, trade_date, price, rsi, macd_sig, trend, sentiment, rng) -> str:
    if key == "market":
        return (
            f"### Market Analyst — {ticker} (as of {trade_date})\n\n"
            f"> **Demo mode:** simulated data for platform demonstration.\n\n"
            f"Verified snapshot: last close **${price}**. RSI(14) at **{rsi}** with a **{macd_sig}** on the MACD; "
            f"Bollinger bands indicate a {trend}. Volume profile shows "
            f"{rng.choice(['accumulation near support', 'distribution into strength', 'neutral participation'])}.\n\n"
            f"| Indicator | Value | Read |\n|---|---|---|\n"
            f"| RSI(14) | {rsi} | {'overbought' if rsi > 70 else 'oversold' if rsi < 30 else 'neutral'} |\n"
            f"| MACD | {macd_sig} | {trend} |\n"
            f"| 50/200 SMA | {rng.choice(['golden cross intact', 'death cross risk', 'converging'])} | trend context |\n"
        )
    if key == "social":
        mood = "bullish" if sentiment > 0.15 else "bearish" if sentiment < -0.15 else "mixed"
        return (
            f"### Sentiment Analyst — {ticker}\n\n"
            f"> **Demo mode:** simulated data.\n\n"
            f"Aggregated read across news headlines, StockTwits, and Reddit: **{mood}** "
            f"(composite score {sentiment:+.2f}). Message volume {rng.choice(['spiked 3x on earnings chatter', 'is in line with 30-day average', 'is thinning, retail attention rotating away'])}. "
            f"Top themes: {rng.choice(['product cycle optimism', 'margin compression worries', 'insider selling debate', 'AI capex narrative'])}."
        )
    if key == "news":
        return (
            f"### News Analyst — {ticker}\n\n"
            f"> **Demo mode:** simulated data.\n\n"
            f"Macro backdrop: {rng.choice(['Fed path repriced dovish', 'yields grinding higher', 'risk-on breadth improving'])}; "
            f"FRED prints show {rng.choice(['cooling inflation', 'sticky services inflation', 'softening labor market'])}. "
            f"Polymarket implies a {rng.randint(18, 74)}% probability on the nearest relevant macro event. "
            f"Company-specific flow is {rng.choice(['light', 'dominated by a single catalyst', 'mixed with sector news'])}."
        )
    return (
        f"### Fundamentals Analyst — {ticker}\n\n"
        f"> **Demo mode:** simulated data.\n\n"
        f"Revenue growth {rng.randint(-4, 38)}% YoY, gross margin {rng.randint(22, 68)}%, "
        f"net cash position {rng.choice(['strong', 'adequate', 'stretched'])}. Insider transactions over the last quarter: "
        f"{rng.choice(['net buying', 'net selling', 'negligible'])}. Valuation at {rng.randint(9, 55)}x forward earnings "
        f"versus sector median {rng.randint(12, 30)}x."
    )


def _debate_points(ticker, trend, sentiment, rng):
    bull = [
        f"{ticker}'s {trend} plus improving breadth supports upside continuation; risk/reward favors adding exposure.",
        "Valuation remains below peers on a growth-adjusted basis; sentiment washout creates asymmetric entry.",
        "Catalyst path (earnings, product cycle) is front-loaded; market underprices execution track record.",
    ]
    bear = [
        "Momentum is late-cycle; indicator divergence historically precedes 8-12% drawdowns.",
        f"Sentiment composite {sentiment:+.2f} shows crowding — marginal buyer likely exhausted.",
        "Macro tape is fragile; a single hot inflation print unwinds the multiple expansion driving this move.",
    ]
    rng.shuffle(bull)
    rng.shuffle(bear)
    return bull, bear


def _investment_plan(ticker, stance, trend, sentiment, rng) -> str:
    rec = {"bull": "Overweight", "bear": "Underweight", "balanced": "Hold"}[stance]
    return (
        f"### Research Manager — Investment Plan for {ticker}\n\n"
        f"> **Demo mode:** simulated output.\n\n"
        f"After weighing the debate, the {stance} case is more persuasive. **Recommendation: {rec}.**\n\n"
        f"Thesis: the {trend} combined with a sentiment read of {sentiment:+.2f} suggests "
        f"{'positioning is not yet stretched' if stance == 'bull' else 'the easy gains are behind us' if stance == 'bear' else 'no edge at current prices'}. "
        f"Key monitorables: {rng.choice(['next earnings print', 'macro CPI release', 'sector rotation breadth'])}, invalidation level, and volume confirmation."
    )


def _trader_plan(ticker, price, trend, rng) -> str:
    stop = round(price * rng.uniform(0.9, 0.97), 2)
    target = round(price * rng.uniform(1.05, 1.22), 2)
    return (
        f"### Trader — Execution Plan for {ticker}\n\n"
        f"> **Demo mode:** simulated output. Price grounded at **${price}** (verified snapshot).\n\n"
        f"Entry: staged over {rng.randint(1, 3)} tranche(s) near ${price}. Stop: ${stop}. First target: ${target}.\n"
        f"Sizing: {rng.choice(['half', 'quarter', 'full'])} unit given {trend} conditions; scale on confirmation."
    )


def _risk_lines(ticker, rng):
    return [
        f"Aggressive: conviction is highest exactly when it feels uncomfortable — size up {ticker} while the crowd hesitates.",
        "Conservative: cap exposure at one unit; correlation to existing book is elevated and liquidity thins after hours.",
        "Neutral: both sides overstate; a staged entry with a hard invalidation satisfies the risk budget either way.",
        "Aggressive: the trader's stop is too tight — give the thesis room or skip the trade.",
        "Conservative: drawdown math dominates; a 10% gap-down costs more than the target gains.",
        "Neutral: reduce debate to the one number that matters — expected value at the stated stop/target is positive but thin.",
    ]


def _final_rating(rsi, sentiment, trend, rng) -> str:
    score = 0.0
    score += 1 if trend == "uptrend" else -1 if trend == "downtrend" else 0
    score += 1 if sentiment > 0.2 else -1 if sentiment < -0.2 else 0
    score += -1 if rsi > 74 else 1 if rsi < 30 else 0
    score += rng.uniform(-0.8, 0.8)
    if score >= 1.6:
        return "Buy"
    if score >= 0.6:
        return "Overweight"
    if score <= -1.6:
        return "Sell"
    if score <= -0.6:
        return "Underweight"
    return "Hold"


def _final_decision(ticker, trade_date, rating, price, rsi, sentiment, trend, memory_ctx) -> str:
    mem = ""
    if memory_ctx:
        lessons = "; ".join(
            f"{m['ticker']} {m['trade_date']}: {m['rating']} → alpha {m['alpha']:+.1%}" if m.get("alpha") is not None
            else f"{m['ticker']} {m['trade_date']}: {m['rating']}"
            for m in memory_ctx[:3]
        )
        mem = f"\n\nDecision-log context considered: {lessons}."
    return (
        f"### Portfolio Manager — Final Decision\n\n"
        f"> **Demo mode:** simulated output — not financial advice.\n\n"
        f"**Rating: {rating}**\n\n"
        f"{ticker} on {trade_date}: price ${price}, RSI {rsi}, sentiment {sentiment:+.2f}, structure {trend}. "
        f"The risk debate surfaced no disqualifying exposure; the {rating} rating reflects the balance of the research "
        f"team's thesis against the conservative desk's drawdown math.{mem}"
    )
