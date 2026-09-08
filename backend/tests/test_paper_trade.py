"""Live paper-trading desk — portfolio open rules, real-quote exits, API."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

SIG = {"symbol": "NIFTY", "rule": "ORB", "direction": "CE",
       "instrument": "NIFTY 24000 CE", "ep": 100.0, "sl": 82.0, "tp": 127.0,
       "spot": 24000.0, "delta": 0.5, "sizing": {"lot_size": 65}}
CFG = {"trading_capital": 500_000, "scalp_risk_pct": 1.0}


def _uid():
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade, User

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
        db.query(ScalpPaperTrade).filter(ScalpPaperTrade.user_id == uid).delete()
        db.commit()
    return uid


def test_open_portfolio_rules(client, auth):
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade
    from app.paper_trade import MAX_CONCURRENT, open_paper_trade

    uid = _uid()
    # risk ₹18/unit × 65 = ₹1,170/lot; budget 1% of 5L = ₹5,000 → 4 lots
    tid = open_paper_trade(uid, SIG, "sig1", "16-Sep-2026", CFG)
    assert tid is not None
    with SessionLocal() as db:
        t = db.get(ScalpPaperTrade, tid)
        assert t.lots == 4 and t.capital_used == 100.0 * 65 * 4
        assert t.status == "open" and t.expiry == "16-Sep-2026"
    # concurrency cap: fill to MAX_CONCURRENT, next is skipped
    assert open_paper_trade(uid, {**SIG, "instrument": "NIFTY 24050 CE"},
                            "sig2", None, CFG) is not None
    assert MAX_CONCURRENT == 2
    assert open_paper_trade(uid, {**SIG, "instrument": "NIFTY 24100 CE"},
                            "sig3", None, CFG) is None
    # unaffordable: tiny capital → 0 lots → skipped
    _uid()
    assert open_paper_trade(uid, SIG, "sig4", None, {"trading_capital": 1_000}) is None


def test_close_math_and_summary(client, auth):
    from app.backtest import trade_cost
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade
    from app.paper_trade import _close, open_paper_trade, paper_trades_summary

    uid = _uid()
    tid = open_paper_trade(uid, SIG, "sig1", None, CFG)
    with SessionLocal() as db:
        t = db.get(ScalpPaperTrade, tid)
        _close(db, t, 127.0, "TP", "kite")   # real bid exit → no slippage added
        t.status = "closed"
        db.commit()
        cost = trade_cost(100.0, 127.0, 65, 4, slip_pct=0.0)
        assert t.charges == cost
        assert t.pnl == round(27.0 * 65 * 4 - cost, 2)
        assert t.r_multiple == 1.5 and t.outcome == "TP"

    s = paper_trades_summary(uid)
    assert s["summary"]["n_closed"] == 1 and s["summary"]["wins"] == 1
    assert s["summary"]["equity"] == round(s["summary"]["base_capital"] + s["summary"]["net_pnl"], 2)
    assert s["rows"][0]["outcome"] == "TP" and s["rows"][0]["exit_source"] == "kite"

    r = client.get("/api/scalp/paper-trades", headers=auth)
    assert r.status_code == 200 and r.json()["summary"]["n_closed"] >= 1
    assert client.get("/api/scalp/paper-trades?days=999", headers=auth).status_code == 422
    _uid()


import pytest


@pytest.mark.anyio
async def test_sweep_resolves_on_kite_quote(client, auth, monkeypatch):
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade
    from app.paper_trade import open_paper_trade, paper_trade_sweep

    uid = _uid()
    tid = open_paper_trade(uid, SIG, "sig1", "16-Sep-2026", CFG)
    monkeypatch.setattr("app.fno.is_market_hours_ist", lambda: True)
    monkeypatch.setattr("app.kite_data.kite_option_quote",
                        lambda *a, **k: {"bid": 80.0, "ask": 80.4, "last_price": 80.2})
    closed = await paper_trade_sweep()
    assert closed == 1
    with SessionLocal() as db:
        t = db.get(ScalpPaperTrade, tid)
        assert t.status == "closed" and t.outcome == "SL"     # bid 80 ≤ SL 82
        assert t.exit_p == 80.0 and t.exit_source == "kite"
        assert t.equity_after is not None
    _uid()
