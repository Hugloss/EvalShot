"""Verified mutation application for frozen benchmark tasks."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from benchmarks.harness.model import Observation, TrialContext
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


class MutationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(context: TrialContext, argv: tuple[str, ...]):
    result = run_bounded(
        repository_root=context.workspace,
        argv=argv,
        environment=context.environment,
        limits=ProcessLimits(
            timeout_seconds=60.0,
            max_stdout_bytes=2_000_000,
            max_stderr_bytes=2_000_000,
        ),
    )
    if (
        result.executable_missing
        or result.timed_out
        or result.return_code != 0
        or result.stdout_truncated
        or result.stderr_truncated
    ):
        raise MutationError(
            f"mutation git command failed: {' '.join(argv)}: "
            + result.stderr.decode("utf-8", errors="replace")
        )
    return result


def apply_mutation(
    context: TrialContext,
    *,
    suite_root: Path,
    mutation: dict[str, Any] | None,
) -> Observation:
    if mutation is None:
        return Observation(
            {"applied": False, "identity": None, "changed_paths": []},
            "",
        )

    artifact = (suite_root / str(mutation["artifact"])).resolve()
    try:
        artifact.relative_to(suite_root.resolve())
    except ValueError as exc:
        raise MutationError("mutation artifact escapes suite root") from exc
    if not artifact.is_file():
        raise MutationError(f"mutation artifact missing: {artifact}")

    actual_sha = _sha256(artifact)
    expected_sha = str(mutation["sha256"])
    if actual_sha != expected_sha:
        raise MutationError(
            f"mutation checksum mismatch: expected {expected_sha}, got {actual_sha}"
        )

    _git(context, ("git", "apply", "--check", str(artifact)))
    _git(context, ("git", "apply", str(artifact)))
    _git(context, ("git", "diff", "--check"))
    tracked = _git(
        context,
        ("git", "diff", "--name-only", "--no-ext-diff", "-z"),
    ).stdout
    added = _git(
        context,
        ("git", "ls-files", "--others", "--exclude-standard", "-z"),
    ).stdout
    changed_paths = sorted(
        {
            value.decode("utf-8", errors="strict")
            for value in (tracked + added).split(b"\0")
            if value
        }
    )
    expected_paths = sorted(str(value) for value in mutation["changed_paths"])
    if changed_paths != expected_paths:
        raise MutationError(
            f"mutation changed-path mismatch: expected {expected_paths}, got {changed_paths}"
        )

    return Observation(
        {
            "applied": True,
            "identity": {
                "id": mutation["id"],
                "sha256": actual_sha,
                "changed_paths": changed_paths,
            },
            "changed_paths": changed_paths,
        },
        "",
    )
