"""F&O Desk tests: trade-plan builder + fno_mode run flow."""
import time

SNAP = {
    "symbol": "NIFTY", "spot": 23937.9, "expiry": "08-Sep-2026", "pcr": 0.898,
    "max_pain": 24000, "total_ce_oi": 100, "total_pe_oi": 90, "strike_count": 40,
    "resistance_strikes": [{"strike": 24000, "ce_oi": 350522}],
    "support_strikes": [{"strike": 23900, "pe_oi": 300920}],
    "atm_iv_ce": 9.5, "atm_iv_pe": 8.5, "india_vix": 10.76,
    "atm_ladder": [
        {"strike": 23800, "ce_ltp": 210.0, "pe_ltp": 45.0, "ce_oi": 1, "pe_oi": 2, "ce_iv": 10, "pe_iv": 9},
        {"strike": 23900, "ce_ltp": 140.0, "pe_ltp": 78.0, "ce_oi": 1, "pe_oi": 2, "ce_iv": 10, "pe_iv": 9},
        {"strike": 24000, "ce_ltp": 85.0, "pe_ltp": 120.0, "ce_oi": 1, "pe_oi": 2, "ce_iv": 9.5, "pe_iv": 8.5},
        {"strike": 24100, "ce_ltp": 48.0, "pe_ltp": 180.0, "ce_oi": 1, "pe_oi": 2, "ce_iv": 10, "pe_iv": 9},
    ],
}


def test_trade_plan_bullish_picks_otm_ce():
    from app.fno import build_trade_plan_md

    md = build_trade_plan_md(SNAP, "Buy", "^NSEI")
    assert "buy the 24000 CE" in md            # first strike ≥ spot
    assert "₹85.0" in md                        # real premium from the ladder
    assert "₹51.0" in md                        # SL = 60% of 85
    assert "₹136.0" in md and "₹187.0" in md    # T1/T2
    assert "R:R **1:1.5**" in md                # (136-85)/(85-51)
    assert "23900 put wall" in md               # spot invalidation
    assert "not advice" in md


def test_trade_plan_bearish_picks_otm_pe():
    from app.fno import build_trade_plan_md

    md = build_trade_plan_md(SNAP, "Sell", "^NSEI")
    assert "buy the 23900 PE" in md            # first strike ≤ spot
    assert "₹78.0" in md
    assert "24000 call wall" in md


def test_trade_plan_hold_means_no_trade():
    from app.fno import build_trade_plan_md

    md = build_trade_plan_md(SNAP, "Hold", "^NSEI")
    assert "no trade" in md.lower()
    assert "23900" in md and "24000" in md      # names the walls to watch


def test_fno_mode_requires_derivatives(client, auth):
    r = client.post("/api/runs", headers=auth, json={
        "ticker": "AAPL", "trade_date": "2026-09-01", "mode": "demo", "fno_mode": True})
    assert r.status_code == 422 and "no NSE derivatives" in r.json()["detail"]


def test_fno_mode_run_produces_trade_plan(client, auth, monkeypatch):
    from app import fno

    async def fake_snapshot(symbol):
        return dict(SNAP, symbol=symbol)

    monkeypatch.setattr(fno, "get_fno_snapshot", fake_snapshot)
    run = client.post("/api/runs", headers=auth, json={
        "ticker": "BANKNIFTY", "trade_date": "2026-09-01", "research_depth": 1,
        "mode": "demo", "fno_mode": True}).json()
    assert run["ticker"] == "^NSEBANK" and run["config"]["fno_mode"] is True

    for _ in range(120):
        r = client.get(f"/api/runs/{run['id']}", headers=auth).json()
        if r["status"] in ("done", "failed", "interrupted"):
            break
        time.sleep(1)
    assert r["status"] == "done"
    reports = {x["section"]: x["content_md"]
               for x in client.get(f"/api/runs/{run['id']}/reports", headers=auth).json()}
    assert "fno_trade_plan" in reports
    plan = reports["fno_trade_plan"]
    assert "Option Trade Plan" in plan
    if r["rating"] in ("Buy", "Overweight", "Sell", "Underweight"):
        assert "Stop loss" in plan and "Target 1" in plan and "R:R" in plan
    else:
        assert "no trade" in plan.lower()
    # plan is the last section in report order
    order = [x["section"] for x in client.get(f"/api/runs/{run['id']}/reports", headers=auth).json()]
    assert order[-1] == "fno_trade_plan" and order[0] == "derivatives_report"
