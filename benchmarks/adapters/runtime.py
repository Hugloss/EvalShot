"""Observed executable identity for benchmark participants."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Mapping

from benchmarks.harness.model import Observation, TrialContext
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def executable_file_metadata(path: Path) -> dict[str, int] | None:
    """Return cheap filesystem identity fields for forensic authority diffs."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size": int(stat.st_size),
        "mode": int(stat.st_mode),
        "mtime_ns": int(stat.st_mtime_ns),
        "ctime_ns": int(stat.st_ctime_ns),
    }


def resolve_native_executable(
    context: TrialContext,
    command: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    effective = dict(environment) if environment is not None else context.environment
    resolved = shutil.which(command, path=effective.get("PATH"))
    return str(Path(resolved).resolve()) if resolved else None


def observe_executable(
    context: TrialContext,
    command: str,
    *,
    version_args: tuple[str, ...] = ("--version",),
    timeout_seconds: float = 30.0,
    environment: Mapping[str, str] | None = None,
) -> Observation:
    resolved = resolve_native_executable(
        context,
        command,
        environment=environment,
    )
    path = Path(resolved) if resolved else None
    before_sha256 = None
    if path is not None and path.is_file():
        try:
            before_sha256 = _sha256_file(path)
        except OSError:
            before_sha256 = None
    result = run_bounded(
        repository_root=context.workspace,
        argv=(command, *version_args),
        environment=(
            dict(environment) if environment is not None else context.environment
        ),
        limits=ProcessLimits(
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=200_000,
            max_stderr_bytes=200_000,
        ),
        inherit_environment=False,
    )
    after_sha256 = None
    if path is not None and path.is_file():
        try:
            after_sha256 = _sha256_file(path)
        except OSError:
            after_sha256 = None
    executable_stable = (
        before_sha256 is not None and before_sha256 == after_sha256
    )
    executable_sha256 = after_sha256 if executable_stable else None
    available = (
        resolved is not None
        and executable_stable
        and not result.executable_missing
        and not result.timed_out
        and result.return_code == 0
        and not result.stdout_truncated
        and not result.stderr_truncated
    )
    return Observation(
        {
            "available": available,
            "command": command,
            "resolved_path": resolved,
            "executable_stable": executable_stable,
            "reason": (
                None
                if executable_stable
                else "executable changed or became unreadable during identity probe"
            ),
            "version": result.stdout.decode(
                "utf-8",
                errors="replace",
            ).strip(),
            "executable_sha256": executable_sha256,
            "file_metadata": (
                executable_file_metadata(path) if path is not None else None
            ),
            "process": result.metrics(),
            "stderr": result.stderr.decode(
                "utf-8",
                errors="replace",
            ),
        },
        result.stdout.decode("utf-8", errors="replace"),
        {"duration_ms": result.elapsed_ms},
    )
