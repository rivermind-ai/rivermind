"""Unit tests for ``rivermind.adapters.extractors.anthropic``.

Patches the ``anthropic.Anthropic`` client so tests never make network
calls and don't need a real API key beyond a dummy string.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rivermind.adapters.extractors.anthropic import AnthropicSynthesizer
from rivermind.core.interfaces import NarrativeSynthesizer


def _mock_response(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def test_requires_key_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RIVERMIND_KEY", raising=False)
    with pytest.raises(ValueError, match="RIVERMIND_KEY"):
        AnthropicSynthesizer()


def test_accepts_explicit_api_key() -> None:
    with patch("anthropic.Anthropic") as client_cls:
        synth = AnthropicSynthesizer(api_key="sk-test")
    client_cls.assert_called_once_with(api_key="sk-test")
    assert isinstance(synth, NarrativeSynthesizer)


def test_falls_back_to_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIVERMIND_KEY", "sk-from-env")
    with patch("anthropic.Anthropic") as client_cls:
        AnthropicSynthesizer()
    client_cls.assert_called_once_with(api_key="sk-from-env")


def test_synthesize_calls_messages_api_and_returns_text() -> None:
    with patch("anthropic.Anthropic") as client_cls:
        client = client_cls.return_value
        client.messages.create.return_value = _mock_response("the narrative body")
        synth = AnthropicSynthesizer(api_key="sk-test", model="claude-test")
        out = synth.synthesize("hello prompt")

    assert out == "the narrative body"
    assert client.messages.create.call_count == 1
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-test"
    assert kwargs["messages"] == [{"role": "user", "content": "hello prompt"}]
    assert isinstance(kwargs["max_tokens"], int)
    assert kwargs["max_tokens"] > 0
