"""F3/F6 ticker normalization, benchmark map, catalog."""
import pytest

from app.tickers import (
    detect_asset_type,
    filter_analysts_for_asset_type,
    normalize_ticker,
    resolve_benchmark,
    validate_trade_date,
)


def test_normalize_crypto_forms():
    for raw in ("BTCUSD", "btc-usd", "BTC-USDT", "btcusdc"):
        assert normalize_ticker(raw) == "BTC-USD"
    assert detect_asset_type("BTC-USD") == "crypto"


def test_normalize_stocks_and_suffixes():
    assert normalize_ticker("aapl") == "AAPL"
    assert normalize_ticker("reliance.ns") == "RELIANCE.NS"
    assert normalize_ticker("0700.hk") == "0700.HK"


@pytest.mark.parametrize("bad", ["", "../etc", "AAPL;rm", "A" * 40, "aa pl"])
def test_invalid_tickers_rejected(bad):
    with pytest.raises(ValueError):
        normalize_ticker(bad)


def test_benchmark_map_engine_parity():
    assert resolve_benchmark("AAPL") == "SPY"
    assert resolve_benchmark("RELIANCE.NS") == "^NSEI"
    assert resolve_benchmark("7203.T") == "^N225"
    assert resolve_benchmark("0700.HK") == "^HSI"
    assert resolve_benchmark("600519.SS") == "000001.SS"
    assert resolve_benchmark("AZN.L", override="^GSPC") == "^GSPC"


def test_crypto_filters_fundamentals():
    assert filter_analysts_for_asset_type(["market", "fundamentals"], "crypto") == ["market"]
    assert filter_analysts_for_asset_type(["market", "fundamentals"], "stock") == ["market", "fundamentals"]


def test_trade_date_validation():
    assert validate_trade_date("2026-09-01") == "2026-09-01"
    with pytest.raises(ValueError):
        validate_trade_date("2099-01-01")  # future
    with pytest.raises(ValueError):
        validate_trade_date("not-a-date")


def test_catalog_covers_all_engine_providers():
    from app.catalog import MODEL_OPTIONS, PROVIDERS

    ids = {p["id"] for p in PROVIDERS}
    # every UI provider has model options and vice versa
    for pid in ids:
        assert pid in MODEL_OPTIONS or pid == "openrouter", pid
    for pid in MODEL_OPTIONS:
        assert pid in ids, pid
    assert len(ids) >= 17  # engine parity: 17+ providers


def test_rating_scale_parity():
    from app.catalog import RATING_REVIEW, RATINGS

    assert RATINGS == ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    assert RATING_REVIEW == "REVIEW"
