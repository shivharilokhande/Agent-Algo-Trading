"""Round-5 review regressions: theta sign, sim/real cooldown separation,
holiday/forming-bar guards, today-only bias, settings merge, no-lookahead equity."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def test_theta_sign_r5_4():
    """Long options ALWAYS bleed theta — theta_day must be negative for ATM
    calls and puts alike (the rate term's sign was inverted)."""
    from app.fno import bs_greeks

    call = bs_greeks(24000, 24000, 12.0, 7 / 365, is_call=True)
    put = bs_greeks(24000, 24000, 12.0, 7 / 365, is_call=False)
    assert call["theta_day"] < 0 and put["theta_day"] < 0
    # put theta magnitude ≤ call theta at same strike (rates make puts decay less)
    assert abs(put["theta_day"]) <= abs(call["theta_day"])


def test_sim_and_real_cooldowns_are_separate_r5_3(client, auth):
    from app.db import SessionLocal
    from app.models import User
    from app.scalp import emit_signal

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
    sig = {"symbol": "BANKNIFTY", "rule": "ORB", "direction": "CE",
           "instrument": "BANKNIFTY 57000 CE", "ep": 700.0, "sl": 574.0, "tp": 889.0,
           "rr": 1.5, "why": "t", "sizing": {"lots": 1}}
    assert emit_signal(uid, sig, simulated=True) is not None
    # a REAL signal must not be blocked by the simulated one just emitted
    assert emit_signal(uid, sig, simulated=False) is not None
    # but a second real one within the cooldown IS blocked
    assert emit_signal(uid, sig, simulated=False) is None


def test_usable_session_bars_guards_r5_2_8():
    from app.scalp import usable_session_bars

    now = datetime(2026, 9, 7, 10, 30, tzinfo=IST)
    mk = lambda ts: {"t": ts.isoformat(), "h": 1, "l": 1, "c": 1, "v": 0}  # noqa: E731
    # previous session (holiday) → rejected entirely
    prev = [mk(now.replace(day=4, hour=10, minute=m)) for m in range(0, 30)]
    assert usable_session_bars(prev, now) == []
    # stale feed (last bar 20 min old) → rejected
    stale = [mk(now - timedelta(minutes=50 - m)) for m in range(0, 30)]
    assert usable_session_bars(stale, now) == []
    # fresh bars with the last one still forming → forming bar dropped
    fresh = [mk(now - timedelta(minutes=10 - m)) for m in range(0, 11)]  # last == now
    out = usable_session_bars(fresh, now)
    assert len(out) == len(fresh) - 1
    # fresh bars, last one already complete (1 min old) → kept as-is
    done = [mk(now - timedelta(minutes=10 - m)) for m in range(0, 10)]
    assert usable_session_bars(done, now) == done


def test_day_bias_today_only_and_finnifty_map_r5_6_7(client, auth):
    from datetime import timezone

    from app.db import SessionLocal
    from app.models import Run, User
    from app.scalp import day_bias

    with SessionLocal() as db:
        uid = db.query(User).filter(User.email == "tester@agentalgo.dev").first().id
        old = Run(user_id=uid, ticker="NIFTY_FIN_SERVICE.NS", trade_date="2026-09-01",
                  config_json="{}", mode="engine", status="done", rating="Sell",
                  finished_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc))
        db.add(old)
        db.commit()
    # stale run → no bias (was silently driving the filter before)
    assert day_bias(uid, "FINNIFTY") is None
    meta = day_bias(uid, "FINNIFTY", with_age=True)
    # …but the ticker map now finds the FINNIFTY run at all (was always None)
    assert meta["rating"] == "Sell" and meta["today"] is False and meta["as_of"] == "2026-09-01"


def test_settings_put_merges_r5_1(client, auth):
    # start clean, set two keys from "the Settings page"
    client.put("/api/settings", headers=auth, json={"config": {}})
    r = client.put("/api/settings", headers=auth,
                   json={"config": {"trading_capital": 500000, "risk_per_trade_pct": 1.0}})
    assert r.status_code == 200
    # a partial save from "the Scalp page" must NOT wipe the other keys
    r = client.put("/api/settings", headers=auth, json={"config": {"scalp_enabled": True}})
    cfg = r.json()["config"]
    assert cfg["trading_capital"] == 500000 and cfg["scalp_enabled"] is True
    # null deletes a single key
    r = client.put("/api/settings", headers=auth, json={"config": {"scalp_enabled": None}})
    assert "scalp_enabled" not in r.json()["config"]
    # explicit empty object still resets everything (legacy semantics)
    r = client.put("/api/settings", headers=auth, json={"config": {}})
    assert r.json()["config"] == {}


def test_backtest_no_lookahead_equity_r5_5(monkeypatch):
    """A winning trade's P&L must not fund an overlapping trade's sizing."""
    from app import backtest as bt

    # 20 flat bars, then a monster rally: trade A (ORB CE) wins big while
    # trade B (VWAP_RECLAIM CE) fires 3 bars later, overlapping A.
    closes, px = [24000.0] * 20, 24000.0
    for i in range(40):
        px += -3 if i % 3 == 2 else 4
        closes.append(px)
    bars = [{"t": f"2026-09-04T{9 + (15 + i) // 60:02d}:{(15 + i) % 60:02d}",
             "hm": (9 + (15 + i) // 60, (15 + i) % 60),
             "h": c + 2, "l": c - 2, "c": c, "v": 0.0} for i, c in enumerate(closes)]
    monkeypatch.setattr(bt, "fetch_history_sessions", lambda s, d=7: {"2026-09-04": bars})
    monkeypatch.setattr(bt, "fetch_vix_map", lambda d=12: {})
    out = bt.backtest_symbol("NIFTY", 7, capital=1_000_000, risk_pct=1.0)
    taken = [t for t in out["trades"] if t["lots"]]
    if len(taken) >= 2:
        # equity recorded at entry of trade 2 must equal starting capital —
        # trade 1 (still open) cannot have settled yet
        assert taken[1]["equity"] == 1_000_000
    # end equity = start + sum of all pnl (settlement is complete, nothing lost)
    assert out["summary"]["capital_end"] == round(
        1_000_000 + sum(t["pnl"] for t in taken), 2)
