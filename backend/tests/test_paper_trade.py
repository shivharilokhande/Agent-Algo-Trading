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


def test_paper_settings_save(client, auth):
    """Regression (11-Sep): paper_capital/paper_risk_pct were range-checked but
    missing from the settings whitelist — the Paper Trade Save button 422'd."""
    r = client.put("/api/settings", headers=auth,
                   json={"config": {"paper_capital": 100_000, "paper_risk_pct": 5}})
    assert r.status_code == 200
    r = client.get("/api/settings", headers=auth)
    assert r.json()["config"]["paper_risk_pct"] == 5
    assert client.put("/api/settings", headers=auth,
                      json={"config": {"paper_risk_pct": 99}}).status_code == 422


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
    assert closed == 2  # the A trade + its shadow-B twin (runup unknown → B fail-open)
    with SessionLocal() as db:
        t = db.get(ScalpPaperTrade, tid)
        assert t.status == "closed" and t.outcome == "SL"     # bid 80 ≤ SL 82
        assert t.exit_p == 80.0 and t.exit_source == "kite"
        # regression (live day-1 bug): equity_after must INCLUDE this trade's
        # own pnl — autoflush=False sessions need an explicit flush first
        import json as _json

        from app.models import Setting
        from app.paper_trade import _base_capital

        srow = db.get(Setting, uid)
        base = _base_capital(_json.loads(srow.config_json or "{}") if srow else {})
        assert t.equity_after == round(base + t.pnl, 2)
    _uid()


@pytest.mark.anyio
async def test_late_quote_settles_by_replay(client, auth, monkeypatch):
    """Regression (10-Sep outage): a bid arriving well past the 20-min window
    must settle by replay — never be stamped as a live TP/SL fill."""
    from datetime import timedelta

    from app.db import SessionLocal
    from app.models import ScalpPaperTrade
    from app.paper_trade import open_paper_trade, paper_trade_sweep

    uid = _uid()
    open_paper_trade(uid, SIG, "sig1", "16-Sep-2026", CFG)
    with SessionLocal() as db:  # age the trade (and its shadow-B twin) 40 min
        for t in db.query(ScalpPaperTrade).filter(ScalpPaperTrade.user_id == uid).all():
            t.created_at = t.created_at - timedelta(minutes=40)
        db.commit()
    monkeypatch.setattr("app.fno.is_market_hours_ist", lambda: True)
    # live bid is way above TP (the post-window waterfall) — must be ignored
    monkeypatch.setattr("app.kite_data.kite_option_quote",
                        lambda *a, **k: {"bid": 179.2, "ask": 179.6, "last_price": 179.4})
    monkeypatch.setattr("app.paper_trade._modeled_exit",
                        lambda t, now: (88.95, "TIME"))
    await paper_trade_sweep()
    with SessionLocal() as db:
        for t in db.query(ScalpPaperTrade).filter(ScalpPaperTrade.user_id == uid).all():
            assert t.status == "closed" and t.outcome == "TIME"
            assert t.exit_p == 88.95 and t.exit_source == "modeled"
    _uid()


def test_manual_exit(client, auth, monkeypatch):
    """Exit button: closes at live bid with outcome MANUAL; 409 with no quote;
    404 for a closed/foreign trade."""
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade
    from app.paper_trade import open_paper_trade

    uid = _uid()
    tid = open_paper_trade(uid, SIG, "sig1", "16-Sep-2026", CFG)
    # no quote (Kite down) → 409, trade stays open
    monkeypatch.setattr("app.kite_data.kite_option_quote", lambda *a, **k: None)
    assert client.post(f"/api/scalp/paper-trades/{tid}/exit", headers=auth).status_code == 409
    # live bid → closed as MANUAL at the bid
    monkeypatch.setattr("app.kite_data.kite_option_quote",
                        lambda *a, **k: {"bid": 111.0, "ask": 111.4, "last_price": 111.2})
    r = client.post(f"/api/scalp/paper-trades/{tid}/exit", headers=auth)
    assert r.status_code == 200 and r.json()["outcome"] == "MANUAL"
    with SessionLocal() as db:
        t = db.get(ScalpPaperTrade, tid)
        assert t.status == "closed" and t.exit_p == 111.0
        assert t.outcome == "MANUAL" and t.exit_source == "kite"
        assert t.equity_after is not None
    # already closed → 404
    assert client.post(f"/api/scalp/paper-trades/{tid}/exit", headers=auth).status_code == 404
    _uid()


def test_shadow_b_runup_gate(client, auth, monkeypatch):
    """Portfolio B: >15% premium run-up → skipped (EXT); calm → B opens too;
    unknown run-up → fail open. A takes every signal regardless."""
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade as T
    from app.paper_trade import open_paper_trade, paper_trades_summary

    uid = _uid()
    sig = {**SIG, "strike": 24000}

    # extended: A opens, B records a skipped EXT row
    monkeypatch.setattr("app.kite_data.kite_option_runup", lambda *a, **k: 21.4)
    assert open_paper_trade(uid, sig, "sig1", "16-Sep-2026", CFG) is not None
    with SessionLocal() as db:
        a = db.query(T).filter(T.user_id == uid, T.account == "A").all()
        b = db.query(T).filter(T.user_id == uid, T.account == "B").all()
        assert len(a) == 1 and a[0].status == "open" and a[0].runup_pct == 21.4
        assert len(b) == 1 and b[0].status == "skipped"
        assert b[0].outcome == "EXT" and b[0].lots == 0 and b[0].runup_pct == 21.4

    # calm: both open, run-up logged on both
    monkeypatch.setattr("app.kite_data.kite_option_runup", lambda *a, **k: 4.2)
    assert open_paper_trade(uid, {**sig, "instrument": "NIFTY 24050 CE",
                                  "strike": 24050}, "sig2", "16-Sep-2026", CFG)
    with SessionLocal() as db:
        b_open = db.query(T).filter(T.user_id == uid, T.account == "B",
                                    T.status == "open").all()
        assert len(b_open) == 1 and b_open[0].runup_pct == 4.2
        # B's equity/concurrency are its own: A has 2 open, B has 1 open
        assert db.query(T).filter(T.user_id == uid, T.account == "A",
                                  T.status == "open").count() == 2

    # summaries are account-scoped; API validates the account param
    sb = paper_trades_summary(uid, account="B")
    assert sb["summary"]["account"] == "B" and sb["summary"]["skipped"] == 1
    assert sb["summary"]["open"] == 1
    sa = paper_trades_summary(uid, account="A")
    assert sa["summary"]["skipped"] == 0 and sa["summary"]["open"] == 2
    r = client.get("/api/scalp/paper-trades?account=B", headers=auth)
    assert r.status_code == 200 and r.json()["summary"]["account"] == "B"
    assert client.get("/api/scalp/paper-trades?account=X", headers=auth).status_code == 422
    _uid()
