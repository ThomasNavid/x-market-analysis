"""Daily OHLCV price ingestion from Charles Schwab into PostgreSQL."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from findb.core.db.connection import connect
from findb.core.marketdata.schwab_client import create_schwab_client_from_token


@dataclass(frozen=True)
class PriceBar:
    """One daily OHLCV row for the prices table."""

    ticker: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal | None
    volume: int


@dataclass(frozen=True)
class PriceEnsureResult:
    """Summary of an idempotent price coverage check/fetch."""

    checked_tickers: int
    fetched_tickers: int
    upserted_rows: int


def _date_to_start_datetime(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_to_end_datetime(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=UTC)


def _next_weekday(value: date) -> date:
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def _previous_weekday(value: date) -> date:
    while value.weekday() >= 5:
        value -= timedelta(days=1)
    return value


def _decimal_from_candle(candle: dict[str, Any], key: str) -> Decimal:
    return Decimal(str(candle[key]))


def price_bar_from_schwab_candle(ticker: str, candle: dict[str, Any]) -> PriceBar:
    """Convert one Schwab candle JSON object into our prices table shape."""
    candle_date = datetime.fromtimestamp(candle["datetime"] / 1000, tz=UTC).date()

    return PriceBar(
        ticker=ticker.upper(),
        date=candle_date,
        open=_decimal_from_candle(candle, "open"),
        high=_decimal_from_candle(candle, "high"),
        low=_decimal_from_candle(candle, "low"),
        close=_decimal_from_candle(candle, "close"),
        adj_close=None,
        volume=int(candle["volume"]),
    )


def fetch_daily_price_bars(
    client: Any,
    ticker: str,
    *,
    start_date: date,
    end_date: date,
) -> list[PriceBar]:
    """Fetch daily Schwab candles for one ticker."""
    response = client.get_price_history_every_day(
        ticker.upper(),
        start_datetime=_date_to_start_datetime(start_date),
        end_datetime=_date_to_end_datetime(end_date),
        need_extended_hours_data=False,
        need_previous_close=False,
    )
    response.raise_for_status()

    payload = response.json()
    candles = payload.get("candles", [])
    return [price_bar_from_schwab_candle(ticker, candle) for candle in candles]


def upsert_price_bars(price_bars: list[PriceBar]) -> int:
    """Insert or update price rows by primary key: (ticker, date)."""
    if not price_bars:
        return 0

    sql = """
        INSERT INTO prices (
            ticker, date, open, high, low, close, adj_close, volume
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (ticker, date)
        DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            adj_close = EXCLUDED.adj_close,
            volume = EXCLUDED.volume
    """

    rows = [
        (
            bar.ticker,
            bar.date,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.adj_close,
            bar.volume,
        )
        for bar in price_bars
    ]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()

    return len(price_bars)


def ingest_prices(tickers: list[str], *, days: int) -> int:
    """Fetch and persist daily price history for the supplied tickers."""
    if days < 1:
        raise ValueError("days must be at least 1")

    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=days)
    client = create_schwab_client_from_token()

    total = 0
    for ticker in tickers:
        bars = fetch_daily_price_bars(
            client,
            ticker,
            start_date=start_date,
            end_date=end_date,
        )
        total += upsert_price_bars(bars)

    return total


def has_price_coverage(ticker: str, *, start_date: date, end_date: date) -> bool:
    """Return true when stored daily bars cover the requested ticker/date range."""
    sql = """
        SELECT min(date), max(date)
        FROM prices
        WHERE ticker = %s
            AND date >= %s
            AND date <= %s
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ticker.upper(), start_date, end_date))
            min_date, max_date = cur.fetchone() or (None, None)

    return min_date is not None and min_date <= start_date and max_date >= end_date


def ensure_price_bars_for_ticker_dates(
    ticker_dates: Iterable[tuple[str, datetime]],
    *,
    days: int,
    end_date: date | None = None,
) -> PriceEnsureResult:
    """Ensure Schwab daily bars exist around extracted ticker post timestamps.

    Each ticker is checked for coverage from its earliest processed post date to
    `days` calendar days after its latest processed post date, capped at today.
    Schwab is only called for tickers whose adjusted weekday range is not covered
    by existing rows in `prices`.
    """
    if days < 1:
        raise ValueError("days must be at least 1")

    today = end_date or datetime.now(UTC).date()
    ranges: dict[str, tuple[date, date]] = {}
    for ticker, timestamp in ticker_dates:
        normalized = ticker.strip().upper()
        if not normalized:
            continue
        post_date = timestamp.date()
        requested_start = post_date
        requested_end = min(today, post_date + timedelta(days=days))
        if normalized in ranges:
            old_start, old_end = ranges[normalized]
            ranges[normalized] = (min(old_start, requested_start), max(old_end, requested_end))
        else:
            ranges[normalized] = (requested_start, requested_end)

    missing: list[tuple[str, date, date]] = []
    for ticker, (requested_start, requested_end) in ranges.items():
        start = _next_weekday(requested_start)
        end = _previous_weekday(requested_end)
        if start > end:
            continue
        if not has_price_coverage(ticker, start_date=start, end_date=end):
            missing.append((ticker, start, end))

    if not missing:
        return PriceEnsureResult(
            checked_tickers=len(ranges),
            fetched_tickers=0,
            upserted_rows=0,
        )

    client = create_schwab_client_from_token()
    total = 0
    for ticker, start, end in missing:
        bars = fetch_daily_price_bars(
            client,
            ticker,
            start_date=start,
            end_date=end,
        )
        total += upsert_price_bars(bars)

    return PriceEnsureResult(
        checked_tickers=len(ranges),
        fetched_tickers=len(missing),
        upserted_rows=total,
    )
