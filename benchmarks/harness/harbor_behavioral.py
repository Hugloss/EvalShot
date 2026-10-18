"""Harbor projection support for frozen behavioral command-oracle tasks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from benchmarks.harness.model import TrialContext
from benchmarks.harness.mutation import apply_mutation
from benchmarks.harness.suite import SuiteDefinition


class HarborBehavioralError(ValueError):
    pass


def behavioral_contract(
    suite: SuiteDefinition,
    task: Mapping[str, Any],
) -> dict[str, Any] | None:
    oracle = task.get("oracle")
    if not isinstance(oracle, Mapping) or oracle.get("adapter") != "command":
        return None
    config = oracle.get("configuration")
    mutation = task.get("mutation")
    task_id = task.get("id")
    if (
        task.get("mode") not in {"edit", "read_only"}
        or not isinstance(task_id, str)
        or not task_id
        or not isinstance(mutation, Mapping)
        or not isinstance(config, Mapping)
        or config.get("result_format") != "lexigram-v1"
    ):
        raise HarborBehavioralError(
            f"unsupported Harbor behavioral task contract: {task_id}"
        )
    expected_health = ["{python}", "{suite}/oracle.py", "health", task_id]
    expected_grade = ["{python}", "{suite}/oracle.py", "grade", task_id]
    if config.get("health_argv") != expected_health or config.get("grade_argv") != expected_grade:
        raise HarborBehavioralError(
            f"Harbor behavioral task requires canonical suite oracle argv: {task_id}"
        )
    oracle_path = suite.root / "oracle.py"
    cases_path = suite.root / "cases.json"
    if not oracle_path.is_file() or not cases_path.is_file():
        raise HarborBehavioralError(
            f"Harbor behavioral suite oracle files are missing: {task_id}"
        )
    contamination = task.get("contamination")
    if not isinstance(contamination, Mapping):
        raise HarborBehavioralError(
            f"Harbor behavioral contamination contract is missing: {task_id}"
        )
    allowed_changes = contamination.get("allowed_change_globs")
    generated = contamination.get("allowed_generated_globs")
    if not (
        isinstance(allowed_changes, list)
        and isinstance(generated, list)
        and all(isinstance(value, str) and value for value in allowed_changes + generated)
    ):
        raise HarborBehavioralError(
            f"Harbor behavioral contamination globs are invalid: {task_id}"
        )
    return {
        "kind": "command-lexigram",
        "task_id": task_id,
        "mutation": dict(mutation),
        "allowed_change_globs": list(allowed_changes),
        "allowed_generated_globs": list(generated),
        "oracle_path": oracle_path,
        "cases_path": cases_path,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_manifest(workspace: Path) -> dict[str, dict[str, str]]:
    manifest: dict[str, dict[str, str]] = {}
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if relative.parts and relative.parts[0] == ".git":
            continue
        key = relative.as_posix()
        if path.is_symlink():
            manifest[key] = {
                "type": "symlink",
                "target": os.readlink(path),
            }
        elif path.is_file():
            manifest[key] = {
                "type": "file",
                "sha256": _sha256(path),
            }
    return manifest


def admit_behavioral_oracle(
    *, workspace: Path, contract: Mapping[str, Any]
) -> dict[str, Any]:
    """Verify the existing frozen suite oracle before fixture mutation/model work."""
    command = (
        sys.executable,
        str(contract["oracle_path"]),
        "health",
        str(contract["task_id"]),
    )
    before = workspace_manifest(workspace)
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarborBehavioralError(
            f"behavioral oracle health unavailable: {contract['task_id']}: "
            f"{type(exc).__name__}"
        ) from exc
    if workspace_manifest(workspace) != before:
        raise HarborBehavioralError(
            f"behavioral oracle health mutated workspace: {contract['task_id']}"
        )
    if result.returncode != 0:
        raise HarborBehavioralError(
            f"behavioral oracle health failed: {contract['task_id']} "
            f"(exit {result.returncode})"
        )
    return {
        "status": "PASS",
        "command": "health",
        "return_code": 0,
        "timeout_seconds": 10,
    }


def prepare_behavioral_workspace(
    *,
    suite: SuiteDefinition,
    task: Mapping[str, Any],
    workspace: Path,
    tests: Path,
    control_root: Path,
) -> dict[str, Any]:
    contract = behavioral_contract(suite, task)
    if contract is None:
        raise HarborBehavioralError(
            f"task is not a behavioral command-oracle task: {task.get('id')}"
        )
    health = admit_behavioral_oracle(
        workspace=workspace,
        contract=contract,
    )
    context = TrialContext(
        workspace=workspace,
        control_root=control_root,
        environment=dict(os.environ),
    )
    observation = apply_mutation(
        context,
        suite_root=suite.root,
        mutation=contract["mutation"],
    )
    identity = observation.payload.get("identity")
    if not isinstance(identity, dict):
        raise HarborBehavioralError(
            f"behavioral mutation authority is missing: {task.get('id')}"
        )
    shutil.copy2(contract["oracle_path"], tests / "oracle.py")
    shutil.copy2(contract["cases_path"], tests / "cases.json")
    manifest = workspace_manifest(workspace)
    (tests / "baseline.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    baseline_sha256 = hashlib.sha256(
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    projection = {
        "schema": "agentscookbook.harbor-behavioral-projection.v1",
        "task_id": contract["task_id"],
        "mutation_identity": identity,
        "baseline_manifest_sha256": baseline_sha256,
        "allowed_change_globs": contract["allowed_change_globs"],
        "allowed_generated_globs": contract["allowed_generated_globs"],
        "oracle": "command-lexigram-v1",
        "oracle_health": health,
    }
    (tests / "projection.json").write_text(
        json.dumps(projection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **contract,
        **projection,
    }


def behavioral_verifier(contract: Mapping[str, Any]) -> str:
    task_id = json.dumps(str(contract["task_id"]))
    allowed_changes = json.dumps(list(contract["allowed_change_globs"]))
    generated = json.dumps(list(contract["allowed_generated_globs"]))
    return f"""#!/bin/sh
set -eu
mkdir -p /logs/verifier
python3 - <<'PY'
import fnmatch
import hashlib
import json
import os
import subprocess
from pathlib import Path

task_id = {task_id}
allowed_changes = {allowed_changes}
allowed_generated = {generated}
workspace = Path("/workspace")
baseline = json.loads(Path("/tests/baseline.json").read_text(encoding="utf-8"))
answer_path = workspace / ".agentscookbook-answer.json"


def allowed(path, patterns):
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def state(path):
    if path.is_symlink():
        return {{"type": "symlink", "target": os.readlink(path)}}
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {{"type": "file", "sha256": digest}}
    return None


current = {{}}
for path in sorted(workspace.rglob("*")):
    relative = path.relative_to(workspace)
    if relative.parts and relative.parts[0] == ".git":
        continue
    key = relative.as_posix()
    if key == ".agentscookbook-answer.json":
        continue
    observed = state(path)
    if observed is not None:
        current[key] = observed

contamination = []
for key, expected in baseline.items():
    if allowed(key, allowed_changes):
        continue
    if current.get(key) != expected:
        contamination.append(key)
for key in current:
    if key in baseline:
        continue
    if allowed(key, allowed_changes) or allowed(key, allowed_generated):
        continue
    contamination.append(key)
contamination = sorted(set(contamination))
tracked_clean = not contamination

observed = None
error = None
try:
    value = json.loads(answer_path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        observed = value
    else:
        error = "answer-not-object"
except FileNotFoundError:
    error = "answer-missing"
except (OSError, json.JSONDecodeError):
    error = "answer-invalid-json"

observation_path = Path("/tmp/agentscookbook-observation.json")
observation_path.write_text(
    json.dumps(
        {{
            "payload": {{
                "final_message": (
                    json.dumps(observed, sort_keys=True)
                    if observed is not None
                    else None
                )
            }}
        }},
        sort_keys=True,
    ) + "\\n",
    encoding="utf-8",
)
env = dict(os.environ)
env["BENCHMARK_OBSERVATION_PATH"] = str(observation_path)
oracle_return_code = None
oracle_stdout = ""
oracle_stderr = ""
oracle_timed_out = False
try:
    result = subprocess.run(
        ["python3", "/tests/oracle.py", "grade", task_id],
        cwd=workspace,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=40,
    )
    oracle_return_code = result.returncode
    oracle_stdout = result.stdout
    oracle_stderr = result.stderr
except subprocess.TimeoutExpired:
    oracle_timed_out = True
    error = error or "oracle-timeout"
except OSError:
    error = error or "oracle-launch-failed"

oversized = (
    len(oracle_stdout.encode("utf-8")) > 65536
    or len(oracle_stderr.encode("utf-8")) > 65536
)
if oversized:
    error = error or "oracle-output-limit"
oracle = None
if not oracle_timed_out and not oversized:
    try:
        candidate = json.loads(oracle_stdout)
        if isinstance(candidate, dict):
            oracle = candidate
    except json.JSONDecodeError:
        pass
oracle_passed = (
    oracle_return_code == 0
    and isinstance(oracle, dict)
    and oracle.get("passed") is True
    and error is None
)
if oracle_return_code is not None and oracle_return_code not in (0, 1):
    error = error or "oracle-invalid-exit"
elif not oracle_timed_out and error is None and oracle is None:
    error = "oracle-invalid-output"

Path("/logs/verifier/oracle.json").write_text(
    json.dumps(
        {{
            "return_code": oracle_return_code,
            "stdout": oracle_stdout[:8192],
            "stderr": oracle_stderr[:8192],
            "stdout_truncated": len(oracle_stdout) > 8192,
            "stderr_truncated": len(oracle_stderr) > 8192,
            "timed_out": oracle_timed_out,
            "result": oracle,
        }},
        sort_keys=True,
    ) + "\\n",
    encoding="utf-8",
)
# Post-grade, verifier-only oracle atoms. This is the SAME frozen cases.json
# used by oracle.py, never provided to the agent during its run.
# Missing or invalid analytics metadata cannot alter the existing reward.
semantic_expected = None
if isinstance(oracle, dict) and oracle.get("schema") == "agents-cookbook-lexigram-oracle.v1":
    try:
        case_rows = json.loads(Path("/tests/cases.json").read_text(encoding="utf-8"))
        case = case_rows["cases"][task_id]
        expected_claims = case["expected"]
        if (
            isinstance(expected_claims, dict)
            and expected_claims
            and all(isinstance(key, str) and key for key in expected_claims)
        ):
            semantic_expected = expected_claims
    except (OSError, ValueError, KeyError, TypeError):
        pass

evidence = {{
    "schema": "agentscookbook.harbor-answer-evidence.v1",
    "observed": observed,
    "expected": semantic_expected,
    "semantic_case_id": task_id if semantic_expected is not None else None,
    "match": oracle_passed,
    "tracked_clean": tracked_clean,
    "error": error,
    "contamination": contamination,
    "oracle": oracle,
}}
Path("/logs/verifier/answer.json").write_text(
    json.dumps(evidence, sort_keys=True) + "\\n",
    encoding="utf-8",
)
Path("/logs/verifier/reward.txt").write_text(
    "1\\n" if oracle_passed and tracked_clean else "0\\n",
    encoding="utf-8",
)
PY
exit 0
"""
