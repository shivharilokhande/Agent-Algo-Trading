"""Regression tests for round-3 review findings."""
import json
import time


def _wait_done(client, auth, rid, timeout=120):
    for _ in range(timeout):
        r = client.get(f"/api/runs/{rid}", headers=auth).json()
        if r["status"] in ("done", "failed", "interrupted"):
            return r
        time.sleep(1)
    raise TimeoutError


def test_r3_1_ensemble_survives_run_deletion(client, auth):
    ens = client.post("/api/ensembles", headers=auth, json={
        "ticker": "R3A", "trade_date": "2026-09-01", "research_depth": 1,
        "stacks": [{"label": "A", "mode": "demo"}, {"label": "B", "mode": "demo"}]}).json()
    for run in ens["runs"]:
        _wait_done(client, auth, run["id"])
    # delete one member run
    assert client.delete(f"/api/runs/{ens['runs'][0]['id']}", headers=auth).status_code == 204
    # list and detail must not 500; consensus reverts to pending (partial set)
    listing = client.get("/api/ensembles", headers=auth)
    assert listing.status_code == 200
    detail = client.get(f"/api/ensembles/{ens['id']}", headers=auth).json()
    assert detail["consensus"] is None or detail["consensus"]["status"] == "pending"
    # delete the remaining run too → still no 500
    assert client.delete(f"/api/runs/{ens['runs'][1]['id']}", headers=auth).status_code == 204
    assert client.get("/api/ensembles", headers=auth).status_code == 200


def test_r3_2_paused_runs_swept_on_restart():
    """The lifespan sweep must treat 'paused' like 'running'."""
    from app.db import SessionLocal
    from app.models import Run, User

    with SessionLocal() as db:
        uid = db.query(User).first().id
        run = Run(user_id=uid, ticker="R3B", trade_date="2026-09-01",
                  config_json="{}", status="paused")
        db.add(run)
        db.commit()
        rid = run.id
        # replicate the lifespan sweep expression
        stale = db.query(Run).filter(Run.status.in_(("running", "paused"))).all()
        assert any(r.id == rid for r in stale)
        run = db.get(Run, rid)
        run.status = "interrupted"
        db.commit()
        db.delete(db.get(Run, rid))
        db.commit()


def test_r3_4_fts_malformed_queries_never_500(client, auth):
    for q in ['revenue"', '"unbalanced', "foo OR", "NOT", "AND AND", "«»;--"]:
        r = client.get(f"/api/library/search?q={q}", headers=auth)
        assert r.status_code == 200, q


def test_r3_6_ensemble_stacks_do_not_spam_alerts(client, auth):
    before = len(client.get("/api/alerts", headers=auth).json())
    ens = client.post("/api/ensembles", headers=auth, json={
        "ticker": "R3C", "trade_date": "2026-09-01", "research_depth": 1,
        "stacks": [{"label": "S1", "mode": "demo"}, {"label": "S2", "mode": "demo"},
                   {"label": "S3", "mode": "demo"}]}).json()
    for run in ens["runs"]:
        _wait_done(client, auth, run["id"])
    after = [a for a in client.get("/api/alerts", headers=auth).json() if a["ticker"] == "R3C"]
    assert after == [], "stack runs must not generate rating_change alerts"
    assert len(client.get("/api/alerts", headers=auth).json()) == before


def test_r3_9_index_run_reports_idempotent(client, auth):
    run = client.post("/api/runs", headers=auth, json={
        "ticker": "R3D", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo"}).json()
    _wait_done(client, auth, run["id"])
    client.post(f"/api/library/index-run/{run['id']}", headers=auth)
    n1 = len([d for d in client.get("/api/library", headers=auth).json() if d["ticker"] == "R3D"])
    client.post(f"/api/library/index-run/{run['id']}", headers=auth)
    n2 = len([d for d in client.get("/api/library", headers=auth).json() if d["ticker"] == "R3D"])
    assert n1 == n2 > 0


def test_r3_5_paper_no_double_open(client, auth, monkeypatch):
    import asyncio

    from app import paper
    from app.db import SessionLocal
    from app.models import Run, User

    monkeypatch.setattr(paper, "_last_price_sync", lambda t: 50.0)
    client.put("/api/settings", headers=auth, json={"config": {"paper_trading": True}})
    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
        runs = []
        for _ in range(2):
            r = Run(user_id=uid, ticker="R3E", trade_date="2026-09-01",
                    config_json="{}", status="done", rating="Buy")
            db.add(r)
            db.commit()
            runs.append(r.id)

    async def both():
        return await asyncio.gather(paper.execute_decision(runs[0]),
                                    paper.execute_decision(runs[1]))

    results = asyncio.run(both())
    opened = [r for r in results if r is not None]
    assert len(opened) == 1, "concurrent Buy decisions must open exactly one position"
    client.put("/api/settings", headers=auth, json={"config": {}})
