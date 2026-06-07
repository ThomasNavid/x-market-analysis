"""Unit tests for enrichment helpers that do not call external APIs."""

import pytest

from xmarket.enrich.anthropic_json import LLMResponseError, extract_json_object
from xmarket.enrich.sentiment import _label, _score
from xmarket.enrich.tickers import _clean_ticker, _confidence


def test_extract_json_object_accepts_plain_json() -> None:
    assert extract_json_object('{"qualified": true, "reason": "Apple demand"}') == {
        "qualified": True,
        "reason": "Apple demand",
    }


def test_extract_json_object_accepts_fenced_json() -> None:
    assert extract_json_object('```json\n{"ticker": "AAPL"}\n```') == {"ticker": "AAPL"}


def test_extract_json_object_rejects_non_object() -> None:
    with pytest.raises(LLMResponseError):
        extract_json_object('["AAPL"]')


def test_clean_ticker_normalizes_cashtag() -> None:
    assert _clean_ticker("$aapl") == "AAPL"
    assert _clean_ticker("BRK.B") == "BRK.B"
    assert _clean_ticker("not a ticker") is None


def test_confidence_is_clamped() -> None:
    assert _confidence("1.5") == 1.0
    assert _confidence("-0.5") == 0.0
    assert _confidence("bad") == 0.0


def test_sentiment_score_and_label_fallbacks() -> None:
    assert _score("2") == 1.0
    assert _score("-2") == -1.0
    assert _label("bullish", 0.4) == "positive"
    assert _label("bearish", -0.4) == "negative"
    assert _label("", 0.0) == "neutral"
