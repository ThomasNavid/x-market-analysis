"""Command-line entrypoint: `xmarket <command>`."""

from typing import Annotated

import httpx
import typer
from anthropic import APIError
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from xmarket.analysis.backtest import BacktestResult, run_backtest
from xmarket.config import settings
from xmarket.db.migrations import apply_pending_migrations, pending_migrations
from xmarket.enrich.reports import fetch_qualified_report_rows, parse_since_date
from xmarket.enrich.sentiment import score_missing_sentiments
from xmarket.enrich.tickers import qualify_and_extract_tickers
from xmarket.ingest.posts import ingest_home_timeline_posts, ingest_recent_posts
from xmarket.ingest.prices import (
    create_schwab_client_from_login,
    ensure_price_bars_for_ticker_dates,
)
from xmarket.ingest.prices import ingest_prices as ingest_prices_job
from xmarket.ingest.x_client import run_x_user_login

app = typer.Typer(help="x-market-analysis command-line interface.")
console = Console()


def _parse_csv(value: str | None) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_tickers(value: str | None) -> list[str]:
    return [item.upper() for item in _parse_csv(value)]


def _print_kv_table(title: str, rows: list[tuple[str, object]]) -> None:
    table = Table(title=title, show_header=False)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value")
    for label, value in rows:
        table.add_row(label, str(value))
    console.print(table)


def _format_optional(value: object | None) -> str:
    if value is None:
        return "-"
    return str(value)


def _clip(value: str | None, *, max_chars: int) -> str:
    if value is None:
        return "-"
    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 3]}..."


@app.command()
def info() -> None:
    """Show the current configuration (sanity check that .env loads)."""
    _print_kv_table(
        "x-market-analysis config",
        [
            ("Watchlist", ", ".join(settings.watchlist_tickers)),
            ("Database", settings.database_url),
            ("Qualify model", settings.qualify_model),
            ("Sentiment model", settings.sentiment_model),
            ("Schwab", "set" if settings.schwab_app_key else "MISSING"),
            ("X token", "set" if settings.x_bearer_token else "MISSING"),
            ("X client", "set" if settings.x_client_id else "MISSING"),
            ("Anthropic", "set" if settings.anthropic_api_key else "MISSING"),
        ],
    )


@app.command("report-qualified")
def report_qualified(
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            help="Maximum report rows to show.",
        ),
    ] = 25,
    ticker: Annotated[
        str | None,
        typer.Option(
            "--ticker",
            help="Only show rows for this ticker.",
        ),
    ] = None,
    min_score: Annotated[
        float | None,
        typer.Option(
            "--min-score",
            help="Minimum absolute sentiment score, e.g. 0.6 shows strong positive/negative rows.",
        ),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="Only show posts at or after this ISO date/datetime, e.g. 2026-06-08.",
        ),
    ] = None,
    text_chars: Annotated[
        int,
        typer.Option(
            "--text-chars",
            help="Maximum characters of post text to show.",
        ),
    ] = 140,
) -> None:
    """Show qualified posts, extracted tickers, and sentiment. (Step 6.7)"""
    if limit < 1:
        raise typer.BadParameter("--limit must be at least 1")
    if min_score is not None and min_score < 0:
        raise typer.BadParameter("--min-score must be non-negative")
    if text_chars < 20:
        raise typer.BadParameter("--text-chars must be at least 20")

    try:
        since_dt = parse_since_date(since)
        rows = fetch_qualified_report_rows(
            limit=limit,
            ticker=ticker.upper() if ticker else None,
            min_abs_score=min_score,
            since=since_dt,
            text_chars=text_chars,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    if not rows:
        console.print("[yellow]No qualified report rows found.[/yellow]")
        return

    table = Table(title="Qualified posts and sentiment", expand=True)
    table.add_column("Created", no_wrap=True)
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Conf", justify="right", no_wrap=True)
    table.add_column("Sentiment")
    table.add_column("Qualification")
    table.add_column("Text")

    for row in rows:
        sentiment = (
            f"{row.sentiment_label} {row.sentiment_score:.2f}\n"
            f"{_clip(row.sentiment_rationale, max_chars=90)}"
            if row.sentiment_label is not None and row.sentiment_score is not None
            else "-"
        )
        table.add_row(
            row.created_at.strftime("%Y-%m-%d %H:%M"),
            _format_optional(row.ticker),
            f"{row.ticker_confidence:.2f}" if row.ticker_confidence is not None else "-",
            sentiment,
            _clip(row.qualification_reason, max_chars=90),
            row.text,
        )

    console.print(table)


@app.command()
def pipeline(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Post source for ingestion: following, home, or search.",
        ),
    ] = "following",
    max_posts: Annotated[
        int,
        typer.Option(
            "--max-posts",
            help="Maximum recent X posts to ingest before enrichment.",
        ),
    ] = 100,
    page_size: Annotated[
        int,
        typer.Option(
            "--page-size",
            help="X API results per request.",
        ),
    ] = 100,
    tickers: Annotated[
        str | None,
        typer.Option(
            "--tickers",
            help="Comma-separated cashtags for search mode. Defaults to WATCHLIST.",
        ),
    ] = None,
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            help="Raw X recent-search query. Only used with --source search.",
        ),
    ] = None,
    enrich_limit: Annotated[
        int,
        typer.Option(
            "--enrich-limit",
            help="Maximum posts/pairs to process per enrichment stage.",
        ),
    ] = 50,
    ensure_prices: Annotated[
        bool,
        typer.Option(
            "--ensure-prices/--no-ensure-prices",
            help="Fetch missing Schwab daily bars for extracted tickers during enrichment.",
        ),
    ] = True,
    price_days: Annotated[
        int,
        typer.Option(
            "--price-days",
            help="Calendar days after each post timestamp to ensure price coverage for.",
        ),
    ] = settings.enrichment_price_days,
    signals: Annotated[
        str,
        typer.Option(
            "--signals",
            help="Comma-separated built-in signals to backtest.",
        ),
    ] = "positive_high,negative_high",
    horizon: Annotated[
        int,
        typer.Option(
            "--horizon",
            help="Trading-day horizon after entry close.",
        ),
    ] = 5,
    min_samples: Annotated[
        int,
        typer.Option(
            "--min-samples",
            help="Sample count below which backtests are flagged as tiny.",
        ),
    ] = 30,
    skip_ingest: Annotated[
        bool,
        typer.Option(
            "--skip-ingest",
            help="Skip X post ingestion and use already-stored posts.",
        ),
    ] = False,
    skip_enrich: Annotated[
        bool,
        typer.Option(
            "--skip-enrich",
            help="Skip qualification/ticker extraction/sentiment.",
        ),
    ] = False,
    skip_backtest: Annotated[
        bool,
        typer.Option(
            "--skip-backtest",
            help="Skip built-in signal backtests.",
        ),
    ] = False,
) -> None:
    """Run the current ingest → enrich → backtest pipeline. (Step 6.5)"""
    if max_posts < 1:
        raise typer.BadParameter("--max-posts must be at least 1")
    if page_size < 10 or page_size > 100:
        raise typer.BadParameter("--page-size must be between 10 and 100")
    if source not in {"following", "home", "search"}:
        raise typer.BadParameter("--source must be following, home, or search")
    if enrich_limit < 1:
        raise typer.BadParameter("--enrich-limit must be at least 1")
    if price_days < 1:
        raise typer.BadParameter("--price-days must be at least 1")
    if horizon < 1:
        raise typer.BadParameter("--horizon must be at least 1")
    if min_samples < 1:
        raise typer.BadParameter("--min-samples must be at least 1")

    signal_names = _parse_csv(signals)
    if not signal_names and not skip_backtest:
        raise typer.BadParameter(
            "--signals must include at least one signal unless --skip-backtest"
        )

    ticker_list = _parse_tickers(tickers) if tickers else settings.watchlist_tickers
    if source == "search" and not query and not ticker_list and not skip_ingest:
        raise typer.BadParameter(
            "No tickers configured. Set WATCHLIST, pass --tickers, or pass --query."
        )

    try:
        active_steps = 0
        if not skip_ingest:
            active_steps += 1
        if not skip_enrich:
            active_steps += 1
            if ensure_prices:
                active_steps += 1
        if not skip_backtest:
            active_steps += len(signal_names)

        if active_steps == 0:
            console.print("[yellow]Pipeline: all stages skipped.[/yellow]")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Starting pipeline", total=active_steps)

            if skip_ingest:
                console.print("[yellow]Ingest skipped[/yellow]")
            elif source in {"following", "home"}:
                progress.update(task, description="Ingesting following feed")
                ingest_result = ingest_home_timeline_posts(
                    max_posts=max_posts,
                    page_size=page_size,
                    exclude_replies=True,
                    exclude_retweets=True,
                )
                progress.console.print(
                    "[green]Ingest complete[/green]: "
                    f"{ingest_result.posts_seen} posts, "
                    f"{ingest_result.authors_seen} authors"
                )
                progress.advance(task)
            else:
                progress.update(task, description="Ingesting search posts")
                ingest_result = ingest_recent_posts(
                    tickers=ticker_list,
                    query=query,
                    max_posts=max_posts,
                    page_size=page_size,
                )
                progress.console.print(
                    "[green]Ingest complete[/green]: "
                    f"{ingest_result.posts_seen} posts, "
                    f"{ingest_result.authors_seen} authors"
                )
                progress.advance(task)

            if skip_enrich:
                console.print("[yellow]Enrich skipped[/yellow]")
            else:
                progress.update(task, description="Qualifying posts and extracting tickers")
                ticker_result = qualify_and_extract_tickers(limit=enrich_limit)
                sentiment_result = score_missing_sentiments(limit=enrich_limit)
                progress.console.print(
                    "[green]Enrich complete[/green]: "
                    f"qualified {ticker_result.qualified}/"
                    f"{ticker_result.qualified_checked}, "
                    f"tickers {ticker_result.ticker_rows_upserted}, "
                    f"sentiments {sentiment_result.scored}"
                )
                progress.advance(task)

                if ensure_prices:
                    progress.update(task, description="Checking Schwab price coverage")
                    price_result = ensure_price_bars_for_ticker_dates(
                        ticker_result.ticker_dates,
                        days=price_days,
                    )
                    progress.console.print(
                        "[green]Prices complete[/green]: "
                        f"checked {price_result.checked_tickers}, "
                        f"fetched {price_result.fetched_tickers}, "
                        f"upserted {price_result.upserted_rows}"
                    )
                    progress.advance(task)
                else:
                    console.print("[yellow]Prices skipped[/yellow]")

            if skip_backtest:
                console.print("[yellow]Backtest skipped[/yellow]")
            else:
                for signal_name in signal_names:
                    progress.update(task, description=f"Backtesting {signal_name}")
                    result = run_backtest(
                        signal_name=signal_name,
                        horizon=horizon,
                        min_samples=min_samples,
                    )
                    metrics = result.metrics
                    progress.console.print(
                        f"[green]Backtest {result.signal.name} complete[/green]: "
                        f"run_id={result.run_id}, "
                        f"samples={metrics['sample_count']}, "
                        f"avg_return={metrics['avg_directional_return']}, "
                        f"win_rate={metrics['win_rate']}"
                    )
                    if metrics["tiny_sample"]:
                        progress.console.print(
                            "[yellow]"
                            f"Backtest {result.signal.name}: tiny sample "
                            f"(< {result.min_samples})."
                            "[/yellow]"
                        )
                    progress.advance(task)

            progress.update(task, description="Pipeline complete")

        console.print("[bold green]Pipeline complete[/bold green]")
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    except httpx.HTTPStatusError as exc:
        typer.secho(
            f"X API request failed: {exc.response.status_code} {exc.response.text}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from exc
    except APIError as exc:
        typer.secho(f"Anthropic API request failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc


@app.command()
def migrate() -> None:
    """Apply pending raw SQL migrations from the migrations/ directory."""
    ran = apply_pending_migrations()

    if not ran:
        typer.echo("Database already up to date.")
        return

    for migration in ran:
        typer.echo(f"Applied {migration.version}")


@app.command("migrate-status")
def migrate_status() -> None:
    """Show raw SQL migrations that have not been applied yet."""
    pending = pending_migrations()

    if not pending:
        typer.echo("No pending migrations.")
        return

    typer.echo("Pending migrations:")
    for migration in pending:
        typer.echo(f"- {migration.version}")


@app.command("schwab-login")
def schwab_login() -> None:
    """Run the one-time Schwab OAuth login and cache a refreshable token."""
    try:
        create_schwab_client_from_login()
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.echo(f"Schwab token ready at {settings.schwab_token_path}")


@app.command("ingest-prices")
def ingest_prices(
    days: Annotated[
        int,
        typer.Option(
            "--days",
            help="Number of recent calendar days of daily bars to fetch.",
        ),
    ] = 30,
    tickers: Annotated[
        str | None,
        typer.Option(
            "--tickers",
            help="Comma-separated tickers. Defaults to WATCHLIST from .env.",
        ),
    ] = None,
) -> None:
    """Fetch daily OHLCV price data from Schwab into the prices table."""
    if days < 1:
        raise typer.BadParameter("--days must be at least 1")

    ticker_list = (
        [ticker.strip().upper() for ticker in tickers.split(",") if ticker.strip()]
        if tickers
        else settings.watchlist_tickers
    )
    if not ticker_list:
        raise typer.BadParameter("No tickers configured. Set WATCHLIST or pass --tickers.")

    try:
        count = ingest_prices_job(ticker_list, days=days)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.echo(f"Upserted {count} price rows for {', '.join(ticker_list)}")


@app.command("x-login")
def x_login() -> None:
    """Run the one-time X OAuth login and cache a user token."""
    try:
        run_x_user_login()
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    except httpx.HTTPStatusError as exc:
        typer.secho(
            f"X OAuth token exchange failed: {exc.response.status_code} {exc.response.text}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from exc

    typer.echo(f"X user token ready at {settings.x_user_token_path}")


@app.command("ingest-posts")
def ingest_posts(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help=(
                "Post source: following for your reverse-chronological X following feed, "
                "search for public cashtag search. home is accepted as an alias for following."
            ),
        ),
    ] = "following",
    max_posts: Annotated[
        int,
        typer.Option(
            "--max-posts",
            help="Maximum number of recent X posts to store.",
        ),
    ] = 100,
    page_size: Annotated[
        int,
        typer.Option(
            "--page-size",
            help="X API results per request. X recent search accepts 10-100.",
        ),
    ] = 100,
    tickers: Annotated[
        str | None,
        typer.Option(
            "--tickers",
            help="Comma-separated cashtags. Defaults to WATCHLIST from .env.",
        ),
    ] = None,
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            help="Raw X recent-search query. Only used with --source search.",
        ),
    ] = None,
    include_replies: Annotated[
        bool,
        typer.Option(
            "--include-replies",
            help="Include replies from following-feed ingestion.",
        ),
    ] = False,
    include_retweets: Annotated[
        bool,
        typer.Option(
            "--include-retweets",
            help="Include retweets/reposts from following-feed ingestion.",
        ),
    ] = False,
) -> None:
    """Fetch X posts into authors/posts."""
    if max_posts < 1:
        raise typer.BadParameter("--max-posts must be at least 1")
    if page_size < 10 or page_size > 100:
        raise typer.BadParameter("--page-size must be between 10 and 100")
    if source not in {"following", "home", "search"}:
        raise typer.BadParameter("--source must be following, home, or search")

    ticker_list = (
        [ticker.strip().upper() for ticker in tickers.split(",") if ticker.strip()]
        if tickers
        else settings.watchlist_tickers
    )
    if source == "search" and not query and not ticker_list:
        raise typer.BadParameter(
            "No tickers configured. Set WATCHLIST, pass --tickers, or pass --query."
        )

    try:
        if source in {"following", "home"}:
            result = ingest_home_timeline_posts(
                max_posts=max_posts,
                page_size=page_size,
                exclude_replies=not include_replies,
                exclude_retweets=not include_retweets,
            )
        else:
            result = ingest_recent_posts(
                tickers=ticker_list,
                query=query,
                max_posts=max_posts,
                page_size=page_size,
            )
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    except httpx.HTTPStatusError as exc:
        typer.secho(
            f"X API request failed: {exc.response.status_code} {exc.response.text}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from exc

    typer.echo(f"Query: {result.query}")
    typer.echo(
        f"Upserted {result.posts_seen} posts and saw {result.authors_seen} authors "
        f"across {result.pages_fetched} page(s)."
    )


@app.command()
def enrich(
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            help="Maximum posts/pairs to process per enrichment stage.",
        ),
    ] = 50,
    ensure_prices: Annotated[
        bool,
        typer.Option(
            "--ensure-prices/--no-ensure-prices",
            help="Fetch missing Schwab daily bars for extracted tickers.",
        ),
    ] = True,
    price_days: Annotated[
        int,
        typer.Option(
            "--price-days",
            help="Calendar days after each post timestamp to ensure price coverage for.",
        ),
    ] = settings.enrichment_price_days,
) -> None:
    """Extract tickers and score sentiment for ingested posts. (Steps 4-5)"""
    if limit < 1:
        raise typer.BadParameter("--limit must be at least 1")
    if price_days < 1:
        raise typer.BadParameter("--price-days must be at least 1")

    try:
        with console.status("Qualifying posts and extracting tickers..."):
            ticker_result = qualify_and_extract_tickers(limit=limit)
        with console.status("Scoring sentiment..."):
            sentiment_result = score_missing_sentiments(limit=limit)

        price_result = None
        if ensure_prices:
            with console.status("Checking Schwab price coverage..."):
                price_result = ensure_price_bars_for_ticker_dates(
                    ticker_result.ticker_dates,
                    days=price_days,
                )
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    except APIError as exc:
        typer.secho(f"Anthropic API request failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    rows: list[tuple[str, object]] = [
        ("Qualified", f"{ticker_result.qualified}/{ticker_result.qualified_checked}"),
        ("Rejected", ticker_result.rejected),
        ("Ticker extraction posts", ticker_result.extraction_checked),
        ("Post ticker rows", ticker_result.ticker_rows_upserted),
        ("Sentiments", f"{sentiment_result.scored}/{sentiment_result.checked}"),
    ]
    if price_result is not None:
        rows.extend(
            [
                ("Price tickers checked", price_result.checked_tickers),
                ("Price tickers fetched", price_result.fetched_tickers),
                ("Price rows upserted", price_result.upserted_rows),
            ]
        )
    else:
        rows.append(("Prices", "skipped"))
    _print_kv_table("Enrichment summary", rows)


def _print_backtest_summary(result: BacktestResult) -> None:
    metrics = result.metrics
    _print_kv_table(
        "Backtest summary",
        [
            ("Run ID", result.run_id),
            ("Signal", result.signal.name),
            ("Horizon", f"{result.horizon} trading days"),
            ("Samples", f"{metrics['sample_count']} / {metrics['matched_candidates']} matched"),
            ("Missing prices", metrics["missing_price_candidates"]),
            ("Duplicates", metrics["duplicate_candidates"]),
            ("Avg return", metrics["avg_directional_return"]),
            ("Win rate", metrics["win_rate"]),
            ("Volatility", metrics["volatility"]),
            ("Sharpe-ish", metrics["simple_sharpe"]),
        ],
    )
    if metrics["tiny_sample"]:
        console.print(
            "[yellow]"
            f"Warning: tiny sample (< {result.min_samples}); do not trust this yet."
            "[/yellow]"
        )


@app.command()
def backtest(
    signal: Annotated[
        str,
        typer.Option(
            "--signal",
            help="Built-in signal name, e.g. positive_high or negative_high.",
        ),
    ],
    horizon: Annotated[
        int,
        typer.Option(
            "--horizon",
            help="Trading-day horizon after entry close.",
        ),
    ] = 5,
    min_samples: Annotated[
        int,
        typer.Option(
            "--min-samples",
            help="Sample count below which the run is flagged as tiny.",
        ),
    ] = 30,
) -> None:
    """Run a signal backtest over price history. (Step 6)"""
    if horizon < 1:
        raise typer.BadParameter("--horizon must be at least 1")
    if min_samples < 1:
        raise typer.BadParameter("--min-samples must be at least 1")

    try:
        with console.status(f"Backtesting {signal}..."):
            result = run_backtest(
                signal_name=signal,
                horizon=horizon,
                min_samples=min_samples,
            )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    _print_backtest_summary(result)


@app.command()
def serve() -> None:
    """Run the FastAPI server. (Step 7)"""
    typer.echo("Not implemented yet — see documentation/plan.md, Step 7.")


if __name__ == "__main__":
    app()
