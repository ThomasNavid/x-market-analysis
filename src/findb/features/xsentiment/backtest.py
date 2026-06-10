"""Signal backtesting over enriched posts and daily OHLCV prices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from statistics import mean, median, stdev
from typing import Any

from psycopg.types.json import Jsonb

from findb.config import settings
from findb.core.db.connection import connect
from findb.features.xsentiment.signals import BuiltInSignal, get_builtin_signal


@dataclass(frozen=True)
class SignalCandidate:
    """One enriched post/ticker row that matches a signal definition."""

    post_id: str
    ticker: str
    created_at: datetime
    sentiment_score: float
    ticker_confidence: float


@dataclass(frozen=True)
class PriceClose:
    """One stored daily close."""

    date: date
    close: Decimal


@dataclass(frozen=True)
class BacktestSample:
    """One deduped trade sample with raw and directional return."""

    post_id: str
    ticker: str
    post_created_at: datetime
    entry_date: date
    exit_date: date
    entry_close: Decimal
    exit_close: Decimal
    sentiment_score: float
    ticker_confidence: float
    raw_return: float
    directional_return: float


@dataclass(frozen=True)
class BacktestResult:
    """Complete result from one backtest run."""

    signal: BuiltInSignal
    horizon: int
    min_samples: int
    run_id: int
    samples: list[BacktestSample]
    matched_candidates: int
    duplicate_candidates: int
    missing_price_candidates: int
    metrics: dict[str, Any]


def _return(entry_close: Decimal, exit_close: Decimal) -> float:
    return float((exit_close - entry_close) / entry_close)


def _round_or_none(value: float | None, places: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, places)


def aggregate_samples(
    samples: list[BacktestSample],
    *,
    matched_candidates: int,
    duplicate_candidates: int,
    missing_price_candidates: int,
    min_samples: int,
) -> dict[str, Any]:
    """Compute aggregate metrics for persisted and CLI results."""
    raw_returns = [sample.raw_return for sample in samples]
    directional_returns = [sample.directional_return for sample in samples]
    sample_count = len(samples)

    volatility = stdev(directional_returns) if sample_count >= 2 else 0.0
    avg_directional = mean(directional_returns) if directional_returns else None
    sharpe = (
        avg_directional / volatility if avg_directional is not None and volatility > 0 else None
    )

    return {
        "sample_count": sample_count,
        "matched_candidates": matched_candidates,
        "duplicate_candidates": duplicate_candidates,
        "missing_price_candidates": missing_price_candidates,
        "avg_raw_return": _round_or_none(mean(raw_returns) if raw_returns else None),
        "median_raw_return": _round_or_none(median(raw_returns) if raw_returns else None),
        "avg_directional_return": _round_or_none(avg_directional),
        "median_directional_return": _round_or_none(
            median(directional_returns) if directional_returns else None
        ),
        "win_rate": _round_or_none(
            (sum(1 for value in directional_returns if value > 0) / len(directional_returns))
            if directional_returns
            else None
        ),
        "volatility": _round_or_none(volatility),
        "simple_sharpe": _round_or_none(sharpe),
        "best_directional_return": _round_or_none(
            max(directional_returns) if directional_returns else None
        ),
        "worst_directional_return": _round_or_none(
            min(directional_returns) if directional_returns else None
        ),
        "tiny_sample": sample_count < min_samples,
    }


def build_sample(
    candidate: SignalCandidate,
    prices: list[PriceClose],
    *,
    horizon: int,
    direction: int,
) -> BacktestSample | None:
    """Build one sample from the first `horizon + 1` closes after the post date."""
    if len(prices) <= horizon:
        return None

    entry = prices[0]
    exit_ = prices[horizon]
    raw_return = _return(entry.close, exit_.close)
    return BacktestSample(
        post_id=candidate.post_id,
        ticker=candidate.ticker,
        post_created_at=candidate.created_at,
        entry_date=entry.date,
        exit_date=exit_.date,
        entry_close=entry.close,
        exit_close=exit_.close,
        sentiment_score=candidate.sentiment_score,
        ticker_confidence=candidate.ticker_confidence,
        raw_return=raw_return,
        directional_return=raw_return * direction,
    )


def fetch_signal_candidates(signal: BuiltInSignal) -> list[SignalCandidate]:
    """Fetch enriched post/ticker rows matching a built-in signal."""
    sentiment_predicates = []
    params: list[Any] = [
        settings.sentiment_model,
        settings.sentiment_prompt_version,
        signal.ticker_confidence_min,
    ]
    if signal.sentiment_min is not None:
        sentiment_predicates.append("s.score >= %s")
        params.append(signal.sentiment_min)
    if signal.sentiment_max is not None:
        sentiment_predicates.append("s.score <= %s")
        params.append(signal.sentiment_max)

    sentiment_sql = " AND ".join(sentiment_predicates) or "true"
    sql = f"""
        SELECT p.id, pt.ticker, p.created_at, s.score, pt.confidence
        FROM posts p
        JOIN post_tickers pt
            ON pt.post_id = p.id
        JOIN sentiments s
            ON s.post_id = pt.post_id
            AND s.ticker = pt.ticker
        WHERE s.model = %s
            AND s.prompt_version = %s
            AND pt.confidence >= %s
            AND {sentiment_sql}
        ORDER BY p.created_at ASC, p.id ASC, pt.ticker ASC
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [
                SignalCandidate(
                    post_id=row[0],
                    ticker=row[1],
                    created_at=row[2],
                    sentiment_score=float(row[3]),
                    ticker_confidence=float(row[4]),
                )
                for row in cur.fetchall()
            ]


def fetch_closes_after_post(
    *,
    ticker: str,
    post_date: date,
    needed_rows: int,
) -> list[PriceClose]:
    """Fetch the first daily closes strictly after the post date."""
    sql = """
        SELECT date, close
        FROM prices
        WHERE ticker = %s
            AND date > %s
        ORDER BY date ASC
        LIMIT %s
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ticker, post_date, needed_rows))
            return [PriceClose(date=row[0], close=row[1]) for row in cur.fetchall()]


def build_deduped_samples(
    candidates: list[SignalCandidate],
    *,
    horizon: int,
    direction: int,
) -> tuple[list[BacktestSample], int, int]:
    """Create samples and keep only the earliest post per ticker/entry date."""
    samples: list[BacktestSample] = []
    seen: set[tuple[str, date]] = set()
    duplicate_candidates = 0
    missing_price_candidates = 0

    for candidate in candidates:
        prices = fetch_closes_after_post(
            ticker=candidate.ticker,
            post_date=candidate.created_at.date(),
            needed_rows=horizon + 1,
        )
        sample = build_sample(candidate, prices, horizon=horizon, direction=direction)
        if sample is None:
            missing_price_candidates += 1
            continue

        dedupe_key = (sample.ticker, sample.entry_date)
        if dedupe_key in seen:
            duplicate_candidates += 1
            continue

        seen.add(dedupe_key)
        samples.append(sample)

    return samples, duplicate_candidates, missing_price_candidates


def upsert_signal(signal: BuiltInSignal) -> int:
    """Ensure a built-in signal row exists and return its database id."""
    sql = """
        INSERT INTO signals (name, description, conditions)
        VALUES (%s, %s, %s)
        ON CONFLICT (name)
        DO UPDATE SET
            description = EXCLUDED.description,
            conditions = EXCLUDED.conditions
        RETURNING id
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (signal.name, signal.description, Jsonb(signal.conditions)))
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise RuntimeError(f"Could not upsert signal {signal.name}.")
    return int(row[0])


def insert_backtest_run(
    *,
    signal_id: int,
    params: dict[str, Any],
    results: dict[str, Any],
) -> int:
    """Persist one backtest run and return its id."""
    sql = """
        INSERT INTO backtest_runs (signal_id, params, results)
        VALUES (%s, %s, %s)
        RETURNING id
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (signal_id, Jsonb(params), Jsonb(results)))
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise RuntimeError("Could not insert backtest run.")
    return int(row[0])


def run_backtest(
    *,
    signal_name: str,
    horizon: int,
    min_samples: int,
) -> BacktestResult:
    """Run and persist a v1 built-in signal backtest."""
    if horizon < 1:
        raise ValueError("horizon must be at least 1")
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1")

    signal = get_builtin_signal(signal_name)
    candidates = fetch_signal_candidates(signal)
    samples, duplicate_candidates, missing_price_candidates = build_deduped_samples(
        candidates,
        horizon=horizon,
        direction=signal.direction,
    )
    metrics = aggregate_samples(
        samples,
        matched_candidates=len(candidates),
        duplicate_candidates=duplicate_candidates,
        missing_price_candidates=missing_price_candidates,
        min_samples=min_samples,
    )
    params = {
        "signal_name": signal.name,
        "horizon": horizon,
        "min_samples": min_samples,
        "entry_price": "next_available_close_after_post_date",
        "exit_price": "close_horizon_trading_days_after_entry",
        "dedupe": "earliest_post_per_ticker_entry_date",
        "sentiment_model": settings.sentiment_model,
        "sentiment_prompt_version": settings.sentiment_prompt_version,
    }
    results = {
        **metrics,
        "samples": [
            {
                "post_id": sample.post_id,
                "ticker": sample.ticker,
                "post_created_at": sample.post_created_at.isoformat(),
                "entry_date": sample.entry_date.isoformat(),
                "exit_date": sample.exit_date.isoformat(),
                "entry_close": str(sample.entry_close),
                "exit_close": str(sample.exit_close),
                "sentiment_score": sample.sentiment_score,
                "ticker_confidence": sample.ticker_confidence,
                "raw_return": round(sample.raw_return, 6),
                "directional_return": round(sample.directional_return, 6),
            }
            for sample in samples
        ],
    }

    signal_id = upsert_signal(signal)
    run_id = insert_backtest_run(signal_id=signal_id, params=params, results=results)
    return BacktestResult(
        signal=signal,
        horizon=horizon,
        min_samples=min_samples,
        run_id=run_id,
        samples=samples,
        matched_candidates=len(candidates),
        duplicate_candidates=duplicate_candidates,
        missing_price_candidates=missing_price_candidates,
        metrics=metrics,
    )
