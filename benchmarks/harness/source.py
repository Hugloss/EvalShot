"""Repository source cache and exact materialization."""

from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from benchmarks.harness.workspace import WorkspaceError, materialize_git
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


def _run_git(root: Path, argv: tuple[str, ...], *, timeout: float = 180.0) -> bytes:
    result = run_bounded(
        repository_root=root,
        argv=argv,
        limits=ProcessLimits(
            timeout_seconds=timeout,
            max_stdout_bytes=5_000_000,
            max_stderr_bytes=5_000_000,
        ),
    )
    if (
        result.executable_missing
        or result.timed_out
        or result.return_code != 0
        or result.stdout_truncated
        or result.stderr_truncated
    ):
        raise WorkspaceError(
            f"source-cache git command failed: {' '.join(argv)}: "
            + result.stderr.decode("utf-8", errors="replace")
        )
    return result.stdout


def _cache_name(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest() + ".git"


@contextmanager
def _source_cache_lock(cache_root: Path, url: str) -> Iterator[None]:
    """Serialize mutation and materialization from one repository mirror."""
    cache_root.mkdir(parents=True, exist_ok=True)
    lock_path = cache_root / (_cache_name(url) + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkspaceError(
                f"repository source cache is already in use: {lock_path}"
            ) from exc
        yield
    finally:
        os.close(fd)


def materialize_repository(
    *,
    repository: dict[str, Any],
    destination: Path,
    cache_root: Path,
    local_source: Path | None = None,
) -> str:
    commit = str(repository["commit"])
    tree = str(repository["tree"])
    if local_source is not None:
        return materialize_git(
            source=local_source,
            commit=commit,
            expected_tree=tree,
            destination=destination,
        )

    url = str(repository["url"])
    with _source_cache_lock(cache_root, url):
        mirror = cache_root / _cache_name(url)
        if not mirror.exists():
            _run_git(
                cache_root,
                ("git", "clone", "--mirror", url, mirror.name),
                timeout=600.0,
            )
        else:
            present = run_bounded(
                repository_root=mirror,
                argv=("git", "cat-file", "-e", f"{commit}^{{commit}}"),
                limits=ProcessLimits(timeout_seconds=30.0),
            )
            if (
                present.executable_missing
                or present.timed_out
                or present.return_code != 0
            ):
                _run_git(
                    mirror,
                    ("git", "fetch", "--prune", "origin"),
                    timeout=600.0,
                )

        actual_commit = _run_git(
            mirror,
            ("git", "rev-parse", commit),
        ).decode().strip()
        if actual_commit != commit:
            raise WorkspaceError(
                f"cached repository commit mismatch: expected {commit}, got {actual_commit}"
            )
        actual_tree = _run_git(
            mirror,
            ("git", "rev-parse", f"{commit}^{{tree}}"),
        ).decode().strip()
        if actual_tree != tree:
            raise WorkspaceError(
                f"cached repository tree mismatch: expected {tree}, got {actual_tree}"
            )
        return materialize_git(
            source=mirror,
            commit=commit,
            expected_tree=tree,
            destination=destination,
        )
