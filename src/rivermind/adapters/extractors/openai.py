"""OpenAI adapter for :class:`NarrativeSynthesizer`.

The ``openai`` SDK is imported lazily. Install it explicitly
(``pip install openai``) or via the packaging extra
(``pip install rivermind[openai]``) before using this adapter at runtime.
"""

from __future__ import annotations

import os

_DEFAULT_MODEL = "gpt-4o"
_ENV_KEY = "RIVERMIND_API_KEY"


class OpenAISynthesizer:
    """Calls OpenAI's Chat Completions API to synthesize narrative text."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        try:
            # Lazy import: users only install the SDK if they opt in.
            import openai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for OpenAISynthesizer. "
                "Install with: pip install openai"
            ) from exc

        resolved_key = api_key or os.environ.get(_ENV_KEY)
        if resolved_key is None:
            raise ValueError(
                f"OpenAISynthesizer requires an api_key argument or the "
                f"{_ENV_KEY} environment variable to be set."
            )
        self._client = openai.OpenAI(api_key=resolved_key)
        self._model = model

    def synthesize(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
