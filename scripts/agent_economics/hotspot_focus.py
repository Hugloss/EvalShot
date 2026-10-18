from __future__ import annotations

import ast
import hashlib
import json
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path

from .bounded_process import ProcessLimits, run_bounded
from .probe_contract import analyzed_input_identity, build_probe_contract
from .refactor_focus_analysis import AnalysisCache
from .refactor_focus_discovery import (
    DEFAULT_EXCLUDE_PATTERNS,
    DiscoveryConfig,
    DiscoveryError,
    discover_python_roots,
)
from .refactor_focus_imports import build_import_index, internal_imports_for_file
from .refactor_focus_matching import (
    build_test_ownership_evidence,
    ownership_map_from_evidence,
)
from .refactor_focus_paths import iso_utc_now, module_path_for_file, report_path

TOOL_NAME = "hotspot-focus"
TOOL_VERSION = "0.12.0"
DEFAULT_RANKING = ("churn_commits", "fan_in", "branch_points", "source_lines")


class HotspotFocusError(ValueError):
    pass


def _bounded_git(
    repository_root: Path,
    args: list[str],
    *,
    timeout_seconds: float,
    max_stdout_bytes: int,
) -> bytes:
    if shutil.which("git") is None:
        raise HotspotFocusError("Git is unavailable")
    result = run_bounded(
        repository_root=repository_root,
        argv=("git", "-C", str(repository_root), *args),
        limits=ProcessLimits(timeout_seconds, max_stdout_bytes, 100_000),
    )
    if result.timed_out:
        raise HotspotFocusError(
            f"Git command timed out after {timeout_seconds} seconds"
        )
    if result.stdout_truncated:
        raise HotspotFocusError(
            f"Git history output exceeded configured byte bound: {max_stdout_bytes}"
        )
    if result.executable_missing or result.return_code != 0:
        raise HotspotFocusError("Git command failed")
    return result.stdout


def _git_history(
    repository_root: Path,
    *,
    max_commits: int,
    max_bytes: int,
    timeout_seconds: float,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    raw = _bounded_git(
        repository_root,
        [
            "log",
            f"--max-count={max_commits}",
            "--first-parent",
            "--format=@@AE@@%ae",
            "--numstat",
            "--no-renames",
        ],
        timeout_seconds=timeout_seconds,
        max_stdout_bytes=max_bytes,
    )
    text = raw.decode("utf-8", errors="replace")
    by_path: dict[str, dict[str, object]] = {}
    current_author = ""
    commits_seen = 0
    touched_this_commit: set[str] = set()
    for line in text.splitlines():
        if line.startswith("@@AE@@"):
            commits_seen += 1
            touched_this_commit = set()
            current_author = hashlib.sha256(line[6:].encode("utf-8")).hexdigest()[:16]
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added_raw, deleted_raw, path = parts
        path = path.replace("\\", "/")
        try:
            added = int(added_raw) if added_raw != "-" else 0
            deleted = int(deleted_raw) if deleted_raw != "-" else 0
        except ValueError:
            continue
        record = by_path.setdefault(
            path,
            {"commits": 0, "additions": 0, "deletions": 0, "authors": Counter()},
        )
        if path not in touched_this_commit:
            record["commits"] = int(record["commits"]) + 1
            touched_this_commit.add(path)
        record["additions"] = int(record["additions"]) + added
        record["deletions"] = int(record["deletions"]) + deleted
        authors = record["authors"]
        assert isinstance(authors, Counter)
        authors[current_author] += 1
    return by_path, {
        "available": True,
        "bytes_read": len(raw),
        "commits_returned": commits_seen,
        "history_identity": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }


def _branch_points(tree: ast.AST | None) -> int | None:
    if tree is None:
        return None
    kinds = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.IfExp,
        ast.Match,
        ast.comprehension,
    )
    return sum(isinstance(node, kinds) for node in ast.walk(tree))


def _largest_definitions(
    tree: ast.AST | None, limit: int = 5
) -> list[dict[str, object]]:
    if tree is None:
        return []
    rows: list[dict[str, object]] = []

    def walk(body: list[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lines = max(
                    1, getattr(node, "end_lineno", node.lineno) - node.lineno + 1
                )
                qualified = ".".join((*prefix, node.name))
                rows.append(
                    {
                        "kind": type(node).__name__,
                        "qualified_name": qualified,
                        "start_line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno),
                        "lines": lines,
                    }
                )
                walk(node.body, (*prefix, node.name))

    walk(getattr(tree, "body", []))
    return sorted(
        rows, key=lambda row: (-int(row["lines"]), str(row["qualified_name"]))
    )[:limit]


def _largest_function(tree: ast.AST | None) -> int | None:
    if tree is None:
        return None
    rows = [
        row
        for row in _largest_definitions(tree, limit=1_000_000)
        if row["kind"] in {"FunctionDef", "AsyncFunctionDef"}
    ]
    return max((int(row["lines"]) for row in rows), default=0)


def _rank_key(
    candidate: dict[str, object], dimensions: tuple[str, ...]
) -> tuple[object, ...]:
    facts = candidate["facts"]
    assert isinstance(facts, dict)
    keys: list[object] = []
    for dimension in dimensions:
        value = facts.get(dimension)
        keys.append(-(value if isinstance(value, (int, float)) else -1))
    keys.append(str(candidate["target"]))
    return tuple(keys)


def hotspot_focus_audit(
    *,
    repository_root: Path,
    source_root: Path,
    tests_root: Path | None = None,
    package_name: str | None = None,
    tests_package_name: str = "tests",
    artifact_path: Path | None = None,
    top_n: int = 20,
    ranking_dimensions: tuple[str, ...] = DEFAULT_RANKING,
    history_max_commits: int = 500,
    history_max_bytes: int = 8_000_000,
    git_timeout_seconds: float = 10.0,
    history_policy: str = "auto",
    discovery_mode: str = "auto",
    exclude_patterns: tuple[str, ...] | None = None,
) -> dict[str, object]:
    started = time.perf_counter()
    repository_root = repository_root.resolve()
    source_root = source_root.resolve()
    tests_root = tests_root.resolve() if tests_root is not None else None
    if top_n < 1 or history_max_commits < 1 or history_max_bytes < 1:
        raise HotspotFocusError("top_n/history bounds must be >= 1")
    if history_policy not in {"auto", "required", "disabled"}:
        raise HotspotFocusError("history_policy must be auto, required, or disabled")
    allowed_dimensions = {
        "source_lines",
        "branch_points",
        "largest_function_lines",
        "fan_in",
        "fan_out",
        "churn_commits",
        "churn_lines",
        "distinct_authors",
        "top_author_share",
        "confirmed_tests",
    }
    if not ranking_dimensions or any(
        d not in allowed_dimensions for d in ranking_dimensions
    ):
        raise HotspotFocusError("ranking_dimensions contains an unsupported dimension")
    if not source_root.exists():
        raise HotspotFocusError(f"source root does not exist: {source_root}")
    try:
        source_root.relative_to(repository_root)
        if tests_root is not None:
            tests_root.relative_to(repository_root)
    except ValueError as exc:
        raise HotspotFocusError(
            "configured roots must stay inside repository_root"
        ) from exc

    requested_excludes = list(DEFAULT_EXCLUDE_PATTERNS)
    requested_excludes.extend(exclude_patterns or ())
    try:
        discovery = discover_python_roots(
            roots={
                "source": source_root,
                **({"tests": tests_root} if tests_root is not None else {}),
            },
            repository_root=repository_root,
            config=DiscoveryConfig(
                mode=discovery_mode, exclude_patterns=tuple(requested_excludes)
            ),
        )
    except DiscoveryError as exc:
        raise HotspotFocusError(str(exc)) from exc
    source_files = list(discovery.files_for("source"))
    all_test_files = (
        list(discovery.files_for("tests")) if tests_root is not None else []
    )
    test_files = [
        path
        for path in all_test_files
        if path.name.startswith("test_") or path.name.endswith("_test.py")
    ]

    cache = AnalysisCache()
    cache.prewarm(source_files + all_test_files)
    module_to_path = {
        module_path_for_file(path=p, root=source_root, package_name=package_name): p
        for p in source_files
    }
    module_by_path = {path: module for module, path in module_to_path.items()}
    import_index = build_import_index(
        files=source_files,
        root=source_root,
        current_package_name=package_name,
        package_names={package_name},
        analysis_cache=cache,
    )
    owners: dict[Path, set[Path]] = {}
    if tests_root is not None and test_files:
        ownership = build_test_ownership_evidence(
            test_files=test_files,
            all_test_python_files=all_test_files,
            module_to_path=module_to_path,
            source_root=source_root,
            tests_root=tests_root,
            package_name=package_name,
            tests_package_name=tests_package_name,
            analysis_cache=cache,
            repository_root=repository_root,
        )
        owners = ownership_map_from_evidence(ownership)

    history: dict[str, dict[str, object]] = {}
    history_meta: dict[str, object] = {"available": False, "reason": "disabled"}
    history_uncertainty: list[dict[str, object]] = []
    if history_policy != "disabled":
        try:
            history, history_meta = _git_history(
                repository_root,
                max_commits=history_max_commits,
                max_bytes=history_max_bytes,
                timeout_seconds=git_timeout_seconds,
            )
        except HotspotFocusError as exc:
            if history_policy == "required":
                raise
            history_meta = {"available": False, "reason": type(exc).__name__}
            history_uncertainty.append(
                {
                    "code": "git_history_unavailable",
                    "message": "Git history evidence is unavailable; history dimensions remain unknown, not zero.",
                }
            )

    candidates: list[dict[str, object]] = []
    identity_entries: list[dict[str, str]] = []
    for path in source_files:
        record = cache.get(path)
        relative = report_path(path=path, anchor=repository_root)
        if record.content_sha256 is not None:
            identity_entries.append({"path": relative, "sha256": record.content_sha256})
        module = module_by_path[path]
        imports = internal_imports_for_file(
            path=path,
            root=source_root,
            current_package_name=package_name,
            package_names={package_name},
            analysis_cache=cache,
        )
        fan_out = len({m for m in imports if m in module_to_path})
        hist = history.get(relative) if bool(history_meta.get("available")) else None
        authors = hist.get("authors") if isinstance(hist, dict) else None
        distinct_authors: int | None = (
            len(authors) if isinstance(authors, Counter) else None
        )
        top_author_share: float | None = None
        if isinstance(authors, Counter) and authors:
            top_author_share = round(max(authors.values()) / sum(authors.values()), 6)
        churn_commits = int(hist["commits"]) if isinstance(hist, dict) else None
        additions = int(hist["additions"]) if isinstance(hist, dict) else None
        deletions = int(hist["deletions"]) if isinstance(hist, dict) else None
        branch_points = _branch_points(record.tree)
        facts: dict[str, object] = {
            "source_lines": record.line_count,
            "branch_points": branch_points,
            "largest_function_lines": _largest_function(record.tree),
            "largest_definitions": _largest_definitions(record.tree),
            "fan_in": len(import_index.get(module, set())),
            "fan_out": fan_out,
            "churn_commits": churn_commits,
            "churn_additions": additions,
            "churn_deletions": deletions,
            "churn_lines": additions + deletions
            if additions is not None and deletions is not None
            else None,
            "distinct_authors": distinct_authors,
            "top_author_share": top_author_share,
            "confirmed_tests": len(owners.get(path, set()))
            if tests_root is not None
            else None,
        }
        candidate_uncertainty: list[dict[str, object]] = []
        if record.parse_error:
            candidate_uncertainty.append(
                {"code": "python_parse_unavailable", "detail": record.parse_error}
            )
        if tests_root is None or not tests_root.exists():
            candidate_uncertainty.append(
                {
                    "code": "test_evidence_unavailable",
                    "message": "No available test root was supplied; confirmed_tests is unknown, not zero.",
                }
            )
        elif not owners.get(path):
            candidate_uncertainty.append(
                {
                    "code": "no_confirmed_test_owner",
                    "message": "No confirmed test owner was recovered; this is evidence absence, not proof of no tests.",
                }
            )
        if not bool(history_meta.get("available")):
            candidate_uncertainty.append(
                {
                    "code": "history_dimensions_unknown",
                    "message": "Churn and author-concentration dimensions are unavailable.",
                }
            )
        candidates.append(
            {
                "target": relative,
                "facts": facts,
                "evidence": {
                    "static_module": module,
                    "confirmed_test_paths": sorted(
                        report_path(path=p, anchor=repository_root)
                        for p in owners.get(path, set())
                    ),
                },
                "derived": {},
                "interpretation": {
                    "ranking_dimensions": list(ranking_dimensions),
                    "ranking_is_investigation_priority_only": True,
                },
                "recommendations": {"next_action": "inspect_before_edit"},
                "uncertainty": candidate_uncertainty,
                "required_next_evidence": [
                    {
                        "kind": "read_target_and_contracts",
                        "reason": "hotspot ranking is not edit authority",
                    }
                ],
                "verification_suggestions": [],
            }
        )

    # Bind every evidence class that can affect the result, not only source bytes.
    if tests_root is not None:
        for test_path in all_test_files:
            test_record = cache.get(test_path)
            if test_record.content_sha256 is not None:
                identity_entries.append(
                    {
                        "path": "@tests/"
                        + report_path(path=test_path, anchor=repository_root),
                        "sha256": test_record.content_sha256,
                    }
                )
        if not all_test_files:
            identity_entries.append(
                {
                    "path": "@tests-state",
                    "sha256": hashlib.sha256(b"enabled-empty").hexdigest(),
                }
            )
    else:
        identity_entries.append(
            {
                "path": "@tests-state",
                "sha256": hashlib.sha256(b"disabled").hexdigest(),
            }
        )
    history_marker = (
        str(history_meta.get("history_identity"))
        if history_meta.get("available")
        else "unavailable:" + str(history_meta.get("reason"))
    )
    identity_entries.append(
        {
            "path": "@history-state",
            "sha256": hashlib.sha256(history_marker.encode("utf-8")).hexdigest(),
        }
    )

    candidates.sort(key=lambda c: _rank_key(c, ranking_dimensions))
    selected = candidates[:top_n]
    deferred = [
        {
            "target": str(c["target"]),
            "reason": "outside configured top_n hotspot budget",
        }
        for c in candidates[top_n:]
    ]
    uncertainty = list(history_uncertainty)
    if deferred:
        uncertainty.append(
            {
                "code": "hotspot_candidate_budget_exhausted",
                "message": "Some source candidates were omitted by top_n.",
                "count": len(deferred),
            }
        )
    if tests_root is None or not tests_root.exists():
        uncertainty.append(
            {
                "code": "test_tree_unavailable",
                "message": "Test ownership was not measured; do not interpret this as weak verification.",
            }
        )

    repository = {
        "root": ".",
        "identity": analyzed_input_identity(identity_entries),
        "identity_kind": "analyzed-source-test-history-content-sha256",
        "input_count": len(identity_entries),
    }
    config = {
        "source_root": report_path(path=source_root, anchor=repository_root),
        "tests_root": report_path(path=tests_root, anchor=repository_root)
        if tests_root is not None
        else None,
        "package_name": package_name,
        "tests_package_name": tests_package_name,
        "top_n": top_n,
        "ranking_dimensions": list(ranking_dimensions),
        "history_max_commits": history_max_commits,
        "history_max_bytes": history_max_bytes,
        "git_timeout_seconds": git_timeout_seconds,
        "history_policy": history_policy,
        "discovery_mode": discovery_mode,
        "exclude_patterns": requested_excludes,
    }
    payload = build_probe_contract(
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        generated_at=iso_utc_now(),
        repository=repository,
        configuration_values=config,
        evidence={
            "history": history_meta,
            "test_tree_available": tests_root is not None and tests_root.exists(),
            "dimensions_are_independent_facts": True,
        },
        derived={"selected_candidate_count": len(selected)},
        interpretation={
            "ranking_policy": "lexicographic",
            "ranking_dimensions": list(ranking_dimensions),
            "opaque_composite_score": False,
            "ranking_authority": "investigation_priority_only",
        },
        uncertainty=uncertainty,
        warnings=[
            {
                "code": "ranking_not_edit_authority",
                "message": "Hotspot ordering prioritizes investigation only; it does not authorize refactoring.",
            }
        ],
        candidates=selected,
        required_next_evidence=[
            {
                "target": c["target"],
                "kind": "inspect_target_and_contracts",
                "reason": "ranking is non-authoritative",
            }
            for c in selected
        ],
        deferred_evidence=deferred,
        verification_suggestions=[],
        economics={
            **cache.metrics(),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "source_candidates": len(candidates),
            "selected_candidates": len(selected),
            "deferred_candidates": len(deferred),
            "history_bytes_read": history_meta.get("bytes_read")
            if history_meta.get("available")
            else None,
        },
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
