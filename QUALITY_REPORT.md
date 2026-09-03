# AgentAlgo — Quality Report

**Date:** 2026-09-03 · **Version:** 1.0.0 · **Verdict: PASS (ship v1)**

## Verification summary

Every feature was verified against the **live running system** (not just unit tests), per the build mandate.

| Check | Method | Result |
|---|---|---|
| Health/boot | `GET /api/health` on live server | ✅ ok, engine_available: true |
| F1 Auth | Live curl: register/login/me/dup(409)/bad-pw(401)/no-token(401) + browser login | ✅ |
| F2 Key vault | Live: save→masked (`sk-…a4f2`), encrypted at rest (DB inspected), real probe against api.openai.com flagged fake key invalid, delete | ✅ |
| F3 Wizard/catalog | Live: 20 providers, models per provider, ticker normalization (btcusdt→BTC-USD crypto; RELIANCE.NS→NSE/INR/^NSEI), UI screenshot-verified | ✅ |
| F4/F5 Run engine + live view | Live WebSocket client: 12 agents streamed, 7 report sections in engine order, tool calls, stats; browser run started from UI and watched to completion (NVDA → Overweight) | ✅ |
| F7 Reports/history | Live: sectioned reports, complete_report.md export (engine §I–V structure), history filters, delete | ✅ |
| F8 Memory | Live: pending entry auto-created; resolved AAPL with **real yfinance data** (raw −0.05%, alpha −0.50% vs SPY), reflection written; F8.3 verified — next NVDA run's Final Decision cited "AAPL 2026-09-01: Underweight → alpha −0.5%" | ✅ |
| F9 Recovery | Live: cancel mid-run → resume → completed with all 7 sections (completed sections skipped) ; server-restart marks stale runs interrupted | ✅ |
| F10 Settings/presets | Live + tests: validation rejects unknown/ill-typed keys loudly; benchmark override applied to new runs | ✅ |
| Engine mode (real TradingAgents) | Engine installed (18-provider catalog imports); live engine run spawned subprocess, resolved instrument identity, streamed events, failed cleanly on invalid key → interrupted + resumable | ✅ structural (full E2E needs a real LLM key) |
| Test suite | 27 pytest tests incl. full run lifecycle, tenant isolation, determinism, 5 review-regression tests | ✅ 27/27 |
| Frontend | `tsc -b` clean, production build clean, all 8 pages screenshot-verified in Chrome | ✅ |
| Super-admin dashboard | Bootstrap login verified live; stats/users/toggle/delete tested (4 tests incl. 403 for non-admins, self-demotion guard); Admin UI screenshot-verified with real platform data | ✅ |

## Independent code review (fresh-context reviewer, author-bias eliminated)

Verdict was **Request Changes** with 19 findings. Disposition:

| ID | Finding | Severity | Status |
|---|---|---|---|
| C1 | FK violation crashed run/account deletion | Critical | **Fixed** + regression tests |
| S1 | Stored XSS via unsanitized markdown (LLM/report content) | High | **Fixed** (DOMPurify) |
| S2 | Full env + all user keys passed to engine subprocess | High | **Fixed** (allow-listed env, per-provider keys only, AGENTALGO_* stripped) |
| C2 | URL-only providers (Ollama) 500 on save | High | **Fixed** + regression test |
| C3 | Multi-worker breaks in-process RunManager | High | **Mitigated**: Dockerfile pins `--workers 1` + documented; Redis/DB-backed state is the Phase-2 fix |
| S7 | `mode` string bypassed demo gate | Medium | **Fixed** (Literal) + regression test |
| C4 | Blocking DB commits on event loop | Medium | **Fixed** for the hot path (`emit` → to_thread) |
| C5 | WS could hang if end-sentinel dropped | Medium | **Fixed** (drain-and-retry) |
| C6 | Concurrency-cap race | Low | **Fixed** (cap enforced under manager lock) |
| C7 | Benchmark/data-vendor defaults were dead code | Medium | **Fixed** + regression test |
| C9 | Timezone edge on trade date | Low | **Fixed** (+1 day slack) |
| S8 | Key-shaped strings in stored errors | Low | **Fixed** (regex scrub + truncate) |
| S3 | JWT in WS query string | Medium | **Accepted for v1** (localhost/dev; ticket: stream tickets) |
| S4 | SSRF via user base_url probe | Medium | **Accepted for v1** (BYO-key single-tenant risk; ticket: private-range blocklist behind flag) |
| S5 | Keys in probe URLs (Google/AV/FRED) | Low | Accepted (AV/FRED are key-in-URL by design; ticket: Google header) |
| S6 | No auth rate limiting | Medium | Ticket (slowapi/Redis in production deployment) |
| C8 | Memory resolve edge cases (holding window, crypto/SPY calendars) | Low-Med | Ticket |
| C10 | No DB unique on (run_id, seq) | Low | Ticket (needs migration; in-process discipline + resume max-seq guard hold today) |
| C11 | Azure/Bedrock extra fields not collectible in UI | Low | Ticket (fields exist in API `extra`; UI form pending) |
| P1/P2 | Event replay volume / gap resync | Med/Low | Ticket (replay reports from run_reports; `gap` marker) |

## Known limitations (v1, by design)
- Engine-mode reflection/decision-log inside the engine's own `trading_memory.md` is per-user (isolated dir) but separate from the platform decision log; both operate.
- Demo mode content is simulated and labeled as such on every report.
- Single-process deployment; horizontal scale requires externalizing RunManager state.

## Disclaimer
AgentAlgo is a research tool built on TradingAgents (Apache-2.0, TauricResearch). Nothing it produces is financial, investment, or trading advice.
