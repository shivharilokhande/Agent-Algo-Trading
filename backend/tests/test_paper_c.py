"""Shadow portfolio C — the 18-Sep risk guards, each recorded with a skip code."""
from datetime import datetime, timedelta, timezone

from app.paper_trade import (C_DAY_LOSS_STOP_PCT, C_MAX_TRADES_DAY, C_RISK_PCT,
                             C_SYMBOL_COOLDOWN_MIN, c_skip_reason, open_paper_trade)
from test_paper_trade import CFG, SIG, _uid

# a WALL_REJECT in a trending tape with a fresh touch — passes every C gate
WR = {**SIG, "rule": "WALL_REJECT", "instrument": "NIFTY 23300 CE", "strike": 23300,
      "quality_ok": True, "touch_age": 0, "day_bias": "Buy"}


def _rows(uid, account="C"):
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade as T

    with SessionLocal() as db:
        return db.query(T).filter(T.user_id == uid, T.account == account).all()


def test_c_opens_alongside_a_and_b_at_2pct(client, auth, monkeypatch):
    monkeypatch.setattr("app.kite_data.kite_option_runup", lambda *a, **k: None)
    uid = _uid()
    assert open_paper_trade(uid, WR, "s1", "22-Sep-2026", CFG) is not None
    a, c = _rows(uid, "A"), _rows(uid, "C")
    assert len(a) == 1 and len(c) == 1 and c[0].status == "open"
    # A: 1% of 5L = ₹5,000 → 4 lots; C: 2% = ₹10,000 → 8 lots (risk ₹18 × 65)
    assert a[0].lots == 4 and c[0].lots == 8 and C_RISK_PCT == 2.0
    _uid()


def test_c_disables_orb_and_records_rule(client, auth, monkeypatch):
    monkeypatch.setattr("app.kite_data.kite_option_runup", lambda *a, **k: None)
    uid = _uid()
    assert open_paper_trade(uid, SIG, "s1", None, CFG) is not None  # SIG is ORB
    c = _rows(uid)
    assert len(c) == 1 and c[0].status == "skipped" and c[0].outcome == "RULE"
    assert _rows(uid, "A")[0].status == "open"  # A unchanged
    _uid()


def test_c_symbol_cooldown_kills_straddle(client, auth, monkeypatch):
    """18-Sep: NIFTY 23300 PE at 11:38 and 23300 CE at 11:40 — C takes one."""
    monkeypatch.setattr("app.kite_data.kite_option_runup", lambda *a, **k: None)
    uid = _uid()
    pe = {**WR, "direction": "PE", "instrument": "NIFTY 23300 PE"}
    assert open_paper_trade(uid, pe, "s1", None, CFG) is not None
    open_paper_trade(uid, WR, "s2", None, CFG)  # CE 2 min later, same symbol
    c = sorted(_rows(uid), key=lambda t: t.created_at)
    assert [t.status for t in c] == ["open", "skipped"] and c[1].outcome == "CD"
    # a different symbol is not blocked
    bn = {**WR, "symbol": "BANKNIFTY", "instrument": "BANKNIFTY 56300 CE",
          "sizing": {"lot_size": 30}}
    open_paper_trade(uid, bn, "s3", None, CFG)
    assert _rows(uid)[-1].status == "open"
    _uid()


def test_c_skip_reason_max_daystop_regime(client, auth):
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade as T

    uid = _uid()
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)

    def seed(n, pnl=100.0, symbol="BANKNIFTY"):
        with SessionLocal() as db:
            for i in range(n):
                db.add(T(user_id=uid, account="C", day=today, symbol=symbol,
                         rule="WALL_REJECT", direction="CE", instrument=f"{symbol} {i} CE",
                         entry_p=100, sl=82, tp=127, lot_size=30, lots=1,
                         capital_used=3000, status="closed", outcome="TIME",
                         pnl=pnl, created_at=old))
            db.commit()

    with SessionLocal() as db:
        # regime: Hold day + stale touch or dirty tape → REG; fresh + clean → ok
        hold = {**WR, "day_bias": "Hold"}
        assert c_skip_reason(db, uid, {**hold, "touch_age": 3}, 100_000) == "REG"
        assert c_skip_reason(db, uid, {**hold, "quality_ok": False}, 100_000) == "REG"
        assert c_skip_reason(db, uid, {**hold, "touch_age": 1}, 100_000) is None
        assert c_skip_reason(db, uid, {**WR, "day_bias": None, "touch_age": 4}, 100_000) == "REG"
        # a Buy/Sell day (PM has a view) does not need the range-regime gate
        assert c_skip_reason(db, uid, {**WR, "touch_age": 4}, 100_000) is None

    seed(C_MAX_TRADES_DAY)
    with SessionLocal() as db:
        assert c_skip_reason(db, uid, WR, 100_000) == "MAX"
    _uid()

    # day stop: 3% of ₹1L = −₹3,000 realised today → DAY (checked before MAX)
    seed(2, pnl=-1500.0)
    with SessionLocal() as db:
        assert c_skip_reason(db, uid, WR, 100_000) == "DAY"
        assert C_DAY_LOSS_STOP_PCT == 3.0
    _uid()

    # cooldown window is time-based: a same-symbol trade 3h ago does not block
    seed(1, symbol="NIFTY")
    with SessionLocal() as db:
        assert c_skip_reason(db, uid, WR, 100_000) is None
        assert C_SYMBOL_COOLDOWN_MIN == 30
    _uid()


def test_c_summary_and_api(client, auth, monkeypatch):
    from app.paper_trade import paper_trades_summary

    monkeypatch.setattr("app.kite_data.kite_option_runup", lambda *a, **k: None)
    uid = _uid()
    open_paper_trade(uid, SIG, "s1", None, CFG)   # ORB → C skips RULE
    open_paper_trade(uid, WR, "s2", None, CFG)    # C opens
    s = paper_trades_summary(uid, account="C")["summary"]
    assert s["account"] == "C" and s["risk_pct"] == 2.0
    assert s["open"] == 1 and s["skipped"] == 1 and s["skipped_by"] == {"RULE": 1}
    assert "Shadow portfolio C" in s["note"]
    r = client.get("/api/scalp/paper-trades?account=C", headers=auth)
    assert r.status_code == 200 and r.json()["summary"]["account"] == "C"
    _uid()
