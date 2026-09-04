"""🎯 Trade Card tests."""
import json
import time

SNAP = {
    "symbol": "NIFTY", "spot": 23897.7, "expiry": "08-Sep-2026", "pcr": 0.852,
    "max_pain": 24000, "total_ce_oi": 100, "total_pe_oi": 90, "strike_count": 40,
    "resistance_strikes": [{"strike": 24000, "ce_oi": 255359}, {"strike": 24200, "ce_oi": 184348}],
    "support_strikes": [{"strike": 23900, "pe_oi": 194321}, {"strike": 23800, "pe_oi": 174899}],
    "atm_iv_ce": 10.75, "atm_iv_pe": 6.8, "india_vix": 10.68,
    "atm_ladder": [
        {"strike": 23800, "ce_ltp": 190.65, "pe_ltp": 29.6, "ce_oi": 1, "pe_oi": 1,
         "ce_iv": 11.9, "pe_iv": 7.6, "ce_delta": 0.654, "pe_delta": -0.269, "ce_theta": -10.9, "pe_theta": -9.2, "ce_vega": 9, "pe_vega": 9},
        {"strike": 23900, "ce_ltp": 119.6, "pe_ltp": 56.55, "ce_oi": 1, "pe_oi": 1,
         "ce_iv": 10.75, "pe_iv": 6.8, "ce_delta": 0.526, "pe_delta": -0.461, "ce_theta": -11.1, "pe_theta": -10.6, "ce_vega": 10, "pe_vega": 10},
        {"strike": 24000, "ce_ltp": 66.9, "pe_ltp": 103.5, "ce_oi": 1, "pe_oi": 1,
         "ce_iv": 10.04, "pe_iv": 5.77, "ce_delta": 0.37, "pe_delta": -0.72, "ce_theta": -10.2, "pe_theta": -9.4, "ce_vega": 10, "pe_vega": 10},
        {"strike": 24050, "ce_ltp": 46.8, "pe_ltp": 134.7, "ce_oi": 1, "pe_oi": 1,
         "ce_iv": 9.7, "pe_iv": 4.72, "ce_delta": 0.292, "pe_delta": -0.872, "ce_theta": -9.1, "pe_theta": -7.1, "ce_vega": 9, "pe_vega": 9},
    ],
}


def test_hold_card_is_two_row_bracket():
    from app.fno import build_trade_card

    card = build_trade_card(SNAP, "Hold", "^NSEI")
    assert card["verdict"] == "NO TRADE NOW"
    assert len(card["rows"]) == 2
    down, up = card["rows"]
    # bearish branch: PE at the lower put wall, primary
    assert down["scenario"] == "IF BREAKS DOWN" and "23800 PE" in down["instrument"]
    assert down["condition"] == "Closes below 23800" and down["primary"] is True
    # EP estimated at trigger: 29.6 + 0.269 * (23897.7-23800) ≈ 55.9
    assert 50 <= down["ep"] <= 62
    assert down["sl"] == round(down["ep"] * 0.6, 2)
    assert down["tp1"] == round(down["ep"] * 1.6, 2) and down["rr1"] == 1.5
    assert "23900" in down["sl_spot"]
    # bullish branch: CE above the call wall, defensive
    assert up["scenario"] == "IF BREAKS UP" and "24050 CE" in up["instrument"]
    assert up["condition"] == "Closes above 24050" and up["primary"] is False
    assert 85 <= up["ep"] <= 100  # 46.8 + 0.292*152 ≈ 91
    assert "24000" in up["sl_spot"]


def test_directional_card_is_single_row():
    from app.fno import build_trade_card

    card = build_trade_card(SNAP, "Buy", "^NSEI")
    assert card["verdict"] == "TRADE"
    assert len(card["rows"]) == 1
    row = card["rows"][0]
    assert row["scenario"] == "NOW" and row["primary"] is True
    assert "24000 CE" in row["instrument"]  # Δ 0.37 closest to 0.40
    assert row["ep"] == 66.9  # live premium, no trigger adjustment

    card_pe = build_trade_card(SNAP, "Sell", "^NSEI")
    assert "23900 PE" in card_pe["rows"][0]["instrument"]  # Δ −0.461 nearest 0.40


def test_fno_run_emits_trade_card(client, auth, monkeypatch):
    from app import fno

    async def fake_snapshot(symbol):
        return dict(SNAP, symbol=symbol)

    monkeypatch.setattr(fno, "get_fno_snapshot", fake_snapshot)
    run = client.post("/api/runs", headers=auth, json={
        "ticker": "NIFTY", "trade_date": "2026-09-02", "research_depth": 1,
        "mode": "demo", "fno_mode": True}).json()
    for _ in range(120):
        r = client.get(f"/api/runs/{run['id']}", headers=auth).json()
        if r["status"] in ("done", "failed", "interrupted"):
            break
        time.sleep(1)
    assert r["status"] == "done"
    # card stored as a report row (WS-replayed to the UI) but kept out of the tab list
    from app.db import SessionLocal
    from app.models import RunReport

    with SessionLocal() as db:
        row = (db.query(RunReport)
               .filter(RunReport.run_id == run["id"], RunReport.section == "fno_trade_card")
               .one_or_none())
        assert row is not None
        card = json.loads(row.content_md)
        assert card["verdict"] in ("TRADE", "NO TRADE NOW")
        assert all({"ep", "sl", "tp1", "tp2", "condition"} <= set(x.keys()) for x in card["rows"])
    sections = [x["section"] for x in client.get(f"/api/runs/{run['id']}/reports", headers=auth).json()]
    assert "fno_trade_card" not in sections  # not a markdown tab
