"""Phase 2 tests: watchlists, schedules, alerts, run queueing."""
import time
from datetime import datetime


def _wait(client, auth, rid, timeout=180):
    for _ in range(timeout):
        r = client.get(f"/api/runs/{rid}", headers=auth).json()
        if r["status"] in ("done", "failed", "interrupted", "cancelled"):
            return r
        time.sleep(1)
    raise TimeoutError


def test_watchlist_crud_and_run_all_with_queueing(client, auth):
    r = client.post("/api/watchlists", headers=auth,
                    json={"name": "Tech", "tickers": ["nvda", "MSFT", "googl", "btcusdt", "AMZN"]})
    assert r.status_code == 201
    wl = r.json()
    assert wl["tickers"] == ["NVDA", "MSFT", "GOOGL", "BTC-USD", "AMZN"]  # normalized, deduped

    # run all 5 — cap is 3, so ≥2 must queue rather than 429
    r = client.post(f"/api/watchlists/{wl['id']}/run", headers=auth,
                    json={"research_depth": 1, "mode": "demo"})
    body = r.json()
    assert len(body["created"]) == 5 and body["errors"] == []
    statuses = {c["status"] for c in body["created"]}
    assert "queued" in statuses  # over-cap runs queued, not rejected

    # the pump drains the queue: every run finishes
    for c in body["created"]:
        assert _wait(client, auth, c["run_id"])["status"] == "done"

    # latest ratings appear on the watchlist
    wl2 = next(w for w in client.get("/api/watchlists", headers=auth).json() if w["id"] == wl["id"])
    assert set(wl2["latest"].keys()) == set(wl["tickers"])

    assert client.delete(f"/api/watchlists/{wl['id']}", headers=auth).status_code == 204


def test_schedule_due_logic():
    from app.main import schedule_is_due

    monday_8am = datetime(2026, 9, 7, 8, 0)   # Monday
    sunday_8am = datetime(2026, 9, 6, 8, 0)   # Sunday
    monday_6am = datetime(2026, 9, 7, 6, 0)

    assert schedule_is_due("daily", 0, 7, "", monday_8am)
    assert not schedule_is_due("daily", 0, 7, "2026-09-07", monday_8am)  # already fired
    assert not schedule_is_due("daily", 0, 7, "", monday_6am)            # before hour
    assert schedule_is_due("weekdays", 0, 7, "", monday_8am)
    assert not schedule_is_due("weekdays", 0, 7, "", sunday_8am)
    assert schedule_is_due("weekly", 0, 7, "", monday_8am)               # Monday
    assert not schedule_is_due("weekly", 2, 7, "", monday_8am)           # wants Wednesday


def test_schedule_crud(client, auth):
    r = client.post("/api/schedules", headers=auth, json={
        "name": "Morning scan", "tickers": ["NVDA"], "cadence": "weekdays", "hour": 7,
        "config": {"research_depth": 1, "mode": "demo"}})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["enabled"] is True
    assert client.post(f"/api/schedules/{sid}/toggle", headers=auth).json()["enabled"] is False
    assert any(s["id"] == sid for s in client.get("/api/schedules", headers=auth).json())
    assert client.delete(f"/api/schedules/{sid}", headers=auth).status_code == 204
    # invalid cadence rejected
    assert client.post("/api/schedules", headers=auth, json={
        "name": "x", "tickers": ["NVDA"], "cadence": "hourly"}).status_code == 422


def test_alert_generation_unit(client, auth):
    from app.db import SessionLocal
    from app.models import Run, User
    from app.runner import maybe_create_alert

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
        prev = Run(user_id=uid, ticker="ALRT", trade_date="2026-08-01", config_json="{}",
                   status="done", rating="Buy")
        db.add(prev)
        db.commit()
        cur = Run(user_id=uid, ticker="ALRT", trade_date="2026-09-01", config_json="{}",
                  status="done", rating="Sell")
        db.add(cur)
        db.commit()
        maybe_create_alert(db, cur)
        review_run = Run(user_id=uid, ticker="ALRT", trade_date="2026-09-02", config_json="{}",
                         status="done", rating="REVIEW")
        db.add(review_run)
        db.commit()
        maybe_create_alert(db, review_run)

    alerts = client.get("/api/alerts", headers=auth).json()
    types = {a["type"] for a in alerts if a["ticker"] == "ALRT"}
    assert types == {"rating_change", "review"}
    change = next(a for a in alerts if a["type"] == "rating_change")
    assert "Buy → Sell" in change["message"]

    # mark read
    r = client.post("/api/alerts/read", headers=auth, json=None)
    assert r.json()["marked_read"] >= 2
    assert client.get("/api/alerts?unread_only=true", headers=auth).json() == []


def test_watchlist_tenant_isolation(client, auth):
    r = client.post("/api/auth/register", json={"email": "wl-other@x.co", "password": "password99"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/watchlists", headers=other).json() == []
    assert client.get("/api/alerts", headers=other).json() == []
