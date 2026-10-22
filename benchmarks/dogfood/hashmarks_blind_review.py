"""Answer-blinded first-pass challenge packets for the staged Hashmarks cohort.

This is NOT EvalShot's canonical oracle reviewer, signer, admission authority,
or an agent runtime. Do not show the reviewer the staged EvalShot task files:
those contain the expected owner. Supply only the emitted packet and an
independently fetched copy of the pinned source repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from benchmarks.harness.identity import digest
from benchmarks.harness.suite import (
    SuiteDefinition,
    SuiteError,
    _validate_definition,
    load_runtime_suite,
    load_suite,
)

SCHEMA = "evalshot.hashmarks-blind-semantic-review-packet.v1"
TASKS = (
    "lookup-known-symbol-paths-under",
    "lookup-known-symbol-sync-remove-stale-paths",
)
STAGED_RELATIVE = Path(
    "benchmarks/dogfood/staged-suites/known-symbol-native-baseline-v1"
)
ACTIVE_RELATIVE = Path("benchmarks/suites/repository-intelligence")
SOURCE_TASK_ROOT = Path("benchmarks/suites/repository-intelligence/heldout-v1/tasks")
PARTICIPANT_SOURCE = Path(
    "benchmarks/suites/repository-intelligence/native-evidence-pilot-v1"
)
PARTICIPANTS = {
    "agents/codex-native.json",
    "subjects/none.json",
    "subjects/hashmarks.json",
}
MAX_BYTES = 100_000


class BlindReviewError(ValueError):
    """Cannot safely prepare an independent first-pass question."""


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise BlindReviewError("expected small regular JSON definition is unavailable")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise BlindReviewError("invalid JSON definition") from exc
    if not isinstance(parsed, dict):
        raise BlindReviewError("JSON definition must be an object")
    return parsed


def _git_blob_identity(raw: bytes) -> str:
    """Ask Git itself for the blob object identity; no second Git hash owner."""
    try:
        result = subprocess.run(
            ["git", "hash-object", "--stdin"],
            input=raw, capture_output=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BlindReviewError("Git blob verification unavailable") from exc
    return result.stdout.decode("ascii").strip()


def _verify_transfer(
    *, staged: Path, original: Path, recorded: dict[str, Any],
) -> bytes:
    if not staged.is_file() or staged.is_symlink() or not original.is_file():
        raise BlindReviewError("missing or unsafe transferred definition")
    raw = staged.read_bytes()
    if len(raw) > MAX_BYTES or raw != original.read_bytes():
        raise BlindReviewError("transferred definition differs from original")
    if recorded.get("git_blob_sha1") != _git_blob_identity(raw):
        raise BlindReviewError("transferred Git object identity mismatch")
    return raw


def prepare_blind_review(
    *, staged: Path, repository_root: Path,
) -> dict[str, Any]:
    """Build a prompt allowlist, preserving every independent qualification gate."""
    staged = staged.resolve()
    repository_root = repository_root.resolve()
    active = (repository_root / ACTIVE_RELATIVE).resolve()
    if staged.is_relative_to(active):
        raise BlindReviewError("unapproved review cannot enter active suite inventory")
    protocol = _load(staged / "protocol.json")
    if (
        protocol.get("schema") != "evalshot.hashmarks-native-baseline-pre-registration.v1"
        or protocol.get("state") != "BLOCKED_PENDING_INDEPENDENT_ORACLE_REVIEW"
        or protocol.get("require_review_before_any_model_work") is not True
        or protocol.get("historical_trials_reused") is not False
        or protocol.get("historical_pilot_2026_10_21_qualified") is not False
    ):
        raise BlindReviewError("staged protocol is not an unapproved baseline")
    if (staged / "qualification" / "oracle-reviews.json").exists():
        raise BlindReviewError("review evidence belongs to the canonical review owner")
    if set(protocol.get("task_transfers", {})) != set(TASKS):
        raise BlindReviewError("unexpected frozen task population")
    if set(protocol.get("participant_transfers", {})) != PARTICIPANTS:
        raise BlindReviewError("unexpected native participant population")
    if protocol.get("canonical_oracle_review_owner") != "benchmarks.harness.oracle_reviews":
        raise BlindReviewError("canonical reviewer ownership changed")

    # Use EvalShot's participant/schema authority, not a second interpretation.
    runtime = load_runtime_suite(staged)
    if runtime.experiment.get("tasks") != list(TASKS):
        raise BlindReviewError("staged experiment population changed")
    if runtime.experiment.get("oracle_reviews") != "qualification/oracle-reviews.json":
        raise BlindReviewError("independent-review gate is not declared")
    if {row["subject"] for row in runtime.experiment["conditions"]} != {
        "none", "hashmarks"
    } or {row["agent"] for row in runtime.experiment["conditions"]} != {
        "codex-native"
    }:
        raise BlindReviewError("native control pairing changed")

    # The staged population is not a changeable experiment-design sandbox:
    # the authority for slot expansion remains SuiteDefinition, and this
    # handoff refuses reconfiguration of the preregistered native A/B pairing.
    controls = protocol.get("controls")
    expected_arms = {
        "none-codex-native": "none",
        "hashmarks-codex-native": "hashmarks",
    }
    conditions = runtime.experiment["conditions"]
    if (
        not isinstance(controls, dict)
        or controls.get("assignments") != 12
        or controls.get("three_replicate_ids") != [9901, 9902, 9903]
        or controls.get("native_agent_config") != "HOST_NATIVE_UNMODIFIED"
        or controls.get("subjects") != ["none", "hashmarks"]
        or controls.get("control_condition") != "none-codex-native"
        or controls.get("assisted_condition") != "hashmarks-codex-native"
        or controls.get("actual_launch_order_independently_attested") is not False
        or controls.get("primary_effectiveness_claim_allowed") is not False
        or controls.get("model_sampling_seed_proven") is not False
        or controls.get("random_assignment_proven") is not False
        or len(conditions) != 2
        or {row["id"]: row["subject"] for row in conditions} != expected_arms
        or any(
            set(row) != {"id", "subject", "agent", "trials", "replicate_ids"}
            or row["trials"] != 3
            or row["replicate_ids"] != [9901, 9902, 9903]
            for row in conditions
        )
    ):
        raise BlindReviewError("frozen native paired trial plan changed")

    # Ensure the canonical loader still rejects this unreviewed cohort.
    try:
        load_suite(staged)
    except SuiteError as exc:
        if "oracle review evidence is missing" not in str(exc):
            raise BlindReviewError("canonical review gate failed unexpectedly") from exc
    else:
        raise BlindReviewError("unreviewed cohort unexpectedly passed canonical admission")

    for relative in sorted(PARTICIPANTS):
        transferred = protocol["participant_transfers"][relative]
        expected_from = (PARTICIPANT_SOURCE / relative).as_posix()
        if transferred.get("from") != expected_from:
            raise BlindReviewError("participant transfer authority changed")
        _verify_transfer(
            staged=staged / relative,
            original=repository_root / expected_from,
            recorded=transferred,
        )

    source = protocol.get("source")
    if not isinstance(source, dict):
        raise BlindReviewError("frozen source identity unavailable")
    packets: list[dict[str, Any]] = []
    validated_tasks: dict[str, dict[str, Any]] = {}
    forbidden_paths: list[str] = []
    for task_id in TASKS:
        source_relative = SOURCE_TASK_ROOT / f"{task_id}.json"
        reference = protocol["task_transfers"][task_id]
        if (
            not isinstance(reference, dict)
            or reference.get("from") != source_relative.as_posix()
            or reference.get("independent_reviews") != "NOT_PROVIDED"
        ):
            raise BlindReviewError("task transfer provenance is not independent")
        path = staged / "tasks" / f"{task_id}.json"
        raw = _verify_transfer(
            staged=path,
            original=repository_root / source_relative,
            recorded=reference,
        )
        task = _load(path)
        _validate_definition(task, schema_name="task", source=path)
        if (
            task.get("id") != task_id
            or task.get("mode") != "read_only"
            or task.get("mutation") is not None
            or task.get("repository") != {
                "url": source.get("repository"),
                "commit": source.get("commit"),
                "tree": source.get("tree"),
            }
        ):
            raise BlindReviewError("frozen task contract changed")
        oracle = task.get("oracle")
        expected = (
            oracle.get("configuration", {}).get("expected")
            if isinstance(oracle, dict) else None
        )
        if (
            not isinstance(expected, dict)
            or not isinstance(expected.get("path"), str)
            or not expected["path"]
        ):
            raise BlindReviewError("expected location is missing from frozen oracle")
        expected_path = expected["path"]
        prompt = task["prompt"]
        if expected_path.casefold() in prompt.casefold():
            raise BlindReviewError("frozen prompt exposes its oracle expected path")
        forbidden_paths.append(expected_path)
        validated_tasks[task_id] = task

        packets.append({
            "packet_id": "sha256:" + digest({
                "contract": SCHEMA, "task_digest": digest(task),
                "staged_protocol_digest": digest(protocol),
            }),
            "task_id": task_id,
            "task_digest": digest(task),
            "task_file_sha256": hashlib.sha256(raw).hexdigest(),
            "frozen_prompt": prompt,
            "repository": task["repository"],
            "family": task["family"],
            "mode": task["mode"],
            "review_status": "FIRST_PASS_UNREVIEWED",
            "proposed_owner": "WITHHELD",
            "semantic_decision": "NOT_MADE",
            "independent_evidence_needed": [
                "Inspect pinned source independently without EvalShot task/oracle files",
                "Identify plausible alternative owners or delegation points",
                "Assess whether the prompt has one defensible interpretation",
                "Record concrete source evidence and ambiguity for a distinct reviewer",
                "Return decision to the existing EvalShot oracle-review authority",
            ],
        })

    frozen = SuiteDefinition(
        root=runtime.root, experiment=runtime.experiment,
        tasks=validated_tasks, subjects=runtime.subjects, agents=runtime.agents,
    )
    assignments = frozen.trial_definitions()
    blocks = [assignments[index:index + 2] for index in range(0, len(assignments), 2)]
    if (
        len(assignments) != 12
        or len({slot["definition_id"] for slot in assignments}) != 12
        or sum(slot["condition_id"] == "none-codex-native" for slot in assignments) != 6
        or sum(slot["condition_id"] == "hashmarks-codex-native" for slot in assignments) != 6
        or len(blocks) != 6
        or any(
            len({(slot["task_id"], slot["replicate_id"]) for slot in block}) != 1
            or {slot["condition_id"] for slot in block} != set(expected_arms)
            for block in blocks
        )
        or sum(block[0]["condition_id"] == "none-codex-native" for block in blocks) != 3
    ):
        raise BlindReviewError("canonical frozen assignment expansion changed")

    rendered = {
        "schema": SCHEMA,
        "qualification": "SOURCE_BLINDED_QUESTION_ONLY",
        "protocol_identity": "sha256:" + digest(protocol),
        "native_control_headroom": "UNKNOWN",
        "independent_oracles_approved": 0,
        "experiment_admitted": False,
        "trial_executions": 0,
        "packets": packets,
        "reviewer_workspace_rule": (
            "Expose only this packet and the pinned Hashmarks source checkout. "
            "Do not mount the EvalShot repository or its task, oracle, score, "
            "review rationale, or previous dogfood answer-bearing reports."
        ),
    }
    serialized = json.dumps(rendered, sort_keys=True)
    if any(path.casefold() in serialized.casefold() for path in forbidden_paths):
        raise BlindReviewError("answer-bearing oracle path leaked into reviewer packet")
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", type=Path,
        default=STAGED_RELATIVE,
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.repository_root.resolve()
        staged = (root / args.suite).resolve()
        if args.output:
            output = args.output.resolve()
            if output.is_relative_to(staged) or output.is_relative_to(
                (root / ACTIVE_RELATIVE).resolve()
            ):
                raise BlindReviewError("review packet must not enter benchmark source trees")
        report = prepare_blind_review(staged=staged, repository_root=root)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
