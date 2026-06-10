"""Route JSON-completion requests to the configured LLM provider."""

from __future__ import annotations

from typing import Any

from findb.config import settings
from findb.core.llm.base import JSONChatClient, LLMError, ModelRef

_clients: dict[str, JSONChatClient] = {}


def _get_client(provider: str) -> JSONChatClient:
    """Return (lazily building) the singleton client for one provider."""
    cached = _clients.get(provider)
    if cached is not None:
        return cached

    client: JSONChatClient
    if provider == "anthropic":
        from findb.core.llm.anthropic_provider import AnthropicJSONClient

        client = AnthropicJSONClient()
    elif provider == "ollama":
        from findb.core.llm.ollama_provider import OllamaJSONClient

        client = OllamaJSONClient(settings.ollama_base_url)
    else:
        raise LLMError(f"Unknown LLM provider '{provider}'. Known: anthropic, ollama.")

    _clients[provider] = client
    return client


def create_json_completion(
    model_ref: str,
    *,
    system: str,
    user: str,
    max_tokens: int,
) -> dict[str, Any]:
    """Parse a `provider:model` ref, dispatch to its provider, return parsed JSON."""
    ref = ModelRef.parse(model_ref)
    client = _get_client(ref.provider)
    return client.create_json(
        model=ref.model,
        system=system,
        user=user,
        max_tokens=max_tokens,
    )
