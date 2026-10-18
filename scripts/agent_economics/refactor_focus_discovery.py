from __future__ import annotations

import fnmatch
import os
import shutil

from .bounded_process import ProcessLimits, run_bounded
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DiscoveryMode = Literal["auto", "git", "filesystem"]
UntrackedPolicy = Literal["include", "exclude"]
IgnoredPolicy = Literal["exclude", "include"]
SymlinkPolicy = Literal["exclude", "reject", "within-repo"]

DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "**/.git/**",
    "**/.agent-artifacts/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/node_modules/**",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.mypy_cache/**",
    "**/.hypothesis/**",
    "**/.tox/**",
    "**/.nox/**",
    "**/.eggs/**",
    "**/build/**",
    "**/dist/**",
    "**/htmlcov/**",
    "**/coverage/**",
    "**/.ipynb_checkpoints/**",
    "docs/_build/**",
    "**/.idea/**",
    "**/.vscode/**",
)


class DiscoveryError(ValueError):
    pass


@dataclass(frozen=True)
class DiscoveryConfig:
    mode: DiscoveryMode = "auto"
    untracked_policy: UntrackedPolicy = "include"
    ignored_policy: IgnoredPolicy = "exclude"
    symlink_policy: SymlinkPolicy = "exclude"
    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_PATTERNS
    git_timeout_seconds: float = 5.0
    git_max_stdout_bytes: int = 8_000_000

    def __post_init__(self) -> None:
        if self.mode not in {"auto", "git", "filesystem"}:
            raise DiscoveryError(f"invalid discovery mode: {self.mode}")
        if self.untracked_policy not in {"include", "exclude"}:
            raise DiscoveryError(f"invalid untracked policy: {self.untracked_policy}")
        if self.ignored_policy not in {"exclude", "include"}:
            raise DiscoveryError(f"invalid ignored policy: {self.ignored_policy}")
        if self.symlink_policy not in {"exclude", "reject", "within-repo"}:
            raise DiscoveryError(f"invalid symlink policy: {self.symlink_policy}")
        if self.ignored_policy == "include" and self.untracked_policy != "include":
            raise DiscoveryError(
                "ignored_policy=include requires untracked_policy=include"
            )
        if self.git_timeout_seconds <= 0:
            raise DiscoveryError("git timeout must be greater than zero")
        if self.git_max_stdout_bytes < 1:
            raise DiscoveryError("git_max_stdout_bytes must be >= 1")
        object.__setattr__(
            self,
            "exclude_patterns",
            normalize_exclude_patterns(self.exclude_patterns),
        )


@dataclass(frozen=True)
class DiscoveryResult:
    files_by_root: dict[str, tuple[Path, ...]]
    backend: Literal["git", "filesystem"]
    git_available: bool
    tracked_python_candidates: int
    untracked_python_candidates: int
    ignored_python_candidates: int
    excluded_by_pattern: int
    symlinks_excluded: int
    missing_files: int
    git_commands: int
    git_stdout_bytes: int
    warnings: tuple[str, ...]

    def files_for(self, label: str) -> tuple[Path, ...]:
        try:
            return self.files_by_root[label]
        except KeyError as exc:
            raise DiscoveryError(f"unknown discovery root label: {label}") from exc

    def metrics(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "git_available": self.git_available,
            "tracked_candidates": self.tracked_python_candidates,
            "untracked_candidates": self.untracked_python_candidates,
            "ignored_candidates": self.ignored_python_candidates,
            # Backward-compatible P5 field names. In generic discovery these
            # counters refer to the configured suffix set, not only Python.
            "tracked_python_candidates": self.tracked_python_candidates,
            "untracked_python_candidates": self.untracked_python_candidates,
            "ignored_python_candidates": self.ignored_python_candidates,
            "excluded_by_pattern": self.excluded_by_pattern,
            "symlinks_excluded": self.symlinks_excluded,
            "missing_files": self.missing_files,
            "git_commands": self.git_commands,
            "git_stdout_bytes": self.git_stdout_bytes,
            "warnings": list(self.warnings),
            "root_file_counts": {
                label: len(paths) for label, paths in sorted(self.files_by_root.items())
            },
        }


def normalize_exclude_patterns(
    patterns: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    normalized: set[str] = set()
    for raw in patterns:
        pattern = str(raw).replace("\\", "/").strip()
        while pattern.startswith("./"):
            pattern = pattern[2:]
        if pattern.startswith("/"):
            raise DiscoveryError(
                f"exclusion pattern must be repository-relative: {raw!r}"
            )
        parts = pattern.split("/")
        if not pattern or any(part in {"", ".", ".."} for part in parts):
            raise DiscoveryError(
                f"invalid repository-relative exclusion pattern: {raw!r}"
            )
        normalized.add(pattern)
    return tuple(sorted(normalized))


def _glob_parts_match(
    path_parts: tuple[str, ...], pattern_parts: tuple[str, ...]
) -> bool:
    if not pattern_parts:
        return not path_parts
    head = pattern_parts[0]
    if head == "**":
        if _glob_parts_match(path_parts, pattern_parts[1:]):
            return True
        return bool(path_parts) and _glob_parts_match(path_parts[1:], pattern_parts)
    if not path_parts or not fnmatch.fnmatchcase(path_parts[0], head):
        return False
    return _glob_parts_match(path_parts[1:], pattern_parts[1:])


def is_excluded_repo_path(relative_path: str, patterns: tuple[str, ...]) -> bool:
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    path_parts = tuple(normalized.split("/"))
    return any(
        _glob_parts_match(path_parts, tuple(pattern.split("/"))) for pattern in patterns
    )


def _relative_to_repository(path: Path, repository_root: Path, *, label: str) -> Path:
    root = repository_root.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(root)
    except ValueError as exc:
        raise DiscoveryError(
            f"{label} must be inside repository_root: {resolved}"
        ) from exc


def _safe_repo_relative(raw: str) -> Path:
    normalized = raw.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise DiscoveryError(
            f"unsafe repository-relative path from discovery backend: {raw!r}"
        )
    return candidate


def _git_command(
    repository_root: Path,
    args: list[str],
    *,
    timeout_seconds: float,
    max_stdout_bytes: int = 8_000_000,
) -> bytes:
    result = run_bounded(
        repository_root=repository_root,
        argv=("git", "-C", str(repository_root), *args),
        limits=ProcessLimits(
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=max_stdout_bytes,
            max_stderr_bytes=100_000,
        ),
    )
    if result.executable_missing:
        raise DiscoveryError("git discovery executable is unavailable")
    if result.timed_out:
        raise DiscoveryError(
            f"git discovery command timed out after {timeout_seconds} seconds"
        )
    if result.stdout_truncated:
        raise DiscoveryError(
            f"git discovery output exceeded configured byte bound: {max_stdout_bytes}"
        )
    if result.return_code != 0:
        raise DiscoveryError(f"git discovery command failed: {' '.join(args)}")
    return result.stdout


def _git_toplevel(
    repository_root: Path, *, timeout_seconds: float, max_stdout_bytes: int = 8_000_000
) -> tuple[Path | None, int, int]:
    if shutil.which("git") is None:
        return None, 0, 0
    try:
        raw = _git_command(
            repository_root,
            ["rev-parse", "--show-toplevel"],
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=max_stdout_bytes,
        )
    except DiscoveryError:
        return None, 1, 0
    top = raw.decode("utf-8", errors="replace").strip()
    return (Path(top).resolve() if top else None), 1, len(raw)


def _decode_nul_paths(raw: bytes) -> list[str]:
    return [item.decode("utf-8", errors="replace") for item in raw.split(b"\0") if item]


def _belongs_to_root(relative: Path, root_relative: Path) -> bool:
    if root_relative == Path("."):
        return True
    try:
        relative.relative_to(root_relative)
        return True
    except ValueError:
        return False


def _admit_path(
    *,
    repository_root: Path,
    relative: Path,
    config: DiscoveryConfig,
    suffixes: frozenset[str],
) -> tuple[Path | None, str | None]:
    relative_posix = relative.as_posix()
    if relative.suffix.lower() not in suffixes:
        return None, None
    if is_excluded_repo_path(relative_posix, config.exclude_patterns):
        return None, "excluded"

    path = repository_root / relative
    if path.is_symlink():
        if config.symlink_policy == "reject":
            raise DiscoveryError(
                f"symlink Python path rejected by policy: {relative_posix}"
            )
        if config.symlink_policy == "exclude":
            return None, "symlink"
        resolved = path.resolve()
        try:
            resolved.relative_to(repository_root.resolve())
        except ValueError as exc:
            raise DiscoveryError(
                f"symlink escapes repository_root: {relative_posix}"
            ) from exc
        if not resolved.is_file():
            return None, "missing"
        return path.absolute(), None

    if not path.is_file():
        return None, "missing"
    return path.absolute(), None


def _partition_candidates(
    *,
    repository_root: Path,
    roots_relative: dict[str, Path],
    paths: list[str],
    config: DiscoveryConfig,
    outputs: dict[str, set[Path]],
    suffixes: frozenset[str],
) -> tuple[int, int, int, int]:
    python_candidates = 0
    excluded = 0
    symlinks = 0
    missing = 0
    for raw in paths:
        relative = _safe_repo_relative(raw)
        if relative.suffix.lower() not in suffixes:
            continue
        labels = [
            label
            for label, root_relative in roots_relative.items()
            if _belongs_to_root(relative, root_relative)
        ]
        if not labels:
            continue
        python_candidates += 1
        admitted, reason = _admit_path(
            repository_root=repository_root,
            relative=relative,
            config=config,
            suffixes=suffixes,
        )
        if admitted is not None:
            for label in labels:
                outputs[label].add(admitted)
        elif reason == "excluded":
            excluded += 1
        elif reason == "symlink":
            symlinks += 1
        elif reason == "missing":
            missing += 1
    return python_candidates, excluded, symlinks, missing


def _git_discover(
    *,
    roots_relative: dict[str, Path],
    repository_root: Path,
    config: DiscoveryConfig,
    probe_commands: int,
    suffixes: frozenset[str],
) -> DiscoveryResult:
    outputs = {label: set() for label in roots_relative}
    tracked_raw = _git_command(
        repository_root,
        ["ls-files", "-z", "--cached"],
        timeout_seconds=config.git_timeout_seconds,
        max_stdout_bytes=config.git_max_stdout_bytes,
    )
    tracked = _decode_nul_paths(tracked_raw)
    git_stdout_bytes = len(tracked_raw)
    git_commands = probe_commands + 1
    untracked: list[str] = []
    ignored: list[str] = []
    if config.untracked_policy == "include":
        untracked_raw = _git_command(
            repository_root,
            ["ls-files", "-z", "--others", "--exclude-standard"],
            timeout_seconds=config.git_timeout_seconds,
            max_stdout_bytes=config.git_max_stdout_bytes,
        )
        git_stdout_bytes += len(untracked_raw)
        untracked = _decode_nul_paths(untracked_raw)
        git_commands += 1
        if config.ignored_policy == "include":
            ignored_raw = _git_command(
                repository_root,
                ["ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
                timeout_seconds=config.git_timeout_seconds,
                max_stdout_bytes=config.git_max_stdout_bytes,
            )
            git_stdout_bytes += len(ignored_raw)
            ignored = _decode_nul_paths(ignored_raw)
            git_commands += 1

    tracked_count, tracked_excluded, tracked_symlinks, tracked_missing = (
        _partition_candidates(
            repository_root=repository_root,
            roots_relative=roots_relative,
            paths=tracked,
            config=config,
            outputs=outputs,
            suffixes=suffixes,
        )
    )
    untracked_count, untracked_excluded, untracked_symlinks, untracked_missing = (
        _partition_candidates(
            repository_root=repository_root,
            roots_relative=roots_relative,
            paths=untracked,
            config=config,
            outputs=outputs,
            suffixes=suffixes,
        )
    )
    ignored_count, ignored_excluded, ignored_symlinks, ignored_missing = (
        _partition_candidates(
            repository_root=repository_root,
            roots_relative=roots_relative,
            paths=ignored,
            config=config,
            outputs=outputs,
            suffixes=suffixes,
        )
    )

    return DiscoveryResult(
        files_by_root={label: tuple(sorted(paths)) for label, paths in outputs.items()},
        backend="git",
        git_available=True,
        tracked_python_candidates=tracked_count,
        untracked_python_candidates=untracked_count,
        ignored_python_candidates=ignored_count,
        excluded_by_pattern=tracked_excluded + untracked_excluded + ignored_excluded,
        symlinks_excluded=tracked_symlinks + untracked_symlinks + ignored_symlinks,
        missing_files=tracked_missing + untracked_missing + ignored_missing,
        git_commands=git_commands,
        git_stdout_bytes=git_stdout_bytes,
        warnings=(),
    )


def _filesystem_discover(
    *,
    roots: dict[str, Path],
    repository_root: Path,
    config: DiscoveryConfig,
    git_available: bool,
    probe_commands: int,
    fallback_warning: bool,
    suffixes: frozenset[str],
) -> DiscoveryResult:
    outputs: dict[str, set[Path]] = {label: set() for label in roots}
    excluded = 0
    symlinks = 0
    missing = 0

    for label, root in roots.items():
        if not root.exists():
            continue
        for current, dirs, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            kept_dirs: list[str] = []
            for name in dirs:
                candidate = current_path / name
                relative = candidate.absolute().relative_to(repository_root).as_posix()
                if is_excluded_repo_path(relative, config.exclude_patterns):
                    excluded += 1
                    continue
                if candidate.is_symlink():
                    if config.symlink_policy == "reject":
                        raise DiscoveryError(
                            f"symlink directory rejected by policy: {relative}"
                        )
                    symlinks += 1
                    continue
                kept_dirs.append(name)
            dirs[:] = kept_dirs

            for name in names:
                if Path(name).suffix.lower() not in suffixes:
                    continue
                path = current_path / name
                relative = path.absolute().relative_to(repository_root)
                admitted, reason = _admit_path(
                    repository_root=repository_root,
                    relative=relative,
                    config=config,
                    suffixes=suffixes,
                )
                if admitted is not None:
                    outputs[label].add(admitted)
                elif reason == "excluded":
                    excluded += 1
                elif reason == "symlink":
                    symlinks += 1
                elif reason == "missing":
                    missing += 1

    warnings = ["filesystem discovery does not evaluate Git ignore/tracked state"]
    if fallback_warning:
        warnings.insert(
            0, "Git discovery unavailable; used deterministic filesystem fallback"
        )
    return DiscoveryResult(
        files_by_root={label: tuple(sorted(paths)) for label, paths in outputs.items()},
        backend="filesystem",
        git_available=git_available,
        tracked_python_candidates=0,
        untracked_python_candidates=0,
        ignored_python_candidates=0,
        excluded_by_pattern=excluded,
        symlinks_excluded=symlinks,
        missing_files=missing,
        git_commands=probe_commands,
        git_stdout_bytes=0,
        warnings=tuple(warnings),
    )


def normalize_suffixes(suffixes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for raw in suffixes:
        suffix = str(raw).strip().lower()
        if not suffix:
            raise DiscoveryError("file suffix must not be empty")
        if "/" in suffix or "\\" in suffix:
            raise DiscoveryError(
                f"file suffix must not contain path separators: {raw!r}"
            )
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        if suffix == ".":
            raise DiscoveryError(f"invalid file suffix: {raw!r}")
        normalized.add(suffix)
    if not normalized:
        raise DiscoveryError("at least one file suffix is required")
    return tuple(sorted(normalized))


def discover_repository_roots(
    *,
    roots: dict[str, Path],
    repository_root: Path,
    config: DiscoveryConfig,
    suffixes: tuple[str, ...] | list[str],
) -> DiscoveryResult:
    if not roots:
        raise DiscoveryError("at least one discovery root is required")
    normalized_suffixes = frozenset(normalize_suffixes(suffixes))
    repository_root = repository_root.resolve()
    roots_resolved = {label: path.resolve() for label, path in roots.items()}
    roots_relative = {
        label: _relative_to_repository(path, repository_root, label=f"{label} root")
        for label, path in roots_resolved.items()
    }

    git_top, probe_commands, probe_bytes = _git_toplevel(
        repository_root,
        timeout_seconds=config.git_timeout_seconds,
        max_stdout_bytes=config.git_max_stdout_bytes,
    )
    if git_top is not None and git_top != repository_root:
        raise DiscoveryError(
            f"repository_root must equal Git worktree root: expected {git_top}, got {repository_root}"
        )
    git_available = git_top == repository_root

    if config.mode == "git":
        if not git_available:
            raise DiscoveryError(
                "Git discovery requested but repository_root is not a Git worktree root or Git is unavailable"
            )
        return _git_discover(
            roots_relative=roots_relative,
            repository_root=repository_root,
            config=config,
            probe_commands=probe_commands,
            suffixes=normalized_suffixes,
        )

    if config.mode == "auto" and git_available:
        return _git_discover(
            roots_relative=roots_relative,
            repository_root=repository_root,
            config=config,
            probe_commands=probe_commands,
            suffixes=normalized_suffixes,
        )

    return _filesystem_discover(
        roots=roots_resolved,
        repository_root=repository_root,
        config=config,
        git_available=git_available,
        probe_commands=probe_commands,
        fallback_warning=(config.mode == "auto" and not git_available),
        suffixes=normalized_suffixes,
    )


def discover_python_roots(
    *,
    roots: dict[str, Path],
    repository_root: Path,
    config: DiscoveryConfig,
) -> DiscoveryResult:
    return discover_repository_roots(
        roots=roots,
        repository_root=repository_root,
        config=config,
        suffixes=(".py",),
    )


def collect_python_files_filesystem(
    root: Path,
    *,
    repository_root: Path | None = None,
    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_PATTERNS,
    symlink_policy: SymlinkPolicy = "exclude",
) -> list[Path]:
    """Compatibility filesystem-only collector used by callers outside the probe workflow."""
    effective_root = (repository_root or root).resolve()
    result = discover_python_roots(
        roots={"root": root},
        repository_root=effective_root,
        config=DiscoveryConfig(
            mode="filesystem",
            symlink_policy=symlink_policy,
            exclude_patterns=exclude_patterns,
        ),
    )
    return list(result.files_for("root"))
