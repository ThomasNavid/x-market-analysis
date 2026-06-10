"""Unit tests for the provider-agnostic LLM JSON-completion layer."""

import json
from typing import Any

import httpx
import pytest

from findb.core.llm import LLMError, ModelRef, create_json_completion
from findb.core.llm import router as llm_router
from findb.core.llm.ollama_provider import OllamaJSONClient

# --- ModelRef.parse -------------------------------------------------------


def test_parse_bare_name_defaults_to_anthropic() -> None:
    assert ModelRef.parse("claude-haiku-4-5") == ModelRef(
        provider="anthropic", model="claude-haiku-4-5"
    )


def test_parse_explicit_anthropic_ref() -> None:
    assert ModelRef.parse("anthropic:claude-haiku-4-5") == ModelRef(
        provider="anthropic", model="claude-haiku-4-5"
    )


def test_parse_ollama_ref_keeps_embedded_colon() -> None:
    assert ModelRef.parse("ollama:qwen3:14b") == ModelRef(provider="ollama", model="qwen3:14b")


def test_parse_empty_ref_raises() -> None:
    with pytest.raises(LLMError):
        ModelRef.parse("")


def test_parse_empty_model_part_raises() -> None:
    with pytest.raises(LLMError):
        ModelRef.parse("anthropic:")


def test_parse_unknown_provider_falls_back_to_anthropic() -> None:
    # Back-compat tradeoff: an unknown prefix is treated as part of a bare
    # model name under anthropic. Anthropic model ids never contain ":", so
    # the Anthropic API rejects these cleanly.
    assert ModelRef.parse("openai:gpt-4") == ModelRef(provider="anthropic", model="openai:gpt-4")


# --- OllamaJSONClient -----------------------------------------------------


def _ollama_client_with_content(
    content: str,
    captured: list[httpx.Request],
) -> OllamaJSONClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        )

    http_client = httpx.Client(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    return OllamaJSONClient("http://ollama.test", client=http_client)


def test_ollama_client_parses_json_and_sends_expected_request() -> None:
    captured: list[httpx.Request] = []
    client = _ollama_client_with_content('{"qualified": true, "reason": "demand"}', captured)

    payload = client.create_json(
        model="qwen3:14b",
        system="You return JSON.",
        user="Is this qualified?",
        max_tokens=300,
    )

    assert payload == {"qualified": True, "reason": "demand"}
    assert len(captured) == 1
    request = captured[0]
    assert request.url.path == "/v1/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == "qwen3:14b"
    assert body["temperature"] == 0
    assert body["max_tokens"] == 300
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [
        {"role": "system", "content": "You return JSON."},
        {"role": "user", "content": "Is this qualified?"},
    ]


def test_ollama_client_accepts_fenced_json_content() -> None:
    captured: list[httpx.Request] = []
    client = _ollama_client_with_content('```json\n{"ticker": "AAPL"}\n```', captured)

    payload = client.create_json(model="qwen3:14b", system="s", user="u", max_tokens=100)
    assert payload == {"ticker": "AAPL"}


def test_ollama_client_wraps_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="model not found")

    http_client = httpx.Client(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaJSONClient("http://ollama.test", client=http_client)

    with pytest.raises(LLMError, match="500"):
        client.create_json(model="qwen3:14b", system="s", user="u", max_tokens=100)


def test_ollama_client_requires_base_url() -> None:
    with pytest.raises(LLMError, match="OLLAMA_BASE_URL"):
        OllamaJSONClient("")


# --- Router ---------------------------------------------------------------


class _StubClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def create_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        self.calls.append(
            {"model": model, "system": system, "user": user, "max_tokens": max_tokens}
        )
        return self.payload


def test_router_dispatches_by_provider_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    anthropic_stub = _StubClient({"from": "anthropic"})
    ollama_stub = _StubClient({"from": "ollama"})
    monkeypatch.setattr(
        llm_router, "_clients", {"anthropic": anthropic_stub, "ollama": ollama_stub}
    )

    assert create_json_completion(
        "anthropic:claude-haiku-4-5", system="s", user="u", max_tokens=10
    ) == {"from": "anthropic"}
    assert create_json_completion("ollama:qwen3:14b", system="s", user="u", max_tokens=20) == {
        "from": "ollama"
    }

    assert anthropic_stub.calls == [
        {"model": "claude-haiku-4-5", "system": "s", "user": "u", "max_tokens": 10}
    ]
    assert ollama_stub.calls == [
        {"model": "qwen3:14b", "system": "s", "user": "u", "max_tokens": 20}
    ]


def test_router_bare_model_name_uses_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    anthropic_stub = _StubClient({"ok": True})
    monkeypatch.setattr(llm_router, "_clients", {"anthropic": anthropic_stub})

    assert create_json_completion("claude-haiku-4-5", system="s", user="u", max_tokens=5) == {
        "ok": True
    }
    assert anthropic_stub.calls[0]["model"] == "claude-haiku-4-5"
