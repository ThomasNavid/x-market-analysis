-- Daily snapshots of Schwab instrument fundamentals
-- (GET /instruments?projection=fundamental via schwab-py get_instruments).
--
-- market_cap is stored in dollars, exactly as Schwab returns it
-- (verified live 2026-06-10: AAPL marketCap = 4514011993040.0 ≈ $4.5T).

CREATE TABLE fundamentals (
    ticker text NOT NULL,
    captured_date date NOT NULL,
    pe_ratio double precision,
    peg_ratio double precision,
    pb_ratio double precision,
    eps double precision,
    div_amount numeric(18, 6),
    div_yield double precision,
    market_cap numeric(24, 2),
    shares_outstanding numeric(24, 0),
    beta double precision,
    high_52 numeric(18, 6),
    low_52 numeric(18, 6),
    raw jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, captured_date)
);

CREATE INDEX idx_fundamentals_captured_date ON fundamentals (captured_date);
