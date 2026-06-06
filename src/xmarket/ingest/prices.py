"""Daily OHLCV price ingestion from Charles Schwab into PostgreSQL."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from schwab import auth

from xmarket.config import settings
from xmarket.db.connection import connect


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


def validate_schwab_config() -> None:
    """Fail early if required Schwab settings are missing."""
    missing = []
    if not settings.schwab_app_key:
        missing.append("SCHWAB_APP_KEY")
    if not settings.schwab_app_secret:
        missing.append("SCHWAB_APP_SECRET")

    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing Schwab config: {joined}. Add it to .env first.")


def create_schwab_client_from_login() -> Any:
    """Run Schwab OAuth if needed and return an authenticated client."""
    validate_schwab_config()
    return auth.easy_client(
        api_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
        callback_url=settings.schwab_callback_url,
        token_path=settings.schwab_token_path,
    )


def create_schwab_client_from_token() -> Any:
    """Load an authenticated Schwab client from the cached token file."""
    validate_schwab_config()
    token_path = Path(settings.schwab_token_path)
    if not token_path.exists():
        raise RuntimeError(
            f"Schwab token file not found at {token_path}. Run `uv run xmarket schwab-login` first."
        )

    return auth.client_from_token_file(
        token_path=str(token_path),
        api_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
    )


def _date_to_start_datetime(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_to_end_datetime(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=UTC)


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
