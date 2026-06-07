# x-market-analysis

> Find winning trading strategies by correlating **X (Twitter) chatter about stocks** with their
> subsequent **price performance**.

The core research question:

> *"When a stock is mentioned on X under conditions **C** (high positive sentiment, a mention-volume
> spike, certain account types…), does it outperform over the next **N** days?"*

The pipeline ingests X posts, extracts stock tickers, scores sentiment with a cheap LLM
(Claude Haiku), joins everything against daily price data, and **backtests signal definitions** to rank
which mention-conditions historically preceded outperformance.

> ⚠️ **Disclaimer:** This is a research/educational tool, **not financial advice**. Backtested results
> do not guarantee future performance. Use of the X API must comply with X's Terms of Service.

---

## How it works

```
   X API ─────► ingest posts ─┐
 (usage-based)                ├─► enrich (tickers + Haiku sentiment) ─► PostgreSQL
  Schwab API ─► ingest prices ┘                                              │
                                                                            ▼
                                                          backtest / strategy engine
                                                                            │
                                                                            ▼
                                                       secured FastAPI  (read results + run jobs)
```

Scheduled jobs (APScheduler) keep data fresh; a secured FastAPI exposes results and lets you trigger jobs.

## Tech stack

- **Python 3.12**, managed with [`uv`](https://github.com/astral-sh/uv)
- **PostgreSQL 16** with **raw SQL migrations** + `psycopg` (portable — local Docker, or Supabase/Neon/RDS)
- **httpx** → X API v2 ingestion (usage-aware, rate-limited)
- **Charles Schwab Trader API** (via [`schwab-py`](https://github.com/alexgolec/schwab-py)) for daily OHLCV — real brokerage data, swappable behind a `PriceProvider` interface
- **Claude Haiku** (`anthropic` SDK) for cheap, cached, structured sentiment scoring
- **FastAPI** + Uvicorn, API-key auth, `slowapi` rate limiting
- **pytest**, **ruff**, **mypy**, GitHub Actions CI

## Quickstart

> Status: 🚧 early development — scaffolding in progress. See [`documentation/plan.md`](documentation/plan.md).

```bash
# 1. Clone + install
git clone https://github.com/<you>/x-market-analysis.git
cd x-market-analysis
uv sync

# 2. Configure secrets (never commit .env)
cp .env.example .env
#   set DATABASE_URL, SCHWAB_APP_KEY/SECRET, X_BEARER_TOKEN, ANTHROPIC_API_KEY, watchlist, etc.

# 3. Start a local Postgres + run raw SQL migrations
docker compose up -d
uv run xmarket migrate

# 4. One-time Schwab OAuth login (caches a refreshable token)
uv run xmarket schwab-login

# 5. Ingest data
uv run xmarket ingest-prices --days 30  # daily OHLCV for your watchlist (Schwab)
uv run xmarket x-login                  # one-time X OAuth login for your following feed
uv run xmarket ingest-posts --source following --max-posts 100
uv run xmarket enrich             # ticker extraction + Haiku sentiment (cached)

# 6. Backtest a signal
uv run xmarket backtest --signal positive_high --horizon 5

# 7. Serve the API
uv run xmarket serve              # docs at http://localhost:8000/docs
```

## Configuration

All config is loaded from `.env` via `pydantic-settings`. Copy `.env.example` and fill in:

| Variable            | Description                                  |
|---------------------|----------------------------------------------|
| `DATABASE_URL`      | Postgres connection string                   |
| `SCHWAB_APP_KEY`    | Schwab developer app key (from developer.schwab.com) |
| `SCHWAB_APP_SECRET` | Schwab developer app secret                  |
| `SCHWAB_CALLBACK_URL` | OAuth callback URL — must match your Schwab app exactly |
| `X_BEARER_TOKEN`    | X API v2 bearer token for optional public search |
| `X_CLIENT_ID`       | X OAuth 2.0 client ID for following-feed auth |
| `X_CLIENT_SECRET`   | X OAuth 2.0 client secret, if your app uses one |
| `X_REDIRECT_URI`    | X OAuth redirect URI; must match developer portal |
| `X_USER_TOKEN_PATH` | Local cached X user token path               |
| `ANTHROPIC_API_KEY` | For Claude Haiku sentiment scoring           |
| `WATCHLIST`         | Comma-separated tickers to track             |
| `API_KEYS`          | Comma-separated API keys for the FastAPI auth|

Secrets live only in `.env` (git-ignored). Only `.env.example` is committed.

## Project layout

```
src/xmarket/      ingest/ · enrich/ · analysis/ · api/ · jobs/ · db/
documentation/    plan.md · architecture.md · strategy-methodology.md
migrations/       raw SQL database migrations
tests/            unit + API tests
```

## Documentation

- [Commands cheat sheet](documentation/commands.md) — every command you'll need, explained
- [Build plan](documentation/plan.md) — step-by-step roadmap and decisions
- Architecture notes & strategy methodology (added as the project matures)

## Roadmap

- [ ] Step 0 — Project scaffold & hygiene
- [ ] Step 1 — raw PostgreSQL schema & migrations
- [ ] Step 2 — Price ingestion (Charles Schwab Trader API)
- [ ] Step 3 — X ingestion
- [ ] Step 4 — Ticker extraction
- [ ] Step 5 — LLM sentiment (Claude Haiku)
- [ ] Step 6 — Signal & backtest engine
- [ ] Step 7 — Secured FastAPI
- [ ] Step 8 — Scheduling
- [ ] Step 9 — Tests, CI, docs polish

## License

TBD (consider MIT for a public portfolio project).
