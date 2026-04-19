-- Rivermind schema v2
-- Audit table: one row per completed re-eval pass for a given period window.
-- Used at startup to decide which completed weeks still need re-evaluation.
-- Decoupled from narrative presence so a run without an API key (no synthesis)
-- still marks its period as attempted.

CREATE TABLE reeval_runs (
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    PRIMARY KEY (period_start, period_end)
) STRICT;
