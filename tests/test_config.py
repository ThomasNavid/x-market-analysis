"""Smoke tests for configuration parsing."""

from findb.config import Settings


def test_watchlist_parses_to_clean_list() -> None:
    s = Settings(watchlist=" aapl, tsla ,, nvda ")
    assert s.watchlist_tickers == ["AAPL", "TSLA", "NVDA"]


def test_enrichment_models_are_configurable() -> None:
    s = Settings(
        qualify_model="claude-haiku-4-5",
        sentiment_model="claude-haiku-4-5",
        qualify_prompt_version="qualify-test",
        ticker_prompt_version="ticker-test",
        sentiment_prompt_version="sentiment-test",
    )
    assert s.qualify_model == "claude-haiku-4-5"
    assert s.sentiment_model == "claude-haiku-4-5"
    assert s.qualify_prompt_version == "qualify-test"
    assert s.ticker_prompt_version == "ticker-test"
    assert s.sentiment_prompt_version == "sentiment-test"
