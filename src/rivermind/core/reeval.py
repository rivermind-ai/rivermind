"""Re-eval pipeline run at server startup.

Catches up overdue work when the server comes online: narrative synthesis for
each completed ISO week that has observations and has not yet been re-evaled,
followed by a compaction pass and a state rebuild sanity check. Idempotent
across restarts via the ``reeval_runs`` audit table: a period is "done" once
its row is inserted, regardless of whether synthesis succeeded, was skipped,
or errored.

Compaction is currently a stub; a later change replaces the body.

Partial failures in one phase do not prevent the next. Exceptions per-phase
and per-week are caught, logged, and appended to the summary warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from rivermind.core.projectors.compaction import compact
from rivermind.core.projectors.narrative import synthesize_narrative
from rivermind.core.projectors.state import rebuild_state

if TYPE_CHECKING:
    from rivermind.core.interfaces import MemoryStore, NarrativeSynthesizer

_logger = structlog.get_logger()

_EPOCH = datetime.min.replace(tzinfo=UTC)
_WEEK = timedelta(days=7)
_MICRO = timedelta(microseconds=1)


def _now() -> datetime:
    """Return the current moment. Separated so tests can monkeypatch it."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class ReevalSummary:
    """Return value from :func:`run_reeval`."""

    weeks_processed: int
    narratives_written: int
    warnings: list[str] = field(default_factory=list)


def _iso_week_bounds(moment: datetime) -> tuple[datetime, datetime]:
    """Return ``(monday_start, sunday_end)`` in UTC for ``moment``'s ISO week.

    The end bound is inclusive: the last representable microsecond before the
    next Monday 00:00 UTC. Both bounds are tz-aware UTC regardless of the
    caller's timezone.
    """
    moment_utc = moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
    day = moment_utc.date()
    monday = day - timedelta(days=day.weekday())
    start = datetime(monday.year, monday.month, monday.day, tzinfo=UTC)
    end = start + _WEEK - _MICRO
    return start, end


def _weeks_needing_reeval(
    store: MemoryStore,
    now: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return the list of overdue ``(period_start, period_end)`` pairs.

    A week is overdue when it is in the past (strictly before the current
    ISO week), contains at least one observation, and has no ``reeval_runs``
    row. Results are ordered by ``period_start`` ascending.
    """
    observations = store.get_observations(_EPOCH, now)
    if not observations:
        return []

    current_week_start, _ = _iso_week_bounds(now)
    weeks: set[tuple[datetime, datetime]] = set()
    for obs in observations:
        week_start, week_end = _iso_week_bounds(obs.observed_at)
        if week_start >= current_week_start:
            continue
        weeks.add((week_start, week_end))

    return [pair for pair in sorted(weeks) if not store.reeval_exists(*pair)]


def _silent_progress(_done: int, _total: int) -> None:
    """Progress callback for :func:`rebuild_state` when run inside re-eval."""


def run_reeval(
    store: MemoryStore,
    *,
    synthesizer: NarrativeSynthesizer | None = None,
    now: datetime | None = None,
) -> ReevalSummary:
    """Run the three-phase re-eval pipeline.

    Phase 1: for each overdue week, attempt narrative synthesis (no-op when
    ``synthesizer`` is None) and record a ``reeval_runs`` row regardless of
    synthesis outcome. Phase 2: compaction. Phase 3: state rebuild.

    One phase failing does not stop the next. Per-week synthesis errors are
    caught and appended to warnings; the surrounding phase loop continues.
    """
    now = now or _now()
    log = _logger.bind(component="reeval")
    log.info("reeval_start")

    weeks = _weeks_needing_reeval(store, now)
    warnings: list[str] = []
    narratives_written = 0

    for period_start, period_end in weeks:
        week_log = log.bind(
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
        )
        week_log.info("reeval_week_start")
        if synthesizer is not None:
            try:
                result = synthesize_narrative(period_start, period_end, None, store, synthesizer)
                if result is not None:
                    narratives_written += 1
            except Exception as exc:
                week_log.exception("reeval_synthesis_error")
                warnings.append(f"synthesis failed for {period_start.isoformat()}: {exc}")
        else:
            week_log.info("reeval_synthesis_skipped", reason="no synthesizer")
        try:
            store.record_reeval(period_start, period_end)
        except Exception as exc:
            week_log.exception("reeval_record_error")
            warnings.append(f"record_reeval failed for {period_start.isoformat()}: {exc}")
        week_log.info("reeval_week_end")

    log.info("reeval_compaction_start")
    try:
        comp = compact(store)
        log.info(
            "reeval_compaction_end",
            superseded_count=comp.superseded_count,
            deduped_count=comp.deduped_count,
        )
        warnings.extend(comp.warnings)
    except Exception as exc:
        log.exception("reeval_compaction_error")
        warnings.append(f"compaction failed: {exc}")

    log.info("reeval_state_rebuild_start")
    try:
        summary = rebuild_state(store, on_progress=_silent_progress)
        log.info(
            "reeval_state_rebuild_end",
            rows_rebuilt=summary.rows_rebuilt,
            duration_seconds=summary.duration_seconds,
        )
        warnings.extend(summary.warnings)
    except Exception as exc:
        log.exception("reeval_state_rebuild_error")
        warnings.append(f"state rebuild failed: {exc}")

    log.info(
        "reeval_end",
        weeks_processed=len(weeks),
        narratives_written=narratives_written,
        warnings_count=len(warnings),
    )
    return ReevalSummary(
        weeks_processed=len(weeks),
        narratives_written=narratives_written,
        warnings=warnings,
    )
