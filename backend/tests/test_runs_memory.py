"""F4/F5/F7/F8/F9 run lifecycle, reports, memory — against a real demo run."""
import time


def _wait_done(client, auth, run_id, timeout=180):
    for _ in range(timeout):
        r = client.get(f"/api/runs/{run_id}", headers=auth).json()
        if r["status"] in ("done", "failed", "interrupted"):
            return r
        time.sleep(1)
    raise TimeoutError("run did not finish")


def test_run_validation(client, auth):
    base = {"ticker": "NVDA", "trade_date": "2026-09-01", "mode": "demo"}
    assert client.post("/api/runs", headers=auth, json={**base, "ticker": "../x"}).status_code == 422
    assert client.post("/api/runs", headers=auth, json={**base, "trade_date": "2099-01-01"}).status_code == 422
    assert client.post("/api/runs", headers=auth, json={**base, "research_depth": 2}).status_code == 422
    assert client.post("/api/runs", headers=auth, json={**base, "analysts": []}).status_code == 422
    # engine mode without a key for the provider
    r = client.post("/api/runs", headers=auth, json={**base, "mode": "engine", "llm_provider": "anthropic"})
    assert r.status_code == 422 and "No credential" in r.json()["detail"]


def test_demo_run_full_lifecycle(client, auth):
    r = client.post("/api/runs", headers=auth, json={
        "ticker": "btcusdt", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo",
        "analysts": ["market", "social", "news", "fundamentals"],
    })
    assert r.status_code == 201
    run = r.json()
    # crypto normalization + analyst filtering + benchmark
    assert run["ticker"] == "BTC-USD" and run["asset_type"] == "crypto"
    assert "fundamentals" not in run["config"]["analysts"]

    final = _wait_done(client, auth, run["id"])
    assert final["status"] == "done"
    assert final["rating"] in ("Buy", "Overweight", "Hold", "Underweight", "Sell")
    assert final["stats"]["llm_calls"] > 0

    # reports: no fundamentals section for crypto, others present in order
    sections = [s["section"] for s in client.get(f"/api/runs/{run['id']}/reports", headers=auth).json()]
    assert "fundamentals_report" not in sections
    assert sections[0] == "market_report" and sections[-1] == "final_trade_decision"

    # event log replay
    events = client.get(f"/api/runs/{run['id']}/events", headers=auth).json()
    assert len(events) > 20
    assert events == sorted(events, key=lambda e: e["seq"])

    # markdown export
    md = client.get(f"/api/runs/{run['id']}/report.md", headers=auth).text
    assert "Analyst Team Reports" in md and "BTC-USD" in md

    # memory entry appended as pending
    mem = client.get("/api/memory?ticker=BTC-USD", headers=auth).json()
    assert mem and mem[0]["status"] == "pending" and mem[0]["rating"] == final["rating"]

    # determinism: same ticker+date ⇒ same rating (seeded demo)
    r2 = client.post("/api/runs", headers=auth, json={
        "ticker": "BTC-USD", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo"})
    final2 = _wait_done(client, auth, r2.json()["id"])
    assert final2["rating"] == final["rating"]


def test_tenant_isolation(client, auth):
    # second user must not see first user's runs/keys/memory
    r = client.post("/api/auth/register", json={"email": "other@x.co", "password": "password99"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/runs", headers=other).json() == []
    assert client.get("/api/memory", headers=other).json() == []
    my_run = client.get("/api/runs", headers=auth).json()[0]
    assert client.get(f"/api/runs/{my_run['id']}", headers=other).status_code == 404


def test_settings_validation(client, auth):
    assert client.put("/api/settings", headers=auth, json={"config": {"research_depth": 3}}).status_code == 200
    assert client.put("/api/settings", headers=auth, json={"config": {"bogus_key": 1}}).status_code == 422
    assert client.put("/api/settings", headers=auth, json={"config": {"research_depth": "deep"}}).status_code == 422
    assert client.get("/api/settings", headers=auth).json()["config"]["research_depth"] == 3


def test_presets_crud(client, auth):
    r = client.post("/api/presets", headers=auth, json={"name": "My NVDA", "config": {"ticker": "NVDA"}})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert any(p["name"] == "My NVDA" for p in client.get("/api/presets", headers=auth).json())
    assert client.delete(f"/api/presets/{pid}", headers=auth).status_code == 204
