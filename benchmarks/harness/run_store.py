"""Numbered, non-overwriting local benchmark campaigns."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from .campaign_authority import CampaignAuthorityError, read_campaign


class RunStoreError(ValueError):
    pass


@dataclass(frozen=True)
class SavedRun:
    run_id: str
    root: Path


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lock_holder_context(root: Path) -> str:
    path = root / ".active.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = None
    if not isinstance(value, dict):
        return (
            "active marker unavailable; a surviving benchmark/model child "
            "may still hold the inherited lock"
        )
    run_id = value.get("run_id")
    definition_id = value.get("definition_id")
    if isinstance(run_id, str) and run_id:
        detail = f"active run {run_id}"
        if isinstance(definition_id, str) and definition_id:
            detail += f", definition {definition_id}"
        return (
            detail
            + "; a surviving benchmark/model child may still hold the inherited lock"
        )
    return (
        "no active trial marker; another live benchmark process or surviving "
        "benchmark/model child still holds the lock"
    )


@contextmanager
def exclusive_store(root: Path) -> Iterator[int]:
    """Exclude another runner; subprocesses may retain the returned lock fd."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / ".run.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunStoreError(
                f"another benchmark command is using {root}; "
                f"{_lock_holder_context(root)}"
            ) from exc
        _write_active(root, None, None)
        yield fd
    finally:
        os.close(fd)


def store_is_active(root: Path) -> bool:
    lock = root.resolve() / ".run.lock"
    if not lock.exists():
        return False
    fd = os.open(lock, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _write_active(root: Path, run_id: str | None, definition_id: str | None) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".active-", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps({"run_id": run_id, "definition_id": definition_id}).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / ".active.json")
        _sync_directory(root)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def active_trial(root: Path, run_id: str, definition_id: str) -> Iterator[None]:
    _write_active(root.resolve(), run_id, definition_id)
    try:
        yield
    finally:
        _write_active(root.resolve(), None, None)


def active_definition(root: Path, run_id: str) -> str | None:
    if not store_is_active(root):
        return None
    path = root.resolve() / ".active.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    definition = (
        value.get("definition_id")
        if isinstance(value, dict) and value.get("run_id") == run_id
        else None
    )
    return definition if isinstance(definition, str) else None


def list_saved_runs(root: Path) -> list[SavedRun]:
    root = root.resolve()
    runs_dir = root / "runs"
    found: list[SavedRun] = []
    if runs_dir.exists():
        if runs_dir.is_symlink() or not runs_dir.is_dir():
            raise RunStoreError(f"invalid run directory: {runs_dir}")
        for path in runs_dir.iterdir():
            if (
                not path.name.isascii()
                or not path.name.isdecimal()
                or path.name != f"{int(path.name):06d}"
                or path.is_symlink()
                or not path.is_dir()
            ):
                raise RunStoreError(f"unexpected run entry: {path}")
            if not (path / "results/.campaign/authority.json").is_file():
                raise RunStoreError(f"saved run has no campaign authority: {path}")
            try:
                read_campaign(path / "results")
            except CampaignAuthorityError as exc:
                raise RunStoreError(f"invalid saved run {path}: {exc}") from exc
            found.append(SavedRun(path.name, path))
    found.sort(key=lambda run: int(run.run_id))
    legacy_results = root / "results"
    if legacy_results.exists() and not (legacy_results / ".campaign/authority.json").is_file():
        raise RunStoreError(f"legacy results lack campaign authority: {legacy_results}")
    if (legacy_results / ".campaign/authority.json").is_file():
        try:
            read_campaign(root / "results")
        except CampaignAuthorityError as exc:
            raise RunStoreError(f"invalid legacy run {root}: {exc}") from exc
        found.insert(0, SavedRun("legacy", root))
    return found


def select_saved_run(root: Path, run_id: str | None = None) -> SavedRun:
    found = list_saved_runs(root)
    if run_id is not None:
        for run in found:
            if run.run_id == run_id:
                return run
        raise RunStoreError(f"unknown saved run {run_id!r} in {root}")
    if not found:
        raise RunStoreError(f"no saved run in {root}; use prepare --new or run --new")
    return found[-1]


T = TypeVar("T")


def prepare_saved_run(root: Path, admit: Callable[[Path], T]) -> tuple[SavedRun, T]:
    """Commit a run only after model-free campaign admission succeeds.

    The caller holds exclusive_store for allocation through publication.
    """
    root = root.resolve()
    existing = list_saved_runs(root)
    next_number = max((int(run.run_id) for run in existing if run.run_id != "legacy"), default=0) + 1
    run_id = f"{next_number:06d}"
    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    _sync_directory(root)
    staging = Path(tempfile.mkdtemp(prefix=".preparing-", dir=root))
    try:
        admitted = admit(staging)
        destination = runs_dir / run_id
        if destination.exists():
            raise RunStoreError(f"saved run already exists: {destination}")
        os.rename(staging, destination)
        _sync_directory(runs_dir)
        return SavedRun(run_id, destination), admitted
    finally:
        if staging.exists():
            shutil.rmtree(staging)
