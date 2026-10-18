"""Replay observed Hashmarks queries against one explicit repository candidate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.harness.bundle import verify_bundle
from benchmarks.harness.trace_diagnostics import _hashmarks_evidence


class HashmarksRetrievalProbeError(ValueError):
    pass


def _command(
    argv: list[str], workspace: Path, *, cache_root: Path, timeout: int = 180
) -> dict[str, Any]:
    try:
        process = subprocess.run(
            argv, cwd=workspace, capture_output=True, text=True,
            timeout=timeout, check=False,
            env={**os.environ, "XDG_CACHE_HOME": str(cache_root)},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HashmarksRetrievalProbeError(f"Hashmarks command could not finish: {exc}") from exc
    if process.returncode != 0:
        raise HashmarksRetrievalProbeError(
            f"Hashmarks command failed with exit {process.returncode}: "
            + process.stderr.strip()[-500:]
        )
    try:
        return json.loads(process.stdout)
    except ValueError as exc:
        raise HashmarksRetrievalProbeError("Hashmarks output is not JSON") from exc


def _observed_queries(
    results_root: Path, task_id: str
) -> tuple[list[str], dict[str, str], str]:
    queries: set[str] = set()
    expected: dict[str, str] | None = None
    repository_commit: str | None = None
    for directory in sorted(results_root.iterdir()):
        if directory.name.startswith("."):
            continue
        valid, reason = verify_bundle(directory)
        if not valid:
            raise HashmarksRetrievalProbeError(f"invalid bundle {directory}: {reason}")
        receipt = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        if receipt.get("task", {}).get("id") != task_id:
            continue
        target = receipt.get("task", {}).get("oracle", {}).get("configuration", {}).get("expected")
        if not isinstance(target, dict):
            raise HashmarksRetrievalProbeError("selected task has no location target")
        if expected is not None and expected != target:
            raise HashmarksRetrievalProbeError("selected task has inconsistent frozen targets")
        expected = target
        source_commit = receipt.get("task", {}).get("repository", {}).get("commit")
        if not isinstance(source_commit, str) or not source_commit:
            raise HashmarksRetrievalProbeError("selected task has no frozen repository commit")
        if repository_commit is not None and repository_commit != source_commit:
            raise HashmarksRetrievalProbeError("selected task has inconsistent repository commits")
        repository_commit = source_commit
        evidence = receipt.get("execution", {}).get("artifacts", {}).get("agent_trace")
        if not isinstance(evidence, dict):
            continue
        try:
            trace = json.loads((directory / evidence["path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for message in trace.get("messages", []):
            for part in message.get("parts", []):
                if part.get("tool") != "hashmarks_task_evidence":
                    continue
                query = part.get("state", {}).get("input", {}).get("task")
                if isinstance(query, str) and query:
                    queries.add(query)
    if not queries or expected is None or repository_commit is None:
        raise HashmarksRetrievalProbeError(
            f"no observed Hashmarks task_evidence queries for {task_id}"
        )
    return sorted(queries), expected, repository_commit


def run_hashmarks_retrieval_probe(
    *, results_root: Path, task_id: str, workspace: Path, executable: Path,
) -> dict[str, Any]:
    if not results_root.is_dir():
        raise HashmarksRetrievalProbeError(f"results directory missing: {results_root}")
    workspace = workspace.resolve()
    executable = executable.resolve()
    if not workspace.is_dir() or not executable.is_file():
        raise HashmarksRetrievalProbeError("workspace or executable is missing")
    queries, expected, expected_commit = _observed_queries(results_root, task_id)
    git_commit = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    if git_commit.returncode != 0:
        raise HashmarksRetrievalProbeError("workspace is not a Git checkout")
    with tempfile.TemporaryDirectory(prefix="hashmarks-retrieval-probe-") as state:
        base = [str(executable), "--workspace", ".", "--state-dir", state]
        cache_root = Path(state)
        _command([*base, "map", "sync"], workspace, cache_root=cache_root)
        observations = []
        for query in queries:
            for limit in (20, 50):
                packet = _command(
                    [*base, "task-evidence", query, "--limit", str(limit)],
                    workspace, cache_root=cache_root,
                )
                observations.append({
                    "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                    "query": query,
                    "limit": limit,
                    "evidence": _hashmarks_evidence(packet, expected),
                })
        exact = _command(
            [*base, "find", expected["symbol"]], workspace,
            cache_root=cache_root,
        )
    exact_rows = exact.get("results") if isinstance(exact, dict) else None
    return {
        "schema": "agents-cookbook-hashmarks-retrieval-probe.v1",
        "authority": {"diagnostic_only": True, "product_defect_proven": False},
        "task_id": task_id,
        "workspace_commit": git_commit.stdout.strip(),
        "frozen_task_repository_commit": expected_commit,
        "workspace_matches_frozen_task_commit": git_commit.stdout.strip() == expected_commit,
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "expected": expected,
        "observations": observations,
        "exact_symbol_find_returned_target": (
            any(
                isinstance(row, dict)
                and row.get("path") == expected["path"]
                and (row.get("name") == expected["symbol"] or row.get("symbol") == expected["symbol"])
                for row in exact_rows
            ) if isinstance(exact_rows, list) else None
        ),
    }
