"""Phase 3 tests: human-in-the-loop (A5) and paper trading (A2)."""
import time

import pytest


def _poll_status(client, auth, rid, target, timeout=120):
    for _ in range(timeout * 2):
        r = client.get(f"/api/runs/{rid}", headers=auth).json()
        if r["status"] in (target if isinstance(target, tuple) else (target,)):
            return r
        time.sleep(0.5)
    raise TimeoutError(f"never reached {target}")


def test_hitl_engine_mode_rejected(client, auth):
    r = client.post("/api/runs", headers=auth, json={
        "ticker": "NVDA", "trade_date": "2026-09-01", "mode": "engine",
        "llm_provider": "openai", "hitl": True})
    assert r.status_code == 422 and "demo-mode only" in r.json()["detail"]


def test_hitl_pause_ask_proceed(client, auth):
    run = client.post("/api/runs", headers=auth, json={
        "ticker": "HOOD", "trade_date": "2026-09-01", "research_depth": 1,
        "mode": "demo", "hitl": True}).json()
    rid = run["id"]

    # reaches the breakpoint
    paused = _poll_status(client, auth, rid, "paused")
    assert paused["status"] == "paused"

    # interrogate agents while paused
    r = client.post(f"/api/runs/{rid}/ask", headers=auth,
                    json={"agent": "Bear Researcher", "question": "What breaks your thesis?"})
    assert r.status_code == 200 and r.json()["answer"].startswith("[demo persona]")
    assert client.post(f"/api/runs/{rid}/ask", headers=auth,
                       json={"agent": "Nonexistent", "question": "hi"}).status_code == 422

    # inject the user's view and resume
    r = client.post(f"/api/runs/{rid}/proceed", headers=auth,
                    json={"user_view": "I hold a long position and want a tighter stop."})
    assert r.status_code == 200 and r.json()["user_view_recorded"] is True

    final = _poll_status(client, auth, rid, "done")
    assert final["rating"]
    reports = {x["section"]: x["content_md"]
               for x in client.get(f"/api/runs/{rid}/reports", headers=auth).json()}
    decision = reports["final_trade_decision"]
    assert "User view considered" in decision and "tighter stop" in decision
    assert "user↔agent exchange" in decision

    # proceed on a finished run → 409
    assert client.post(f"/api/runs/{rid}/proceed", headers=auth, json={"user_view": ""}).status_code == 409


@pytest.fixture()
def fixed_price(monkeypatch):
    from app import paper

    monkeypatch.setattr(paper, "_last_price_sync", lambda ticker: 100.0)
    yield
    # monkeypatch auto-restores


def _make_done_run(uid, ticker, rating):
    from app.db import SessionLocal
    from app.models import Run

    with SessionLocal() as db:
        run = Run(user_id=uid, ticker=ticker, trade_date="2026-09-01",
                  config_json="{}", status="done", rating=rating)
        db.add(run)
        db.commit()
        return run.id


def test_paper_trading_lifecycle(client, auth, fixed_price):
    import asyncio

    from app.db import SessionLocal
    from app.models import User
    from app.paper import execute_decision

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id

    # disabled by default: no position
    rid = _make_done_run(uid, "PPR", "Buy")
    assert asyncio.run(execute_decision(rid)) is None

    # enable paper trading
    assert client.put("/api/settings", headers=auth,
                      json={"config": {"paper_trading": True, "paper_notional": 10000}}).status_code == 200

    # Buy opens a full-notional position at the (patched) price
    rid = _make_done_run(uid, "PPR", "Buy")
    pos = asyncio.run(execute_decision(rid))
    assert pos is not None and pos.qty == 100.0 and pos.entry_price == 100.0

    # second Buy while open: no duplicate
    rid2 = _make_done_run(uid, "PPR", "Buy")
    assert asyncio.run(execute_decision(rid2)) is None

    # Hold: no action
    rid3 = _make_done_run(uid, "PPR", "Hold")
    assert asyncio.run(execute_decision(rid3)) is None

    # Sell closes at the patched price → flat pnl
    rid4 = _make_done_run(uid, "PPR", "Sell")
    closed = asyncio.run(execute_decision(rid4))
    assert closed is not None and closed.status == "closed" and closed.realized_pnl == 0.0

    # portfolio endpoint reflects it
    snap = client.get("/api/portfolio", headers=auth).json()
    tickers = [p["ticker"] for p in snap["positions"]]
    assert "PPR" in tickers
    ppr = next(p for p in snap["positions"] if p["ticker"] == "PPR")
    assert ppr["status"] == "closed"

    # manual close of an already-closed position → 409
    assert client.post(f"/api/portfolio/{ppr['id']}/close", headers=auth).status_code == 409

    client.put("/api/settings", headers=auth, json={"config": {}})


def test_portfolio_tenant_isolation(client):
    r = client.post("/api/auth/register", json={"email": "paper-other@x.co", "password": "password99"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    snap = client.get("/api/portfolio", headers=other).json()
    assert snap["positions"] == [] and snap["realized_pnl"] == 0
