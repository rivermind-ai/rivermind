"""Unit tests for ``rivermind.adapters.extractors.openai``.

Patches the ``openai.OpenAI`` client so tests never make network calls
and don't need a real API key beyond a dummy string.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rivermind.adapters.extractors.openai import OpenAISynthesizer
from rivermind.core.interfaces import NarrativeSynthesizer


def _mock_response(text: str) -> MagicMock:
    message = MagicMock()
    message.content = text
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def test_requires_key_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RIVERMIND_KEY", raising=False)
    with pytest.raises(ValueError, match="RIVERMIND_KEY"):
        OpenAISynthesizer()


def test_accepts_explicit_api_key() -> None:
    with patch("openai.OpenAI") as client_cls:
        synth = OpenAISynthesizer(api_key="sk-test")
    client_cls.assert_called_once_with(api_key="sk-test")
    assert isinstance(synth, NarrativeSynthesizer)


def test_falls_back_to_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIVERMIND_KEY", "sk-from-env")
    with patch("openai.OpenAI") as client_cls:
        OpenAISynthesizer()
    client_cls.assert_called_once_with(api_key="sk-from-env")


def test_synthesize_calls_chat_completions_and_returns_text() -> None:
    with patch("openai.OpenAI") as client_cls:
        client = client_cls.return_value
        client.chat.completions.create.return_value = _mock_response("the narrative body")
        synth = OpenAISynthesizer(api_key="sk-test", model="gpt-test")
        out = synth.synthesize("hello prompt")

    assert out == "the narrative body"
    assert client.chat.completions.create.call_count == 1
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-test"
    assert kwargs["messages"] == [{"role": "user", "content": "hello prompt"}]


def test_synthesize_returns_empty_string_when_content_is_none() -> None:
    with patch("openai.OpenAI") as client_cls:
        client = client_cls.return_value
        client.chat.completions.create.return_value = _mock_response(None)
        synth = OpenAISynthesizer(api_key="sk-test")
        assert synth.synthesize("x") == ""
