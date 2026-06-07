# Commands Cheat Sheet

A reference for every command you'll use day to day. Run all of these from the project root
(`x-market-analysis/`).

> **Why `uv run ...` in front of everything?**
> `uv run` executes a command inside this project's isolated environment (its own Python + installed
> libraries), so you never have to "activate" anything or worry about clashing with other projects.
> Rule of thumb: if it's a Python tool for *this* project, prefix it with `uv run`.

---

## 1. Environment & dependencies (`uv`)

| Command | What it does |
|---------|--------------|
| `uv sync --extra dev` | Install all dependencies (app + dev tools) into `.venv`. Run after cloning or editing `pyproject.toml`. |
| `uv add <package>` | Add a new dependency and record it in `pyproject.toml`. |
| `uv add --dev <package>` | Add a dev-only dependency (test/lint tools). |
| `uv remove <package>` | Remove a dependency. |
| `uv run <command>` | Run any command inside the project environment. |
| `uv lock` | Refresh the lockfile (`uv.lock`) that pins exact versions. |

---

## 2. The app CLI (`xmarket`)

Your project's own commands. `xmarket` exists because of the `[project.scripts]` line in `pyproject.toml`.

| Command | What it does | Status |
|---------|--------------|--------|
| `uv run xmarket --help` | List all available commands. | ✅ |
| `uv run xmarket <command> --help` | Show help for one command. | ✅ |
| `uv run xmarket info` | Print current config — sanity-check that `.env` loaded and which keys are set. | ✅ |
| `uv run xmarket migrate` | Apply pending raw SQL migrations from `migrations/`. | ✅ |
| `uv run xmarket migrate-status` | Show unapplied SQL migrations. | ✅ |
| `uv run xmarket schwab-login` | One-time Schwab OAuth login; caches a refreshable token. | ✅ |
| `uv run xmarket ingest-prices --days 30` | Fetch daily OHLCV price data from Schwab into the `prices` table. | ✅ |
| `uv run xmarket x-login` | One-time X OAuth login; caches a user token for your following feed. | ✅ |
| `uv run xmarket ingest-posts --source following --max-posts 100` | Fetch your reverse-chronological X following feed into `posts`/`authors`. | ✅ |
| `uv run xmarket ingest-posts --source search --max-posts 100` | Optional public cashtag search mode. | ✅ |
| `uv run xmarket enrich` | Qualify posts, extract tickers, score sentiment, and fetch missing Schwab price bars. | ✅ |
| `uv run xmarket backtest --signal positive_high --horizon 5` | Backtest a built-in signal over price history and save the run. | ✅ |
| `uv run xmarket pipeline` | Run ingest, enrich, price coverage, and both built-in backtests with Rich progress output. | ✅ |
| `uv run xmarket serve` | Run the FastAPI server (docs at http://localhost:8000/docs). | 🚧 Step 7 |

✅ = works now · 🚧 = stub, prints "not implemented yet" until that build step is done.

---

## 3. Database — local Postgres (`docker compose`)

The database runs in a Docker container defined by `docker-compose.yml`.

| Command | What it does |
|---------|--------------|
| `docker compose up -d` | Start Postgres in the background. |
| `docker compose ps` | Show whether the DB container is running / healthy. |
| `docker compose stop` | Stop the DB (keeps your data). |
| `docker compose down` | Stop and remove the container (data **survives** in a volume). |
| `docker compose down -v` | Stop and **delete all data** (the `-v` wipes the volume). Use to start fresh. |
| `docker compose logs -f db` | Tail the database logs (Ctrl+C to stop). |

### Poke around inside the database (psql)
| Command | What it does |
|---------|--------------|
| `docker exec -it xmarket-db psql -U xmarket -d xmarket` | Open an interactive SQL shell. |
| `docker exec xmarket-db psql -U xmarket -d xmarket -c '\dt'` | List all tables (one-off). |
| `docker exec xmarket-db psql -U xmarket -d xmarket -c 'SELECT * FROM prices LIMIT 5;'` | Run a quick query. |

Inside the interactive `psql` shell: `\dt` lists tables, `\d prices` describes a table, `\q` quits.

---

## 4. Database migrations (raw SQL)

Migrations are version control for your database schema (see `documentation/plan.md`, Step 1).
This project uses plain PostgreSQL files in `migrations/` so you can learn the SQL directly.

| Command | What it does |
|---------|--------------|
| `uv run xmarket migrate` | Apply all pending `.sql` migrations. |
| `uv run xmarket migrate-status` | Show SQL files that have not been applied. |
| `docker exec xmarket-db psql -U xmarket -d xmarket -c 'SELECT * FROM schema_migrations;'` | Show migration history. |
| `docker exec -i xmarket-db psql -U xmarket -d xmarket < migrations/001_initial_schema.sql` | Manually apply one SQL file while learning. |

**Typical workflow when you change the schema:**
1. Create a new file like `migrations/002_add_post_lang.sql`.
2. Write the PostgreSQL change yourself, for example `ALTER TABLE posts ADD COLUMN source text;`.
3. Run `uv run xmarket migrate`.
4. Open `psql` and inspect the table with `\d posts`.

---

## 5. PostgreSQL essentials (psql + SQL)

New to Postgres? This is the survival kit. First, open a SQL shell **inside** the database container:

```bash
docker exec -it xmarket-db psql -U xmarket -d xmarket
```

You'll get a `xmarket=#` prompt. Everything below is typed at that prompt.

### psql meta-commands (start with `\`, no semicolon needed)
| Command | What it does |
|---------|--------------|
| `\dt` | List all tables. |
| `\d prices` | Describe one table — its columns, types, indexes. |
| `\d+ prices` | Same, but with extra detail (sizes, comments). |
| `\l` | List all databases. |
| `\dn` | List schemas. |
| `\df` | List functions. |
| `\dv` | List views. |
| `\di` | List indexes. |
| `\du` | List users/roles and their permissions. |
| `\x` | Toggle "expanded" display — great for wide rows (each column on its own line). |
| `\timing` | Toggle showing how long each query took. |
| `\conninfo` | Show what you're connected to (db, user, host). |
| `\e` | Open the last query in an editor (for multi-line edits). |
| `\?` | Help on all `\` meta-commands. |
| `\h SELECT` | SQL syntax help for a specific command (e.g. `SELECT`). |
| `\q` | Quit the shell. |

### Everyday SQL (end every statement with a `;`)
| Command | What it does |
|---------|--------------|
| `SELECT * FROM prices LIMIT 10;` | Peek at the first 10 rows. |
| `SELECT count(*) FROM prices;` | How many rows are in the table. |
| `SELECT * FROM prices WHERE symbol = 'AAPL';` | Filter rows (text values use single quotes). |
| `SELECT * FROM prices ORDER BY date DESC LIMIT 5;` | Newest 5 rows. |
| `SELECT symbol, count(*) FROM prices GROUP BY symbol;` | Count rows per symbol. |
| `SELECT DISTINCT symbol FROM prices;` | List the unique symbols. |
| `INSERT INTO authors (id, handle) VALUES ('123', 'someone');` | Add a row. |
| `UPDATE posts SET source = 'x' WHERE id = 1;` | Change existing rows (always include a `WHERE`!). |
| `DELETE FROM posts WHERE id = 1;` | Remove rows (always include a `WHERE`!). |

> ⚠️ **`UPDATE` and `DELETE` without a `WHERE` clause change every row.** When unsure, run a
> `SELECT` with the same `WHERE` first to see exactly which rows you'd affect.

### Quick one-off queries without entering the shell
Add `-c` to run a single statement and exit (handy in scripts):
```bash
docker exec xmarket-db psql -U xmarket -d xmarket -c 'SELECT count(*) FROM prices;'
```

---

## 6. Tests, linting, type-checking

| Command | What it does |
|---------|--------------|
| `uv run pytest` | Run all tests. |
| `uv run pytest -q` | Run tests, quiet output. |
| `uv run pytest tests/test_config.py` | Run one test file. |
| `uv run pytest -k watchlist` | Run only tests whose name matches "watchlist". |
| `uv run ruff check .` | Lint the code (style + common bugs). |
| `uv run ruff check --fix .` | Auto-fix the issues that can be fixed safely. |
| `uv run ruff format .` | Auto-format the code. |
| `uv run mypy src` | Type-check the code. |

---

## 7. Git basics (public GitHub project)

| Command | What it does |
|---------|--------------|
| `git status` | See what's changed. |
| `git add -A` | Stage all changes. |
| `git commit -m "message"` | Save a snapshot. |
| `git push` | Upload commits to GitHub. |
| `git log --oneline` | Compact history. |

> ⚠️ **Never commit secrets.** `.env` and `.schwab_token.json` are git-ignored on purpose. Before
> pushing, a quick `git status` should never show those files.

---

## Quick "from zero" sequence

Starting fresh (e.g. on a new machine after cloning):

```bash
uv sync --extra dev                 # install everything
cp .env.example .env                # then fill in your keys
docker compose up -d                # start the database
uv run xmarket migrate              # create the tables from raw SQL
uv run xmarket info                 # confirm config loads
```
