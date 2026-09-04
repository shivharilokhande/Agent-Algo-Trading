"""Subprocess worker: runs the real TradingAgents graph, streaming JSON lines.

Protocol (stdout, one JSON object per line):
  {"type": "agent_status", "agent": str, "payload": {"status": str}}
  {"type": "message",      "agent": str, "payload": {"kind": str, "text": str}}
  {"type": "tool_call",    "agent": str, "payload": {"tool": str, "args": dict}}
  {"type": "stats",        "payload": {llm_calls, tool_calls, tokens_in, tokens_out}}
  {"type": "report_section", "section": str, "content_md": str}
  {"type": "result", "rating": str, "decision_summary": str, "stats": dict}

Faithful port of cli/main.py's streaming loop (v0.4.0): message dedupe by id,
report-driven analyst statuses, debate/risk state handling, checkpoint
begin/end, memory-log decision store, REVIEW-safe signal extraction.

Run standalone; expects one JSON payload line on stdin. All TradingAgents
config (memory path, cache dir, API keys) arrives via environment variables.
"""
from __future__ import annotations

import json
import sys


def out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}


def main() -> int:
    payload = json.loads(sys.stdin.readline())
    cfg_in: dict = payload["config"]
    ticker: str = payload["ticker"]
    trade_date: str = payload["trade_date"]

    from langchain_core.callbacks import BaseCallbackHandler

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = cfg_in.get("llm_provider", "openai")
    if cfg_in.get("quick_think_llm"):
        config["quick_think_llm"] = cfg_in["quick_think_llm"]
    if cfg_in.get("deep_think_llm"):
        config["deep_think_llm"] = cfg_in["deep_think_llm"]
    if cfg_in.get("backend_url"):
        config["backend_url"] = cfg_in["backend_url"]
    depth = int(cfg_in.get("research_depth", 1))
    config["max_debate_rounds"] = depth
    config["max_risk_discuss_rounds"] = depth
    config["output_language"] = cfg_in.get("output_language", "English")
    config["checkpoint_enabled"] = bool(cfg_in.get("checkpoint_enabled", True))
    for knob in ("temperature", "max_tokens", "llm_max_retries",
                 "google_thinking_level", "openai_reasoning_effort", "anthropic_effort"):
        if cfg_in.get(knob) not in (None, ""):
            config[knob] = cfg_in[knob]
    if cfg_in.get("data_vendors"):
        config["data_vendors"] = {**config["data_vendors"], **cfg_in["data_vendors"]}
    if cfg_in.get("benchmark_ticker"):
        config["benchmark_ticker"] = cfg_in["benchmark_ticker"]

    selected_analysts = cfg_in.get("analysts") or ANALYST_ORDER
    asset_type = cfg_in.get("asset_type", "stock")
    if asset_type == "index":
        asset_type = "stock"  # engine knows stock|crypto; indices ride the stock pipeline

    stats = {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0}

    class StatsHandler(BaseCallbackHandler):
        def on_chat_model_start(self, *a, **k):
            stats["llm_calls"] += 1

        def on_llm_end(self, response, **k):
            try:
                for gens in response.generations:
                    for gen in gens:
                        usage = getattr(gen.message, "usage_metadata", None) or {}
                        stats["tokens_in"] += usage.get("input_tokens", 0)
                        stats["tokens_out"] += usage.get("output_tokens", 0)
            except Exception:
                pass
            out({"type": "stats", "payload": dict(stats)})

        def on_tool_start(self, serialized, input_str, **k):
            stats["tool_calls"] += 1
            out({
                "type": "tool_call",
                "agent": "",
                "payload": {"tool": (serialized or {}).get("name", "tool"), "args": {"input": str(input_str)[:200]}},
            })

    graph = TradingAgentsGraph(selected_analysts=selected_analysts, config=config, debug=False)

    instrument_context = graph.resolve_instrument_context(ticker, asset_type)
    # F&O: derivatives snapshot travels with the instrument identity so EVERY
    # agent grounds its view in live option-chain positioning
    if cfg_in.get("_fno_context"):
        instrument_context += (
            "\n\nLive NSE derivatives positioning for this instrument (option chain):\n"
            + cfg_in["_fno_context"]
            + "\nIncorporate PCR, max pain, OI support/resistance, and IV into the "
              "technical and risk assessment where relevant."
        )
    out({"type": "message", "agent": "", "payload": {"kind": "system", "text": f"Instrument resolved: {instrument_context[:300]}"}})

    past_context = ""
    try:
        graph._resolve_pending_entries(ticker)  # Phase B outcome resolution (engine parity)
        past_context = graph.memory_log.get_past_context(ticker, as_of=graph._memory_as_of(trade_date))
    except Exception as exc:  # memory features must never block a run
        out({"type": "message", "agent": "", "payload": {"kind": "system", "text": f"Memory context unavailable: {exc}"}})

    init_state = graph.propagator.create_initial_state(
        ticker, trade_date, asset_type=asset_type,
        past_context=past_context, instrument_context=instrument_context,
    )
    args = graph.propagator.get_graph_args(callbacks=[StatsHandler()])

    checkpoint_tid = graph.begin_checkpoint(ticker, trade_date, asset_type)
    if checkpoint_tid is not None:
        args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_tid

    report_sections: dict[str, str] = {}
    agent_status: dict[str, str] = {}
    processed_message_ids: set = set()
    research_done = trader_done = False

    def set_status(agent: str, status: str) -> None:
        if agent_status.get(agent) != status:
            agent_status[agent] = status
            out({"type": "agent_status", "agent": agent, "payload": {"status": status}})

    def set_section(section: str, content: str) -> None:
        if content and report_sections.get(section) != content:
            report_sections[section] = content
            out({"type": "report_section", "section": section, "content_md": content})

    trace = []
    try:
        for chunk in graph.graph.stream(graph.checkpoint_input(init_state), **args):
            # messages + tool calls (dedupe by id, CLI parity)
            for message in chunk.get("messages", []):
                msg_id = getattr(message, "id", None)
                if msg_id is not None:
                    if msg_id in processed_message_ids:
                        continue
                    processed_message_ids.add(msg_id)
                content = getattr(message, "content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                    )
                if content and str(content).strip():
                    out({"type": "message", "agent": "", "payload": {"kind": type(message).__name__, "text": str(content)[:4000]}})
                for tc in getattr(message, "tool_calls", None) or []:
                    name = tc["name"] if isinstance(tc, dict) else tc.name
                    targs = tc["args"] if isinstance(tc, dict) else tc.args
                    out({"type": "tool_call", "agent": "", "payload": {"tool": name, "args": targs}})

            # analyst statuses from accumulated report state (CLI parity)
            found_active = False
            for key in ANALYST_ORDER:
                if key not in selected_analysts:
                    continue
                agent = ANALYST_AGENT_NAMES[key]
                rkey = ANALYST_REPORT_MAP[key]
                if chunk.get(rkey):
                    set_section(rkey, chunk[rkey])
                if report_sections.get(rkey):
                    set_status(agent, "done")
                elif not found_active:
                    set_status(agent, "in_progress")
                    found_active = True
                else:
                    set_status(agent, "pending")

            debate = chunk.get("investment_debate_state") or {}
            if debate:
                if debate.get("bull_history", "").strip():
                    set_status("Bull Researcher", "in_progress")
                if debate.get("bear_history", "").strip():
                    set_status("Bear Researcher", "in_progress")
                judge = debate.get("judge_decision", "").strip()
                if judge and not research_done:
                    research_done = True
                    for a in ("Bull Researcher", "Bear Researcher", "Research Manager"):
                        set_status(a, "done")
                    set_status("Trader", "in_progress")

            if chunk.get("investment_plan"):
                set_section("investment_plan", chunk["investment_plan"])
            if chunk.get("trader_investment_plan"):
                set_section("trader_investment_plan", chunk["trader_investment_plan"])
                if not trader_done:
                    trader_done = True
                    set_status("Trader", "done")
                    set_status("Aggressive Analyst", "in_progress")

            risk = chunk.get("risk_debate_state") or {}
            if risk:
                for hist_key, agent in (
                    ("aggressive_history", "Aggressive Analyst"),
                    ("conservative_history", "Conservative Analyst"),
                    ("neutral_history", "Neutral Analyst"),
                ):
                    if risk.get(hist_key, "").strip() and agent_status.get(agent) != "done":
                        set_status(agent, "in_progress")
                if risk.get("judge_decision", "").strip():
                    for a in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst", "Portfolio Manager"):
                        set_status(a, "done")

            trace.append(chunk)

        graph.clear_checkpoint_on_success(ticker, trade_date, asset_type)
    finally:
        graph.end_checkpoint()

    final_state: dict = {}
    for chunk in trace:
        final_state.update(chunk)

    for section in ("market_report", "sentiment_report", "news_report", "fundamentals_report",
                    "investment_plan", "trader_investment_plan", "final_trade_decision"):
        if final_state.get(section):
            set_section(section, final_state[section])

    decision = final_state.get("final_trade_decision", "")
    graph.memory_log.store_decision(ticker=ticker, trade_date=trade_date, final_trade_decision=decision)
    rating = graph.process_signal(decision) if decision else "REVIEW"
    summary = decision.strip().split("\n")[0][:300] if decision else "No decision produced."
    out({"type": "result", "rating": rating, "decision_summary": summary, "stats": stats})
    return 0


if __name__ == "__main__":
    sys.exit(main())
