from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    group: str
    module: str


COMMANDS = (
    CommandSpec(
        "refactor-focus",
        "Find bounded refactoring candidates and ownership evidence.",
        "repository evidence",
        "refactor_focus_cli",
    ),
    CommandSpec(
        "context-focus",
        "Select bounded repository context for a task.",
        "repository evidence",
        "context_focus_cli",
    ),
    CommandSpec(
        "test-focus",
        "Build a bounded verification ladder for changed paths.",
        "repository evidence",
        "test_focus_cli",
    ),
    CommandSpec(
        "change-impact",
        "Inspect bounded reverse source impact.",
        "repository evidence",
        "change_impact_cli",
    ),
    CommandSpec(
        "coupling-focus",
        "Inspect bounded Git co-change correlation.",
        "repository evidence",
        "coupling_focus_cli",
    ),
    CommandSpec(
        "hotspot-focus",
        "Rank transparent repository investigation hotspots.",
        "repository evidence",
        "hotspot_focus_cli",
    ),
    CommandSpec(
        "quality-debt",
        "Measure configured analyzer debt without policy authority.",
        "repository evidence",
        "quality_debt_cli",
    ),
    CommandSpec(
        "refactor-locality",
        "Compare structural-change locality without an opaque score.",
        "measurement",
        "refactor_locality",
    ),
    CommandSpec(
        "doctor",
        "Inspect repository readiness and suggest configuration.",
        "environment",
        "doctor",
    ),
    CommandSpec(
        "next",
        "Translate required next evidence into a bounded command without executing it.",
        "environment",
        "evidence_next",
    ),
    CommandSpec(
        "evidence-batches",
        "Plan and bind resumable evidence batches without executing them.",
        "environment",
        "bounded_evidence_batches_cli",
    ),
    CommandSpec(
        "explain",
        "Project a probe artifact into concise human-readable evidence.",
        "environment",
        "explain",
    ),
    CommandSpec(
        "capabilities",
        "Report available local capabilities.",
        "environment",
        "capabilities",
    ),
    CommandSpec(
        "run-command",
        "Run one explicitly authorized repository command.",
        "local verification",
        "command_runner",
    ),
    CommandSpec(
        "qualify-local",
        "Escalate repository-declared local verification.",
        "local verification",
        "local_qualify",
    ),
    CommandSpec(
        "benchmark-outcomes",
        "Compare paired agent-economics outcomes.",
        "measurement",
        "agent_outcome_benchmark",
    ),
    CommandSpec(
        "dogfood-campaign",
        "Validate the complete empirical dogfood closeout campaign.",
        "measurement",
        "dogfood_campaign",
    ),
    CommandSpec(
        "dogfood-corpus",
        "Emit the immutable dogfood task corpus.",
        "measurement",
        "dogfood_corpus",
    ),
)

COMMAND_BY_NAME = {item.name: item for item in COMMANDS}
PROBE_COMMANDS = tuple(
    item.name for item in COMMANDS if item.group == "repository evidence"
)
