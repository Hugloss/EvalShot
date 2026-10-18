# Behavioral repository-intelligence benchmark v4

This explicit research suite adds 32 agent tasks across eight capability families:
post-change delta, change impact, verification selection, dependency transitions,
external correlation, repository declarations, freshness, and bounded negative evidence.
It runs 192 frozen trials with two native agents, bare control, Hashmarks, and Enola.

The suite is independent of Hashmarks unit tests. Each task is anchored to a frozen
agentsCookbook repository tree, uses synthetic fixtures or mutations, and is graded by
the suite oracle. Native Hashmarks probes are available in `native_probe.py` for the
structured evidence lane; unsupported Enola probes are recorded as not comparable.

The score is a lexigram: ordered word grades for admissibility, authority, resolution,
evidence preservation, and paired benefit. Numeric measurements remain diagnostics.
Unknown, incomplete, stale, or truncated evidence is never converted to zero or absence.

Use a fresh campaign root and configure `BENCHMARK_SUITE_PATH` and
`BENCHMARK_SCORE_SCRIPT_PATH` to this directory. The normal `benchmark-check`,
`benchmark`, `benchmark-report`, and `benchmark-score` commands apply unchanged.

This is not a CI or release gate.
