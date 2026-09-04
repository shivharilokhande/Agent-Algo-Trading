"""Tests for the ai-fund borrows: sizing (#1), desk review (#2), briefings (#3)."""


def _uid(db):
    from app.models import User

    return db.query(User).filter(User.email == "tester@agentalgo.dev").first().id


# ---------- #1 position sizing ----------

def test_size_position_math():
    from app.fno import size_position

    s = size_position(ep=75.8, sl=45.5, lot_size=65, capital=100_000, risk_pct=1.0)
    # risk/lot = 30.3*65 = 1969.5; budget 1000 → 0 lots (honest zero)
    assert s["lots"] == 0
    s2 = size_position(ep=75.8, sl=45.5, lot_size=65, capital=500_000, risk_pct=1.0)
    # budget 5000 / 1969.5 = 2 lots; outlay 2*65*75.8 = 9854 < 30% cap
    assert s2["lots"] == 2 and s2["max_loss"] == 3939.0 and s2["premium_outlay"] == 9854.0
    # outlay cap binds: huge risk% but premium-heavy
    s3 = size_position(ep=500, sl=490, lot_size=65, capital=100_000, risk_pct=50.0)
    assert s3["premium_outlay"] <= 30_000  # ≤30% of capital
    # unknown lot size → per-unit note
    s4 = size_position(ep=75.8, sl=45.5, lot_size=None, capital=100_000, risk_pct=1.0)
    assert s4["lots"] is None and "lot size" in s4["note"]


def test_enrich_card_sizing():
    from app.fno import enrich_card_sizing

    card = {"symbol": "NIFTY", "rows": [{"ep": 75.8, "sl": 45.5}]}
    out = enrich_card_sizing(card, {"trading_capital": 500_000, "risk_per_trade_pct": 1.0})
    assert out["rows"][0]["sizing"]["lots"] == 2
    assert out["sizing_basis"]["lot_size"] == 65  # NIFTY default (Jan-2026 series)
    # lot override via settings
    out2 = enrich_card_sizing({"symbol": "NIFTY", "rows": [{"ep": 10, "sl": 6}]},
                              {"trading_capital": 100_000, "fno_lot_sizes": {"NIFTY": 50}})
    assert out2["sizing_basis"]["lot_size"] == 50


# ---------- #2 desk review ----------

def test_extract_call():
    from app.desk import extract_call

    assert extract_call("FINAL TRANSACTION PROPOSAL: **HOLD**\n# report") == "Hold"
    assert extract_call("...\nRecommendation: Overweight.") == "Overweight"
    assert extract_call("Rating: **Sell**") == "Sell"
    assert extract_call("no proposal here") is None


def test_record_and_grade_agent_calls(client, auth):
    from app.db import SessionLocal
    from app.desk import desk_review, grade_agent_calls, record_agent_calls
    from app.models import Run, RunReport

    with SessionLocal() as db:
        uid = _uid(db)
        run = Run(user_id=uid, ticker="DESK", trade_date="2026-09-01", config_json="{}",
                  status="done", rating="Underweight")
        db.add(run)
        db.commit()
        rid = run.id
        db.add(RunReport(run_id=rid, section="market_report",
                         content_md="FINAL TRANSACTION PROPOSAL: **SELL**\ntechnicals..."))
        db.add(RunReport(run_id=rid, section="news_report",
                         content_md="FINAL TRANSACTION PROPOSAL: **HOLD**\nnews..."))
        db.add(RunReport(run_id=rid, section="final_trade_decision",
                         content_md="Rating: Underweight\n..."))
        db.commit()

    assert record_agent_calls(rid) == 3
    assert record_agent_calls(rid) == 0  # idempotent
    # negative alpha → bearish calls correct, Hold wrong (|alpha| ≥ 2%)
    assert grade_agent_calls(rid, alpha=-0.03) == 3
    review = {r["agent"]: r for r in desk_review(uid)}
    assert review["Market Analyst"]["hit_rate"] == 1.0
    assert review["Portfolio Manager"]["hit_rate"] == 1.0
    assert review["News Analyst"]["hit_rate"] == 0.0
    r = client.get("/api/desk-review", headers=auth)
    assert r.status_code == 200 and any(x["agent"] == "Market Analyst" for x in r.json())


# ---------- #3 briefing books ----------

def test_briefing_append_trim_and_context(client, auth):
    from app.db import SessionLocal
    from app.desk import append_briefing, briefing_context

    with SessionLocal() as db:
        uid = _uid(db)
    append_briefing(uid, "^NSEI", "[2026-09-01] run → Hold. Box 23800-24000 held.")
    append_briefing(uid, "^NSEI", "[2026-09-04] run → Underweight. Watching 23800 break.")
    ctx = briefing_context(uid, "^NSEI")
    assert "23800" in ctx and "Underweight" in ctx
    # trim: giant entries never exceed the cap
    for i in range(30):
        append_briefing(uid, "^NSEI", f"[filler {i}] " + "x" * 500)
    from app.models import Briefing

    with SessionLocal() as db:
        row = db.query(Briefing).filter(Briefing.user_id == uid, Briefing.ticker == "^NSEI").one()
        assert len(row.content) <= 8_000
    # API endpoint
    r = client.get("/api/briefings/NIFTY", headers=auth)
    assert r.status_code == 200 and r.json()["ticker"] == "^NSEI"
