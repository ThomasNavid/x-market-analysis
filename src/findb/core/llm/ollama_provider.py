"""Ollama (local) JSON chat client via the OpenAI-compatible API."""

from __future__ import annotations

from typing import Any

import httpx

from findb.core.llm.base import LLMError, LLMResponseError
from findb.core.llm.json_utils import extract_json_object


class OllamaJSONClient:
    """JSONChatClient backed by Ollama's OpenAI-compatible chat endpoint."""

    def __init__(self, base_url: str, *, client: httpx.Client | None = None) -> None:
        if client is None and not base_url.strip():
            raise LLMError("Missing OLLAMA_BASE_URL. Add it to .env first.")
        self._client = client or httpx.Client(base_url=base_url, timeout=120.0)

    def create_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Call Ollama's /v1/chat/completions and parse the reply as JSON."""
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = self._client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"Ollama API request failed: {exc.response.status_code} {exc.response.text[:500]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama API request failed: {exc}") from exc

        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                f"Ollama returned an unexpected response shape: {str(body)[:200]}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("Model returned no text content.")
        return extract_json_object(content)
