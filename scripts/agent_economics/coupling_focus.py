from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Iterable

from .bounded_process import ProcessLimits, run_bounded
from .probe_contract import analyzed_input_identity, build_probe_contract
from .refactor_focus_discovery import (
    DEFAULT_EXCLUDE_PATTERNS,
    DiscoveryError,
    is_excluded_repo_path,
    normalize_exclude_patterns,
    normalize_suffixes,
)
from .refactor_focus_paths import iso_utc_now

TOOL_NAME = "coupling-focus"
TOOL_VERSION = "0.8.0"
_COMMIT_TOKEN = re.compile(r"^[0-9a-f]{40}$")


class CouplingFocusError(ValueError):
    pass


def _portable_path(value: Path | str, *, repository_root: Path) -> str:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repository_root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(repository_root)
    except ValueError as exc:
        raise CouplingFocusError(
            f"target path escapes repository root: {value}"
        ) from exc
    return relative.as_posix()


def _run_git(
    repository_root: Path,
    args: list[str],
    *,
    timeout_seconds: float,
    max_stdout_bytes: int = 1_000_000,
) -> bytes:
    if shutil.which("git") is None:
        raise CouplingFocusError("Git is required for coupling-focus")
    result = run_bounded(
        repository_root=repository_root,
        argv=("git", "-C", str(repository_root), *args),
        limits=ProcessLimits(timeout_seconds, max_stdout_bytes, 100_000),
    )
    if result.timed_out:
        raise CouplingFocusError(
            f"git history command timed out after {timeout_seconds} seconds"
        )
    if result.stdout_truncated:
        raise CouplingFocusError(
            f"git history output exceeded configured byte bound: {max_stdout_bytes}"
        )
    if result.executable_missing or result.return_code != 0:
        raise CouplingFocusError(f"git history command failed: {' '.join(args)}")
    return result.stdout


def _require_git_root(repository_root: Path, *, timeout_seconds: float) -> None:
    raw = _run_git(
        repository_root,
        ["rev-parse", "--show-toplevel"],
        timeout_seconds=timeout_seconds,
    )
    top = Path(raw.decode("utf-8", errors="replace").strip()).resolve()
    if top != repository_root.resolve():
        raise CouplingFocusError(
            f"repository_root must equal Git worktree root: expected {top}, got {repository_root.resolve()}"
        )


def _parse_log(raw: bytes) -> list[tuple[str, tuple[str, ...]]]:
    commits: list[tuple[str, tuple[str, ...]]] = []
    current_hash: str | None = None
    current_paths: list[str] = []
    for token_raw in raw.split(b"\0"):
        token = token_raw.decode("utf-8", errors="replace")
        if token.startswith("\n"):
            token = token[1:]
        if not token:
            continue
        if _COMMIT_TOKEN.fullmatch(token):
            if current_hash is not None:
                commits.append((current_hash, tuple(current_paths)))
            current_hash = token
            current_paths = []
        elif current_hash is not None:
            current_paths.append(token.replace("\\", "/"))
    if current_hash is not None:
        commits.append((current_hash, tuple(current_paths)))
    return commits


def _head_identity(repository_root: Path, *, timeout_seconds: float) -> str:
    raw = _run_git(
        repository_root, ["rev-parse", "HEAD"], timeout_seconds=timeout_seconds
    )
    return raw.decode("ascii", errors="replace").strip()


def coupling_focus_audit(
    *,
    repository_root: Path,
    target_paths: Iterable[Path | str],
    artifact_path: Path | None = None,
    history_max_commits: int = 500,
    max_files_per_commit: int = 200,
    min_shared_commits: int = 2,
    top_n: int = 20,
    candidate_suffixes: tuple[str, ...] | None = None,
    exclude_patterns: tuple[str, ...] | None = None,
    use_default_excludes: bool = True,
    sample_commits_per_candidate: int = 3,
    first_parent: bool = True,
    history_max_bytes: int = 8_000_000,
    git_timeout_seconds: float = 10.0,
) -> dict[str, object]:
    started = time.perf_counter()
    repository_root = repository_root.resolve()
    if history_max_commits < 1:
        raise CouplingFocusError("history_max_commits must be >= 1")
    if max_files_per_commit < 2:
        raise CouplingFocusError("max_files_per_commit must be >= 2")
    if min_shared_commits < 1:
        raise CouplingFocusError("min_shared_commits must be >= 1")
    if top_n < 1:
        raise CouplingFocusError("top_n must be >= 1")
    if sample_commits_per_candidate < 0:
        raise CouplingFocusError("sample_commits_per_candidate must be >= 0")
    if history_max_bytes < 1:
        raise CouplingFocusError("history_max_bytes must be >= 1")
    if git_timeout_seconds <= 0:
        raise CouplingFocusError("git_timeout_seconds must be > 0")
    if not repository_root.exists():
        raise CouplingFocusError(f"repository root does not exist: {repository_root}")
    _require_git_root(repository_root, timeout_seconds=git_timeout_seconds)

    targets = sorted(
        {_portable_path(p, repository_root=repository_root) for p in target_paths}
    )
    if not targets:
        raise CouplingFocusError("at least one target path is required")
    requested_excludes = list(DEFAULT_EXCLUDE_PATTERNS if use_default_excludes else ())
    requested_excludes.extend(exclude_patterns or ())
    try:
        excludes = normalize_exclude_patterns(requested_excludes)
        suffixes = (
            tuple(normalize_suffixes(candidate_suffixes)) if candidate_suffixes else ()
        )
    except DiscoveryError as exc:
        raise CouplingFocusError(str(exc)) from exc
    excluded_targets = [
        path for path in targets if is_excluded_repo_path(path, excludes)
    ]
    if excluded_targets:
        raise CouplingFocusError(
            f"target path excluded by discovery policy: {excluded_targets[0]}"
        )

    log_args = [
        "log",
        f"--max-count={history_max_commits}",
        "--format=%H",
        "--name-only",
        "-z",
        "--no-renames",
    ]
    if first_parent:
        log_args.extend(["--first-parent", "--diff-merges=first-parent"])
    raw_log = _run_git(
        repository_root,
        log_args,
        timeout_seconds=git_timeout_seconds,
        max_stdout_bytes=history_max_bytes,
    )
    commits = _parse_log(raw_log)
    count_args = ["rev-list", "--count"]
    if first_parent:
        count_args.append("--first-parent")
    count_args.append("HEAD")
    raw_total = _run_git(
        repository_root,
        count_args,
        timeout_seconds=git_timeout_seconds,
    )
    try:
        total_commit_count = int(
            raw_total.decode("ascii", errors="replace").strip() or "0"
        )
    except ValueError as exc:
        raise CouplingFocusError("cannot parse repository commit count") from exc
    head = _head_identity(repository_root, timeout_seconds=git_timeout_seconds)

    target_set = set(targets)
    candidate_commit_counts: Counter[str] = Counter()
    shared_counts: Counter[str] = Counter()
    shared_samples: dict[str, list[str]] = {}
    target_commit_count = 0
    oversized_skipped = 0
    empty_skipped = 0
    considered_commits = 0

    def candidate_allowed(path: str) -> bool:
        if path in target_set:
            return False
        if is_excluded_repo_path(path, excludes):
            return False
        if suffixes and Path(path).suffix.lower() not in suffixes:
            return False
        return True

    for commit_hash, raw_paths in commits:
        paths = sorted(
            {p for p in raw_paths if p and not is_excluded_repo_path(p, excludes)}
        )
        if not paths:
            empty_skipped += 1
            continue
        if len(paths) > max_files_per_commit:
            oversized_skipped += 1
            continue
        considered_commits += 1
        path_set = set(paths)
        candidates = [p for p in paths if candidate_allowed(p)]
        for path in candidates:
            candidate_commit_counts[path] += 1
        if not (target_set & path_set):
            continue
        target_commit_count += 1
        for path in candidates:
            shared_counts[path] += 1
            samples = shared_samples.setdefault(path, [])
            if len(samples) < sample_commits_per_candidate:
                samples.append(commit_hash)

    candidates: list[dict[str, object]] = []
    for path, shared in shared_counts.items():
        if shared < min_shared_commits:
            continue
        candidate_commits = candidate_commit_counts[path]
        union = target_commit_count + candidate_commits - shared
        target_coverage = shared / target_commit_count if target_commit_count else 0.0
        candidate_coverage = shared / candidate_commits if candidate_commits else 0.0
        jaccard = shared / union if union else 0.0
        candidates.append(
            {
                "target": path,
                "facts": {
                    "shared_commits": shared,
                    "target_commits": target_commit_count,
                    "candidate_commits": candidate_commits,
                },
                "evidence": {
                    "history": [
                        {
                            "kind": "cochange_commit_sample",
                            "commits": list(shared_samples.get(path, [])),
                        }
                    ]
                },
                "derived": {
                    "target_coverage": round(target_coverage, 6),
                    "candidate_coverage": round(candidate_coverage, 6),
                    "jaccard": round(jaccard, 6),
                },
                "interpretation": {
                    "relationship": "historical co-change correlation",
                    "dependency_not_proven": True,
                },
                "recommendations": {"next_action": "inspect_coupling_before_edit"},
                "uncertainty": [],
                "required_next_evidence": [
                    {
                        "kind": "inspect_structural_or_domain_relationship",
                        "reason": "co-change history does not establish dependency or ownership",
                    }
                ],
                "verification_suggestions": [],
            }
        )
    candidates.sort(
        key=lambda c: (
            -int(c["facts"]["shared_commits"]),
            -float(c["derived"]["target_coverage"]),
            -float(c["derived"]["jaccard"]),
            str(c["target"]),
        )
    )
    selected = candidates[:top_n]
    deferred = [
        {
            "target": str(c["target"]),
            "reason": "outside configured top_n coupling budget",
        }
        for c in candidates[top_n:]
    ]

    uncertainty: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    if total_commit_count > history_max_commits:
        uncertainty.append(
            {
                "code": "history_window_truncated",
                "message": "Only the newest configured history window was scanned.",
                "history_max_commits": history_max_commits,
                "repository_commit_count": total_commit_count,
            }
        )
    if oversized_skipped:
        uncertainty.append(
            {
                "code": "oversized_commits_skipped",
                "message": "Commits above max_files_per_commit were excluded to avoid bulk-change noise.",
                "count": oversized_skipped,
            }
        )
    if target_commit_count == 0:
        uncertainty.append(
            {
                "code": "targets_not_seen_in_history_window",
                "message": "No considered commit in the bounded history window touched a target path.",
            }
        )
    if not selected:
        uncertainty.append(
            {
                "code": "no_coupling_candidates",
                "message": "No candidate met the configured shared-commit threshold in the bounded history window.",
            }
        )
    if deferred:
        uncertainty.append(
            {
                "code": "coupling_candidate_budget_exhausted",
                "message": "Some qualifying co-change candidates were omitted by top_n.",
                "count": len(deferred),
            }
        )
    warnings.append(
        {
            "code": "correlation_not_dependency",
            "message": "Co-change is historical correlation only and must not be used as dependency or edit authority.",
        }
    )

    repository_entries = [
        {"path": "<git-head>", "sha256": hashlib.sha256(head.encode()).hexdigest()},
        {
            "path": "<targets>",
            "sha256": hashlib.sha256("\n".join(targets).encode()).hexdigest(),
        },
    ]
    repository = {
        "root": ".",
        "identity": analyzed_input_identity(repository_entries),
        "identity_kind": "git-head-and-target-set-sha256",
        "input_count": len(repository_entries),
    }
    configuration_values = {
        "target_paths": targets,
        "history_max_commits": history_max_commits,
        "max_files_per_commit": max_files_per_commit,
        "min_shared_commits": min_shared_commits,
        "top_n": top_n,
        "candidate_suffixes": list(suffixes),
        "exclude_patterns": list(excludes),
        "sample_commits_per_candidate": sample_commits_per_candidate,
        "first_parent": first_parent,
        "history_max_bytes": history_max_bytes,
        "git_timeout_seconds": git_timeout_seconds,
    }
    evidence_records = [
        {
            "target": c["target"],
            "shared_commits": c["facts"]["shared_commits"],
            "sample_commits": c["evidence"]["history"][0]["commits"],
        }
        for c in selected
    ]
    required_next = [
        {
            "target": c["target"],
            "kind": "inspect_structural_or_domain_relationship",
            "reason": "historical correlation alone is non-authoritative",
        }
        for c in selected
    ]
    economics = {
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "git_commands": 4,
        "history_bytes_read": len(raw_log),
        "history_max_bytes": history_max_bytes,
        "history_commits_returned": len(commits),
        "history_commits_considered": considered_commits,
        "repository_commit_count": total_commit_count,
        "target_commits": target_commit_count,
        "oversized_commits_skipped": oversized_skipped,
        "empty_commits_skipped": empty_skipped,
        "qualifying_candidates": len(candidates),
        "selected_candidates": len(selected),
        "deferred_candidates": len(deferred),
    }
    payload = build_probe_contract(
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        generated_at=iso_utc_now(),
        repository=repository,
        configuration_values=configuration_values,
        evidence={
            "records": evidence_records,
            "authority": "historical co-change correlation only",
        },
        derived={
            "target_commit_count": target_commit_count,
            "selected_candidate_count": len(selected),
        },
        interpretation={
            "ranking_policy": [
                "shared_commits_desc",
                "target_coverage_desc",
                "jaccard_desc",
                "path",
            ],
            "dependency_authority": False,
        },
        uncertainty=uncertainty,
        warnings=warnings,
        candidates=selected,
        required_next_evidence=required_next,
        deferred_evidence=deferred,
        verification_suggestions=[],
        economics=economics,
    )
    if artifact_path is not None:
        target = (
            artifact_path
            if artifact_path.is_absolute()
            else repository_root / artifact_path
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return payload
