"""Workspace contamination classification with explicit frozen allowances."""

from __future__ import annotations

import fnmatch
from typing import Any

from benchmarks.harness.workspace import diff_snapshots


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _filtered(
    state: dict[str, dict[str, object]],
    generated_globs: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    return {
        path: evidence
        for path, evidence in state.items()
        if not _matches(path, generated_globs)
    }


def classify_contamination(
    *,
    before: dict[str, dict[str, object]],
    after: dict[str, dict[str, object]],
    allowed_change_globs: tuple[str, ...],
    allowed_generated_globs: tuple[str, ...],
) -> dict[str, Any]:
    diff = diff_snapshots(
        _filtered(before, allowed_generated_globs),
        _filtered(after, allowed_generated_globs),
    )
    unexpected: dict[str, list[str]] = {}
    for kind, paths in diff.items():
        values = [path for path in paths if not _matches(path, allowed_change_globs)]
        if values:
            unexpected[kind] = values
    return {
        "contaminated": bool(unexpected),
        "diff": diff,
        "unexpected": unexpected,
        "allowed_change_globs": list(allowed_change_globs),
        "allowed_generated_globs": list(allowed_generated_globs),
    }
