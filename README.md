# x-market-analysis / findb

> A personal financial data platform that builds a growing private database of market data and
> derived signals. The first feature correlates **X (Twitter) chatter about stocks** with their
> subsequent **price performance**.

The core research question for the X-sentiment feature:

> *"When a stock is mentioned on X under conditions **C** (high positive sentiment, a mention-volume
> spike, certain account types…), does it outperform over the next **N** days?"*

The platform ingests X posts, qualifies stock-specific trade intelligence, extracts tickers,
scores sentiment with a cheap LLM (Claude Haiku), joins everything against daily Schwab price data
and fundamentals, and **backtests signal definitions** to rank which mention-conditions historically
preceded outperformance.

> ⚠️ **Disclaimer:** This is a research/educational tool, **not financial advice**. Backtested results
> do not guarantee future performance. Use of the X API must comply with X's Terms of Service.

---

## How it works

```
   X API ─────► ingest posts ─┐
 (usage-based)                ├─► enrich (qualify + tickers + sentiment) ─► PostgreSQL
  Schwab API ─► ingest prices ┘                                              │
  Schwab API ─► ingest fundamentals ────────────────────────────────────────►│
                                                                            ▼
                                                          backtest / strategy engine
                                                                            │
                                                                            ▼
                                                       CLI reports + saved backtest runs
```

`uv run findb x pipeline` runs the X-sentiment flow end to end. Focused commands remain available
for debugging and reruns.

## Tech stack

- **Python 3.12**, managed with [`uv`](https://github.com/astral-sh/uv)
- **PostgreSQL 16** with **raw SQL migrations** + `psycopg` (portable — local Docker, or Supabase/Neon/RDS)
- **httpx** → X API v2 ingestion (usage-aware, rate-limited)
- **Charles Schwab Trader API** (via [`schwab-py`](https://github.com/alexgolec/schwab-py)) for daily OHLCV and fundamentals — real brokerage data, swappable behind a `PriceProvider` interface
- **Claude Haiku** (`anthropic` SDK) for cheap, cached, structured sentiment scoring; **Ollama** supported as a local alternative via `OLLAMA_BASE_URL`
- **pytest**, **ruff**, **mypy**, GitHub Actions CI

## Quickstart

> Status: CLI research pipeline is implemented; see [`documentation/plan.md`](documentation/plan.md).

```bash
# 1. Clone + install
git clone https://github.com/<you>/x-market-analysis.git
cd x-market-analysis
uv sync --extra dev

# 2. Configure secrets (never commit .env)
cp .env.example .env
#   set DATABASE_URL, SCHWAB_APP_KEY/SECRET, X_BEARER_TOKEN, ANTHROPIC_API_KEY, watchlist, etc.

# 3. Start a local Postgres + run raw SQL migrations
docker compose up -d
uv run findb migrate

# 4. One-time Schwab OAuth login (caches a refreshable token)
uv run findb schwab-login

# 5. Ingest market data
uv run findb ingest-prices --days 30        # daily OHLCV for your watchlist
uv run findb ingest-fundamentals            # daily fundamentals snapshot

# 6. One-time X OAuth login and post ingestion
uv run findb x login
uv run findb x ingest-posts --source following --max-posts 100
uv run findb x enrich             # qualify + ticker extraction + Haiku sentiment (cached)

# 7. Backtest a signal, or run the whole current pipeline
uv run findb x backtest --signal positive_high --horizon 5
uv run findb x pipeline
uv run findb x report-qualified --limit 25

# 8. Run checks
uv run ruff check .
uv run mypy src tests
uv run pytest
```

## Configuration

All config is loaded from `.env` via `pydantic-settings`. Copy `.env.example` and fill in:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Postgres connection string |
| `SCHWAB_APP_KEY` | Schwab developer app key (from developer.schwab.com) |
| `SCHWAB_APP_SECRET` | Schwab developer app secret |
| `SCHWAB_CALLBACK_URL` | OAuth callback URL — must match your Schwab app exactly |
| `X_BEARER_TOKEN` | X API v2 bearer token for optional public search |
| `X_CLIENT_ID` | X OAuth 2.0 client ID for following-feed auth |
| `X_CLIENT_SECRET` | X OAuth 2.0 client secret, if your app uses one |
| `X_REDIRECT_URI` | X OAuth redirect URI; must match developer portal |
| `X_USER_TOKEN_PATH` | Local cached X user token path |
| `ANTHROPIC_API_KEY` | For Claude Haiku qualification, ticker extraction, and sentiment scoring |
| `QUALIFY_MODEL` | LLM for qualification and ticker extraction — accepts `anthropic:<model>` or `ollama:<model>`; bare names default to Anthropic |
| `SENTIMENT_MODEL` | LLM for sentiment scoring — same format as `QUALIFY_MODEL` |
| `OLLAMA_BASE_URL` | Base URL of an Ollama instance, e.g. `http://localhost:11434` (leave blank to use Anthropic only) |
| `WATCHLIST` | Comma-separated tickers to track |

Secrets live only in `.env` (git-ignored). Only `.env.example` is committed.

## Project layout

```
src/findb/        cli.py · config.py · core/{db,llm,marketdata} · features/xsentiment/
documentation/    plan.md · architecture.md · commands.md · strategy-methodology.md · home-server.md
migrations/       raw SQL database migrations
deploy/           home-server/ — Postgres + Ollama on a Windows PC (see documentation/home-server.md)
tests/            unit tests
```

## Documentation

- [Commands cheat sheet](documentation/commands.md) — every command you'll need, explained
- [Build plan](documentation/plan.md) — step-by-step roadmap and decisions
- [Architecture notes](documentation/architecture.md)
- [Strategy methodology](documentation/strategy-methodology.md)
- [Home server runbook](documentation/home-server.md) — Postgres + Ollama on a Windows PC over Tailscale

## Roadmap

- [x] Step 0 — Project scaffold & hygiene
- [x] Step 1 — raw PostgreSQL schema & migrations
- [x] Step 2 — Price ingestion (Charles Schwab Trader API)
- [x] Step 3 — X ingestion
- [x] Step 4 — Qualification + ticker extraction
- [x] Step 5 — LLM sentiment (Claude Haiku)
- [x] Step 6 — Signal & backtest engine
- [x] Step 6.5 — Pipeline orchestration CLI
- [x] Step 6.6 — Rich CLI progress and summaries
- [x] Step 6.7 — Qualified post report CLI
- [x] Step 7 — Tests, CI, docs polish
- [x] Platform revamp — `findb` package, LLM routing, fundamentals ingestion, home-server deploy

## License

MIT. See [LICENSE](LICENSE).
