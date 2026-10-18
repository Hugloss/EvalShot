"""Verification for published benchmark evidence bundles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.harness.identity import (
    EXECUTION_EVIDENCE_CONTRACT,
    REPLICATE_EVIDENCE_CONTRACT,
    REPLICATE_SCORE_CONTRACT,
    canonical_json,
    digest,
    execution_evidence_id,
    score_projection_id,
)
from benchmarks.harness.receipt import is_complete_receipt


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_bundle(directory: Path) -> tuple[bool, str | None]:
    if directory.is_symlink() or not directory.is_dir():
        return False, "result bundle is not a real directory"
    if not is_complete_receipt(directory):
        return False, "result receipt is incomplete or invalid"
    try:
        receipt = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "result receipt cannot be parsed"

    trial_id = receipt.get("trial_id")
    if not isinstance(trial_id, str) or directory.name != trial_id:
        return False, "result directory does not match trial_id"

    execution = receipt.get("execution")
    if not isinstance(execution, dict):
        return False, "result receipt has no execution evidence"
    artifacts = execution.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return False, "result receipt has no artifact manifest"

    loaded: dict[str, bytes] = {}
    for name, evidence in artifacts.items():
        if not isinstance(name, str) or not isinstance(evidence, dict):
            return False, "artifact manifest entry is invalid"
        relative = evidence.get("path")
        if not isinstance(relative, str) or not relative:
            return False, f"artifact {name} has no path"
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            return False, f"artifact {name} path escapes result bundle"
        path = directory / candidate
        if path.is_symlink():
            return False, f"artifact {name} must not be a symlink"
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(directory.resolve())
        except (OSError, ValueError):
            return False, f"artifact {name} resolves outside result bundle"
        try:
            payload = path.read_bytes()
        except OSError:
            return False, f"artifact {name} is missing"
        if evidence.get("bytes") != len(payload):
            return False, f"artifact {name} byte count mismatch"
        if evidence.get("sha256") != _sha256(payload):
            return False, f"artifact {name} checksum mismatch"
        loaded[name] = payload

    declared_files = {
        "result.json",
        "result.sha256",
        "completion.json",
        *(str(value["path"]) for value in artifacts.values()),
    }
    actual_files = {path.name for path in directory.iterdir()}
    if actual_files != declared_files:
        return False, "result bundle contains undeclared or missing files"

    if receipt.get("backend") == "harbor":
        return _verify_harbor_bundle(receipt, loaded)

    events_evidence = execution.get("events")
    if not isinstance(events_evidence, dict):
        return False, "result receipt has no sealed event evidence"
    events = loaded.get("events")
    seal = loaded.get("events_seal")
    if events is None or seal is None:
        return False, "event artifacts are missing from manifest"
    if events_evidence.get("events_sha256") != _sha256(events):
        return False, "event stream checksum differs from receipt"
    lines = events.splitlines()
    if events_evidence.get("event_count") != len(lines):
        return False, "event count differs from receipt"
    for sequence, line in enumerate(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return False, f"event {sequence} is not valid JSON"
        if event.get("trial_id") != trial_id:
            return False, f"event {sequence} trial identity mismatch"
        if event.get("sequence") != sequence:
            return False, f"event {sequence} sequence mismatch"
    try:
        seal_value = json.loads(seal)
    except json.JSONDecodeError:
        return False, "event seal is not valid JSON"
    if seal_value != events_evidence:
        return False, "event seal differs from receipt"
    if canonical_json(seal_value) != seal:
        return False, "event seal is not canonical JSON"
    contract = execution.get("evidence_contract")
    if contract is not None:
        if contract not in {EXECUTION_EVIDENCE_CONTRACT, REPLICATE_EVIDENCE_CONTRACT}:
            return False, "unsupported execution evidence contract"
        trace = artifacts.get("agent_trace")
        scoring = receipt.get("scoring")
        authority = receipt.get("authority")
        if (
            not isinstance(trace, dict)
            or not isinstance(scoring, dict)
            or not isinstance(authority, dict)
        ):
            return False, "execution evidence is missing trace or scoring authority"
        try:
            evidence_identity = execution_evidence_id(
                task=receipt["task"],
                condition=receipt["condition"],
                trial=execution["trial_index"],
                **(
                    {"seed": execution["seed"]}
                    if contract == EXECUTION_EVIDENCE_CONTRACT
                    else {"replicate_id": execution["replicate_id"]}
                ),
                subject_identity=authority["subject"],
                agent_identity=authority["agent"],
                harness_identity=authority["harness"],
                environment_identity=authority["environment"],
                mutation_identity=authority["mutation"],
                agent_answer=execution["agent_answer"],
                workspace_root=execution["workspace_root"],
                location_observation=execution["location_observation"],
                agent_trace_sha256=trace["sha256"],
                **(
                    {
                        "campaign_id": execution["campaign_id"],
                        "admitted_state_sha256": execution["admitted_state_sha256"],
                    }
                    if contract == REPLICATE_EVIDENCE_CONTRACT
                    else {}
                ),
            )
            projection_identity = score_projection_id(
                execution_evidence=evidence_identity,
                oracle_identity=authority["oracle"]["declared"],
                **(
                    {"contract": REPLICATE_SCORE_CONTRACT}
                    if contract == REPLICATE_EVIDENCE_CONTRACT
                    else {}
                ),
            )
        except (KeyError, TypeError, ValueError):
            return False, "execution evidence identity inputs are invalid"
        if execution.get("evidence_identity") != evidence_identity:
            return False, "execution evidence identity mismatch"
        if scoring.get("projection_identity") != projection_identity:
            return False, "score projection identity mismatch"
    return True, None


def _verify_harbor_bundle(
    receipt: dict[str, object],
    loaded: dict[str, bytes],
) -> tuple[bool, str | None]:
    """Verify Harbor reward evidence without applying native receipt semantics."""
    execution = receipt.get("execution")
    result = receipt.get("harbor")
    if not isinstance(execution, dict) or not isinstance(result, dict):
        return False, "Harbor receipt lacks execution evidence"
    if (
        not isinstance(execution.get("campaign_id"), str)
        or not isinstance(receipt.get("definition_id"), str)
        or type(execution.get("launch_attempt")) is not int
        or execution["launch_attempt"] < 1
    ):
        return False, "Harbor receipt identity is invalid"
    if receipt.get("trial_id") != digest({
        "campaign_id": execution["campaign_id"],
        "definition_id": receipt["definition_id"],
    }):
        return False, "Harbor trial identity does not bind campaign and definition"
    if execution.get("job_name") != (
        f"h{str(receipt['trial_id'])[:24]}-a{execution['launch_attempt']:06d}"
    ):
        return False, "Harbor job name does not bind launch attempt"
    raw = loaded.get("harbor_result")
    if raw is None or raw != canonical_json(result):
        return False, "Harbor result artifact differs from receipt"
    for receipt_key, result_key in (
        ("task_id", "task"),
        ("harness", "harness"),
        ("subject", "subject"),
        ("model", "model"),
    ):
        if receipt.get(receipt_key) != result.get(result_key):
            return False, f"Harbor {receipt_key} differs from job result"
    if receipt.get("replicate_id") != result.get("attempt"):
        return False, "Harbor replicate differs from job result"
    reward = result.get("reward")
    return_code = result.get("harbor_return_code")
    if result.get("status") not in {"COMPLETE", "INCOMPLETE"}:
        return False, "Harbor result has invalid status"
    completed = result.get("status") == "COMPLETE"
    expected_success = completed and type(reward) in {int, float} and reward > 0
    if "success" in result and result["success"] is not expected_success:
        return False, "Harbor success flag disagrees with completed reward"
    if completed:
        if (
            type(return_code) is not int
            or return_code != 0
            or type(reward) not in {int, float}
            or not 0 <= reward <= 1
        ):
            return False, "Harbor completed result has invalid reward or exit status"
        if receipt.get("status") != ("PASS" if reward > 0 else "FAIL"):
            return False, "Harbor reward and receipt outcome disagree"
        reward_bytes = loaded.get("reward")
        if reward_bytes is None:
            return False, "Harbor reward artifact is missing"
        try:
            observed = float(reward_bytes.decode("utf-8").strip())
        except (UnicodeDecodeError, ValueError):
            try:
                value = json.loads(reward_bytes)
                observed = float(value.get("reward") if isinstance(value, dict) else value)
            except (UnicodeDecodeError, ValueError, TypeError, AttributeError):
                return False, "Harbor reward artifact is invalid"
        if observed != reward:
            return False, "Harbor reward artifact differs from receipt"
    elif receipt.get("status") != "INCOMPLETE":
        return False, "Harbor incomplete result has a completed outcome"
    if result.get("trial_id") != execution.get("job_name"):
        return False, "Harbor job identity differs from receipt"
    return True, None
