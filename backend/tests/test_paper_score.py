"""Paper-week scoreboard — scoring real signals against actual candles."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _bars(day: str, closes: list[float], start=(10, 0)):
    out = []
    h, m = start
    for i, c in enumerate(closes):
        mm = m + i
        hh, mm = h + mm // 60, mm % 60
        out.append({"t": f"{day}T{hh:02d}:{mm:02d}:00+05:30", "hm": (hh, mm),
                    "h": c + 1, "l": c - 1, "c": c, "v": 0.0})
    return out


def test_score_signal_tp_and_sl():
    from app.paper_score import score_signal

    day = "2026-09-08"
    created = datetime(2026, 9, 8, 10, 1, tzinfo=IST)  # enters on the 10:00 bar (strictly before)
    # ep 100 → risk 18 premium ≈ spot move 18/0.5=36 for SL, 54 for TP
    up = _bars(day, [24000 + 15 * i for i in range(10)])   # strong rally → TP
    res = score_signal({"ep": 100.0}, "CE", up, created)
    assert res and res["outcome"] == "TP" and res["r"] == 1.5
    dn = _bars(day, [24000 - 15 * i for i in range(10)])   # dump → SL for CE
    res = score_signal({"ep": 100.0}, "CE", dn, created)
    assert res and res["outcome"] == "SL" and res["r"] == -1.0
    # signal after the last bar → unscorable
    late = datetime(2026, 9, 8, 15, 29, tzinfo=IST)
    assert score_signal({"ep": 100.0}, "CE", up, late) is None


def test_score_day_and_summary(client, auth, monkeypatch):
    from app.db import SessionLocal
    from app.models import ScalpSignal, User
    from app.paper_score import paper_summary, score_day

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
        db.query(ScalpSignal).filter(ScalpSignal.user_id == uid).delete()
        db.commit()

    now_ist = datetime.now(IST)
    day = now_ist.date().isoformat()
    # a real signal "created" at 10:00 IST today (stored naive UTC)
    created_utc = datetime(now_ist.year, now_ist.month, now_ist.day, 10, 1,
                           tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        db.add(ScalpSignal(user_id=uid, symbol="NIFTY", rule="WALL_REJECT",
                           direction="CE", instrument="NIFTY 24000 CE",
                           payload_json=json.dumps({"ep": 100.0}), simulated=False,
                           created_at=created_utc))
        db.add(ScalpSignal(user_id=uid, symbol="NIFTY", rule="ORB", direction="CE",
                           instrument="NIFTY 24000 CE",
                           payload_json=json.dumps({"ep": 100.0}), simulated=True,
                           created_at=created_utc))  # simulated → never scored
        db.add(ScalpSignal(user_id=uid, symbol="NIFTY", rule="WALL_REJECT", direction="PE",
                           instrument="NIFTY 24000 PE",
                           payload_json=json.dumps({"ep": 100.0, "voided": "PIN_BUG"}),
                           simulated=False, created_at=created_utc))  # voided → kept, not scored
        db.commit()

    rally = _bars(day, [24000 + 15 * i for i in range(10)])
    monkeypatch.setattr("app.backtest.fetch_history_sessions",
                        lambda sym, days=8: {day: rally})
    assert score_day(day) == 1          # real one scored; sim and voided ignored
    assert score_day(day) == 0          # idempotent

    s = paper_summary(uid, days=7)
    assert s["total"]["n"] == 1 and s["total"]["wins"] == 1
    assert s["unscored"] == 0           # the voided row is not "pending" either
    assert s["total"]["win_pct"] == 100.0 and s["total"]["sum_r"] == 1.5
    assert s["wall_reject"]["n"] == 1 and s["wall_reject"]["win_pct"] == 100.0
    assert s["days"][0]["day"] == day and s["days"][0]["signals"][0]["outcome"] == "TP"

    # API surface
    r = client.get("/api/scalp/paper?days=7", headers=auth)
    assert r.status_code == 200 and r.json()["total"]["n"] == 1
    import app.paper_score as ps
    monkeypatch.setattr(ps, "SCORE_AFTER_HM", (0, 0))   # "session over" → allowed
    r = client.post("/api/scalp/paper/score", headers=auth)
    assert r.status_code == 200 and r.json()["scored"] == 0
    monkeypatch.setattr(ps, "SCORE_AFTER_HM", (23, 59))  # mid-session → refused (R6-1)
    assert client.post("/api/scalp/paper/score", headers=auth).status_code == 409
    assert client.get("/api/scalp/paper?days=99", headers=auth).status_code == 422


def test_rupee_pnl_and_provisional_today(client, auth, monkeypatch):
    """Paper month: ₹ P&L from real sizing + live provisional strip."""
    from app.backtest import trade_cost
    from app.db import SessionLocal
    from app.models import ScalpSignal, User
    from app.paper_score import _rupee_pnl, provisional_today

    # ₹ math: ep 100, sl 82 (risk 18), TP +1.5R, 2 lots of 65
    p = {"ep": 100.0, "sl": 82.0, "sizing": {"lots": 2, "lot_size": 65}}
    pnl, cost = _rupee_pnl(p, "NIFTY", 1.5)
    assert cost == trade_cost(100.0, 127.0, 65, 2)
    assert pnl == round(1.5 * 18.0 * 65 * 2 - cost, 2)
    assert _rupee_pnl({"ep": 100.0}, "NIFTY", 1.5) == (None, None)  # no sizing

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
        db.query(ScalpSignal).filter(ScalpSignal.user_id == uid).delete()
        now_ist = datetime.now(IST)
        created_utc = datetime(now_ist.year, now_ist.month, now_ist.day, 10, 1,
                               tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
        db.add(ScalpSignal(user_id=uid, symbol="NIFTY", rule="ORB", direction="CE",
                           instrument="NIFTY 24000 CE", payload_json=json.dumps(p),
                           simulated=False, created_at=created_utc))
        db.commit()

    day = datetime.now(IST).date().isoformat()
    rally = _bars(day, [24000 + 15 * i for i in range(10)])
    monkeypatch.setattr("app.scalp.fetch_session_bars", lambda sym: rally)
    live = provisional_today(uid)
    assert len(live) == 1 and live[0]["status"] == "TP" and live[0]["r"] == 1.5
    assert live[0]["pnl"] == pnl
    # short flat session → window incomplete, no bracket hit → OPEN
    flat = _bars(day, [24000.0] * 6)
    monkeypatch.setattr("app.scalp.fetch_session_bars", lambda sym: flat)
    assert provisional_today(uid)[0]["status"] == "OPEN"

    with SessionLocal() as db:
        db.query(ScalpSignal).filter(ScalpSignal.user_id == uid).delete()
        db.commit()


def test_live_day_sl_count_feeds_day_stop(client, auth, monkeypatch):
    """2-SL day stop: intraday SL counter on today's REAL signals only."""
    # R6-10 fallback would hit the network when bars are missing — stub it here
    monkeypatch.setattr("app.backtest.fetch_history_sessions",
                        lambda sym, days=8: {})
    from app.db import SessionLocal
    from app.models import ScalpSignal, User
    from app.paper_score import live_day_sl_count

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
        db.query(ScalpSignal).filter(ScalpSignal.user_id == uid).delete()
        now_ist = datetime.now(IST)
        created_utc = datetime(now_ist.year, now_ist.month, now_ist.day, 10, 1,
                               tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
        for i, sim in enumerate([False, False, True]):  # 2 real + 1 sim
            db.add(ScalpSignal(user_id=uid, symbol="NIFTY", rule="ORB", direction="PE",
                               instrument="NIFTY 24000 PE",
                               payload_json=json.dumps({"ep": 100.0}), simulated=sim,
                               created_at=created_utc))
        db.commit()

    day = datetime.now(IST).date().isoformat()
    dump = _bars(day, [24000 + 15 * i for i in range(10)])  # spot UP → PE hits SL
    # live bars have no 'hm' key — the counter must derive it from 't'
    live_bars = [{k: v for k, v in b.items() if k != "hm"} for b in dump]
    assert live_day_sl_count(uid, {"NIFTY": live_bars}) == 2   # sim excluded
    rally_for_pe = _bars(day, [24000 - 15 * i for i in range(10)])  # PE wins → 0 SLs
    assert live_day_sl_count(uid, {"NIFTY": rally_for_pe}) == 0
    assert live_day_sl_count(uid, {}) == 0  # no bars → never blocks

    with SessionLocal() as db:  # cleanup for other tests
        db.query(ScalpSignal).filter(ScalpSignal.user_id == uid).delete()
        db.commit()
