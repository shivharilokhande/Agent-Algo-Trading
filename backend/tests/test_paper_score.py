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
    created = datetime(2026, 9, 8, 10, 0, tzinfo=IST)
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
    created_utc = datetime(now_ist.year, now_ist.month, now_ist.day, 10, 0,
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
        db.commit()

    rally = _bars(day, [24000 + 15 * i for i in range(10)])
    monkeypatch.setattr("app.backtest.fetch_history_sessions",
                        lambda sym, days=8: {day: rally})
    assert score_day(day) == 1          # real one scored, sim ignored
    assert score_day(day) == 0          # idempotent

    s = paper_summary(uid, days=7)
    assert s["total"]["n"] == 1 and s["total"]["wins"] == 1
    assert s["total"]["win_pct"] == 100.0 and s["total"]["sum_r"] == 1.5
    assert s["wall_reject"]["n"] == 1 and s["wall_reject"]["win_pct"] == 100.0
    assert s["days"][0]["day"] == day and s["days"][0]["signals"][0]["outcome"] == "TP"

    # API surface
    r = client.get("/api/scalp/paper?days=7", headers=auth)
    assert r.status_code == 200 and r.json()["total"]["n"] == 1
    r = client.post("/api/scalp/paper/score", headers=auth)
    assert r.status_code == 200 and r.json()["scored"] == 0
    assert client.get("/api/scalp/paper?days=99", headers=auth).status_code == 422
