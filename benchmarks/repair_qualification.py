"""Independent repair-behavior qualification over existing bounded-process evidence.

This is a small qualification consumer, not a process runner, agent loop, or
replacement for the frozen campaign/scoring authority.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from scripts.agent_economics.bounded_process import ProcessLimits, ProcessResult, run_bounded


@dataclass(frozen=True)
class RepairChecks:
    defect_regression: tuple[str, ...]
    preservation: tuple[tuple[str, ...], ...]


def _qualified(result: ProcessResult) -> bool:
    return (
        result.return_code == 0
        and not result.timed_out
        and not result.executable_missing
        and not result.stdout_truncated
        and not result.stderr_truncated
    )


def check_repair(
    *,
    before: Path,
    after: Path,
    checks: RepairChecks,
    limits: ProcessLimits = ProcessLimits(timeout_seconds=10),
) -> dict[str, object]:
    """Run one frozen set of commands on disposable before/after workspaces.

    A repaired defect must fail its independent regression before the change,
    pass after it, and preserve all declared behavior on both versions.
    Missing or unstable evidence is not a repair success.
    """
    if not checks.defect_regression or not checks.preservation:
        raise ValueError("repair checks require a defect regression and preservation controls")
    if not all(checks.preservation) or any(not a for a in checks.defect_regression):
        raise ValueError("empty command is not a valid repair check")
    if before.resolve() == after.resolve():
        raise ValueError("before and after must be distinct workspaces")
    roots = (before.resolve(), after.resolve())
    if any(not root.is_dir() for root in roots):
        raise ValueError("both workspaces must exist")

    def execute(root: Path, argv: Sequence[str]) -> ProcessResult:
        return run_bounded(
            repository_root=root, argv=argv, limits=limits,
            inherit_environment=False,
            environment={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
            close_stdin=True,
        )

    old_regression = execute(roots[0], checks.defect_regression)
    new_regression = execute(roots[1], checks.defect_regression)
    controls = [
        (execute(roots[0], argv), execute(roots[1], argv))
        for argv in checks.preservation
    ]
    before_failure_observed = (
        old_regression.return_code is not None
        and old_regression.return_code != 0
        and not old_regression.timed_out
        and not old_regression.executable_missing
        and not old_regression.stdout_truncated
        and not old_regression.stderr_truncated
    )
    after_pass = _qualified(new_regression)
    control_pass = all(_qualified(old) and _qualified(new) for old, new in controls)
    qualified = before_failure_observed and after_pass and control_pass
    return {
        "schema": "evalshot.repair-behavior-qualification.v1",
        "status": "PASS" if qualified else "NOT_QUALIFIED",
        "defect_precondition": "OBSERVED_FAILURE" if before_failure_observed else "NOT_ESTABLISHED",
        "defect_postcondition": "PASS" if after_pass else "NOT_QUALIFIED",
        "preservation": "PASS" if control_pass else "NOT_QUALIFIED",
        "before": old_regression.metrics(),
        "after": new_regression.metrics(),
        "controls": [
            {"before": old.metrics(), "after": new.metrics()}
            for old, new in controls
        ],
        "semantic_root_cause": "NOT_ADJUDICATED",
        "agent_improvement": "NOT_MEASURED",
    }
