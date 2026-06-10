# x-market-analysis — Build Plan

## Context

Build a system that finds **winning trading strategies** by correlating X (Twitter) chatter about
stocks with subsequent price performance. The core research question is:

> "When a stock is mentioned on X under conditions _C_ (e.g. high positive sentiment, mention-volume
> spike, specific account types), does it outperform over the next _N_ days?"

The pipeline: ingest X posts → qualify posts as stock-trade intelligence → extract tickers →
score sentiment with an LLM (Claude Haiku, cheap) → join against price data → backtest signal
definitions → rank strategies by forward returns.

This is **both a public GitHub portfolio project and a personal trading tool**, so it needs clean code,
a useful CLI, tests, CI, and good docs. X API access is sorted (usage-based billing). The DB is
**plain PostgreSQL with raw SQL migrations** for portability and learning (runs locally in Docker, deploys to
Supabase/Neon/RDS unchanged).

---

## Architecture at a glance

```
                 ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
   X API  ─────► │  ingest_x    │ ──► │  enrich      │ ──► │  PostgreSQL  │
 (usage-based)   │  (posts)     │     │  qualify +   │     │ (raw SQL)    │
                 └──────────────┘     │  tickers +   │     └──────┬───────┘
                                      │  sentiment   │            │
                                      │  (Haiku LLM) │            │
 Schwab API  ─► ingest_prices ─────────┴──────────────┘            │
 (price/OHLCV)                                                    ▼
                                                          ┌──────────────┐
                                                          │  backtest /  │
                                                          │  strategy    │
                                                          │  engine      │
                                                          └──────┬───────┘
                                                                 ▼
                                                          ┌──────────────┐
                                                          │ CLI reports  │
                                                          │ + saved runs │
                                                          └──────────────┘
   `xmarket pipeline` orchestrates the current end-to-end flow.
```

---

## Tech stack

| Concern              | Choice                                       | Why |
|----------------------|----------------------------------------------|-----|
| Language             | Python 3.12                                  | Rich data/ML ecosystem |
| Dependency mgmt      | `uv` (with `pyproject.toml`)                  | Fast, modern, reproducible |
| DB                   | PostgreSQL 16                                | Universal, portable |
| DB access / migrations | `psycopg` + raw PostgreSQL `.sql` files    | Learn the real SQL; no ORM abstraction |
| Local DB             | Docker Compose (`postgres:16`)               | One-command setup |
| X ingestion          | `httpx` against X API v2 (usage-based)       | Async, simple |
| Price data           | **Charles Schwab Trader API** via `schwab-py` | Real brokerage data; same API can trade later. Behind a `PriceProvider` interface so it's swappable |
| Sentiment            | Claude **Haiku** via `anthropic` SDK          | Cheap, batchable, structured output |
| Config               | `pydantic-settings` + `.env`                  | Typed, 12-factor, secrets out of git |
| CLI UX               | Typer + Rich                                 | Friendly command surface and progress output |
| Testing              | `pytest`                                     | Standard |
| Lint/format          | `ruff` + `mypy`                               | Portfolio polish |
| CI                   | GitHub Actions (lint + test)                 | Public-repo quality signal |

---

## Repository layout

```
x-market-analysis/
├── README.md                      # project intro, setup (GitHub front page)
├── documentation/
│   ├── plan.md                    # this plan
│   ├── architecture.md            # deeper design notes
│   └── strategy-methodology.md    # how backtesting/signals work + caveats
├── pyproject.toml                 # deps + tooling config
├── docker-compose.yml             # local Postgres
├── .env.example                   # documented env vars (NO secrets)
├── .gitignore
├── migrations/                    # raw PostgreSQL migrations
├── src/xmarket/
│   ├── config.py                  # pydantic-settings
│   ├── db/
│   │   ├── connection.py          # psycopg connection helper
│   │   └── migrations.py          # tiny raw-SQL migration runner
│   ├── ingest/
│   │   ├── x_client.py            # X API v2 wrapper (usage-aware, rate-limited)
│   │   ├── posts.py               # fetch + persist posts
│   │   └── prices.py              # PriceProvider interface + Schwab impl
│   ├── enrich/
│   │   ├── reports.py             # qualified post/sentiment report queries
│   │   ├── sentiment.py           # Claude Haiku sentiment (cached)
│   │   └── tickers.py             # LLM qualification + ticker resolution
│   ├── analysis/
│   │   ├── signals.py             # built-in signal definitions
│   │   └── backtest.py            # forward-return computation + stats
│   └── cli.py                     # `xmarket ingest|enrich|backtest|pipeline`
└── tests/
```

---

## Data model (PostgreSQL)

- **posts** — `id` (X post id), `author_id`, `text`, `created_at`,
  `like_count`, `repost_count`, `reply_count`, `lang`, `raw` (JSONB), `fetched_at`.
- **authors** — `id`, `handle`, `followers`, `verified`, `account_tier` (for "account type" conditions).
- **post_qualifications** — cached qualification decisions per `post_id` + `prompt_version`, including
  `qualified`, `reason`, and `model`; rejected posts are cached here so they are not re-scored.
- **post_ticker_extractions** — cached raw ticker-extraction output per `post_id` + `prompt_version`,
  including qualified posts where no ticker could be resolved.
- **post_tickers** — normalized `post_id` → `ticker`, `match_method` (llm / cashtag / name),
  `confidence`, qualification/extraction prompt metadata.
- **sentiments** — `post_id`, `ticker`, `label` (pos/neg/neutral), `score` (-1..1),
  `model`, `prompt_version`, `created_at`. Cached so we never re-pay for the same post.
- **prices** — `ticker`, `date`, `open/high/low/close/adj_close`, `volume`. Daily OHLCV.
- **signals** — saved strategy definitions (conditions as JSONB).
- **backtest_runs** — `signal_id`, params (horizon N, thresholds), aggregate results (avg forward
  return, win rate, sample size, Sharpe-ish), `created_at`. Reproducible + comparable over time.

---

## Build steps (incremental, each step independently runnable/committable)

### Step 0 — Project scaffold & hygiene
`pyproject.toml` (deps + ruff/mypy/pytest config), `.gitignore`, `.env.example`, `docker-compose.yml`,
`README.md`, `documentation/`. Configure `pydantic-settings` in `config.py`.
**Outcome:** `docker compose up -d` gives a local Postgres; config loads from `.env`.

### Step 1 — raw PostgreSQL schema & migrations
Write the schema directly in `migrations/001_initial_schema.sql`. Use `db/connection.py` for psycopg
connections and `db/migrations.py` for a tiny migration runner that records applied files in
`schema_migrations`.
**Outcome:** `uv run xmarket migrate` creates all tables, and you can inspect them in `psql`.

### Step 2 — Price ingestion (build first — fast feedback loop, no LLM cost)
- One-time Schwab OAuth setup: register an app on developer.schwab.com (App Key, App Secret, callback
  URL), then run a login flow once; `schwab-py` caches the token (access + refresh) to
  `SCHWAB_TOKEN_PATH` and auto-refreshes it after.
- `PriceProvider` interface + `SchwabProvider` (wraps `schwab-py`'s `get_price_history`); `ingest/prices.py`
  persists daily OHLCV for the watchlist. CLI: `xmarket schwab-login` (one-off) and `xmarket ingest-prices`.
- **Outcome:** `prices` table populated for configured tickers.
- **Current implementation:** `xmarket schwab-login` creates/refreshes `.schwab_token.json`, and
  `xmarket ingest-prices --days 30` fetches daily candles for `WATCHLIST` and upserts them into `prices`.

> **Schwab auth notes:** access tokens expire ~30 min and `schwab-py` refreshes them automatically; the
> refresh token lasts ~7 days, after which you re-run `xmarket schwab-login`. The token file
> (`.schwab_token.json`) holds live credentials — it is git-ignored and must never be committed.

### Step 3 — X ingestion
Primary mode: OAuth-login to X as the local user, read the authenticated user's reverse-chronological
following feed, and store posts from followed accounts. This reflects the user's chronological Following
feed rather than only broad public cashtag search or the algorithmic For You feed. Public recent search
stays available as an optional research mode.

`x_client.py`: X API v2 OAuth 2.0 Authorization Code + PKCE login, token refresh/cache,
reverse-chronological following-feed calls, and optional recent-search calls. `ingest/posts.py`: fetch
timeline/search pages, normalize author/post JSON, then upsert `posts` + `authors`. Store full `raw` JSONB.
CLI: `xmarket x-login`, `xmarket ingest-posts --source following`, optional `--source search`.
**Outcome:** posts + authors populated from the user's feed; idempotent re-runs.

### Step 4 — Qualify trade-intel posts, then extract tickers
`enrich/tickers.py` runs a two-pass LLM workflow before sentiment:

1. **Qualification pass** with `QUALIFY_MODEL=claude-haiku-4-5`: decide whether the post is discussing a
   public company, ticker, market-moving event, trading setup, or other stock-specific information that
   could plausibly be useful as trading intelligence. Examples include positive/negative product news,
   executive or regulatory updates, earnings commentary, supply-chain signals, unusual demand, or explicit
   trade commentary about a particular stock. Generic market chatter, politics without a clear stock link,
   jokes, and posts with no investable public-company target are rejected.
2. **Ticker extraction pass** only for qualified posts: resolve the investable ticker(s) mentioned or
   strongly implied by the post, returning canonical symbols such as `AAPL` when the text says "Apple".
   Cashtags and company-name matches can be used as hints, but the LLM is responsible for disambiguation
   and structured output.

Writes `post_tickers` with ticker, confidence, qualification decision/reason, model, and prompt version.
Skip posts already qualified/extracted for the same prompt version unless forced.
**Outcome:** only trade-relevant posts are linked to canonical tickers with method + confidence.

### Step 5 — LLM sentiment (Claude Haiku)
`enrich/sentiment.py`: for qualified `(post, ticker)` pairs, run `SENTIMENT_MODEL=claude-haiku-4-5`
with batched prompts and **structured JSON output** (label + score + rationale). Track
`prompt_version`, cache results in `sentiments`, and skip already-scored post+ticker/model/prompt-version
pairs. Prompt caching on the system prompt cuts cost. CLI: `xmarket enrich`.
**Outcome:** every qualified (post, ticker) has cached sentiment; re-runs are nearly free.

After ticker extraction/sentiment, ensure Schwab daily OHLCV exists for the extracted ticker around the
post timestamp. Use an idempotent price-ingestion path: before calling Schwab, check `prices` for the
required ticker/date range and only request missing days. Since Schwab prices are daily candles, a
same-day ticker fetch should satisfy later posts for that ticker without another market-data request.

### Step 6 — Signal & backtest engine (the heart of it)
`analysis/signals.py`: v1 built-in signals `positive_high` (`sentiment_score >= 0.6`) and
`negative_high` (`sentiment_score <= -0.6`), both requiring ticker confidence `>= 0.6`.
`analysis/backtest.py`: for each matching enriched post/ticker, enter at the next available trading-day
close after the post date, exit after horizon `N` trading days, dedupe to the earliest post per
ticker/entry date, then aggregate raw and directional returns. Guardrails against look-ahead bias and
tiny samples (flag `n < threshold`). Every run upserts the signal definition and persists results to
`backtest_runs`.
CLI: `xmarket backtest --signal positive_high --horizon N`.
**Outcome:** saved, comparable backtest runs for the first bullish and bearish sentiment signals.

### Step 6.5 — Pipeline orchestration CLI
`xmarket pipeline`: run the current end-to-end flow in order: ingest posts, enrich/score/cache, ensure
missing Schwab price coverage, then backtest `positive_high` and `negative_high`. Keep individual commands
available for debugging and focused reruns. Options include source/max posts/page size, enrich limit,
price coverage toggle, signal list, horizon, min samples, and skip flags for ingest/enrich/backtest.
**Outcome:** one command exercises the full system so far while individual commands remain available for
debugging and focused reruns.

### Step 6.6 — Rich CLI progress and summaries
Use Rich for CLI readability: config/enrich/backtest summary tables, status spinners around long-running
single commands, and a progress bar for `xmarket pipeline` stages. Keep command arguments and underlying
behavior unchanged.
**Outcome:** pipeline runs are easier to monitor without changing data semantics.

### Step 6.7 — Qualified post report CLI
`xmarket report-qualified`: read-only Rich table for inspecting qualified posts, extracted tickers,
sentiment labels/scores/rationales, qualification reasons, and post text previews. Options include
`--limit`, `--ticker`, `--min-score` (absolute sentiment score), `--since`, and `--text-chars`.
**Outcome:** fast manual QA of qualification and sentiment quality before trusting backtests.

### Step 7 — Tests, CI, docs polish
`pytest` units cover config parsing, enrichment helpers/reports, signal matching, forward-return math,
dedupe, and aggregate metrics. GitHub Actions runs formatting, lint, type checks, and tests on PR/push.
Finalize `README.md`, `architecture.md`, `strategy-methodology.md`, and command docs.

---

## Security & public-repo safeguards
- All secrets via `.env` (git-ignored); only `.env.example` committed. X + Anthropic keys never in code.
- `.gitignore` covers `.env`, data dumps, caches, virtualenvs.
- README disclaimer: educational/research, not financial advice; respect X API ToS.

## Cost control (X + LLM are usage-billed)
- Qualification, ticker extraction, and sentiment results cached in DB → never re-pay for the same post
  and prompt version.
- Haiku + prompt caching + batching to minimize tokens.
- X queries scoped to a configurable watchlist + time window; usage logged.
- Schwab market data is included with a brokerage account; cache OHLCV in the `prices` table and check
  ticker/date coverage before calling Schwab so repeated same-day mentions do not refetch the same data.

---

## Verification (end-to-end)
1. `docker compose up -d && uv run xmarket migrate` — tables exist (psql `\dt`).
2. `xmarket ingest-prices` → `prices` populated for the watchlist.
3. `xmarket x-login && xmarket ingest-posts --source following --max-posts 100` → `posts`/`authors`
   populated from the following feed; re-run is idempotent.
4. `xmarket enrich` → qualified posts produce `post_tickers` + `sentiments`; second run scores ~0 new
   and performs ~0 duplicate Schwab fetches for already-covered ticker/date ranges.
5. `xmarket backtest --signal positive_high --horizon 5` → ranked forward-return stats; row in `backtest_runs`.
6. `xmarket pipeline` → ingest/enrich/price coverage/backtests complete with Rich progress output.
7. `xmarket report-qualified --limit 25` → qualified posts and sentiment render in a table.
8. `pytest` green; GitHub Actions green on a PR.

## Decisions locked
- **Dependency tool:** uv.
- **Build order:** foundation-first (Steps 0–2 before X/LLM).
- **Price provider:** Charles Schwab Trader API (individual) via `schwab-py`, behind a `PriceProvider`
  interface (swappable for Polygon/Alpha Vantage later). Requires a Schwab brokerage account + a
  registered developer app.
- **DB:** plain PostgreSQL with raw SQL migrations — deploy target left open (Supabase/Neon/Railway/RDS all viable).

> ⚠️ **Disclaimer:** This is a research/educational tool, not financial advice. Backtested results do
> not guarantee future performance.

---

## Platform revamp — Phase 1 (completed 2026-06-10)

The project was restructured from a single-purpose X-sentiment pipeline (`xmarket` CLI, `src/xmarket/`)
into a personal financial data platform (`findb` CLI, `src/findb/`). The `xmarket` entry point
remains as a temporary alias.

### What Phase 1 delivered

**Package + CLI restructure**

- Renamed package `xmarket` → `findb`; new layout separates shared infrastructure
  (`src/findb/core/`) from feature logic (`src/findb/features/xsentiment/`).
- Root CLI (`uv run findb`) owns platform-wide commands: `info`, `migrate`, `migrate-status`,
  `schwab-login`, `ingest-prices`, `ingest-fundamentals`.
- X-sentiment pipeline moved to the `findb x` sub-app: `login`, `ingest-posts`, `enrich`,
  `report-qualified`, `backtest`, `pipeline`.

**LLM provider routing (`core/llm/`)**

- New provider-agnostic layer parses `provider:model` reference strings and dispatches to the
  matching client.
- `QUALIFY_MODEL` and `SENTIMENT_MODEL` accept `anthropic:<model>` or `ollama:<model>` refs.
- Bare model names (e.g. `claude-haiku-4-5`) default to Anthropic to preserve existing database
  cache keys without a data migration.
- `OLLAMA_BASE_URL` setting enables local or remote Ollama instances.

**Schwab fundamentals ingestion**

- `migrations/004_fundamentals.sql`: new `fundamentals` table, PK `(ticker, captured_date)`,
  raw JSONB column, `market_cap` stored in dollars.
- `uv run findb ingest-fundamentals`: fetches via Schwab `GET /instruments?projection=fundamental`,
  idempotent daily snapshot upsert for the watchlist.
- Schwab research finding: **no news endpoint exists** in the Trader API; fundamentals are the
  correct projection for financial ratios; rate limit is 120 req/min.

**Home server deploy assets**

- `deploy/home-server/`: Docker Compose stack for a `findb` Postgres database on a Windows PC.
- `documentation/home-server.md`: full runbook covering Docker Desktop, Tailscale tunnel,
  Ollama setup, Windows Firewall rules, and data migration from the local dev database.

---

## Planned later phases

The following capabilities are prioritised for future build steps. They are recorded here as
research findings and design intent, not commitments.

| Phase | Capability | Notes |
|-------|-----------|-------|
| Quotes snapshots | `ingest-quotes` fetching real-time or delayed quote data from Schwab | Foundation for intraday signal work |
| Intraday candles | `ingest-candles` with configurable interval/period | Requires Schwab `get_price_history` intraday params |
| Option chains | `ingest-options` snapshot ingestion | High data volume; schema TBD |
| Market movers | `ingest-movers` (Schwab movers endpoint) | Useful cross-signal for sentiment spikes |
| Third-party news | `ingest-news` via Finnhub or similar | Schwab has no news endpoint; external provider required |
| Connection pooling | `PgPool` or `pgBouncer` in front of home-server Postgres | Needed when multiple ingestion commands run concurrently |
| Scheduled runs | Cron or cloud-agent triggered pipeline | Keeps the database current without manual runs |
