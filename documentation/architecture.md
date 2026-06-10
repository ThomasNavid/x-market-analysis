# Architecture

`findb` is a personal financial data platform with a CLI-first design. Its purpose is to build a
growing private database of market data and derived signals. X-sentiment analysis is the first
feature; additional data domains (fundamentals, price snapshots, option chains, news) are ingested
through the same core infrastructure.

---

## Table of Contents

1. [Repository layout](#repository-layout)
2. [Data flow](#data-flow)
3. [LLM routing](#llm-routing)
4. [Storage and cache keys](#storage-and-cache-keys)
5. [Deployment topology](#deployment-topology)
6. [External services](#external-services)
7. [Quality gates](#quality-gates)

---

## Repository layout

```text
x-market-analysis/
├── src/findb/
│   ├── cli.py              root Typer app: info, migrate, migrate-status,
│   │                       schwab-login, ingest-prices, ingest-fundamentals, x
│   ├── config.py           pydantic-settings — all env vars in one typed place
│   ├── core/               shared infrastructure (no feature logic here)
│   │   ├── paths.py        canonical file-system paths (token files, etc.)
│   │   ├── cli_utils.py    Rich helpers shared across commands
│   │   ├── db/
│   │   │   ├── connection.py   psycopg connection helper
│   │   │   └── migrations.py   raw-SQL migration runner
│   │   ├── llm/            provider-agnostic LLM JSON-completion layer
│   │   │   ├── base.py         ModelRef, JSONChatClient protocol, error types
│   │   │   ├── router.py       parse provider:model ref → dispatch to provider
│   │   │   ├── anthropic_provider.py
│   │   │   └── ollama_provider.py
│   │   └── marketdata/     Schwab market-data wrappers
│   │       ├── schwab_client.py
│   │       ├── prices.py       OHLCV fetch + upsert
│   │       └── fundamentals.py daily snapshot fetch + upsert
│   └── features/
│       └── xsentiment/     X-sentiment pipeline feature
│           ├── cli.py          Typer sub-app mounted at `findb x`
│           ├── x_client.py     X API v2 OAuth + feed/search client
│           ├── posts.py        fetch + persist posts/authors
│           ├── tickers.py      LLM qualification + ticker extraction
│           ├── sentiment.py    LLM sentiment scoring (cached)
│           ├── signals.py      built-in signal definitions
│           ├── backtest.py     forward-return computation + stats
│           └── reports.py      qualified post/sentiment report queries
├── migrations/             raw PostgreSQL SQL files
├── deploy/
│   └── home-server/        Docker Compose + docs for the Windows home PC
├── documentation/
└── tests/
```

The `core/` tree owns everything a second feature would also need: the database connection,
migrations runner, LLM routing, and Schwab client. Features live under `features/` and import
from `core/`; they never import from each other.

---

## Data flow

### X-sentiment pipeline

```text
X following/search posts
  -> authors / posts
  -> post_qualifications          (LLM qualify — cached by post_id + prompt_version)
  -> post_ticker_extractions      (LLM ticker extract — cached by post_id + prompt_version)
  -> post_tickers                 (normalized post_id → ticker rows)
  -> sentiments                   (LLM sentiment — cached by post_id + ticker + model + prompt_version)
  -> prices                       (Schwab OHLCV — cached by ticker + date)
  -> signals / backtest_runs
```

### Fundamentals pipeline

```text
Schwab get_instruments (projection=fundamental)
  -> fundamentals                 (daily snapshot — PK: ticker + captured_date, idempotent upsert)
```

The main orchestration command for the X-sentiment pipeline is:

```bash
uv run findb x pipeline
```

Focused commands remain available for reruns and debugging:

```bash
uv run findb x ingest-posts --source following --max-posts 100
uv run findb x enrich
uv run findb x report-qualified --limit 25
uv run findb x backtest --signal positive_high --horizon 5
```

Fundamentals are ingested separately:

```bash
uv run findb ingest-fundamentals   # default: full watchlist, idempotent daily upsert
```

---

## LLM routing

`QUALIFY_MODEL` and `SENTIMENT_MODEL` accept a `provider:model` reference string:

| Format | Provider | Example |
|--------|----------|---------|
| `anthropic:<model>` | Anthropic API | `anthropic:claude-haiku-4-5` |
| `ollama:<model>` | Local Ollama instance | `ollama:qwen3:14b` |
| bare name (no prefix, or unknown prefix) | Anthropic (back-compat default) | `claude-haiku-4-5` |

The bare-name fallback to Anthropic exists specifically for backwards compatibility: existing
database cache keys encode the raw model string (e.g. `claude-haiku-4-5`) so they continue to
match after the routing layer was added, without any data migration.

Set `OLLAMA_BASE_URL` in `.env` to route Ollama calls to a remote host:

```bash
# local
OLLAMA_BASE_URL=http://localhost:11434

# home server over Tailscale
OLLAMA_BASE_URL=http://home-pc.<tailnet>.ts.net:11434
SENTIMENT_MODEL=ollama:qwen3:14b
```

---

## Storage and cache keys

PostgreSQL is the source of truth. The schema is managed with raw SQL migrations in `migrations/`
and applied with `uv run findb migrate`.

Important cache / deduplication keys:

| Table | Key | What it prevents |
|-------|-----|-----------------|
| `post_qualifications` | `post_id + qualify_prompt_version` | Re-paying to qualify the same post |
| `post_ticker_extractions` | `post_id + ticker_prompt_version` | Re-paying to extract tickers from the same post |
| `sentiments` | `post_id + ticker + sentiment_model + sentiment_prompt_version` | Re-paying to score the same post/ticker pair |
| `prices` | `ticker + date` | Re-fetching OHLCV bars already in the database |
| `fundamentals` | `ticker + captured_date` | Re-fetching the same day's snapshot (idempotent upsert) |

Because cache keys encode the exact model string, switching from `claude-haiku-4-5` (bare) to
`anthropic:claude-haiku-4-5` (explicit prefix) would be treated as a different key and force
re-scoring. Use bare names or consistently prefixed names — do not mix them for the same model.

---

## Deployment topology

### Local development

The repository root `docker-compose.yml` runs a `postgres:16` container (`xmarket-db`, user/db
`xmarket`) on port 5432. This is the default `DATABASE_URL`.

### Home server (Windows PC over Tailscale)

`deploy/home-server/` contains a separate Docker Compose stack that runs a `findb` database.
The Mac connects to it over a Tailscale tunnel; switching is a one-line change to `DATABASE_URL`.

See [documentation/home-server.md](home-server.md) for the full setup runbook.

---

## External services

| Service | Used for | Notes |
|---------|----------|-------|
| X API v2 | Posts from the authenticated user's following feed, or optional public cashtag search | Usage-based billing; tokens cached in `.x_user_token.json` |
| Anthropic Claude | Qualification, ticker extraction, sentiment scoring | Default LLM provider; model selected via `QUALIFY_MODEL` / `SENTIMENT_MODEL` |
| Ollama | Local LLM alternative for enrichment | Enabled by setting `OLLAMA_BASE_URL`; use `ollama:<model>` prefix in model settings |
| Charles Schwab Trader API | Daily OHLCV prices and fundamentals snapshots | Requires a brokerage account + registered developer app. **No news endpoint exists** — fundamentals come from `GET /instruments?projection=fundamental` at 120 req/min |

Secrets are loaded from `.env` via `pydantic-settings`. `.env` and OAuth token files are
git-ignored and must never be committed.

---

## Quality gates

Local and CI checks are:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```
