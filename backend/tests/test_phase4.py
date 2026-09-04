"""Phase 4 tests: ensembles (A3), triggers (A6), Agent Studio (A1), library (A4)."""
import time


def _wait_done(client, auth, rid, timeout=180):
    for _ in range(timeout):
        r = client.get(f"/api/runs/{rid}", headers=auth).json()
        if r["status"] in ("done", "failed", "interrupted"):
            return r
        time.sleep(1)
    raise TimeoutError


def test_ensemble_consensus(client, auth):
    r = client.post("/api/ensembles", headers=auth, json={
        "ticker": "ENSM", "trade_date": "2026-09-01", "research_depth": 1,
        "stacks": [
            {"label": "Stack Alpha", "mode": "demo"},
            {"label": "Stack Beta", "mode": "demo"},
            {"label": "Stack Gamma", "mode": "demo"},
        ]})
    assert r.status_code == 201
    ens = r.json()
    assert len(ens["runs"]) == 3
    for run in ens["runs"]:
        _wait_done(client, auth, run["id"])
    detail = client.get(f"/api/ensembles/{ens['id']}", headers=auth).json()
    c = detail["consensus"]
    assert c["status"] == "done"
    assert c["verdict"] in ("unanimous", "majority", "split")
    assert len(c["stacks"]) == 3 and all(s["rating"] for s in c["stacks"])
    labels = {s["label"] for s in c["stacks"]}
    assert labels == {"Stack Alpha", "Stack Beta", "Stack Gamma"}
    # stack seeding: at least the runs completed independently with own ratings
    assert len(c["ratings"]) == 3
    # validation: fewer than 2 stacks rejected
    assert client.post("/api/ensembles", headers=auth, json={
        "ticker": "ENSM", "trade_date": "2026-09-01",
        "stacks": [{"label": "solo"}]}).status_code == 422


def test_scoreboard(client, auth):
    board = client.get("/api/scoreboard", headers=auth).json()
    assert isinstance(board, list)
    if board:  # resolved entries exist from earlier tests
        assert {"stack", "decisions", "avg_alpha", "hit_rate"} <= set(board[0].keys())


def test_triggers_crud(client, auth):
    r = client.post("/api/triggers", headers=auth, json={"ticker": "nvda", "threshold": 2.5})
    assert r.status_code == 201 and r.json()["ticker"] == "NVDA"
    tid = r.json()["id"]
    assert client.post(f"/api/triggers/{tid}/toggle", headers=auth).json()["enabled"] is False
    assert any(t["id"] == tid for t in client.get("/api/triggers", headers=auth).json())
    assert client.post("/api/triggers", headers=auth,
                       json={"ticker": "NVDA", "threshold": 0.1}).status_code == 422
    assert client.delete(f"/api/triggers/{tid}", headers=auth).status_code == 204


def test_agent_studio_and_run_integration(client, auth):
    # persona override for a stock agent
    assert client.post("/api/studio", headers=auth, json={
        "agent_key": "Market Analyst",
        "persona": "You are a mean-reversion specialist; distrust momentum."}).status_code == 200
    # custom analyst
    assert client.post("/api/studio", headers=auth, json={
        "agent_key": "custom:ESG Screener", "display_name": "ESG Screener",
        "persona": "Evaluate governance and regulatory risk only.",
        "tools": ["get_news", "get_fundamentals"]}).status_code == 200
    # invalid agent / tools rejected
    assert client.post("/api/studio", headers=auth,
                       json={"agent_key": "Nope"}).status_code == 422
    assert client.post("/api/studio", headers=auth, json={
        "agent_key": "custom:X", "tools": ["rm_rf"]}).status_code == 422

    state = client.get("/api/studio", headers=auth).json()
    assert len(state["profiles"]) == 2

    # a run picks both up
    run = client.post("/api/runs", headers=auth, json={
        "ticker": "STUD", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo"}).json()
    final = _wait_done(client, auth, run["id"])
    assert final["status"] == "done"
    reports = {x["section"]: x["content_md"]
               for x in client.get(f"/api/runs/{run['id']}/reports", headers=auth).json()}
    assert "mean-reversion specialist" in reports["market_report"]  # persona note
    assert "custom_report" in reports and "ESG Screener" in reports["custom_report"]

    # cleanup profiles so other tests aren't affected
    for p in state["profiles"]:
        client.delete(f"/api/studio/{p['id']}", headers=auth)


def test_library_index_search_and_grounding(client, auth):
    from app.library import add_document
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id

    add_document(uid, "LIBT", "sec_filing", "LIBT 10-K filed 2026-01-15", "2026-01-15",
                 "https://example.com/10k",
                 "Revenue increased 24 percent year over year driven by data-center demand. "
                 "Risk factors include supply chain concentration and customer concentration.")
    add_document(uid, "LIBT", "sec_filing", "LIBT 10-Q filed 2026-12-01", "2026-12-01",
                 "https://example.com/10q", "Future document that must be excluded by as-of filtering.")

    # FTS search works and ranks
    hits = client.get("/api/library/search?q=revenue+data-center&ticker=LIBT", headers=auth).json()
    assert hits and "10-K" in hits[0]["title"]

    # as-of filtering excludes the future filing
    hits = client.get("/api/library/search?q=document&ticker=LIBT&as_of=2026-09-01", headers=auth).json()
    assert all("10-Q" not in h["title"] for h in hits)

    # a run grounds its fundamentals report in the library
    run = client.post("/api/runs", headers=auth, json={
        "ticker": "LIBT", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo"}).json()
    _wait_done(client, auth, run["id"])
    reports = {x["section"]: x["content_md"]
               for x in client.get(f"/api/runs/{run['id']}/reports", headers=auth).json()}
    assert "Grounded in the research library" in reports["fundamentals_report"]
    assert "10-K" in reports["fundamentals_report"]

    # tenant isolation: another user sees nothing
    r = client.post("/api/auth/register", json={"email": "lib-other@x.co", "password": "password99"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/library/search?q=revenue&ticker=LIBT", headers=other).json() == []
