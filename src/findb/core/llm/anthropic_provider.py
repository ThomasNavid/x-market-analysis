"""Anthropic (Claude) JSON chat client."""

from __future__ import annotations

from typing import Any

import anthropic

from findb.config import settings
from findb.core.llm.base import LLMError, LLMResponseError
from findb.core.llm.json_utils import extract_json_object


def _message_text(response: Any) -> str:
    """Collect text blocks from an Anthropic Messages API response."""
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise LLMResponseError("Model returned no text content.")
    return text


class AnthropicJSONClient:
    """JSONChatClient backed by the Anthropic Messages API."""

    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None

    def _sdk_client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not settings.anthropic_api_key:
                raise LLMError("Missing ANTHROPIC_API_KEY. Add it to .env first.")
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    def create_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Call Claude and parse the text response as a JSON object."""
        client = self._sdk_client()
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=0,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API request failed: {exc}") from exc
        return extract_json_object(_message_text(response))
