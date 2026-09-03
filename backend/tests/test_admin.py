"""Super-admin dashboard tests."""


def _admin_headers(client):
    r = client.post("/api/auth/login", json={"email": "admin@agentalgo.dev", "password": "admin12345"})
    assert r.status_code == 200, "admin bootstrap should create the account at startup"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_admin_bootstrap_and_me_flag(client):
    h = _admin_headers(client)
    me = client.get("/api/auth/me", headers=h).json()
    assert me["is_admin"] is True


def test_non_admin_forbidden(client, auth):
    assert client.get("/api/admin/stats", headers=auth).status_code == 403
    assert client.get("/api/admin/users", headers=auth).status_code == 403


def test_admin_stats_and_users(client):
    h = _admin_headers(client)
    stats = client.get("/api/admin/stats", headers=h).json()
    assert stats["users"] >= 2 and stats["runs_total"] >= 0
    for key in ("ratings", "top_tickers", "runs_by_status", "tokens_in", "keys_stored"):
        assert key in stats
    users = client.get("/api/admin/users", headers=h).json()
    assert any(u["email"] == "admin@agentalgo.dev" and u["is_admin"] for u in users)
    assert all({"runs", "keys", "last_run"} <= set(u.keys()) for u in users)


def test_admin_toggle_and_self_guard(client):
    h = _admin_headers(client)
    users = client.get("/api/admin/users", headers=h).json()
    admin_id = next(u["id"] for u in users if u["email"] == "admin@agentalgo.dev")
    victim = next((u for u in users if not u["is_admin"]), None)
    assert victim is not None
    # promote + demote round-trip
    assert client.post(f"/api/admin/users/{victim['id']}/toggle-admin", headers=h).json()["is_admin"] is True
    assert client.post(f"/api/admin/users/{victim['id']}/toggle-admin", headers=h).json()["is_admin"] is False
    # self-demotion and self-deletion blocked
    assert client.post(f"/api/admin/users/{admin_id}/toggle-admin", headers=h).status_code == 409
    assert client.delete(f"/api/admin/users/{admin_id}", headers=h).status_code == 409
