"""Helpers for Claude calls that must return structured JSON."""

from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic

from xmarket.config import settings


class LLMResponseError(RuntimeError):
    """Raised when a model response cannot be parsed into the expected JSON."""


def create_anthropic_client() -> Anthropic:
    """Create an Anthropic client after validating local config."""
    if not settings.anthropic_api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY. Add it to .env first.")
    return Anthropic(api_key=settings.anthropic_api_key)


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from plain text or a fenced model response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMResponseError(f"Model did not return a JSON object: {text[:200]}") from None
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"Model returned invalid JSON: {text[:200]}") from exc

    if not isinstance(value, dict):
        raise LLMResponseError("Model JSON response must be an object.")
    return value


def message_text(response: Any) -> str:
    """Collect text blocks from an Anthropic Messages API response."""
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise LLMResponseError("Model returned no text content.")
    return text


def create_json_message(
    client: Anthropic,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
) -> dict[str, Any]:
    """Call Claude and parse the text response as a JSON object."""
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return extract_json_object(message_text(response))
