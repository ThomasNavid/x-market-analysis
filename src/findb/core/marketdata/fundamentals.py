"""Daily fundamentals snapshots from Charles Schwab into PostgreSQL."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg.types.json import Jsonb
from schwab.client import Client

from findb.core.db.connection import connect
from findb.core.marketdata.schwab_client import create_schwab_client_from_token


@dataclass(frozen=True)
class FundamentalsSnapshot:
    """One daily fundamentals row for the fundamentals table."""

    ticker: str
    captured_date: date
    pe_ratio: float | None
    peg_ratio: float | None
    pb_ratio: float | None
    eps: float | None
    div_amount: Decimal | None
    div_yield: float | None
    market_cap: Decimal | None
    shares_outstanding: Decimal | None
    beta: float | None
    high_52: Decimal | None
    low_52: Decimal | None
    raw: dict[str, Any]


def _float_or_none(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(payload: dict[str, Any], key: str) -> Decimal | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def snapshot_from_payload(
    ticker: str,
    captured_date: date,
    fundamental: dict[str, Any],
) -> FundamentalsSnapshot:
    """Convert one Schwab `fundamental` JSON object into our fundamentals table shape."""
    return FundamentalsSnapshot(
        ticker=ticker.upper(),
        captured_date=captured_date,
        pe_ratio=_float_or_none(fundamental, "peRatio"),
        peg_ratio=_float_or_none(fundamental, "pegRatio"),
        pb_ratio=_float_or_none(fundamental, "pbRatio"),
        eps=_float_or_none(fundamental, "eps"),
        div_amount=_decimal_or_none(fundamental, "dividendAmount"),
        div_yield=_float_or_none(fundamental, "dividendYield"),
        market_cap=_decimal_or_none(fundamental, "marketCap"),
        shares_outstanding=_decimal_or_none(fundamental, "sharesOutstanding"),
        beta=_float_or_none(fundamental, "beta"),
        high_52=_decimal_or_none(fundamental, "high52"),
        low_52=_decimal_or_none(fundamental, "low52"),
        raw=fundamental,
    )


def fetch_fundamentals(client: Any, tickers: list[str]) -> list[FundamentalsSnapshot]:
    """Fetch instrument fundamentals from Schwab for the supplied tickers."""
    response = client.get_instruments(
        [ticker.upper() for ticker in tickers],
        Client.Instrument.Projection.FUNDAMENTAL,
    )
    response.raise_for_status()

    payload = response.json()
    captured_date = datetime.now(UTC).date()

    snapshots: list[FundamentalsSnapshot] = []
    for instrument in payload.get("instruments", []):
        symbol = instrument.get("symbol")
        fundamental = instrument.get("fundamental")
        if not symbol or not isinstance(fundamental, dict):
            continue
        snapshots.append(snapshot_from_payload(symbol, captured_date, fundamental))

    return snapshots


def upsert_fundamentals(snapshots: list[FundamentalsSnapshot]) -> int:
    """Insert or update fundamentals rows by primary key: (ticker, captured_date)."""
    if not snapshots:
        return 0

    sql = """
        INSERT INTO fundamentals (
            ticker, captured_date, pe_ratio, peg_ratio, pb_ratio, eps,
            div_amount, div_yield, market_cap, shares_outstanding, beta,
            high_52, low_52, raw
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (ticker, captured_date)
        DO UPDATE SET
            pe_ratio = EXCLUDED.pe_ratio,
            peg_ratio = EXCLUDED.peg_ratio,
            pb_ratio = EXCLUDED.pb_ratio,
            eps = EXCLUDED.eps,
            div_amount = EXCLUDED.div_amount,
            div_yield = EXCLUDED.div_yield,
            market_cap = EXCLUDED.market_cap,
            shares_outstanding = EXCLUDED.shares_outstanding,
            beta = EXCLUDED.beta,
            high_52 = EXCLUDED.high_52,
            low_52 = EXCLUDED.low_52,
            raw = EXCLUDED.raw,
            fetched_at = now()
    """

    rows = [
        (
            s.ticker,
            s.captured_date,
            s.pe_ratio,
            s.peg_ratio,
            s.pb_ratio,
            s.eps,
            s.div_amount,
            s.div_yield,
            s.market_cap,
            s.shares_outstanding,
            s.beta,
            s.high_52,
            s.low_52,
            Jsonb(s.raw),
        )
        for s in snapshots
    ]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()

    return len(snapshots)


def ingest_fundamentals(tickers: list[str]) -> int:
    """Fetch and persist a daily fundamentals snapshot for the supplied tickers."""
    if not tickers:
        raise ValueError("tickers must not be empty")

    client = create_schwab_client_from_token()
    snapshots = fetch_fundamentals(client, tickers)
    return upsert_fundamentals(snapshots)
