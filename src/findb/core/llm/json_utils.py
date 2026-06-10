"""Parsing helpers for LLM responses that must contain JSON objects."""

from __future__ import annotations

import json
from typing import Any

from findb.core.llm.base import LLMResponseError


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
