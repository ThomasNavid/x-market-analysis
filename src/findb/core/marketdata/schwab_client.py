"""Charles Schwab API client factories (OAuth login + cached token)."""

from pathlib import Path
from typing import Any

from schwab import auth

from findb.config import settings


def validate_schwab_config() -> None:
    """Fail early if required Schwab settings are missing."""
    missing = []
    if not settings.schwab_app_key:
        missing.append("SCHWAB_APP_KEY")
    if not settings.schwab_app_secret:
        missing.append("SCHWAB_APP_SECRET")

    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing Schwab config: {joined}. Add it to .env first.")


def create_schwab_client_from_login() -> Any:
    """Run Schwab OAuth if needed and return an authenticated client."""
    validate_schwab_config()
    return auth.easy_client(
        api_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
        callback_url=settings.schwab_callback_url,
        token_path=settings.schwab_token_path,
    )


def create_schwab_client_from_token() -> Any:
    """Load an authenticated Schwab client from the cached token file."""
    validate_schwab_config()
    token_path = Path(settings.schwab_token_path)
    if not token_path.exists():
        raise RuntimeError(
            f"Schwab token file not found at {token_path}. Run `uv run findb schwab-login` first."
        )

    return auth.client_from_token_file(
        token_path=str(token_path),
        api_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
    )
