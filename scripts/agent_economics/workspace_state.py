from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .bounded_process import ProcessLimits, run_bounded


class WorkspaceStateError(ValueError):
    pass


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_workspace_state(
    root: Path,
    *,
    max_paths: int = 200_000,
    max_path_bytes: int = 16_000_000,
    max_file_bytes: int = 100_000_000,
    max_total_bytes: int = 2_000_000_000,
) -> dict[str, object]:
    root = root.resolve()
    if shutil.which("git") is None:
        raise WorkspaceStateError("Git is required for tracked workspace identity")
    listing = run_bounded(
        repository_root=root,
        argv=("git", "ls-files", "-z"),
        limits=ProcessLimits(
            timeout_seconds=10.0,
            max_stdout_bytes=max_path_bytes,
            max_stderr_bytes=100_000,
        ),
    )
    if (
        listing.executable_missing
        or listing.return_code != 0
        or listing.stdout_truncated
    ):
        raise WorkspaceStateError("cannot obtain bounded tracked-file listing")
    raw_paths = [p for p in listing.stdout.split(b"\0") if p]
    if len(raw_paths) > max_paths:
        raise WorkspaceStateError(
            f"tracked file count exceeds configured bound: {max_paths}"
        )
    entries: list[dict[str, object]] = []
    total_bytes = 0
    for raw in raw_paths:
        rel = raw.decode("utf-8", errors="strict").replace("\\", "/")
        candidate = Path(rel)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise WorkspaceStateError(f"unsafe tracked path: {rel!r}")
        path = root / candidate
        if path.is_symlink():
            payload = path.readlink().as_posix().encode()
            size = len(payload)
            sha = hashlib.sha256(payload).hexdigest()
            kind = "symlink"
        elif path.is_file():
            size = path.stat().st_size
            if size > max_file_bytes:
                raise WorkspaceStateError(
                    f"tracked file exceeds configured byte bound: {rel}"
                )
            total_bytes += size
            if total_bytes > max_total_bytes:
                raise WorkspaceStateError(
                    "tracked workspace exceeds configured cumulative byte bound"
                )
            sha = _hash_file(path)
            kind = "file"
        else:
            size = 0
            sha = None
            kind = "missing"
        entries.append({"path": rel, "kind": kind, "size": size, "sha256": sha})
    entries.sort(key=lambda item: str(item["path"]))
    canonical = "\n".join(
        f"{e['path']}\0{e['kind']}\0{e['size']}\0{e['sha256'] or '-'}" for e in entries
    ).encode()
    return {
        "identity": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "tracked_files": len(entries),
        "tracked_bytes": total_bytes,
        "entries": entries,
    }


def changed_tracked_paths(
    before: dict[str, object], after: dict[str, object]
) -> list[str]:
    b = {str(e["path"]): e for e in before.get("entries", []) if isinstance(e, dict)}
    a = {str(e["path"]): e for e in after.get("entries", []) if isinstance(e, dict)}
    return sorted(path for path in set(b) | set(a) if b.get(path) != a.get(path))
