"""Unit tests for Step 6 backtest behavior."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from xmarket.analysis.backtest import (
    BacktestSample,
    PriceClose,
    SignalCandidate,
    aggregate_samples,
    build_deduped_samples,
    build_sample,
)
from xmarket.analysis.signals import get_builtin_signal


def candidate(
    post_id: str,
    *,
    ticker: str = "AAPL",
    created_at: datetime | None = None,
    score: float = 0.8,
) -> SignalCandidate:
    return SignalCandidate(
        post_id=post_id,
        ticker=ticker,
        created_at=created_at or datetime(2026, 1, 2, 12, tzinfo=UTC),
        sentiment_score=score,
        ticker_confidence=0.9,
    )


def sample(value: float) -> BacktestSample:
    return BacktestSample(
        post_id=f"post-{value}",
        ticker="AAPL",
        post_created_at=datetime(2026, 1, 2, 12, tzinfo=UTC),
        entry_date=date(2026, 1, 5),
        exit_date=date(2026, 1, 6),
        entry_close=Decimal("100"),
        exit_close=Decimal(str(100 + value * 100)),
        sentiment_score=0.8,
        ticker_confidence=0.9,
        raw_return=value,
        directional_return=value,
    )


def test_get_builtin_signal_returns_positive_and_negative() -> None:
    positive = get_builtin_signal("positive_high")
    negative = get_builtin_signal("negative_high")

    assert positive.sentiment_min == 0.6
    assert positive.direction == 1
    assert negative.sentiment_max == -0.6
    assert negative.direction == -1


def test_get_builtin_signal_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown signal"):
        get_builtin_signal("missing")


def test_build_sample_uses_entry_and_horizon_exit_close() -> None:
    prices = [
        PriceClose(date(2026, 1, 5), Decimal("100")),
        PriceClose(date(2026, 1, 6), Decimal("110")),
        PriceClose(date(2026, 1, 7), Decimal("120")),
    ]

    result = build_sample(candidate("1"), prices, horizon=2, direction=1)

    assert result is not None
    assert result.entry_date == date(2026, 1, 5)
    assert result.exit_date == date(2026, 1, 7)
    assert result.raw_return == 0.2
    assert result.directional_return == 0.2


def test_build_sample_flips_direction_for_negative_signal() -> None:
    prices = [
        PriceClose(date(2026, 1, 5), Decimal("100")),
        PriceClose(date(2026, 1, 6), Decimal("90")),
    ]

    result = build_sample(candidate("1", score=-0.8), prices, horizon=1, direction=-1)

    assert result is not None
    assert result.raw_return == -0.1
    assert result.directional_return == 0.1


def test_build_sample_returns_none_without_enough_price_rows() -> None:
    prices = [PriceClose(date(2026, 1, 5), Decimal("100"))]

    assert build_sample(candidate("1"), prices, horizon=1, direction=1) is None


def test_build_deduped_samples_keeps_earliest_per_ticker_entry_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prices = [
        PriceClose(date(2026, 1, 5), Decimal("100")),
        PriceClose(date(2026, 1, 6), Decimal("105")),
    ]

    def fake_fetch_closes_after_post(
        *,
        ticker: str,
        post_date: date,
        needed_rows: int,
    ) -> list[PriceClose]:
        return prices

    monkeypatch.setattr(
        "xmarket.analysis.backtest.fetch_closes_after_post",
        fake_fetch_closes_after_post,
    )

    samples, duplicates, missing = build_deduped_samples(
        [
            candidate("earliest", created_at=datetime(2026, 1, 2, 9, tzinfo=UTC)),
            candidate("later", created_at=datetime(2026, 1, 2, 10, tzinfo=UTC)),
        ],
        horizon=1,
        direction=1,
    )

    assert [item.post_id for item in samples] == ["earliest"]
    assert duplicates == 1
    assert missing == 0


def test_aggregate_samples_calculates_metrics() -> None:
    metrics = aggregate_samples(
        [sample(0.1), sample(-0.05), sample(0.2)],
        matched_candidates=4,
        duplicate_candidates=1,
        missing_price_candidates=0,
        min_samples=5,
    )

    assert metrics["sample_count"] == 3
    assert metrics["matched_candidates"] == 4
    assert metrics["duplicate_candidates"] == 1
    assert metrics["avg_directional_return"] == 0.083333
    assert metrics["median_directional_return"] == 0.1
    assert metrics["win_rate"] == 0.666667
    assert metrics["tiny_sample"] is True
