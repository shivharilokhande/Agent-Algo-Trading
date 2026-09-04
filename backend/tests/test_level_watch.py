"""Level Watch tests: card→levels parsing, arming, market-hours gate."""
from datetime import datetime
from zoneinfo import ZoneInfo


def test_levels_from_card_parsing():
    from app.fno import levels_from_card

    card = {"rows": [
        {"scenario": "IF BREAKS DOWN", "condition": "Confirmed close below 23,800",
         "instrument": "NIFTY 23850 PE", "ep": 75.8, "sl": 45.5, "tp1": 121.3, "primary": True},
        {"scenario": "IF BREAKS UP", "condition": "Reclaim through 24,000–24,200 on volume",
         "instrument": "NIFTY 23950 CE", "ep": 135.8, "sl": 81.5, "tp1": 217.3, "primary": False},
        {"scenario": "NOW", "condition": "enter on strength", "instrument": "X", "ep": 1},
    ]}
    levels = levels_from_card(card)
    assert len(levels) == 2
    assert levels[0] == {"direction": "below", "level": 23800.0, "instrument": "NIFTY 23850 PE",
                         "ep": 75.8, "sl": 45.5, "tp1": 121.3, "primary": True}
    # "Reclaim through 24,000–24,200" → above 24000 (first number after the keyword)
    assert levels[1]["direction"] == "above" and levels[1]["level"] == 24000.0


def test_market_hours_ist():
    from app.fno import is_market_hours_ist

    ist = ZoneInfo("Asia/Kolkata")
    assert is_market_hours_ist(datetime(2026, 9, 4, 10, 30, tzinfo=ist))   # Fri mid-session
    assert is_market_hours_ist(datetime(2026, 9, 4, 9, 15, tzinfo=ist))
    assert not is_market_hours_ist(datetime(2026, 9, 4, 9, 0, tzinfo=ist))   # pre-open
    assert not is_market_hours_ist(datetime(2026, 9, 4, 15, 45, tzinfo=ist)) # post-close
    assert not is_market_hours_ist(datetime(2026, 9, 6, 11, 0, tzinfo=ist))  # Sunday


def test_arm_level_watch_from_stored_card(client, auth):
    import json

    from app.db import SessionLocal
    from app.models import Run, RunReport, Trigger, User
    from app.runner import arm_level_watch

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
        run = Run(user_id=uid, ticker="^NSEI", trade_date="2026-09-04",
                  config_json="{}", status="done", rating="Underweight", mode="engine")
        db.add(run)
        db.commit()
        rid = run.id
        card = {"verdict": "NO TRADE NOW", "rows": [
            {"scenario": "IF BREAKS DOWN", "condition": "close below 23800",
             "instrument": "NIFTY 23850 PE", "ep": 75.8, "sl": 45.5, "tp1": 121.3, "primary": True},
            {"scenario": "IF BREAKS UP", "condition": "close above 24050",
             "instrument": "NIFTY 23950 CE", "ep": 135.8, "sl": 81.5, "tp1": 217.3, "primary": False},
        ]}
        db.add(RunReport(run_id=rid, section="fno_trade_card", content_md=json.dumps(card)))
        db.commit()

    assert arm_level_watch(rid, uid, "NIFTY") == 2
    assert arm_level_watch(rid, uid, "NIFTY") == 0  # idempotent — no duplicates

    with SessionLocal() as db:
        rows = db.query(Trigger).filter(Trigger.user_id == uid, Trigger.ticker == "NIFTY",
                                        Trigger.type.in_(("level_below", "level_above"))).all()
        assert {(t.type, t.threshold) for t in rows} == {("level_below", 23800.0), ("level_above", 24050.0)}
        cfg = json.loads(rows[0].config_json)
        assert cfg["source"] == "trade_card" and cfg["run_id"] == rid
        for t in rows:  # cleanup
            db.delete(t)
        db.commit()
