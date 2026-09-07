"""Scalp backtester — trade simulation math and outcome ordering."""


def _bars(closes, start=(9, 15)):
    h, m = start
    out = []
    for i, c in enumerate(closes):
        mm = m + i
        out.append({"t": f"T{i}", "hm": (h + mm // 60, mm % 60),
                    "h": c + 2, "l": c - 2, "c": c, "v": 0.0})
    return out


def test_simulate_tp_and_sl():
    from app.backtest import ASSUMED_DELTA, ATM_PREMIUM_PCT, _simulate_trade
    from app.scalp import SCALP_RR, SCALP_SL_PCT

    spot0 = 24000.0
    p0 = spot0 * ATM_PREMIUM_PCT / 100          # 67.2
    risk = p0 * SCALP_SL_PCT / 100              # 12.096
    tp_move = risk * SCALP_RR / ASSUMED_DELTA   # ≈36.3 spot points
    sl_move = risk / ASSUMED_DELTA              # ≈24.2 spot points
    # CE trade that rallies straight to TP
    closes = [spot0] + [spot0 + 10 * k for k in range(1, 8)]
    sim = _simulate_trade(_bars(closes), 0, "CE")
    assert sim["outcome"] == "TP" and sim["r"] == SCALP_RR
    # CE trade that dumps to SL
    closes = [spot0] + [spot0 - 10 * k for k in range(1, 8)]
    sim = _simulate_trade(_bars(closes), 0, "CE")
    assert sim["outcome"] == "SL" and sim["r"] == -1.0
    # PE mirror: dump = win
    sim = _simulate_trade(_bars(closes), 0, "PE")
    assert sim["outcome"] == "TP"
    # flat tape → time stop with small |r|
    closes = [spot0] * 25
    sim = _simulate_trade(_bars(closes), 0, "CE")
    assert sim["outcome"] == "TIME" and abs(sim["r"]) < 0.2
    # both levels inside one wild bar → conservative SL
    bars = _bars([spot0, spot0])
    bars[1]["h"] = spot0 + tp_move + 5
    bars[1]["l"] = spot0 - sl_move - 5
    sim = _simulate_trade(bars, 0, "CE")
    assert sim["outcome"] == "SL"


def test_backtest_symbol_walkforward(monkeypatch):
    from app import backtest as bt

    # synthetic session: 20 flat bars then a clean +4/-3 uptrend → ORB CE fires
    closes, px = [24000.0] * 20, 24000.0
    for i in range(60):
        px += -3 if i % 3 == 2 else 4
        closes.append(px)
    session = {"2026-09-04": _bars(closes)}
    monkeypatch.setattr(bt, "fetch_history_sessions", lambda s, d=7: session)
    out = bt.backtest_symbol("NIFTY", 7)
    assert out["summary"]["n"] >= 1
    assert all(t["rule"] in ("ORB", "VWAP_RECLAIM") for t in out["trades"])
    assert out["summary"]["tp"] + out["summary"]["sl"] + out["summary"]["time_exits"] == out["summary"]["n"]
    # theta cutoff respected: no trade at/after 14:30
    assert all(t["time"] < "14:30" for t in out["trades"])
