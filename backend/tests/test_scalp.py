"""Scalp Mode — indicator math, rule evaluation, bias filter, API surface."""
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _bars(closes, base_vol=1000.0):
    """Synthetic 1m bars: h/l wrap the close by ±2 points."""
    return [{"t": f"09:{15 + i:02d}", "h": c + 2, "l": c - 2, "c": c, "v": base_vol}
            for i, c in enumerate(closes)]


def test_indicators():
    from app.scalp import ema, opening_range, rsi, vwap

    closes = [100 + i for i in range(30)]
    bars = _bars(closes)
    assert ema(closes, 9) is not None and ema(closes, 9) > ema(closes, 20)
    assert rsi(closes) == 100.0  # monotonic up
    assert vwap(bars) is not None
    # NSE index feeds report zero volume — VWAP must fall back, never go None
    zero_vol = [{**b, "v": 0.0} for b in bars]
    assert vwap(zero_vol) is not None
    orh, orl = opening_range(bars)
    assert orh == closes[14] + 2 and orl == closes[0] - 2
    assert ema([1, 2], 9) is None and rsi([1, 2]) is None  # insufficient data


def test_orb_and_vwap_rules():
    from app.scalp import evaluate_rules

    def trend(start, step_main, step_pull, n):
        out, px = [], start
        for i in range(n):
            px += step_pull if i % 3 == 2 else step_main
            out.append(px)
        return out

    # FRESH breakout: consolidate inside the range, then break out in the last bars
    up = [24000.0 + (1 if i % 2 else -1) for i in range(15)]          # opening range ≈ 24003
    up += trend(24000, 2, -1, 15)[:12]                                # drift up under OR high
    up += [24001, 24002, 24004.5, 24008, 24012]                       # cross happens HERE
    hits = evaluate_rules(_bars(up))
    assert any(h["rule"] == "ORB" and h["direction"] == "CE" for h in hits)
    # STALE breakout (broke long ago, still trending) must NOT re-fire
    stale = trend(24000, 4, -3, 40)
    hits = evaluate_rules(_bars(stale))
    assert not any(h["rule"] == "ORB" for h in hits)
    # fresh breakdown → ORB PE
    dn = [24000.0 + (1 if i % 2 else -1) for i in range(15)]
    dn += trend(24000, -2, 1, 15)[:12]
    dn += [23999, 23998, 23995.5, 23992, 23988]
    hits = evaluate_rules(_bars(dn))
    assert any(h["rule"] == "ORB" and h["direction"] == "PE" for h in hits)
    # flat chop inside range → nothing
    flat = [24000 + (1 if i % 2 else -1) for i in range(35)]
    assert evaluate_rules(_bars(flat)) == []
    # too little data → nothing
    assert evaluate_rules(_bars([24000] * 10)) == []


def test_quality_filters_gate_trend_rules():
    """R6: wide opening range / extended EMA gap suppress ORB & VWAP_RECLAIM
    (60d-validated filters) — WALL_REJECT is not gated (no backtest data)."""
    from app.scalp import evaluate_rules

    def trend(start, step_main, step_pull, n):
        out, px = [], start
        for i in range(n):
            px += step_pull if i % 3 == 2 else step_main
            out.append(px)
        return out

    # same fresh ORB CE shape as the passing test, but a HUGE opening range
    up = [24000.0 + (200 if i % 2 else -200) for i in range(15)]  # OR ≈ 400+ pts wide
    up += trend(24000, 2, -1, 15)[:12]
    up += [24201, 24202, 24404.5, 24408, 24412]
    bars = _bars(up)
    assert not any(h["rule"] in ("ORB", "VWAP_RECLAIM") for h in evaluate_rules(bars))
    # WALL_REJECT can still fire on a wide-OR day (ungated): take the passing
    # wall-reject shape and widen the opening range via early highs/lows only
    closes = [24000.0] * 20 + [24030, 24060, 24080, 24096, 24098, 24060, 24030, 24010]
    bars2 = _bars(closes)
    bars2[1]["h"] = 24250.0   # OR width ≈ 500 pts — way past the 55bp gate
    bars2[2]["l"] = 23750.0
    hits = evaluate_rules(bars2, {"resistance": 24100.0, "support": 23000.0})
    assert any(h["rule"] == "WALL_REJECT" for h in hits)
    assert not any(h["rule"] == "ORB" for h in hits)  # trend rules stay gated


def test_wall_reject_rule():
    from app.scalp import evaluate_rules

    # rallies into the 24100 CE wall then REJECTS hard (momentum lost at the end)
    closes = [24000] * 20 + [24030, 24060, 24080, 24096, 24098, 24060, 24030, 24010]
    hits = evaluate_rules(_bars(closes), {"resistance": 24100.0, "support": 23800.0})
    assert any(h["rule"] == "WALL_REJECT" and h["direction"] == "PE" for h in hits)
    pe = next(h for h in hits if h["rule"] == "WALL_REJECT")
    assert pe["touch_age"] == 3 and "quality_ok" in pe  # high was 4 bars back → age 3


def test_wall_reject_pin_and_straddle_guard():
    """18-Sep rule fix: top CE-OI and top PE-OI on the SAME strike is a max-pain
    pin, not a wall — no WALL_REJECT. And when both walls are touched inside
    the 5-bar window (flat tape), emit neither, never a PE+CE pair."""
    from app.scalp import evaluate_rules

    # 12:10 shape: wobble around 23300 with spot == VWAP (no trend either way);
    # wall touches injected symmetrically so VWAP stays exactly 23300
    flat = [23300.0] * 20 + [23301, 23299] * 3 + [23300, 23300]
    bars = _bars(flat)
    bars[-2]["h"] = 23314.0   # +12 on the high …
    bars[-2]["l"] = 23286.0   # … −12 on the low of the SAME bar → VWAP unchanged
    # same strike both sides → rule off entirely
    assert not any(h["rule"] == "WALL_REJECT"
                   for h in evaluate_rules(bars, {"resistance": 23300.0, "support": 23300.0}))
    # two distinct walls, both touched within the band (0.15% ≈ 35 pts) → neither
    hits = evaluate_rules(bars, {"resistance": 23315.0, "support": 23290.0})
    assert not any(h["rule"] == "WALL_REJECT" for h in hits)
    # one wall touched → still fires (the rule itself is intact)
    hits = evaluate_rules(bars, {"resistance": 23315.0, "support": 23000.0})
    assert [h["direction"] for h in hits if h["rule"] == "WALL_REJECT"] == ["PE"]


def test_bias_filter_and_theta_cutoff():
    from app.scalp import bias_allows, theta_cutoff_passed

    assert bias_allows("CE", "Overweight") and not bias_allows("PE", "Overweight")
    assert bias_allows("PE", "Sell") and bias_allows("CE", "Buy")  # R5-18: was vacuous
    assert bias_allows("CE", "Hold") and bias_allows("PE", None)
    # cutoff moved 14:30 → 15:00 (paper-month day 1, swept + user-approved)
    assert theta_cutoff_passed(datetime(2026, 9, 7, 15, 0, tzinfo=IST))
    assert not theta_cutoff_passed(datetime(2026, 9, 7, 14, 30, tzinfo=IST))
    assert not theta_cutoff_passed(datetime(2026, 9, 7, 11, 0, tzinfo=IST))


def test_pick_strike_and_build_signal():
    from app.scalp import build_scalp_signal, pick_scalp_strike

    ladder = [
        {"strike": 23900, "ce_ltp": 120.0, "pe_ltp": 40.0, "ce_delta": 0.62, "pe_delta": -0.38,
         "ce_theta": -8.0, "pe_theta": -7.0},
        {"strike": 23950, "ce_ltp": 90.0, "pe_ltp": 55.0, "ce_delta": 0.52, "pe_delta": -0.48,
         "ce_theta": -9.0, "pe_theta": -8.5},
        {"strike": 24000, "ce_ltp": 66.0, "pe_ltp": 75.0, "ce_delta": 0.41, "pe_delta": -0.59,
         "ce_theta": -9.5, "pe_theta": -9.0},
    ]
    # CE pick: 23950 (delta .52 closest to .50 inside band; 0.62 is out of band)
    assert pick_scalp_strike(ladder, "CE")["strike"] == 23950
    assert pick_scalp_strike(ladder, "PE")["strike"] == 23950
    snap = {"spot": 23940.0, "atm_ladder": ladder}
    sig = build_scalp_signal("NIFTY", {"rule": "ORB", "direction": "CE", "why": "test"},
                             snap, {"trading_capital": 500_000, "risk_per_trade_pct": 1.0})
    assert sig["instrument"] == "NIFTY 23950 CE"
    assert sig["ep"] == 90.0 and sig["sl"] == 73.8  # −18%
    assert sig["tp"] == round(90.0 + 16.2 * 1.5, 2)  # 1:1.5
    # scalp risk defaults to half the swing risk → budget 2500; risk/lot 16.2*65=1053 → 2 lots
    assert sig["sizing"]["lots"] == 2 and sig["sizing"]["profit_tp"] == round(24.3 * 65 * 2, 2)
    # empty ladder → no signal
    assert build_scalp_signal("NIFTY", {"rule": "ORB", "direction": "CE", "why": ""},
                              {"spot": 1, "atm_ladder": []}, {}) is None
    # policy G (test mode) attaches the ride plan; A (default) doesn't
    sig_g = build_scalp_signal("NIFTY", {"rule": "ORB", "direction": "CE", "why": "t"},
                               snap, {"trading_capital": 500_000, "scalp_exit_policy": "G"})
    risk = round(90.0 * 0.18, 2)
    assert sig_g["exit_policy"] == "G"
    assert sig_g["ride_floor"] == round(90.0 + 1.2 * risk, 2)
    assert sig_g["trail_gap"] == round(0.3 * risk, 2)
    sig_a = build_scalp_signal("NIFTY", {"rule": "ORB", "direction": "CE", "why": "t"},
                               snap, {"trading_capital": 500_000})
    assert "exit_policy" not in sig_a




def test_scalp_api_and_dedupe(client, auth):
    from app.db import SessionLocal
    from app.models import User
    from app.scalp import emit_signal

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
    sig = {"symbol": "NIFTY", "rule": "ORB", "direction": "CE", "instrument": "NIFTY 23950 CE",
           "ep": 90.0, "sl": 73.8, "tp": 114.3, "rr": 1.5, "why": "t", "sizing": {"lots": 2}}
    assert emit_signal(uid, sig, simulated=True) is not None
    assert emit_signal(uid, sig, simulated=True) is None  # cooldown dedupe
    r = client.get("/api/scalp/signals", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body and body[0]["instrument"] == "NIFTY 23950 CE" and body[0]["simulated"] is True
    r2 = client.get("/api/scalp/status", headers=auth)
    assert r2.status_code == 200 and "market_open" in r2.json()
