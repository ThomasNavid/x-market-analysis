"""X API v2 clients and OAuth helpers."""

import base64
import hashlib
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from xmarket.config import settings

API_BASE_URL = "https://api.x.com"
RECENT_SEARCH_URL = f"{API_BASE_URL}/2/tweets/search/recent"
AUTHORIZATION_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = f"{API_BASE_URL}/2/oauth2/token"
TWEET_FIELDS = "author_id,created_at,lang,public_metrics"
USER_FIELDS = "username,public_metrics,verified"
EXPANSIONS = "author_id"
X_USER_SCOPES = ["tweet.read", "users.read", "offline.access"]


def validate_x_search_config() -> None:
    """Fail early if app-only X search settings are missing."""
    if not settings.x_bearer_token:
        raise RuntimeError("Missing X config: X_BEARER_TOKEN. Add it to .env first.")


def validate_x_user_config() -> None:
    """Fail early if X OAuth user settings are missing."""
    missing = []
    if not settings.x_client_id:
        missing.append("X_CLIENT_ID")
    if not settings.x_redirect_uri:
        missing.append("X_REDIRECT_URI")

    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing X user auth config: {joined}. Add it to .env first.")


def build_cashtag_query(tickers: list[str], *, language: str | None = "en") -> str:
    """Build a recent-search query for stock cashtags."""
    if not tickers:
        raise ValueError("At least one ticker is required")

    cashtags = " OR ".join(f"${ticker.upper()}" for ticker in tickers)
    parts = [f"({cashtags})", "-is:retweet"]
    if language:
        parts.append(f"lang:{language}")

    return " ".join(parts)


def _token_path() -> Path:
    return Path(settings.x_user_token_path)


def _save_x_user_token(token: dict[str, Any]) -> None:
    _token_path().write_text(json.dumps(token, indent=2), encoding="utf-8")


def _load_x_user_token() -> dict[str, Any]:
    token_path = _token_path()
    if not token_path.exists():
        raise RuntimeError(
            f"X user token file not found at {token_path}. Run `uv run xmarket x-login` first."
        )

    return cast(dict[str, Any], json.loads(token_path.read_text(encoding="utf-8")))


def _new_pkce_pair() -> tuple[str, str]:
    """Create a PKCE verifier/challenge pair for OAuth 2.0."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


def _build_authorization_url() -> tuple[str, str, str]:
    code_verifier, code_challenge = _new_pkce_pair()
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": settings.x_client_id,
        "redirect_uri": settings.x_redirect_uri,
        "scope": " ".join(X_USER_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZATION_URL}?{urlencode(params)}", state, code_verifier


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: "_OAuthCallbackServer"

    def do_GET(self) -> None:
        self.server.callback_url = f"http://{self.headers['Host']}{self.path}"

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "error" in params:
            message = f"X OAuth failed: {params['error'][0]}"
            self.send_response(400)
        else:
            message = "X OAuth complete. You can close this browser tab."
            self.send_response(200)

        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


class _OAuthCallbackServer(HTTPServer):
    callback_url: str | None = None


def _listen_for_oauth_callback(timeout_seconds: int) -> str:
    parsed = urlparse(settings.x_redirect_uri)
    if parsed.hostname is None or parsed.port is None:
        raise RuntimeError(
            "X_REDIRECT_URI must include a host and port, "
            "e.g. http://127.0.0.1:8001/x/callback"
        )

    server = _OAuthCallbackServer((parsed.hostname, parsed.port), _OAuthCallbackHandler)
    timer = threading.Timer(timeout_seconds, server.shutdown)
    timer.start()
    try:
        server.handle_request()
    finally:
        timer.cancel()
        server.server_close()

    if not server.callback_url:
        raise RuntimeError("Timed out waiting for X OAuth callback.")

    return server.callback_url


def _oauth_code_from_callback(callback_url: str, *, expected_state: str) -> str:
    params = parse_qs(urlparse(callback_url).query)
    if "error" in params:
        raise RuntimeError(f"X OAuth failed: {params['error'][0]}")
    if params.get("state", [None])[0] != expected_state:
        raise RuntimeError("X OAuth state mismatch. Run `uv run xmarket x-login` again.")
    if "code" not in params:
        raise RuntimeError("X OAuth callback did not include an authorization code.")

    return params["code"][0]


def run_x_user_login(*, timeout_seconds: int = 300) -> None:
    """Run OAuth 2.0 Authorization Code + PKCE and cache the X user token."""
    validate_x_user_config()
    authorization_url, state, code_verifier = _build_authorization_url()

    webbrowser.open(authorization_url)
    callback_url = _listen_for_oauth_callback(timeout_seconds)
    code = _oauth_code_from_callback(callback_url, expected_state=state)

    data = {
        "grant_type": "authorization_code",
        "client_id": settings.x_client_id,
        "code": code,
        "redirect_uri": settings.x_redirect_uri,
        "code_verifier": code_verifier,
    }
    auth: tuple[str, str] | None = None
    if settings.x_client_secret:
        auth = (settings.x_client_id, settings.x_client_secret)

    response = httpx.post(TOKEN_URL, data=data, auth=auth, timeout=30.0)
    response.raise_for_status()
    _save_x_user_token(cast(dict[str, Any], response.json()))


def refresh_x_user_token(token: dict[str, Any]) -> dict[str, Any]:
    """Refresh and persist the cached X user token."""
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "Cached X token has no refresh_token. Run `uv run xmarket x-login` again."
        )

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.x_client_id,
    }
    auth: tuple[str, str] | None = None
    if settings.x_client_secret:
        auth = (settings.x_client_id, settings.x_client_secret)

    response = httpx.post(TOKEN_URL, data=data, auth=auth, timeout=30.0)
    response.raise_for_status()
    refreshed = cast(dict[str, Any], response.json())
    _save_x_user_token(refreshed)
    return refreshed


class XBearerClient:
    """Thin wrapper around app-only X API v2 recent search."""

    def __init__(self, bearer_token: str) -> None:
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def search_recent_posts(
        self,
        query: str,
        *,
        max_results: int,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        """Fetch one page from the recent-search endpoint."""
        params: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "tweet.fields": TWEET_FIELDS,
            "expansions": EXPANSIONS,
            "user.fields": USER_FIELDS,
        }
        if next_token:
            params["next_token"] = next_token

        response = self._client.get(RECENT_SEARCH_URL, params=params)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())


class XUserClient:
    """X API v2 client authenticated as the local user."""

    def __init__(self, token: dict[str, Any]) -> None:
        self._token = token
        self._client = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._client.close()

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token['access_token']}"}

    def _get(self, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        response = self._client.get(url, headers=self._headers, params=params)
        if response.status_code == 401:
            self._token = refresh_x_user_token(self._token)
            response = self._client.get(url, headers=self._headers, params=params)

        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def get_me(self) -> dict[str, Any]:
        """Fetch the authenticated X user."""
        return self._get(
            f"{API_BASE_URL}/2/users/me",
            params={"user.fields": USER_FIELDS},
        )

    def get_home_timeline(
        self,
        user_id: str,
        *,
        max_results: int,
        pagination_token: str | None = None,
        exclude: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one page of the authenticated user's following feed.

        X names this API surface the reverse-chronological home timeline.
        """
        params: dict[str, Any] = {
            "max_results": max_results,
            "tweet.fields": TWEET_FIELDS,
            "expansions": EXPANSIONS,
            "user.fields": USER_FIELDS,
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        if exclude:
            params["exclude"] = ",".join(exclude)

        return self._get(
            f"{API_BASE_URL}/2/users/{user_id}/timelines/reverse_chronological",
            params=params,
        )


def create_x_bearer_client() -> XBearerClient:
    """Create an app-only X API client from settings."""
    validate_x_search_config()
    return XBearerClient(settings.x_bearer_token)


def create_x_user_client() -> XUserClient:
    """Create a user-authenticated X API client from the cached token."""
    validate_x_user_config()
    return XUserClient(_load_x_user_token())
