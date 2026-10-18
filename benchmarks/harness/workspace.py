"""Fresh-workspace materialization and bounded contamination evidence."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path

from benchmarks.harness.runtime_authority import PROCESS_SUBSTRATE_ENV_KEYS
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


class WorkspaceError(RuntimeError):
    pass


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record_symlink(
    out: dict[str, dict[str, object]],
    *,
    root: Path,
    path: Path,
) -> None:
    relative = path.relative_to(root).as_posix()
    payload = os.readlink(path).encode()
    out[relative] = {
        "kind": "symlink",
        "size": len(payload),
        "sha256": _hash_bytes(payload),
    }


def snapshot(
    root: Path,
    *,
    max_paths: int = 200_000,
    max_file_bytes: int = 100_000_000,
    max_total_bytes: int = 2_000_000_000,
) -> dict[str, dict[str, object]]:
    root = root.resolve()
    out: dict[str, dict[str, object]] = {}
    total = 0

    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        if current_path == root:
            directories[:] = [name for name in directories if name != ".git"]

        retained_directories: list[str] = []
        for name in sorted(directories):
            path = current_path / name
            if path.is_symlink():
                _record_symlink(out, root=root, path=path)
            else:
                retained_directories.append(name)
        directories[:] = retained_directories

        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                _record_symlink(out, root=root, path=path)
            else:
                size = path.stat().st_size
                if size > max_file_bytes:
                    raise WorkspaceError(
                        f"workspace file exceeds configured bound: {relative}"
                    )
                total += size
                if total > max_total_bytes:
                    raise WorkspaceError(
                        "workspace exceeds configured cumulative byte bound"
                    )
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                out[relative] = {
                    "kind": "file",
                    "size": size,
                    "sha256": digest.hexdigest(),
                }

            if len(out) > max_paths:
                raise WorkspaceError("workspace path count exceeds configured bound")

        if len(out) > max_paths:
            raise WorkspaceError("workspace path count exceeds configured bound")

    return dict(sorted(out.items()))


def _run_git(
    repository_root: Path,
    argv: tuple[str, ...],
    *,
    timeout: float = 120.0,
) -> bytes:
    result = run_bounded(
        repository_root=repository_root,
        argv=argv,
        limits=ProcessLimits(
            timeout_seconds=timeout,
            max_stdout_bytes=2_000_000,
            max_stderr_bytes=2_000_000,
        ),
    )
    if result.executable_missing:
        raise WorkspaceError("git executable unavailable")
    if result.timed_out or result.stdout_truncated or result.stderr_truncated:
        raise WorkspaceError(
            f"bounded git command did not complete cleanly: {' '.join(argv)}"
        )
    if result.return_code != 0:
        raise WorkspaceError(
            f"git command failed ({result.return_code}): {' '.join(argv)}: "
            + result.stderr.decode(errors="replace")
        )
    return result.stdout


def materialize_git(
    *,
    source: Path,
    commit: str,
    destination: Path,
    expected_tree: str | None = None,
) -> str:
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        destination.parent,
        ("git", "clone", "--no-hardlinks", str(source.resolve()), destination.name),
    )
    _run_git(destination, ("git", "checkout", "--detach", commit))
    actual = _run_git(destination, ("git", "rev-parse", "HEAD")).decode().strip()
    if actual != commit:
        raise WorkspaceError(
            f"workspace authority mismatch: expected {commit}, got {actual}"
        )
    if expected_tree is not None:
        actual_tree = (
            _run_git(destination, ("git", "rev-parse", "HEAD^{tree}")).decode().strip()
        )
        if actual_tree != expected_tree:
            raise WorkspaceError(
                f"workspace tree mismatch: expected {expected_tree}, got {actual_tree}"
            )
    return actual


def isolated_environment(
    root: Path, *, source: Mapping[str, str] | None = None
) -> dict[str, str]:
    source = os.environ if source is None else source
    root = root.resolve()
    home = root / "_environment" / "home"
    tmp = root / "_environment" / "tmp"
    config = root / "_environment" / "xdg-config"
    cache = root / "_environment" / "xdg-cache"
    data = root / "_environment" / "xdg-data"
    state = root / "_environment" / "xdg-state"
    codex_home = root / "_environment" / "codex-home"
    for directory in (home, tmp, config, cache, data, state, codex_home):
        directory.mkdir(parents=True, exist_ok=False)
    environment = {
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "TMP": str(tmp),
        "TEMP": str(tmp),
        "XDG_CONFIG_HOME": str(config),
        "XDG_CACHE_HOME": str(cache),
        "XDG_DATA_HOME": str(data),
        "XDG_STATE_HOME": str(state),
        "CODEX_HOME": str(codex_home),
        "ENOLA_NO_UPDATE_CHECK": "1",
    }
    for name in PROCESS_SUBSTRATE_ENV_KEYS:
        value = source.get(name)
        if value:
            environment[name] = value
    return environment


def diff_snapshots(
    before: dict[str, dict[str, object]],
    after: dict[str, dict[str, object]],
) -> dict[str, list[str]]:
    before_paths = set(before)
    after_paths = set(after)
    return {
        "added": sorted(after_paths - before_paths),
        "removed": sorted(before_paths - after_paths),
        "changed": sorted(
            path for path in before_paths & after_paths if before[path] != after[path]
        ),
    }
