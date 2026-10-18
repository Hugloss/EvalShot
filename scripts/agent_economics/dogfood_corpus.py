from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


class DogfoodCorpusError(ValueError):
    pass


@dataclass(frozen=True)
class DogfoodTask:
    task_id: str
    category: str
    description: str
    expected_classification: str
    command_name: str
    oracle: str
    fixture_identity: str
    expected_final_command: str | None = None


DEFAULT_TASKS = (
    (
        "localized-python",
        "assertion",
        "Localized Python assertion failure",
        "assertion_test_failure",
    ),
    (
        "syntax-import",
        "syntax-import",
        "Syntax or import failure",
        "syntax_compile_failure",
    ),
    (
        "affected-dependent",
        "verification-selection",
        "Wrong-test or affected-dependent repair failure",
        "assertion_test_failure",
    ),
    ("lint-type", "static-analysis", "Lint or type failure", "lint_static_failure"),
    (
        "focused-pass-broad-fail",
        "verification-escalation",
        "Focused verification passes but broader gate fails",
        "assertion_test_failure",
    ),
    (
        "missing-executable",
        "environment",
        "Declared executable is unavailable",
        "executable_missing",
    ),
    (
        "stdout-stderr-flood",
        "bounds",
        "Command exceeds output byte bounds",
        "output_limit_exceeded",
    ),
    (
        "timeout-child-tree",
        "bounds",
        "Command times out with a child process",
        "timeout",
    ),
    (
        "non-utf8-output",
        "encoding",
        "Command emits non-UTF8 diagnostic bytes",
        "unknown_failure",
    ),
    (
        "tracked-mutation",
        "authority",
        "Verification mutates a tracked file",
        "policy_mutation_violation",
    ),
    ("allowed-mutation", "authority", "Declared tracked mutation is allowed", "pass"),
    (
        "no-progress-oscillation",
        "loop",
        "Repair loop repeats or oscillates without progress",
        "NO_PROGRESS",
    ),
    (
        "repository-bytes-unavailable",
        "identity",
        "Tracked repository byte identity cannot be established",
        "workspace_state_unavailable",
    ),
)


def _identity(value: dict[str, object]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def default_corpus() -> dict[str, object]:
    tasks = []
    for task_id, category, description, classification in DEFAULT_TASKS:
        semantic = {
            "task_id": task_id,
            "category": category,
            "description": description,
            "expected_classification": classification,
        }
        tasks.append(
            DogfoodTask(
                task_id=task_id,
                category=category,
                description=description,
                expected_classification=classification,
                command_name=task_id,
                oracle="freeze agent outcome before opening expected result",
                fixture_identity=_identity(semantic),
                expected_final_command="repository"
                if task_id == "focused-pass-broad-fail"
                else None,
            )
        )
    payload = {
        "schema": {"name": "agent-economics-dogfood-corpus", "version": 1},
        "protocol": {
            "paired_modes": ["baseline", "bridge"],
            "freeze_before_oracle": True,
            "repair_loop_must_be_local": True,
            "independent_qualification_during_repair_loop": False,
            "independent_qualification_after_local_freeze": "repository-owned-independent-qualification",
            "scripts_may_repair_source": False,
        },
        "tasks": [asdict(t) for t in tasks],
    }
    payload["identity"] = _identity(payload)
    return payload


def write_corpus(path: Path) -> dict[str, object]:
    payload = default_corpus()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Emit the immutable P10/P11 Agent Economics empirical dogfood corpus specification."
    )
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args(argv)
    payload = write_corpus(args.artifact) if args.artifact else default_corpus()
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
