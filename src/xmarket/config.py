"""Typed application configuration, loaded from environment / `.env`.

Import the shared `settings` object anywhere you need config:

    from xmarket.config import settings
    print(settings.database_url)
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration in one typed, validated place."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql://xmarket:xmarket@localhost:5432/xmarket"

    # Charles Schwab Trader API (price/market data)
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_callback_url: str = "https://127.0.0.1:8182"
    schwab_token_path: str = ".schwab_token.json"

    # X (Twitter) API
    x_bearer_token: str = ""
    x_client_id: str = ""
    x_client_secret: str = ""
    x_redirect_uri: str = "http://127.0.0.1:8001/x/callback"
    x_user_token_path: str = ".x_user_token.json"

    # Anthropic / enrichment
    anthropic_api_key: str = ""
    qualify_model: str = "claude-haiku-4-5"
    sentiment_model: str = "claude-haiku-4-5"
    qualify_prompt_version: str = "qualify-v1"
    ticker_prompt_version: str = "ticker-v1"
    sentiment_prompt_version: str = "sentiment-v1"
    enrichment_price_days: int = 30

    # Application
    watchlist: str = "AAPL,TSLA,NVDA,MSFT,AMZN"
    log_level: str = "INFO"

    @property
    def watchlist_tickers(self) -> list[str]:
        """Watchlist as a clean, upper-cased list of tickers."""
        return [t.strip().upper() for t in self.watchlist.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    """Build settings once and cache them for the process lifetime."""
    return Settings()


# Convenience singleton for everyday imports.
settings = get_settings()
