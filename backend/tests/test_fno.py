"""F&O feature tests: aliases, max pain, chain analytics, run integration."""
import time


def test_index_aliases_and_asset_type():
    from app.tickers import detect_asset_type, filter_analysts_for_asset_type, normalize_ticker

    assert normalize_ticker("nifty") == "^NSEI"
    assert normalize_ticker("BANKNIFTY") == "^NSEBANK"
    assert normalize_ticker("SENSEX") == "^BSESN"
    assert detect_asset_type("^NSEI") == "index"
    assert filter_analysts_for_asset_type(["market", "news", "fundamentals"], "index") == ["market", "news"]


def test_fno_symbol_mapping():
    from app.fno import fno_symbol_for

    assert fno_symbol_for("^NSEI") == "NIFTY"
    assert fno_symbol_for("^NSEBANK") == "BANKNIFTY"
    assert fno_symbol_for("RELIANCE.NS") == "RELIANCE"
    assert fno_symbol_for("AAPL") is None
    assert fno_symbol_for("BTC-USD") is None


def test_max_pain_and_chain_analytics():
    from app.fno import analyze_chain, compute_max_pain

    # symmetric book pinned at 200
    strikes = [
        {"strike": 100, "ce_oi": 10, "pe_oi": 1000},
        {"strike": 200, "ce_oi": 500, "pe_oi": 500},
        {"strike": 300, "ce_oi": 1000, "pe_oi": 10},
    ]
    assert compute_max_pain(strikes) == 200

    records = {
        "underlyingValue": 205.0,
        "expiryDates": ["11-Sep-2026", "18-Sep-2026"],
        "data": [
            {"expiryDate": "11-Sep-2026", "strikePrice": 100,
             "CE": {"openInterest": 10, "impliedVolatility": 14.0},
             "PE": {"openInterest": 1000, "impliedVolatility": 15.0}},
            {"expiryDate": "11-Sep-2026", "strikePrice": 200,
             "CE": {"openInterest": 500, "impliedVolatility": 12.5},
             "PE": {"openInterest": 500, "impliedVolatility": 13.0}},
            {"expiryDate": "11-Sep-2026", "strikePrice": 300,
             "CE": {"openInterest": 1000, "impliedVolatility": 16.0},
             "PE": {"openInterest": 10, "impliedVolatility": 17.0}},
            # different expiry must be excluded
            {"expiryDate": "18-Sep-2026", "strikePrice": 999,
             "CE": {"openInterest": 99999}, "PE": {"openInterest": 99999}},
        ],
    }
    s = analyze_chain(records, "NIFTY")
    assert s["expiry"] == "11-Sep-2026"
    assert s["strike_count"] == 3
    assert s["pcr"] == round(1510 / 1510, 3)
    assert s["max_pain"] == 200
    assert s["resistance_strikes"][0]["strike"] == 300
    assert s["support_strikes"][0]["strike"] == 100
    assert s["atm_iv_ce"] == 12.5  # ATM at 200 (spot 205)

    from app.fno import snapshot_to_md

    md = snapshot_to_md(s)
    assert "Max Pain" in md and "Put/Call Ratio" in md and "not trading advice" in md


def test_index_run_with_fno_context(client, auth, monkeypatch):
    """A NIFTY demo run embeds the (mocked) live chain into its reports."""
    from app import fno

    async def fake_snapshot(symbol):
        return {
            "symbol": symbol, "spot": 25000.0, "expiry": "11-Sep-2026", "pcr": 1.31,
            "total_ce_oi": 100, "total_pe_oi": 131, "max_pain": 24900,
            "resistance_strikes": [{"strike": 25200, "ce_oi": 50}],
            "support_strikes": [{"strike": 24800, "pe_oi": 60}],
            "atm_iv_ce": 11.2, "atm_iv_pe": 11.8, "strike_count": 40, "india_vix": 13.5,
        }

    monkeypatch.setattr(fno, "get_fno_snapshot", fake_snapshot)

    run = client.post("/api/runs", headers=auth, json={
        "ticker": "NIFTY", "trade_date": "2026-09-01", "research_depth": 1, "mode": "demo"}).json()
    assert run["ticker"] == "^NSEI" and run["asset_type"] == "index"
    assert "fundamentals" not in run["config"]["analysts"]
    assert run["config"]["_fno_symbol"] == "NIFTY"

    for _ in range(120):
        r = client.get(f"/api/runs/{run['id']}", headers=auth).json()
        if r["status"] in ("done", "failed", "interrupted"):
            break
        time.sleep(1)
    assert r["status"] == "done"
    reports = {x["section"]: x["content_md"]
               for x in client.get(f"/api/runs/{run['id']}/reports", headers=auth).json()}
    assert "derivatives_report" in reports
    assert "Max Pain" in reports["derivatives_report"] and "24900" in reports["derivatives_report"]
    assert "India VIX" in reports["derivatives_report"]
    assert "Derivatives context (LIVE NSE data)" in reports["market_report"]


def test_ticker_preview_flags_fno(client, auth):
    p = client.get("/api/catalog/ticker/NIFTY", headers=auth).json()
    assert p["symbol"] == "^NSEI" and p["fno_symbol"] == "NIFTY" and p["asset_type"] == "index"
    p2 = client.get("/api/catalog/ticker/AAPL", headers=auth).json()
    assert p2["fno_symbol"] is None
