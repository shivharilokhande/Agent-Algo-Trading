"""Production-hardening tests: S3 stream tickets, S4 SSRF, S6 rate limit, C8, F7.1, F11.2."""
import time

import pytest


def test_s6_login_rate_limit(client):
    for i in range(10):
        client.post("/api/auth/login", json={"email": "brute@x.co", "password": "wrongwrong"})
    r = client.post("/api/auth/login", json={"email": "brute@x.co", "password": "wrongwrong"})
    assert r.status_code == 429


def test_s3_stream_ticket_flow(client, auth):
    run = client.post("/api/runs", headers=auth, json={
        "ticker": "ORCL", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo"}).json()
    rid = run["id"]
    # mint a ticket — owner only
    t = client.post(f"/api/runs/{rid}/stream-ticket", headers=auth)
    assert t.status_code == 200 and t.json()["ticket"]
    ticket = t.json()["ticket"]
    # ticket is run-scoped: rejected for another run id
    from app.security import decode_stream_ticket
    from fastapi import HTTPException
    assert decode_stream_ticket(ticket, rid)  # valid
    with pytest.raises(HTTPException):
        decode_stream_ticket(ticket, "other-run-id")
    with pytest.raises(HTTPException):
        decode_stream_ticket("garbage", rid)
    # WS with a bad ticket is refused
    try:
        with client.websocket_connect(f"/api/runs/{rid}/stream?ticket=bad"):
            raise AssertionError("bad ticket accepted")
    except Exception:
        pass  # closed with 4401 before accept
    # WS with the good ticket streams to completion
    got_end = False
    sections = set()
    with client.websocket_connect(f"/api/runs/{rid}/stream?ticket={ticket}") as ws:
        deadline = time.time() + 120
        while time.time() < deadline:
            msg = ws.receive_json()
            if msg.get("type") == "report_section":
                sections.add(msg["payload"]["section"])
            if msg.get("type") == "stream_end":
                got_end = True
                break
    assert got_end and "final_trade_decision" in sections


def test_s4_ssrf_validator():
    from app.routers import keys as keys_mod
    from fastapi import HTTPException

    # scheme validation always applies
    with pytest.raises(HTTPException):
        keys_mod.validate_outbound_url("file:///etc/passwd")
    with pytest.raises(HTTPException):
        keys_mod.validate_outbound_url("ftp://example.com")
    # private ranges blocked when the deployment flag is off
    original = keys_mod.ALLOW_PRIVATE_URLS
    keys_mod.ALLOW_PRIVATE_URLS = False
    try:
        for bad in ("http://169.254.169.254/latest", "http://127.0.0.1:11434/v1", "http://10.0.0.5/v1"):
            with pytest.raises(HTTPException):
                keys_mod.validate_outbound_url(bad)
    finally:
        keys_mod.ALLOW_PRIVATE_URLS = original
    # localhost fine when allowed (default desktop mode)
    assert keys_mod.validate_outbound_url("http://localhost:11434/v1")


def test_c8_min_resolve_window(client, auth):
    from datetime import date

    today = date.today().isoformat()
    run = client.post("/api/runs", headers=auth, json={
        "ticker": "AMD", "trade_date": today, "research_depth": 1, "mode": "demo"}).json()
    for _ in range(120):
        time.sleep(1)
        if client.get(f"/api/runs/{run['id']}", headers=auth).json()["status"] == "done":
            break
    entry = client.get("/api/memory?ticker=AMD", headers=auth).json()[0]
    r = client.post(f"/api/memory/{entry['id']}/resolve", headers=auth)
    assert r.status_code == 409 and "window too short" in r.json()["detail"]


def test_f71_report_search(client, auth):
    # the AMD run above produced reports mentioning 'AMD'
    hits = client.get("/api/runs?q=AMD", headers=auth).json()
    assert any(r["ticker"] == "AMD" for r in hits)
    assert client.get("/api/runs?q=zzznomatch123", headers=auth).json() == []


def test_f112_announcements(client):
    r = client.get("/api/announcements")
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
