"""Shared types for the provider-agnostic LLM JSON-completion layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

KNOWN_PROVIDERS = frozenset({"anthropic", "ollama"})


class LLMError(RuntimeError):
    """Raised for LLM configuration or transport failures."""


class LLMResponseError(LLMError):
    """Raised when a model response cannot be parsed into the expected JSON."""


@dataclass(frozen=True)
class ModelRef:
    """A parsed `provider:model` reference."""

    provider: str
    model: str

    @classmethod
    def parse(cls, ref: str) -> ModelRef:
        """Parse a model reference string into provider and model parts.

        Examples:
            "anthropic:claude-haiku-4-5" -> ("anthropic", "claude-haiku-4-5")
            "ollama:qwen3:14b"           -> ("ollama", "qwen3:14b")
            "claude-haiku-4-5"           -> ("anthropic", "claude-haiku-4-5")

        Bare names (or names whose prefix is not a known provider) default to
        anthropic for backwards compatibility.
        """
        cleaned = ref.strip()
        if not cleaned:
            raise LLMError(
                "Empty model reference. Use 'provider:model', e.g. "
                "'anthropic:claude-haiku-4-5' or 'ollama:qwen3:14b'."
            )

        provider, sep, model = cleaned.partition(":")
        if sep and provider in KNOWN_PROVIDERS:
            if not model.strip():
                raise LLMError(
                    f"Model reference '{cleaned}' is missing the model name. "
                    "Use 'provider:model', e.g. 'anthropic:claude-haiku-4-5'."
                )
            return cls(provider=provider, model=model.strip())

        # Bare model name (or unknown prefix): default to anthropic.
        return cls(provider="anthropic", model=cleaned)


class JSONChatClient(Protocol):
    """A provider client that returns a JSON object for a system+user chat."""

    def create_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> dict[str, Any]: ...
