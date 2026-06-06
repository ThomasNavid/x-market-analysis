"""Smoke tests for configuration parsing."""

from xmarket.config import Settings


def test_watchlist_parses_to_clean_list() -> None:
    s = Settings(watchlist=" aapl, tsla ,, nvda ")
    assert s.watchlist_tickers == ["AAPL", "TSLA", "NVDA"]


def test_api_keys_parse_to_set() -> None:
    s = Settings(api_keys="key1, key2 ,key1")
    assert s.api_key_set == {"key1", "key2"}
