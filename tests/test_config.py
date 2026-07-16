from app.config import load_config


def test_catalogue_has_free_proxy_universe_and_unique_symbols():
    config = load_config()
    symbols = [x["canonical_symbol"] for x in config.instruments]
    assert len(symbols) == len(set(symbols))
    required = {
        "USO_WTI_PROXY", "BNO_BRENT_PROXY", "GLD_GOLD_PROXY", "SLV_SILVER_PROXY", "BTC_USD_SPOT",
        "SPY_SP500_PROXY", "QQQ_NASDAQ100_PROXY", "DIA_DJIA_PROXY", "IWM_RUSSELL2000_PROXY",
        "VIXY_VIX_FUTURES_PROXY", "EWU_UK_EQUITY_PROXY", "EWG_GERMANY_EQUITY_PROXY",
        "FEZ_EUROSTOXX50_PROXY", "EWJ_JAPAN_EQUITY_PROXY", "EWH_HONGKONG_EQUITY_PROXY",
        "SHY_US2Y_RATE_PROXY", "IEI_US5Y_RATE_PROXY", "IEF_US10Y_RATE_PROXY", "TLT_US30Y_RATE_PROXY",
        "US2Y_YIELD", "US5Y_YIELD", "US10Y_YIELD", "US30Y_YIELD",
        "UK2Y_YIELD", "UK10Y_YIELD", "DE2Y_YIELD", "DE10Y_YIELD",
    }
    assert required.issubset(set(symbols))


def test_all_alpaca_instruments_are_explicit_proxies_with_partial_volume():
    config = load_config()
    alpaca = [x for x in config.enabled_instruments if x["provider"] == "alpaca"]
    assert len(alpaca) == 18
    for item in alpaca:
        assert item["instrument_type"] == "etf_proxy"
        assert item["proxy_status"] == "explicit_non_equivalent_substitution"
        assert item["volume_type"] == "genuine_single_venue_partial"
        assert item["alpaca_feed"] == "iex"


def test_rate_etf_proxies_are_not_labelled_as_yields():
    config = load_config()
    rate_proxies = [x for x in config.enabled_instruments if x["subcategory"] == "us_treasury_etf_proxy"]
    assert rate_proxies
    for item in rate_proxies:
        assert item["instrument_type"] == "etf_proxy"
        assert "yield" not in item["instrument_type"]
