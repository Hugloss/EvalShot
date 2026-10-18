from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .bounded_process import ProcessLimits, run_bounded
from .command_manifest import CommandManifestError, load_command_manifest
from .workspace_state import (
    WorkspaceStateError,
    changed_tracked_paths,
    tracked_workspace_state,
)


class CommandRunnerError(ValueError):
    pass


def _allowed(path: str, patterns: tuple[str, ...]) -> bool:
    from .refactor_focus_discovery import is_excluded_repo_path

    return any(is_excluded_repo_path(path, (pattern,)) for pattern in patterns)


def classify_result(
    *,
    return_code: int | None,
    timed_out: bool,
    executable_missing: bool,
    stdout: str,
    stderr: str,
    policy_violation: bool,
    output_limited: bool = False,
) -> str:
    if policy_violation:
        return "policy_mutation_violation"
    if executable_missing:
        return "executable_missing"
    if timed_out:
        return "timeout"
    if output_limited:
        return "output_limit_exceeded"
    if return_code == 0:
        return "pass"
    text = (stdout + "\n" + stderr).lower()
    if "syntaxerror" in text or "syntax error" in text:
        return "syntax_compile_failure"
    if (
        "importerror" in text
        or "modulenotfounderror" in text
        or "error collecting" in text
    ):
        return "collection_import_failure"
    dependency_tokens = (
        "command not found",
        "no such file or directory",
        "cannot find module",
        "could not find a version",
        "dependency",
        "environment variable",
        "not installed",
    )
    if any(token in text for token in dependency_tokens):
        return "dependency_environment_missing"
    if "mypy" in text or "type error" in text or "incompatible type" in text:
        return "type_check_failure"
    if "ruff" in text or "lint" in text:
        return "lint_static_failure"
    if "assertionerror" in text or " failed" in text or "failure" in text:
        return "assertion_test_failure"
    if "network" in text or "connection" in text or "tls" in text or "dns" in text:
        return "infrastructure_network_failure"
    return "unknown_failure"


_PRODUCT_FAILURE_CLASSIFICATIONS = frozenset(
    {
        "assertion_test_failure",
        "syntax_compile_failure",
        "type_check_failure",
        "lint_static_failure",
        "unknown_failure",
    }
)


def _process_outcome(classification: str) -> dict[str, object]:
    if classification == "pass":
        return {
            "status": "PASS",
            "classification": classification,
            "product_failure": False,
        }
    if classification in _PRODUCT_FAILURE_CLASSIFICATIONS:
        return {
            "status": "FAIL",
            "classification": classification,
            "product_failure": True,
        }
    return {
        "status": "INCOMPLETE",
        "classification": classification,
        "product_failure": False,
    }


def _overall_evidence_status(
    process_outcome: dict[str, object],
    harness_outcome: dict[str, object],
) -> str:
    if process_outcome["status"] == "FAIL":
        return "PRODUCT_FAILURE"
    if process_outcome["status"] != "PASS" or harness_outcome["status"] != "PASS":
        return "INCOMPLETE"
    return "COMPLETE_PASS"


def run_named_command(
    *,
    repository_root: Path,
    manifest_path: Path,
    name: str,
    selected_paths: Iterable[str] = (),
    timeout_seconds: float = 60.0,
    max_stdout_bytes: int = 1_000_000,
    max_stderr_bytes: int = 1_000_000,
) -> dict[str, object]:
    root = repository_root.resolve()
    target = manifest_path if manifest_path.is_absolute() else root / manifest_path
    try:
        manifest = load_command_manifest(target)
    except (OSError, CommandManifestError) as exc:
        raise CommandRunnerError(
            f"cannot load command manifest: {type(exc).__name__}"
        ) from exc
    if name not in manifest.commands:
        raise CommandRunnerError(f"unknown command: {name}")
    spec = manifest.commands[name]
    argv = list(spec.argv)
    selected: list[str] = []
    for raw in selected_paths:
        p = Path(raw.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts:
            raise CommandRunnerError(
                f"selected path must be repository-relative: {raw}"
            )
        selected.append(p.as_posix())
    if selected and not spec.append_selected_tests:
        raise CommandRunnerError(
            f"command {name!r} does not admit selected path arguments"
        )
    argv.extend(selected)

    try:
        before_state = tracked_workspace_state(root)
    except WorkspaceStateError as exc:
        raise CommandRunnerError(
            f"cannot establish pre-command tracked-byte identity: {exc}"
        ) from exc
    started_at = datetime.now(timezone.utc).isoformat()
    runtime_environment = {"TERM": "dumb", **dict(spec.environment)}
    result = run_bounded(
        repository_root=root,
        argv=argv,
        cwd=spec.cwd,
        limits=ProcessLimits(timeout_seconds, max_stdout_bytes, max_stderr_bytes),
        environment=runtime_environment,
    )
    ended_at = datetime.now(timezone.utc).isoformat()
    postflight_error: str | None = None
    try:
        after_state = tracked_workspace_state(root)
    except WorkspaceStateError as exc:
        after_state = None
        changed: list[str] = []
        unexpected: list[str] = []
        policy_violation = False
        postflight_error = f"{type(exc).__name__}: {exc}"
    else:
        changed = changed_tracked_paths(before_state, after_state)
        unexpected = [
            p for p in changed if not _allowed(p, spec.allowed_mutation_paths)
        ]
        policy_violation = bool(spec.must_not_modify_tracked_files and unexpected)
    stdout_text = result.stdout.decode("utf-8", errors="replace")
    stderr_text = result.stderr.decode("utf-8", errors="replace")
    process_classification = classify_result(
        return_code=result.return_code,
        timed_out=result.timed_out,
        executable_missing=result.executable_missing,
        stdout=stdout_text,
        stderr=stderr_text,
        policy_violation=False,
        output_limited=result.stdout_truncated or result.stderr_truncated,
    )
    process_outcome = _process_outcome(process_classification)
    if postflight_error is not None:
        harness_outcome = {
            "status": "FAIL",
            "classification": "postflight_workspace_identity_unavailable",
            "detail": postflight_error,
        }
    elif policy_violation:
        harness_outcome = {
            "status": "FAIL",
            "classification": "policy_mutation_violation",
            "detail": None,
        }
    else:
        harness_outcome = {
            "status": "PASS",
            "classification": "pass",
            "detail": None,
        }
    evidence_status = _overall_evidence_status(process_outcome, harness_outcome)
    classification = (
        str(process_outcome["classification"])
        if process_outcome["status"] != "PASS"
        else str(harness_outcome["classification"])
    )
    semantic = {
        "manifest_identity": manifest.identity,
        "command_identity": result.command_identity,
        "stage": spec.stage,
        "classification": classification,
        "return_code": result.return_code,
        "signal": result.signal,
        "timed_out": result.timed_out,
        "executable_missing": result.executable_missing,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
        "unexpected_tracked_mutations": unexpected,
        "evidence_status": evidence_status,
        "process_outcome": process_outcome,
        "harness_outcome": harness_outcome,
        "environment_identity": result.environment_identity,
    }
    failure_identity = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return {
        "schema": {"name": "agent-economics-command-result", "version": 2},
        "command": {
            "name": name,
            "argv": argv,
            "cwd": spec.cwd,
            "stage": spec.stage,
            "identity": result.command_identity,
        },
        "manifest_identity": manifest.identity,
        "status": "PASS" if evidence_status == "COMPLETE_PASS" else "FAIL",
        "classification": classification,
        "evidence_status": evidence_status,
        "outcomes": {
            "process": process_outcome,
            "harness": harness_outcome,
            "controller": {
                "status": "NOT_OBSERVED",
                "classification": "external-controller-boundary",
            },
        },
        "failure_identity": failure_identity,
        "execution": {
            **result.metrics(),
            "started_at": started_at,
            "ended_at": ended_at,
        },
        "provenance": {
            "platform": platform.system(),
            "python": platform.python_version(),
            "shell_used": False,
            "network_isolation_enforced": False,
            "sandbox_isolation_enforced": False,
            "runtime_environment": {
                "identity": result.environment_identity,
                "explicit_variables": list(result.environment_variables),
                "default_noninteractive_term": "TERM" in result.environment_variables,
            },
        },
        "stdout": stdout_text,
        "stderr": stderr_text,
        "workspace": {
            "before_identity": before_state["identity"],
            "after_identity": None if after_state is None else after_state["identity"],
            "tracked_files": None
            if after_state is None
            else after_state["tracked_files"],
            "tracked_bytes": None
            if after_state is None
            else after_state["tracked_bytes"],
            "unexpected_tracked_mutations": unexpected,
            "postflight_error": postflight_error,
        },
        "authority": {
            "repair_performed": False,
            "ci_status": "NOT_RUN",
            "network_isolation_enforced": False,
            "sandbox_isolation_enforced": False,
            "authoritative_execution_evidence": evidence_status
            in {"COMPLETE_PASS", "PRODUCT_FAILURE"},
            "product_failure": evidence_status == "PRODUCT_FAILURE",
        },
    }


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run one declared repository command with hard bounds."
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--selected-path", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--max-stdout-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-stderr-bytes", type=int, default=1_000_000)
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args(argv)
    payload = run_named_command(
        repository_root=args.repository_root,
        manifest_path=args.manifest,
        name=args.name,
        selected_paths=args.selected_path,
        timeout_seconds=args.timeout_seconds,
        max_stdout_bytes=args.max_stdout_bytes,
        max_stderr_bytes=args.max_stderr_bytes,
    )
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.artifact:
        target = (
            args.artifact
            if args.artifact.is_absolute()
            else args.repository_root / args.artifact
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
