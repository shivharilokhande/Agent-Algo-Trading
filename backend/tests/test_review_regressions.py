"""Regression tests for the code-review findings (C1, C2, S7, C7)."""
import time


def _wait_done(client, auth, run_id, timeout=120):
    for _ in range(timeout):
        r = client.get(f"/api/runs/{run_id}", headers=auth).json()
        if r["status"] in ("done", "failed", "interrupted"):
            return r
        time.sleep(1)
    raise TimeoutError


def test_c1_delete_completed_run_with_memory_entry(client, auth):
    r = client.post("/api/runs", headers=auth, json={
        "ticker": "MSFT", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo"})
    rid = r.json()["id"]
    assert _wait_done(client, auth, rid)["status"] == "done"
    # memory entry exists and references the run
    assert any(m["ticker"] == "MSFT" for m in client.get("/api/memory", headers=auth).json())
    # deleting the run must not 500 (FK detach) and memory entry survives
    assert client.delete(f"/api/runs/{rid}", headers=auth).status_code == 204
    assert any(m["ticker"] == "MSFT" for m in client.get("/api/memory", headers=auth).json())


def test_c1_account_deletion_cascades_everything(client):
    r = client.post("/api/auth/register", json={"email": "deleteme@x.co", "password": "password99"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.put("/api/keys", headers=h, json={"provider": "openai", "secret": "sk-x-123456789"})
    client.post("/api/presets", headers=h, json={"name": "p", "config": {}})
    client.put("/api/settings", headers=h, json={"config": {"research_depth": 3}})
    rid = client.post("/api/runs", headers=h, json={
        "ticker": "IBM", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo"}).json()["id"]
    assert _wait_done(client, h, rid)["status"] == "done"
    assert client.delete("/api/auth/me", headers=h).status_code == 204
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_c2_url_only_provider_key(client, auth):
    r = client.put("/api/keys", headers=auth, json={
        "provider": "ollama", "secret": "", "extra": {"base_url": "http://localhost:11434/v1"}})
    assert r.status_code == 200
    assert r.json()["extra"]["base_url"].endswith("/v1")
    client.delete("/api/keys/ollama", headers=auth)


def test_s7_mode_must_be_literal(client, auth):
    r = client.post("/api/runs", headers=auth, json={
        "ticker": "NVDA", "trade_date": "2026-09-01", "mode": "sneaky"})
    assert r.status_code == 422


def test_c7_benchmark_override_applied(client, auth):
    assert client.put("/api/settings", headers=auth,
                      json={"config": {"benchmark_ticker": "QQQ"}}).status_code == 200
    r = client.post("/api/runs", headers=auth, json={
        "ticker": "TSLA", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo"})
    assert r.json()["benchmark"] == "QQQ"
    client.put("/api/settings", headers=auth, json={"config": {}})
    _wait_done(client, auth, r.json()["id"])
