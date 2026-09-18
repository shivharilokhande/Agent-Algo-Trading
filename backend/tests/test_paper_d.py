"""Hero-zero portfolio D — expiry-day tickets, isolated from A/B/C."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.paper_trade import (D_ARM_MULT, D_BUDGET_PCT, D_MAX_TICKETS_DAY, D_TRAIL_PCT,
                             _expiry_is_today, _resolve_hero_zero, d_skip_reason,
                             open_hero_zero, pick_hero_strike)
from test_paper_trade import CFG, SIG, _uid

IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime.now(IST).strftime("%d-%b-%Y")
_DECAY = [250, 180, 120, 80, 50, 30, 18, 12, 8, 5, 3]  # premium by distance in 50-pt steps
LADDER = ([{"strike": 24000 + 50 * i, "ce_ltp": p, "pe_ltp": 300} for i, p in enumerate(_DECAY)]
          + [{"strike": 24000 - 50 * i, "ce_ltp": 300, "pe_ltp": p} for i, p in enumerate(_DECAY) if i])
SNAP = {"expiry": TODAY, "spot": 24000.0, "otm_ladder": LADDER}
ORB = {**SIG, "rule": "ORB"}  # SIG is ORB CE already; explicit for readability


def _at(h, m):
    return datetime.now(IST).replace(hour=h, minute=m, second=0, microsecond=0)


def test_expiry_and_strike_pick():
    assert _expiry_is_today(TODAY, _at(13, 0)) and not _expiry_is_today("01-Jan-2020", _at(13, 0))
    assert not _expiry_is_today(None, _at(13, 0)) and not _expiry_is_today("garbage", _at(13, 0))
    # CE: cheapest ₹4–15 premium ABOVE spot; ladder 24300 → ₹? : 250-270 <0 → 0.5 … pick in band
    pick = pick_hero_strike(LADDER, "CE", 24000.0)
    assert pick and pick["strike"] > 24000 and 4 <= pick["ltp"] <= 15
    pe = pick_hero_strike(LADDER, "PE", 24000.0)
    assert pe and pe["strike"] < 24000 and 4 <= pe["ltp"] <= 15
    # nothing in band → None
    assert pick_hero_strike([{"strike": 24500, "ce_ltp": 1.0, "pe_ltp": 1.0}], "CE", 24000.0) is None


def test_d_skip_reasons(client, auth):
    from app.db import SessionLocal

    uid = _uid()
    with SessionLocal() as db:
        assert d_skip_reason(db, uid, ORB, {**SNAP, "expiry": "01-Jan-2020"}, _at(13, 30)) == "NOEXP"
        assert d_skip_reason(db, uid, ORB, SNAP, _at(12, 59)) == "WIN"
        assert d_skip_reason(db, uid, ORB, SNAP, _at(15, 0)) == "WIN"
        assert d_skip_reason(db, uid, {**ORB, "rule": "WALL_REJECT"}, SNAP, _at(13, 30)) == "RULE"
        assert d_skip_reason(db, uid, ORB, SNAP, _at(13, 30)) is None
    _uid()


def test_d_ticket_open_and_limits(client, auth, monkeypatch):
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade as T

    uid = _uid()
    fixed = _at(13, 30)

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr("app.paper_trade.datetime", _Now)

    tid = open_hero_zero(uid, ORB, "s1", SNAP, CFG)
    assert tid is not None
    with SessionLocal() as db:
        t = db.get(T, tid)
        assert t.account == "D" and t.status == "open" and t.sl == 0.05
        assert t.tp == round(t.entry_p * D_ARM_MULT, 2) and t.peak_p == t.entry_p
        # 1% of 5L = ₹5,000 budget; lots = budget // (premium × 65)
        assert t.lots == int(500_000 * D_BUDGET_PCT / 100 // (t.entry_p * 65)) >= 1
        assert t.instrument.split()[1] != "24000"  # far-OTM, not ATM
        # A/B/C are untouched by D
        assert db.query(T).filter(T.user_id == uid, T.account != "D").count() == 0
    # second ticket while one is open → OPEN skip row
    assert open_hero_zero(uid, ORB, "s2", SNAP, CFG) is None
    with SessionLocal() as db:
        rows = db.query(T).filter(T.user_id == uid, T.account == "D").order_by(T.created_at).all()
        assert [r.status for r in rows] == ["open", "skipped"] and rows[1].outcome == "OPEN"
        # close it, then fill the day cap
        rows[0].status = "closed"
        db.commit()
    assert open_hero_zero(uid, ORB, "s3", SNAP, CFG) is not None
    with SessionLocal() as db:
        for r in db.query(T).filter(T.user_id == uid, T.account == "D", T.status == "open"):
            r.status = "closed"
        db.commit()
    assert D_MAX_TICKETS_DAY == 2
    assert open_hero_zero(uid, ORB, "s4", SNAP, CFG) is None
    with SessionLocal() as db:
        assert db.query(T).filter(T.user_id == uid, T.account == "D",
                                  T.outcome == "MAX").count() == 1
    # non-expiry day: silent (no row at all)
    _uid()
    assert open_hero_zero(uid, ORB, "s5", {**SNAP, "expiry": "01-Jan-2020"}, CFG) is None
    with SessionLocal() as db:
        assert db.query(T).filter(T.user_id == uid).count() == 0
    _uid()


def test_d_exit_rules(client, auth):
    from app.db import SessionLocal
    from app.models import ScalpPaperTrade as T

    uid = _uid()

    def mk(ep=10.0):
        with SessionLocal() as db:
            t = T(user_id=uid, account="D", day=datetime.now(IST).date().isoformat(),
                  symbol="NIFTY", rule="ORB", direction="CE", instrument="NIFTY 24300 CE",
                  expiry=TODAY, entry_p=ep, sl=0.05, tp=ep * D_ARM_MULT, peak_p=ep,
                  lot_size=65, lots=7, capital_used=ep * 65 * 7)
            db.add(t)
            db.commit()
            return t.id

    with SessionLocal() as db:
        t = db.get(T, mk())
        # pre-arm drawdown does NOT exit (premium is the stop)
        assert _resolve_hero_zero(db, t, 4.0, _at(13, 45)) is False and t.status == "open"
        # runs to 3× → armed; a dip to 62% of peak (<40% off) holds …
        assert _resolve_hero_zero(db, t, 30.0, _at(13, 50)) is False and t.peak_p == 30.0
        assert _resolve_hero_zero(db, t, 19.0, _at(13, 51)) is False
        # … 40% off the peak exits on TRAIL at the bid
        assert _resolve_hero_zero(db, t, 18.0, _at(13, 52)) is True
        assert t.outcome == "TRAIL" and t.exit_p == 18.0 and t.pnl > 0
        db.commit()
        # settle at 15:20 whatever the level
        t2 = db.get(T, mk())
        assert _resolve_hero_zero(db, t2, 6.0, _at(15, 20)) is True and t2.outcome == "EXPIRY"
        # no quote after the close → expires at the floor
        t3 = db.get(T, mk())
        assert _resolve_hero_zero(db, t3, None, _at(14, 0)) is False  # in-session, wait
        assert _resolve_hero_zero(db, t3, None, _at(15, 45)) is True
        assert t3.outcome == "EXPIRY" and t3.exit_p == 0.05 and t3.exit_source == "modeled"
        db.commit()
    assert D_TRAIL_PCT == 40.0
    _uid()


def test_d_api_and_summary(client, auth):
    from app.paper_trade import paper_trades_summary

    uid = _uid()
    s = paper_trades_summary(uid, account="D")["summary"]
    assert s["account"] == "D" and "Hero-zero" in s["note"]
    r = client.get("/api/scalp/paper-trades?account=D", headers=auth)
    assert r.status_code == 200 and r.json()["summary"]["account"] == "D"
    assert client.get("/api/scalp/paper-trades?account=E", headers=auth).status_code == 422
