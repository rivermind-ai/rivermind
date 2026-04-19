"""Narrative synthesis projector.

Reads observations in a time window, renders them into a prompt, calls a
:class:`NarrativeSynthesizer`, saves the result as a :class:`Narrative`,
and marks any prior narrative for the same exact period + topic as
superseded.

Gated on the ``RIVERMIND_API_KEY`` environment variable. When unset,
:func:`synthesize_narrative` logs a warning and returns ``None``; no
synthesizer is constructed, no API call is made, and no narrative is
written. This is how the "optional, API-key gated" contract is enforced.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from rivermind.core.ids import new_narrative_id
from rivermind.core.models import Narrative

if TYPE_CHECKING:
    from datetime import datetime

    from rivermind.core.interfaces import MemoryStore, NarrativeSynthesizer
    from rivermind.core.models import Observation

_logger = structlog.get_logger()

_ENV_KEY = "RIVERMIND_API_KEY"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "narrative.md"


def _load_prompt_template() -> str:
    return _PROMPT_PATH.read_text()


def _render_observations(observations: list[Observation]) -> str:
    lines: list[str] = []
    for obs in observations:
        header = f"- [{obs.observed_at.isoformat()}] ({obs.kind.value})"
        if obs.subject and obs.attribute:
            header += f" {obs.subject}.{obs.attribute}"
            if obs.value is not None:
                header += f" = {obs.value!r}"
        lines.append(f"{header}: {obs.content}")
    return "\n".join(lines)


def _render_prompt(
    period_start: datetime,
    period_end: datetime,
    topic: str | None,
    observations: list[Observation],
) -> str:
    return _load_prompt_template().format(
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        topic=topic or "(none)",
        observations=_render_observations(observations) or "(no observations in period)",
    )


def _find_prior_exact_match(
    store: MemoryStore,
    period_start: datetime,
    period_end: datetime,
    topic: str | None,
) -> Narrative | None:
    matches = store.get_narratives(period_start, period_end, topic, include_superseded=False)
    for n in matches:
        if n.period_start == period_start and n.period_end == period_end and n.topic == topic:
            return n
    return None


def synthesize_narrative(
    period_start: datetime,
    period_end: datetime,
    topic: str | None,
    store: MemoryStore,
    synthesizer: NarrativeSynthesizer | None = None,
) -> Narrative | None:
    """Synthesize and persist a narrative for ``[period_start, period_end]``.

    Returns ``None`` when the feature is disabled (env var unset, no
    synthesizer provided, or no observations in the period). Otherwise
    returns the persisted :class:`Narrative`. If a prior narrative exists
    for the same exact period and topic, it is marked superseded; the
    original row is never mutated in place.
    """
    if os.environ.get(_ENV_KEY) is None:
        _logger.warning(
            "narrative_synthesis_skipped",
            reason="RIVERMIND_API_KEY not set; narrative synthesis is disabled",
        )
        return None
    if synthesizer is None:
        _logger.warning(
            "narrative_synthesis_skipped",
            reason="no synthesizer provided",
        )
        return None

    observations = store.get_observations(period_start, period_end)
    if not observations:
        _logger.info(
            "narrative_synthesis_skipped",
            reason="no observations in period",
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
        )
        return None

    prompt = _render_prompt(period_start, period_end, topic, observations)
    content = synthesizer.synthesize(prompt)

    prior = _find_prior_exact_match(store, period_start, period_end, topic)

    narrative = Narrative(
        id=new_narrative_id(),
        content=content,
        topic=topic,
        period_start=period_start,
        period_end=period_end,
        source_observations=[o.id for o in observations],
    )
    store.save_narrative(narrative)

    if prior is not None:
        store.mark_narrative_superseded(prior.id, narrative.id)

    return narrative
