# Architecture

`x-market-analysis` is a CLI-first research pipeline. It ingests X posts, enriches them with LLM
qualification/ticker/sentiment data, joins them to Schwab daily OHLCV prices, and backtests built-in
signals.

## Data Flow

```text
X following/search posts
  -> authors/posts
  -> post_qualifications
  -> post_ticker_extractions/post_tickers
  -> sentiments
  -> prices
  -> signals/backtest_runs
```

The main orchestration command is:

```bash
uv run xmarket pipeline
```

Focused commands remain available for reruns and debugging:

```bash
uv run xmarket ingest-posts --source following --max-posts 100
uv run xmarket enrich
uv run xmarket report-qualified --limit 25
uv run xmarket backtest --signal positive_high --horizon 5
```

## Storage

PostgreSQL is the source of truth. The schema is managed with raw SQL migrations in `migrations/` and
applied with `uv run xmarket migrate`.

Important cache keys:

- Qualification: `post_id + QUALIFY_PROMPT_VERSION`
- Ticker extraction: `post_id + TICKER_PROMPT_VERSION`
- Sentiment: `post_id + ticker + SENTIMENT_MODEL + SENTIMENT_PROMPT_VERSION`
- Prices: `ticker + date`

These keys are what make repeated pipeline runs cheap: existing enrichment and price rows are skipped.

## External Services

- X API v2 provides posts from the authenticated user's following feed or optional public search.
- Anthropic Claude provides qualification, ticker extraction, and sentiment.
- Schwab provides daily OHLCV prices.

Secrets are loaded from `.env` through `pydantic-settings`. `.env` and OAuth token files are git-ignored.

## Quality Gates

Local and CI checks are:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```
