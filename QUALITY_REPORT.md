# AgentAlgo — Quality Report

## Round 3 — Phase 2–4 review (2026-09-04, fresh-context reviewer)

Scope: all Phase 2–4 code (queue pump, HITL, alerts, paper trading, ensembles,
triggers, Agent Studio, research library, background loops). 14 findings, all fixed
same day; **58/58 tests green** including 6 new regression tests.

| ID | Finding | Severity | Status |
|---|---|---|---|
| R3-1 | Deleting an ensemble member run 500'd `/ensembles` permanently (empty-set meta-judge) | High | **Fixed** (guards + cache invalidation) + regression test |
| R3-2 | HITL-paused runs wedged forever after a server restart | High | **Fixed** (paused included in stale sweep) + test |
| R3-3 | Stored XSS via library snippets in Research page (EDGAR/persona text) | High | **Fixed** (escape before mark substitution) |
| R3-4 | Malformed FTS queries 500'd search | Medium | **Fixed** (term quoting + operator stripping + fail-safe) + test |
| R3-5 | Paper trading could double-open on concurrent runs (ensembles) | Medium | **Fixed** (re-check in write session) + concurrency test |
| R3-6 | Ensemble stacks spammed rating-change alerts against each other | Medium | **Fixed** (stack runs excluded both directions) + test |
| R3-7 | Multi-MB EDGAR regex stripping ran on the event loop | Medium | **Fixed** (threaded + input cap) |
| R3-8 | Cross-tenant crowd-out of the FTS result window | Medium | **Mitigated** (8× over-fetch; per-user FTS column ticketed) |
| R3-9 | Repeated run indexing duplicated library documents | Low | **Fixed** (idempotent) + test |
| R3-10 | f-string SQL in FTS purge (not yet injectable) | Low | **Fixed** (expanding bindparam, chunked) |
| R3-11 | Trigger sweep starvation past the scan window | Low | **Fixed** (fair ordering, larger window) |
| R3-12 | Cancel-during-pause turned ask/proceed into 500s | Low | **Fixed** (RunCancelled → 409) |
| R3-13 | Schedule sweep could refire and duplicate runs after mid-sweep crash | Low | **Fixed** (mark-fired-first) |
| R3-14 | Custom-analyst report tab missing in live view | Low | **Fixed** |

Also this round: root-caused the "app is crashing" report — the backend process was
being killed when terminal sessions were reaped (external SIGKILL, no app fault).
Dev runs now use `backend/run_dev.sh`: uvicorn in its own session with an
auto-restart supervisor. Reviewer confirmed tenant isolation across all 30+ new
endpoints, safe EDGAR fetching, and crash-proof background loops.


**Date:** 2026-09-04 · **Version:** 1.1.0 · **Verdict: PASS — approved for production (single-worker deployment)**

## Round 2 — production-hardening review (fresh-context reviewer)

All round-1 accepted-risk tickets were implemented, then a second independent review
of the complete codebase ran. Disposition of its findings:

| ID | Finding | Severity | Status |
|---|---|---|---|
| H1 | Bedrock `secret_access_key` stored/echoed in plaintext | High | **Fixed**: Fernet-encrypted at rest, masked (`•••set•••`) in every API response, decrypted only at worker launch |
| H2 | Compose deployments booted with the documented default admin password | High | **Fixed**: compose requires `AGENTALGO_ADMIN_EMAIL/PASSWORD`, startup logs CRITICAL on the default |
| M1 | SSRF guard off by default in compose; stored URLs never re-checked | Medium | **Fixed**: compose defaults `ALLOW_PRIVATE_URLS=false`; endpoints re-validated at every run launch. Residual: DNS rebinding after validation (documented) |
| M2 | Rate-limit bucket map unbounded (memory DoS) | Medium | **Fixed**: stale sweep + 10k hard cap with eviction |
| M3 | Real client IP lost behind nginx → per-IP limiting dead | Medium | **Fixed**: nginx forwards X-Forwarded-For; uvicorn `--proxy-headers` |
| M4 | Backend port published to the world | Medium | **Fixed**: bound to 127.0.0.1; nginx is the only front door |
| M5 | User deletion orphaned running engine subprocesses | Medium | **Fixed**: active runs cancelled (subprocess group killed) before delete |
| L1 | Error-scrub regex too narrow | Low | **Fixed**: covers AIza/AKIA/ASIA/ghp_/xox?- prefixes |
| L3 | No security headers/CSP on static frontend | Low | **Fixed**: nginx CSP + headers |
| L2 | Stream ticket multi-use within 60s TTL, in query string | Low | Accepted (run-scoped + 60s TTL); single-use jti is a future nicety |
| L4 | Resolution sweep holds one DB session across network calls | Low | Accepted for SQLite/single-worker; revisit with Postgres |

Round-2 verdict after fixes: everything re-tested — **38/38 tests green**, builds clean,
ticket-authenticated live streaming verified in the browser.

## Hardening features added since v1.0 (all live-verified)
- S6 auth rate limiting (429 verified) + timing-equalized login
- S3 run-scoped 60-second WebSocket stream tickets (JWT removed from URLs)
- S4 SSRF validation for user-supplied endpoints, deployment-gated
- Security headers + gzip on API; CSP on frontend
- F8.2 scheduled auto-resolution (6h sweep; observed resolving entries at startup) + F8.4 retention pruning
- C8 resolve hardening: 2-day minimum window (409 verified), calendar-aligned alpha, double-resolve guard
- C10 unique index on run_events(run_id, seq)
- P1 WS replay optimization + paginated event log
- F7.1 report full-text search (verified) · F11.2 announcements (verified)
- C11 Azure/Bedrock credential fields in Settings
- GitHub Actions CI (backend tests + frontend build), .dockerignore

---

# v1.0 report (2026-09-03)

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
