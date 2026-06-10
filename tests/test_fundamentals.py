"""DB-free tests for Schwab fundamentals payload parsing."""

from datetime import date
from decimal import Decimal

from findb.core.marketdata.fundamentals import snapshot_from_payload

CAPTURED = date(2026, 6, 10)

# Trimmed from a real Schwab GET /instruments?projection=fundamental response.
AAPL_FUNDAMENTAL = {
    "symbol": "AAPL",
    "peRatio": 35.22224,
    "pegRatio": 1.81512,
    "pbRatio": 34.26882,
    "prRatio": 8.1483,
    "eps": 7.46,
    "epsTTM": 8.24905,
    "dividendAmount": 1.08,
    "dividendYield": 0.3514,
    "marketCap": 4514011993040.0,
    "sharesOutstanding": 14687356000.0,
    "beta": 1.08598,
    "high52": 317.4,
    "low52": 195.07,
}


def test_snapshot_parses_headline_fields() -> None:
    snapshot = snapshot_from_payload("aapl", CAPTURED, AAPL_FUNDAMENTAL)

    assert snapshot.ticker == "AAPL"
    assert snapshot.captured_date == CAPTURED
    assert snapshot.pe_ratio == 35.22224
    assert snapshot.peg_ratio == 1.81512
    assert snapshot.pb_ratio == 34.26882
    assert snapshot.eps == 7.46
    assert snapshot.div_amount == Decimal("1.08")
    assert snapshot.div_yield == 0.3514
    assert snapshot.market_cap == Decimal("4514011993040")
    assert snapshot.shares_outstanding == Decimal("14687356000")
    assert snapshot.beta == 1.08598
    assert snapshot.high_52 == Decimal("317.4")
    assert snapshot.low_52 == Decimal("195.07")
    assert snapshot.raw == AAPL_FUNDAMENTAL


def test_snapshot_tolerates_sparse_payload() -> None:
    sparse = {"symbol": "SPY", "high52": 612.5}

    snapshot = snapshot_from_payload("SPY", CAPTURED, sparse)

    assert snapshot.pe_ratio is None
    assert snapshot.peg_ratio is None
    assert snapshot.pb_ratio is None
    assert snapshot.eps is None
    assert snapshot.div_amount is None
    assert snapshot.div_yield is None
    assert snapshot.market_cap is None
    assert snapshot.shares_outstanding is None
    assert snapshot.beta is None
    assert snapshot.high_52 == Decimal("612.5")
    assert snapshot.low_52 is None
    assert snapshot.raw == sparse


def test_snapshot_treats_junk_values_as_none() -> None:
    junk = {
        "peRatio": "N/A",
        "pegRatio": None,
        "eps": "",
        "dividendAmount": "not-a-number",
        "marketCap": "--",
        "beta": "NM",
    }

    snapshot = snapshot_from_payload("XYZ", CAPTURED, junk)

    assert snapshot.pe_ratio is None
    assert snapshot.peg_ratio is None
    assert snapshot.eps is None
    assert snapshot.div_amount is None
    assert snapshot.market_cap is None
    assert snapshot.beta is None
