# AgentAlgo — Product Requirements Document

**Version:** 1.0 · **Date:** 2026-09-03 · **Author:** Shivhari Lokhande · **Status:** Draft for build kickoff

---

## 1. Executive Summary

AgentAlgo is a multi-user SaaS web application that brings the full capability of the open-source **TradingAgents** framework (TauricResearch, v0.4.0) to the browser. TradingAgents is a LangGraph-based multi-agent LLM system that mirrors a real trading firm: analyst agents gather market, sentiment, news, and fundamentals evidence; bull and bear researchers debate it; a research manager rules; a trader drafts a plan; a three-way risk debate stress-tests it; and a portfolio manager issues the final rated decision. Today this runs only as a Python CLI. AgentAlgo turns every one of those features into a web experience: configure an analysis in a guided UI, watch agents work in real time, read structured reports, build a persistent decision memory that learns from outcomes, and manage everything across devices with a user account and bring-your-own API keys.

**Positioning:** a research and decision-support platform — not a broker, not financial advice. The product carries the same research-purpose disclaimer as the upstream framework.

## 2. Background: the TradingAgents engine

The engine AgentAlgo wraps (and reuses as-is, per §10) consists of:

| Layer | Repo modules | What it does |
|---|---|---|
| Agent graph | `tradingagents/graph/*` | LangGraph state machine: analysts → bull/bear debate → research manager → trader → risk debate → portfolio manager; conditional routing, crash-safe path maps, checkpointing, signal extraction, reflection |
| Agents | `tradingagents/agents/*` | 13 agent roles + structured-output schemas, 5-tier rating vocabulary (Buy / Overweight / Hold / Underweight / Sell + REVIEW), shared toolkits |
| Data flows | `tradingagents/dataflows/*` | Vendor-routed data access: yfinance, Alpha Vantage, FRED, Polymarket, Reddit, StockTwits, Yahoo Finance news; look-ahead (point-in-time) protection; market-data validator; symbol normalization; caching |
| LLM clients | `tradingagents/llm_clients/*` | Provider registry (OpenAI, Anthropic, Google, xAI, DeepSeek, Qwen ×2 regions, GLM ×2, MiniMax ×2, OpenRouter, Ollama, Azure, Bedrock, Mistral, Kimi, Groq, NVIDIA, any OpenAI-compatible endpoint), model catalog, capability/validation layer, retry & token budgets |
| Memory | `agents/utils/memory.py`, `graph/reflection.py` | Persistent decision log with pending → resolved lifecycle, realized return + alpha vs. benchmark, LLM-written reflections re-injected into future runs |
| CLI/UX | `cli/*` | Interactive selection flow, live progress board, token/cost stats, report assembly, i18n output language, checkpoint resume flags |
| Reporting | `tradingagents/reporting.py` | Per-section markdown tree + assembled `complete_report.md` per run |

## 3. Goals and Non-Goals

**Goals (v1):**

- G1. Full feature parity: every capability of the TradingAgents repo is reachable from the web UI — nothing requires the CLI.
- G2. Multi-user SaaS: accounts, isolated data, per-user encrypted API keys, run history.
- G3. Live observability: users watch each agent's status, messages, tool calls, and report sections stream in real time, matching or exceeding the CLI's live board.
- G4. Persistent learning: the decision log and reflection loop work per user, across sessions and devices.
- G5. Zero platform API cost: all LLM and data calls run on user-supplied keys (BYO).

**Non-goals (v1):**

- No order execution or brokerage connectivity (extension candidate, Phase 3).
- No platform-provided LLM/data keys, billing, or metered plans.
- No mobile native apps (responsive web only).
- No changes to the agent engine's core logic beyond service-hardening (the engine is consumed as a library).

## 4. Users and Personas

- **Retail researcher "Ravi":** analyzes 2–5 tickers a week, wants a decision plus the reasoning trail, uses one LLM provider key.
- **Quant hobbyist "Elena":** compares providers/models and debate depths, exports reports, cares about token cost and reproducibility settings.
- **Finance team lead "Marcus":** runs the team's recurring watchlist through the pipeline, reviews decision-log accuracy over time (Phase 2 features).

## 5. Feature Inventory — repo capability → AgentAlgo web feature (traceability)

Every repo feature maps to a numbered product feature (F#). This table is the parity contract.

| # | Repo capability | Source | AgentAlgo feature |
|---|---|---|---|
| 1 | Ticker input, validation, normalization (US, .HK, .T, .L, .NS/.BO, .TO, .AX, .SS/.SZ, crypto BTC-USD) | `cli/utils.py`, `dataflows/symbol_utils.py` | F3.1 Ticker picker with symbol search, exchange-suffix help, crypto detection |
| 2 | Analysis date selection with boundary validation | `cli/main.py`, `dataflows/date_window.py` | F3.2 Date picker (default today; historical dates for backtest-style runs) |
| 3 | Analyst team selection (market / sentiment / news / fundamentals), crypto-aware filtering | `cli/utils.py select_analysts`, `filter_analysts_for_asset_type` | F3.3 Analyst multi-select; fundamentals auto-disabled for crypto |
| 4 | Research depth (Shallow=1, Medium=3, Deep=5 debate rounds) | `cli/utils.py DEPTH_OPTIONS` | F3.4 Depth selector with round counts and cost hint |
| 5 | LLM provider selection (17+ providers incl. regional variants) | `llm_clients/factory.py`, provider registry | F3.5 Provider picker driven by which keys the user has saved |
| 6 | Quick-think + deep-think model selection, curated catalog + custom model ID, dynamic OpenRouter list | `llm_clients/model_catalog.py`, `cli/utils.py` | F3.6 Dual model pickers; custom ID field; OpenRouter models fetched live |
| 7 | Provider thinking knobs (Google thinking level, OpenAI reasoning effort, Anthropic effort) | `default_config.py`, `cli/main.py` | F3.7 Advanced settings panel, shown only for relevant provider |
| 8 | Temperature, max tokens, LLM retry budget | `default_config.py` | F3.7 Advanced settings |
| 9 | Output language (i18n reports; internal debate stays English) | `cli/utils.py ask_output_language`, i18n tests | F3.8 Language selector persisted per user |
| 10 | Data vendor routing per category + per tool, ordered fallback chains | `default_config.py data_vendors/tool_vendors`, `dataflows/interface.py` | F6.2 Data-source settings page |
| 11 | News/macro tuning: article limits, lookback days, custom global-news queries | `default_config.py` | F6.3 Advanced data settings |
| 12 | Benchmark auto-detection by exchange suffix + override | `default_config.py benchmark_map` | F6.4 Benchmark setting (auto with override) |
| 13 | Live agent status board, streamed messages & tool calls, in-progress report sections | `cli/main.py MessageBuffer`, layout | F4 Live Run view (WebSocket) |
| 14 | Token in/out, LLM call and tool call stats | `cli/stats_handler.py` | F4.5 Run stats panel + est. cost |
| 15 | Full agent pipeline: 4 analysts with tool loops, bull/bear debate, research manager, trader, aggressive/conservative/neutral risk debate, portfolio manager | `graph/setup.py`, `agents/*` | F5 Analysis engine (unchanged core) |
| 16 | Structured outputs for Research Manager / Trader / Portfolio Manager; 5-tier rating; REVIEW signal instead of fabricated Hold | `agents/schemas.py`, `agents/utils/rating.py`, `graph/signal_processing.py` | F5.3 Decision card with rating chip; REVIEW state with re-run CTA |
| 17 | Verified market snapshot / price grounding for Market Analyst & Trader | `market_data_validator.py`, `market_data_validation_tools.py` | F5.4 (engine parity) + "verified data" badge on report |
| 18 | Deterministic company-identity resolution before agents run | README, `instrument_identity` tests | F5.5 Instrument card (name, exchange, currency) shown pre-run |
| 19 | Point-in-time / look-ahead protection (news, social, FRED, memory) | `date_window.py`, lookahead tests | F5.6 (engine parity; surfaced as "as-of" labels) |
| 20 | Per-section report tree + complete report | `reporting.py` | F7 Report viewer + Markdown/PDF export |
| 21 | Decision log: pending → resolved entries, realized return + alpha vs. benchmark, LLM reflection, same-ticker + cross-ticker context injection | `agents/utils/memory.py`, `graph/reflection.py` | F8 Memory & Performance pages |
| 22 | Memory log rotation cap | `memory_log_max_entries` | F8.4 Retention setting |
| 23 | Checkpoint resume (LangGraph SQLite per ticker), clear-checkpoints | `graph/checkpointer.py`, CLI flags | F9 Run recovery (auto-resume interrupted runs; cancel/discard) |
| 24 | Env-var configuration (`TRADINGAGENTS_*`) with validation | `default_config.py` | F10 Per-user settings stored in DB (replaces env vars); server ops keep env for deployment |
| 25 | API-key auto-detection per provider | `llm_clients/api_key_env.py` | F2 Key vault: provider enabled iff key present & validated |
| 26 | Ticker path-traversal hardening, safe file components | `safe_ticker_component` tests | NFR-S3 (kept in engine; server adds its own input validation) |
| 27 | Docker / docker-compose deployment (incl. Ollama profile) | `Dockerfile`, `docker-compose.yml` | §10 Deployment: containerized services |
| 28 | Announcements surface | `cli/announcements.py` | F11 In-app announcements banner |
| 29 | Interactive CLI itself | `cli/main.py` | Superseded by web UI; CLI remains usable against the same engine |

## 6. Functional Requirements

### F1. Accounts & Workspace
- F1.1 Email/password signup with verification; OAuth (Google, GitHub) optional at v1.
- F1.2 Each user owns: API keys, settings/presets, runs, reports, decision log. Strict tenant isolation at the DB layer.
- F1.3 Session management (JWT access + refresh), password reset, account deletion (cascades all user data).

### F2. API Key Vault (bring-your-own keys)
- F2.1 Settings page listing every supported provider (all 17+ LLM providers, plus Alpha Vantage and FRED data keys). One input per key, incl. regional variants (e.g. `DASHSCOPE_CN_API_KEY`) and endpoint URLs for Ollama / OpenAI-compatible / Azure / Bedrock credentials.
- F2.2 Keys encrypted at rest (envelope encryption, e.g. AES-256-GCM with a KMS-managed master key); never returned to the client after save (masked display: `sk-…a4f2`); never logged.
- F2.3 "Test key" action performs a cheap validation call and shows pass/fail (mirrors `api_key_env` auto-detection + `validators.py`).
- F2.4 Provider picker in F3 only offers providers whose required credentials are present and valid.
- F2.5 Ollama/vLLM/LM Studio: user supplies a base URL instead of a key (must be reachable from the worker; document the tunnel requirement).

### F3. New Analysis (configuration wizard)
Single page or 2-step wizard replicating the CLI selection flow:
- F3.1 **Ticker** — free-text with normalization (`BTCUSD → BTC-USD`, case, suffix handling), instrument identity preview card (company name, exchange, currency, asset type, auto-benchmark). Invalid tickers rejected before run start.
- F3.2 **Analysis date** — defaults to today; any historical date allowed (point-in-time engine guarantees apply); future dates rejected.
- F3.3 **Analysts** — multi-select of Market, Sentiment (Social), News, Fundamentals; ≥1 required; crypto asset type filters non-applicable analysts exactly as `filter_analysts_for_asset_type` does.
- F3.4 **Research depth** — Shallow (1 round), Medium (3), Deep (5); maps to `max_debate_rounds` / `max_risk_discuss_rounds`; shows estimated LLM-call count.
- F3.5 **Provider** — from user's validated keys.
- F3.6 **Models** — quick-think + deep-think from `model_catalog.py`; "Custom model ID" always available; OpenRouter list fetched live server-side.
- F3.7 **Advanced** (collapsed by default) — temperature, max output tokens, LLM retry budget, provider thinking knob (Google thinking level / OpenAI reasoning effort / Anthropic effort — shown contextually), checkpoint on/off.
- F3.8 **Output language** — persisted user default; internal debate remains English (engine behavior).
- F3.9 **Presets** — save/load named configurations; "Run again with same settings" from any past run.

### F4. Live Run View (web equivalent of the CLI live board)
- F4.1 Pipeline visualization: all agents in their team groupings (Analyst Team → Research Team → Trader → Risk Team → Portfolio Manager) with per-agent status: pending / in-progress / done / error.
- F4.2 Streaming message feed: agent messages, tool calls with arguments (truncated like `format_tool_args`), classified by type — delivered over WebSocket from the LangGraph stream.
- F4.3 Report sections render progressively as each agent completes (market report, sentiment report, news report, fundamentals report, investment plan, trader plan, risk decision, final decision).
- F4.4 Run controls: cancel (graceful stop + checkpoint), and on reconnect/refresh the view replays state from the run event log — no lost progress.
- F4.5 Stats panel: LLM calls, tool calls, tokens in/out (from `stats_handler` callbacks), elapsed time, estimated cost (client-side price table per model, editable).
- F4.6 Concurrency: a user may run multiple analyses in parallel up to a per-user limit (default 3, configurable per deployment).

### F5. Analysis Engine (parity requirements — engine consumed as a library)
- F5.1 The `tradingagents` package runs unmodified inside worker processes; AgentAlgo passes a per-run config dict (equivalent of `_build_run_config`) assembled from user settings; user API keys are injected into the worker environment per run, never persisted on disk.
- F5.2 Full graph parity: analyst tool loops with message-clear nodes, bull/bear debate rounds, research manager judgment, trader plan, aggressive/conservative/neutral risk debate, portfolio manager final decision, crash-safe path maps.
- F5.3 Decision output: 5-tier rating (Buy / Overweight / Hold / Underweight / Sell) extracted by `signal_processing`; a REVIEW result renders a distinct "needs review" state with a one-click re-run, never a fabricated Hold.
- F5.4 Verified market snapshot grounding for price/indicator claims; report viewer shows a "price-grounded" badge with snapshot timestamp.
- F5.5 Deterministic instrument identity resolved before the run and displayed in the run header.
- F5.6 Point-in-time discipline: for historical dates, news/social/macro/memory context respects as-of filtering (engine behavior); UI labels every data section with its as-of window.
- F5.7 Structured-output agents (Research Manager, Trader, Portfolio Manager) with schema validation; malformed output surfaces as run warning, not silent failure.

### F6. Data Layer & Vendor Routing
- F6.1 Supported vendors at parity: yfinance (OHLCV, indicators via stockstats, fundamentals, news), Alpha Vantage (stock, indicators, fundamentals, news — with hardening/backoff), FRED (macro indicators), Polymarket (prediction markets, keyless), Reddit + StockTwits (social sentiment with fallback), Yahoo Finance news, insider transactions.
- F6.2 Data-source settings: per category (core_stock_apis, technical_indicators, fundamental_data, news_data, macro_data, prediction_markets) choose an exact vendor chain with ordered fallback (e.g. `yfinance, alpha_vantage`); optional per-tool override. No silent routing to unchosen vendors.
- F6.3 Tunables: news article limit, global news article limit & lookback days, editable global-news query list.
- F6.4 Benchmark: auto by exchange suffix (`benchmark_map`) with per-user override ticker.
- F6.5 Server-side response caching (per vendor+symbol+window) with the engine's freshness guards (stale-OHLCV protection) to cut duplicate spend across a user's runs.

### F7. Reports & History
- F7.1 Run history list: ticker, date, rating chip, provider/models, depth, duration, token totals; filter by ticker/rating/date; full-text search over reports.
- F7.2 Report viewer: sectioned exactly like `complete_report.md` — I. Analyst Team Reports, II. Research Team Decision, III. Trading Team Plan, IV. Risk Management Team Decision, V. Portfolio Manager Decision — with per-agent tabs and the raw debate transcript available.
- F7.3 Export: Markdown download (parity with report tree) and rendered PDF; shareable read-only link (auth-gated, revocable) — v1 nice-to-have, Phase 2 firm.
- F7.4 Compare view: open two runs of the same ticker side-by-side (Phase 2).

### F8. Memory & Performance (decision log)
- F8.1 Per-user decision log replicates `trading_memory.md` semantics in the DB: each completed run appends a pending entry `[date | ticker | rating | pending]` + decision summary.
- F8.2 Outcome resolution: a scheduled job (and on-demand "resolve now") fetches realized return and alpha vs. the run's benchmark once the horizon has data, generates the 2–4 sentence LLM reflection (Reflector), and marks the entry resolved — using the user's own LLM key at reflection time (skip + retry later if key absent).
- F8.3 Context injection parity: on a new run, the engine receives the most recent same-ticker resolved decisions plus recent cross-ticker lessons, respecting as-of dates for historical runs.
- F8.4 Memory page: table of all entries (pending/resolved), rating vs. outcome, alpha, reflection text; aggregate stats (hit rate by rating tier, average alpha); retention cap setting (oldest resolved pruned; pending never pruned).

### F9. Run Recovery (checkpointing)
- F9.1 Checkpointing on by default for web runs (LangGraph checkpointer, per-run scope, Postgres- or SQLite-backed per worker).
- F9.2 If a worker dies or the run is interrupted, the run shows "interrupted — resumable"; Resume continues from the last successful node ("Resuming from step N"); Discard clears the checkpoint.
- F9.3 Checkpoints auto-clear on successful completion; stale checkpoints garbage-collected after N days.

### F10. Settings & Defaults
- F10.1 Every `TRADINGAGENTS_*` env override becomes a per-user setting with the same validation semantics (invalid values rejected loudly at save time, mirroring `_coerce`).
- F10.2 User default preset applied to each new analysis; per-run overrides never mutate defaults.
- F10.3 Deployment-level env config remains for operators (DB URLs, encryption keys, worker counts).

### F11. Platform surfaces
- F11.1 Dashboard: recent runs, pending memory resolutions, quick "analyze again" for recent tickers.
- F11.2 Announcements banner (product news; parity with `cli/announcements.py`).
- F11.3 Persistent research-purpose disclaimer in footer and on every decision card ("not financial, investment, or trading advice"), linking to the Tauric disclaimer language.

## 7. Phase 2+ Extensions (beyond repo parity)

| Phase | Feature | Description |
|---|---|---|
| 2 | Watchlists | Named ticker lists; run the pipeline across a list; grid of latest ratings |
| 2 | Scheduled analyses | Cron-style schedules (e.g. every weekday 07:00) per ticker/watchlist using saved presets; email/in-app notification with decision summary |
| 2 | Alerts | Notify when a scheduled run's rating changes tier vs. previous run, or when REVIEW occurs |
| 2 | Run comparison | Side-by-side diff of two runs (models, depth, decision, key arguments) |
| 3 | Portfolio dashboard | Track hypothetical positions from decisions; P&L and alpha over time built on the decision log |
| 3 | Backtesting UI | Batch historical runs over a date range with the point-in-time engine; equity-curve visualization; cost estimator and guardrails before launch |
| 3 | Team workspaces | Shared watchlists, runs, and memory within an organization; role-based access |
| 3 | Broker/paper-trade integration | Route approved decisions to a paper-trading API (explicitly out of v1) — superseded by A2 below |

### 7.1 Advanced Differentiators (Phase 3–4)

These six features go beyond parity and define AgentAlgo's moat. Each depends on the v1 platform (runs, memory, presets) being in place.

**A1. Agent Studio** — no-code multi-agent pipeline customization.
- A1.1 Prompt editor per agent role with versioning, diff view, and one-click revert to stock prompt; per-preset prompt overrides.
- A1.2 Custom agents: user defines a new analyst (name, persona prompt, tool selection from the existing toolkit) and inserts it into the analyst stage; engine builds the LangGraph plan dynamically (extends `build_analyst_execution_plan`).
- A1.3 Visual pipeline view: read-only graph in v1 of Studio; drag-drop topology editing (reorder analysts, debate round counts per stage, skip stages) as a follow-on.
- A1.4 Template gallery: save, share, and import agent/pipeline configs (JSON schema, validated server-side; prompts sandboxed — no tool or system access beyond the declared toolkit).
- Dependencies: F3 presets, F5 engine wrapper. Risk: upstream graph refactors — isolate behind an adapter that owns graph construction.

**A2. Broker Integration & Paper-Trading Loop** — close the learning loop with real outcomes.
- A2.1 Connectors: Zerodha Kite Connect (NSE/BSE — `.NS`/`.BO` tickers already supported by the engine) and Alpaca paper trading (US). OAuth/token flow per user; credentials in the F2 key vault.
- A2.2 Paper portfolio: on user approval (or auto-execute toggle per watchlist), a Buy/Overweight/Sell/Underweight decision opens/adjusts a simulated position sized by a simple rule (fixed fraction v1; A6-aware sizing later). Hold = no action; REVIEW never executes.
- A2.3 Daily P&L sync and automatic outcome resolution: realized/unrealized return and alpha flow into the F8 decision log without manual "resolve now", with reflections generated on schedule.
- A2.4 Execution safety: paper-only by default; live execution explicitly out of scope until a dedicated compliance review (own PRD).
- Dependencies: F8 memory, scheduler (Phase 2). Metrics: % of decisions auto-resolved within 7 days.

**A3. Ensemble & Model Arbitration** — run one analysis, N model stacks.
- A3.1 Ensemble run type: same ticker/date/config executed in parallel across 2–3 user-selected provider/model stacks (cost estimate shown ×N up front).
- A3.2 Meta-judge agent compares the final decisions, highlights agreements/disagreements per section (market read, thesis, risk), and emits a consensus card (unanimous / split, with the dissenting argument quoted).
- A3.3 Provider scoreboard: per-user table of realized alpha and hit rate by provider/model/depth, computed from resolved decision-log entries (needs A2 or manual resolution for data).
- Dependencies: F4 (parallel run infra exists via concurrency), F8. Guardrail: hard token budget per ensemble run.

**A4. Deep Research Grounding (RAG)** — citations to primary sources.
- A4.1 Ingestion: SEC EDGAR filings (10-K/10-Q/8-K) and earnings-call transcripts for the analyzed ticker, chunked and embedded into a per-user (later shared read-only) vector store; all AgentAlgo reports auto-indexed.
- A4.2 Fundamentals and News analysts get a retrieval tool; reports render inline citations linking to the exact source passage (viewer side-panel).
- A4.3 Research search: semantic search across the user's report history and indexed filings ("what did we say about NVDA margins in March?").
- A4.4 Point-in-time discipline extends to RAG: retrieval filtered to documents filed on or before the analysis date.
- Dependencies: F6 data layer, F7 report viewer. Stack: pgvector; embeddings on user's LLM key where the provider offers them, else a configurable embedding provider.

**A5. Human-in-the-Loop Runs** — from black box to collaborator.
- A5.1 Breakpoint mode: run pauses after the risk debate, before the Portfolio Manager (LangGraph interrupt); state persists via the F9 checkpointer indefinitely.
- A5.2 Interrogation chat: while paused, user chats with any agent in that run's context ("Bear: what breaks your thesis?"); exchanges are appended to run state and visible to the Portfolio Manager.
- A5.3 View injection: user submits their own position/notes as a first-class input the Portfolio Manager must address in its decision.
- A5.4 Resume/abort controls in the Live Run view; paused runs surfaced on the dashboard.
- Dependencies: F4, F9. This is the primary UX for "Deep" runs once shipped.

**A6. Event-Driven Autonomy** — a semi-autonomous research desk.
- A6.1 Triggers per ticker/watchlist: earnings date (T-1 and T+1), price move > X% intraday/close, volume z-score spike, news-volume spike; each trigger fires a run with a chosen preset.
- A6.2 Trigger evaluation service polls vendor data on the user's keys at a bounded cadence; per-user daily run and spend caps, quiet hours, and dedup (one run per ticker per trigger per day).
- A6.3 Notifications: in-app + email with the decision card; escalation when rating changes tier vs. the last run or when REVIEW occurs (extends Phase 2 alerts).
- A6.4 Combined with A2 auto-execute, forms the full loop: event → analysis → paper trade → outcome → reflection. Every autonomous action is logged and reversible.
- Dependencies: Phase 2 scheduler/alerts, A2 for the full loop.

**Sequencing recommendation:** A5 and A2 first (deepen trust and close the learning loop), then A3/A4 (research quality), then A1 and A6 (power-user surface area).

## 8. Non-Functional Requirements

- **NFR-S1 Security:** BYO keys encrypted at rest (per-user data key, KMS master); TLS everywhere; keys injected into workers per-run in memory only; audit log of key create/delete/test events; OWASP ASVS L2 target.
- **NFR-S2 Isolation:** per-user rate limits and run concurrency caps; one run's failure can't affect another (process-isolated workers).
- **NFR-S3 Input hardening:** server-side ticker/date validation in addition to the engine's safe-ticker-component protections; all user text sanitized in UI.
- **NFR-P1 Performance:** run start latency < 5 s from submit to first agent event; WebSocket event latency < 1 s; UI usable on 100+ run histories.
- **NFR-P2 Throughput:** horizontal worker scaling; a run's duration is dominated by LLM latency (typ. 3–12 min at Medium depth) — the platform must handle ≥ 50 concurrent runs per worker pool node group.
- **NFR-R1 Reliability:** interrupted runs always resumable (F9); event log is the source of truth for replay; zero data loss on worker restart.
- **NFR-C1 Compliance:** research-tool disclaimer on every decision surface; no advice language in UI copy; user data export & deletion (GDPR-style).
- **NFR-O1 Observability:** structured logs per run, error taxonomy matching `dataflows/errors.py` vendor errors, provider-failure dashboards.

## 9. Success Metrics

- Activation: ≥ 60% of new signups save a key and complete a first run within 24 h.
- Reliability: ≥ 98% of started runs reach a final decision or a resumable checkpoint.
- Parity: 100% of the §5 traceability rows demonstrable in the web UI (release gate).
- Engagement: median ≥ 3 runs/user/week among weekly actives; ≥ 40% of users with 5+ runs have ≥ 1 resolved memory entry.

## 10. Technical Architecture

**Stack:** FastAPI (Python 3.12) backend · React + TypeScript (Vite) frontend · PostgreSQL · Redis · Celery (or RQ/Arq) workers running the unmodified `tradingagents` package · WebSockets (FastAPI) for run streaming · Docker Compose for dev, Kubernetes-ready for prod.

```
[React SPA] ⇄ HTTPS/WSS ⇄ [FastAPI API + WS gateway]
                                │            │
                          [PostgreSQL]   [Redis broker]
                                │            │
                        [Celery workers: TradingAgentsGraph per run]
                                │
                 [LLM providers + data vendors (user keys)]
```

**Key decisions:**
1. **Engine as a library, not a fork.** Workers import `TradingAgentsGraph` and call `propagate()` with a per-run config; LangGraph's `stream()` events are relayed to Redis pub/sub → WebSocket. Upstream updates are absorbed by bumping the dependency.
2. **Event-sourced runs.** Every agent/tool/report event is appended to a `run_events` table; the live view and any later replay render from the same log.
3. **Key injection.** Worker decrypts the user's keys just-in-time, sets them in the run's process environment (matching `api_key_env` detection), and scrubs on completion.
4. **Checkpointing** uses LangGraph's checkpointer pointed at per-run storage; run resumption re-enqueues the same run ID.
5. **Memory in Postgres**, exposed to the engine through its `memory_log_path` file interface materialized per run (or a thin adapter), preserving pending/resolved semantics.

**Core data model (simplified):**
`users` · `api_keys(user_id, provider, ciphertext, status)` · `presets(user_id, name, config_json)` · `runs(id, user_id, ticker, trade_date, config_json, status, rating, decision_summary, stats_json, started_at, finished_at)` · `run_events(run_id, seq, type, agent, payload_json, ts)` · `run_reports(run_id, section, content_md)` · `memory_entries(user_id, ticker, trade_date, rating, summary, status, raw_return, alpha, benchmark, reflection, resolved_at)` · `checkpoints(run_id, blob/ref)`

**API sketch:** `POST /auth/*` · `GET/POST/DELETE /keys` + `POST /keys/{provider}/test` · `GET /catalog/providers|models` · `POST /runs` · `GET /runs`, `GET /runs/{id}` · `POST /runs/{id}/cancel|resume` · `WS /runs/{id}/stream` · `GET /runs/{id}/report(.md|.pdf)` · `GET/PUT /settings` · `GET /memory`, `POST /memory/resolve` · `GET/POST /presets`

## 11. Milestones

| Milestone | Scope | Target |
|---|---|---|
| M0 — Skeleton | Repo/monorepo setup, auth, key vault, provider catalog API | Week 2 |
| M1 — First run | Config wizard (F3), worker executing the engine, basic live view, report viewer | Week 5 |
| M2 — Full live UX | Complete F4 (statuses, stats, streaming sections), run history, exports | Week 7 |
| M3 — Memory + recovery | F8 decision log & resolution job, F9 checkpoint resume, settings (F10) | Week 9 |
| M4 — Hardening & launch | NFRs, disclaimer/compliance pass, Docker/K8s deploy, parity audit vs. §5 | Week 11 |
| Phase 2 | Watchlists, schedules, alerts, comparison | Post-launch |
| Phase 3 | A5 Human-in-the-loop, A2 Broker/paper-trading loop (Kite, Alpaca) | Post-Phase 2 |
| Phase 4 | A3 Ensemble, A4 RAG grounding, A1 Agent Studio, A6 Event-driven autonomy | Sequenced per §7.1 |

## 12. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Long-running LLM runs (5–15 min) hit web timeouts | Broken UX | Async workers + WS streaming; never block HTTP; resumable checkpoints |
| User key misuse concerns | Trust | Encryption, masking, JIT injection, audit log, delete-anytime |
| Upstream engine changes (fast-moving repo, v0.2→v0.4 in 6 months) | Rework | Pin version; consume as library; parity tests against the §5 matrix per upgrade |
| Vendor rate limits (Alpha Vantage free tier, Reddit) | Failed runs | Engine's fallback chains + caching (F6.5); clear vendor-error surfacing (`errors.py` taxonomy) |
| Cost surprises for users (deep runs on frontier models) | Churn | Pre-run cost estimate (F3.4/F4.5), token caps, per-run budget warning |
| Regulatory perception (financial advice) | Legal | Research-only positioning, persistent disclaimers, no execution in v1 |

## 13. Open Questions

1. Reflection-job LLM spend happens on user keys in the background — require an explicit user opt-in toggle? (default: on, using quick-think model)
2. PDF export engine (WeasyPrint vs. headless Chromium) — decide at M2.
3. Shareable read-only report links in v1 or Phase 2?
4. Self-host distribution (docker-compose bundle of AgentAlgo itself) as a parallel offering?

---

*AgentAlgo wraps the TradingAgents research framework (Apache-licensed, TauricResearch). It is a research tool; nothing it produces is financial, investment, or trading advice.*


