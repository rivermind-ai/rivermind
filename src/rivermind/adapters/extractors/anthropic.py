"""Anthropic adapter for :class:`NarrativeSynthesizer`.

The ``anthropic`` SDK is imported lazily. Install it explicitly
(``pip install anthropic``) or via the packaging extra
(``pip install rivermind[anthropic]``) before using this adapter at
runtime.
"""

from __future__ import annotations

import os

_DEFAULT_MODEL = "claude-sonnet-4-6"
_ENV_KEY = "RIVERMIND_API_KEY"
_MAX_TOKENS = 2048


class AnthropicSynthesizer:
    """Calls Anthropic's Messages API to synthesize narrative text."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        try:
            # Lazy import: users only install the SDK if they opt in.
            import anthropic  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for AnthropicSynthesizer. "
                "Install with: pip install anthropic"
            ) from exc

        resolved_key = api_key or os.environ.get(_ENV_KEY)
        if resolved_key is None:
            raise ValueError(
                f"AnthropicSynthesizer requires an api_key argument or the "
                f"{_ENV_KEY} environment variable to be set."
            )
        self._client = anthropic.Anthropic(api_key=resolved_key)
        self._model = model

    def synthesize(self, prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        # The first block is a TextBlock for a plain user prompt like ours;
        # narrow at runtime rather than importing anthropic.types here.
        block = response.content[0]
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            raise RuntimeError(
                f"Anthropic response did not include text; got block type {type(block).__name__}"
            )
        return text
