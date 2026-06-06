"""Command-line entrypoint: `xmarket <command>`."""

from typing import Annotated

import typer

from xmarket.config import settings
from xmarket.db.migrations import apply_pending_migrations, pending_migrations
from xmarket.ingest.prices import create_schwab_client_from_login
from xmarket.ingest.prices import ingest_prices as ingest_prices_job

app = typer.Typer(help="x-market-analysis command-line interface.")


@app.command()
def info() -> None:
    """Show the current configuration (sanity check that .env loads)."""
    typer.echo(f"Watchlist : {', '.join(settings.watchlist_tickers)}")
    typer.echo(f"DB        : {settings.database_url}")
    typer.echo(f"Model     : {settings.sentiment_model}")
    typer.echo(f"Schwab    : {'set' if settings.schwab_app_key else 'MISSING'}")
    typer.echo(f"X token   : {'set' if settings.x_bearer_token else 'MISSING'}")
    typer.echo(f"Anthropic : {'set' if settings.anthropic_api_key else 'MISSING'}")


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


@app.command("ingest-posts")
def ingest_posts() -> None:
    """Fetch X posts mentioning the watchlist. (Step 3)"""
    typer.echo("Not implemented yet — see documentation/plan.md, Step 3.")


@app.command()
def enrich() -> None:
    """Extract tickers and score sentiment for ingested posts. (Steps 4-5)"""
    typer.echo("Not implemented yet — see documentation/plan.md, Steps 4-5.")


@app.command()
def backtest() -> None:
    """Run a signal backtest over price history. (Step 6)"""
    typer.echo("Not implemented yet — see documentation/plan.md, Step 6.")


@app.command()
def serve() -> None:
    """Run the FastAPI server. (Step 7)"""
    typer.echo("Not implemented yet — see documentation/plan.md, Step 7.")


if __name__ == "__main__":
    app()
