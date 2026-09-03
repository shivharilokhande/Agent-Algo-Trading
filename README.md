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
- **Super-admin SaaS dashboard** — platform stats (users, runs, tokens, ratings, top tickers) and user management (promote/demote/delete). Bootstrap account: `AGENTALGO_ADMIN_EMAIL` / `AGENTALGO_ADMIN_PASSWORD` (dev default `admin@agentalgo.dev` / `admin12345` — **change in production**). Admins see a Research/Admin switch in the top bar.

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
