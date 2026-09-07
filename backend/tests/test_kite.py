"""Zerodha Kite adapter — symbol formatting, graceful degradation, endpoints."""


def test_tradingsymbols_weekly_and_monthly():
    from app.kite_data import _is_monthly, tradingsymbols
    from datetime import datetime

    # 08-Sep-2026 is a weekly NIFTY expiry (not the last Tuesday of Sep)
    ts = tradingsymbols("NIFTY", "08-Sep-2026", 23800, "PE")
    assert ts[0] == "NFO:NIFTY2690823800PE"          # weekly first
    assert "NFO:NIFTY26SEP23800PE" in ts             # monthly fallback listed
    # 29-Sep-2026 IS the last Tuesday → monthly naming first
    assert _is_monthly(datetime(2026, 9, 29))
    ts = tradingsymbols("BANKNIFTY", "29-Sep-2026", 57000, "CE")
    assert ts[0] == "NFO:BANKNIFTY26SEP57000CE"
    # October weekly uses the O month code
    ts = tradingsymbols("NIFTY", "06-Oct-2026", 24000, "CE")
    assert ts[0] == "NFO:NIFTY26O0624000CE"
    # bad expiry → no candidates, never raises
    assert tradingsymbols("NIFTY", "garbage", 1, "CE") == []


def test_kite_degrades_without_credentials(client, auth):
    from app.kite_data import kite_spot, refine_signal_with_kite

    # no vault credential → spot None, signal passes through untouched
    assert kite_spot("NIFTY") is None
    sig = {"symbol": "NIFTY", "strike": 23800, "direction": "PE",
           "ep": 50.0, "sl": 41.0, "tp": 63.5, "sizing": {"lots": 1}}
    assert refine_signal_with_kite(dict(sig), "08-Sep-2026")["ep"] == 50.0
    # endpoints: login-url 400s with guidance, status reports unconfigured
    r = client.get("/api/kite/login-url", headers=auth)
    assert r.status_code == 400 and "Settings" in r.json()["detail"]
    r = client.get("/api/kite/status", headers=auth)
    assert r.status_code == 200 and r.json() == {"configured": False, "connected": False}
    # session exchange without creds → 400, not a crash
    r = client.post("/api/kite/session", headers=auth, json={"request_token": "abcdefgh"})
    assert r.status_code == 400


def test_kite_callback_state_and_redirects(client):
    from app.kite_data import make_state, verify_state

    # signed state round-trips; garbage doesn't
    assert verify_state(make_state("user123")) == "user123"
    assert verify_state("garbage") is None
    # access tokens are NOT valid as callback state (scope check)
    from app.security import create_access_token

    assert verify_state(create_access_token("user123")) is None
    # callback is unauthenticated but redirects safely on bad/expired state
    r = client.get("/api/kite/callback?request_token=abc&state=garbage",
                   follow_redirects=False)
    assert r.status_code == 303 and "/settings?kite=error" in r.headers["location"]
    # failed Zerodha login → error bounce (valid state, status!=success)
    st = make_state("user123")
    r = client.get(f"/api/kite/callback?state={st}&status=cancelled",
                   follow_redirects=False)
    assert r.status_code == 303 and "kite=error" in r.headers["location"]
