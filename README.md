# AgentAlgo

Multi-user web platform for the [TradingAgents](https://github.com/TauricResearch/TradingAgents) multi-agent LLM trading framework. Configure an analysis in the browser, watch 12 agents (analysts → bull/bear debate → research manager → trader → risk debate → portfolio manager) work in real time, and build a decision log that learns from realized outcomes.

> Research tool only — **not financial, investment, or trading advice.**

## Features (v1)
- **Accounts** with isolated data per user (JWT auth)
- **Bring-your-own-keys vault** — 20 LLM/data providers, Fernet-encrypted at rest, masked, live "Test key" probes
- **Analysis wizard** — any Yahoo-Finance market (US, HK, Tokyo, London, India, China A-shares, crypto), analyst team selection, Shallow/Medium/Deep debate depth, dual model pickers, 10 report languages, thinking-effort knobs
- **Live run view** — WebSocket stream of agent statuses, messages, tool calls, token stats; progressive report sections
- **Two run modes** — Demo (simulated agents, no keys) and Engine (real TradingAgents in an isolated subprocess with just-in-time key injection)
- **Reports** — engine-parity sections (I–V) with markdown export
- **Decision log** — pending → resolved with real yfinance returns, alpha vs. auto-detected benchmark (SPY/^NSEI/^N225/…), reflections re-injected into future runs
- **Run recovery** — cancel/resume from checkpoints; server restarts leave runs resumable
- **Presets & defaults** with loud validation (engine `TRADINGAGENTS_*` parity)
- **Watchlists** (P2) — run the whole pipeline across a ticker list in one click; over-cap runs queue and start automatically; latest-rating grid
- **Scheduled analyses** (P2) — daily / weekdays / weekly at a chosen hour (server-local), with per-schedule depth/mode
- **Alerts** (P2) — rating-tier changes and REVIEW outcomes, with an unread bell in the top bar
- **Run comparison** (P2) — select two runs in history for a side-by-side section diff
- **Human-in-the-loop runs** (P3) — pause before the Portfolio Manager, interrogate any agent, inject your own view; the final decision must address it (demo mode; engine support on the roadmap)
- **Paper trading** (P3) — decisions auto-execute into a simulated book at real market prices (Buy/Overweight open, Underweight/Sell close), live unrealized/realized P&L. **No real broker orders are ever placed.**
- **Ensemble & arbitration** (P4) — one analysis across 2–3 model stacks in parallel; a meta-judge rules unanimous/majority/split and surfaces dissent; provider scoreboard ranks stacks by realized alpha
- **Event-driven triggers** (P4) — price-move triggers (±N% day move, checked every 10 min) fire analyses and alerts automatically
- **Agent Studio** (P4) — override any agent's persona or add custom analysts (name, mandate, tool selection) to the pipeline (demo runner today; engine prompt overrides roadmapped)
- **Research library** (P4) — ingest real SEC EDGAR 10-K/10-Q filings + index your reports; FTS5 full-text search with BM25 snippets; analyses cite matching passages with point-in-time filtering
- **Super-admin SaaS dashboard** — platform stats (users, runs, tokens, ratings, top tickers) and user management (promote/demote/delete). Bootstrap account: `AGENTALGO_ADMIN_EMAIL` / `AGENTALGO_ADMIN_PASSWORD` (dev default `admin@agentalgo.dev` / `admin12345` — **change in production**). Admins see a Research/Admin switch in the top bar.

## Free engine runs — Cowork bridge (no API key)

`bridge/cowork_bridge.py` is a local OpenAI-compatible server (port 8765) that routes
every engine LLM call through the **Claude Code CLI**, so real TradingAgents runs bill
your existing Claude subscription instead of a metered API key. It emulates tool
calling and structured output on top of `claude -p`, and neutralizes any global
`~/.claude` base-URL redirection (Ollama/OpenRouter) via its own `--settings` override —
your global CLI config is untouched.

One-time setup: `claude setup-token` in Terminal (browser OAuth on your subscription).
Then in AgentAlgo: Settings → API Keys → *OpenAI-compatible / Cowork bridge* →
Base URL `http://127.0.0.1:8765/v1` (no secret) → and run analyses in **Live engine**
mode with provider *OpenAI-compatible*, custom model id `cowork` (or `cowork-haiku`
for speed). The bridge starts/stops with `backend/run_dev.sh`.

Notes: personal use on your own machine; subscription rate limits apply; the bridge
listens on 127.0.0.1 only and runs at most 2 concurrent CLI calls.

## Local development (macOS/Linux)

```bash
# backend
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python -e ../../TradingAgents   # optional: engine mode
.venv/bin/uvicorn app.main:app --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev            # → http://localhost:5173 (proxies /api to :8000)

# tests
cd backend && .venv/bin/python -m pytest tests/ -q
```

## Production (Docker)

```bash
cp .env.example .env    # fill AGENTALGO_SECRET_KEY + AGENTALGO_FERNET_KEY (see file)
docker compose up --build   # app on http://localhost:8080
```

Keep `--workers 1` for the backend (run state is in-process; see QUALITY_REPORT.md).

## Architecture

```
React SPA (Vite, TS) ⇄ /api + WS ⇄ FastAPI ── SQLite/Postgres (users, keys, runs,
                                     │          run_events, reports, memory)
                                     └─ RunManager (asyncio)
                                         ├─ DemoRunner (simulated pipeline)
                                         └─ EngineRunner ─ subprocess: TradingAgentsGraph
                                                           (allow-listed env, JIT keys,
                                                            LangGraph checkpoints)
```

Docs: `AgentAlgo_PRD.md` (product spec, one level up) · `QUALITY_REPORT.md` (verification + review disposition).
