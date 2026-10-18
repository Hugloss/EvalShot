from __future__ import annotations

from pathlib import Path
from typing import Any

from .probe_contract import analyzed_input_identity, build_probe_contract
from .refactor_focus_discovery import DiscoveryConfig, DiscoveryResult
from .refactor_focus_analysis import AnalysisCache
from .refactor_focus_models import MATCH_AUTHORITY, FocusRow, MatchRecord
from .refactor_focus_paths import report_path

TOOL_NAME = "refactor-focus"
TOOL_VERSION = "0.5.0"


def _portable_optional_path(path: Path | None, *, repository_root: Path) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    root = repository_root.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return f"<external>/{resolved.name}"


def _candidate_uncertainty(row: FocusRow) -> list[dict[str, object]]:
    status = row["correspondence_status"]
    if status == "confirmed":
        return []
    if status == "supported":
        return [
            {
                "code": "test_correspondence_unconfirmed",
                "message": "Structural or naming support exists, but no confirmed test ownership path was proved.",
            }
        ]
    if status == "ambiguous":
        return [
            {
                "code": "test_correspondence_ambiguous",
                "message": "Only heuristic candidate evidence exists for test correspondence.",
            }
        ]
    return [
        {
            "code": "test_correspondence_missing",
            "message": "No test correspondence evidence was discovered within the configured bounds.",
        }
    ]


def _candidate_required_next_evidence(row: FocusRow) -> list[dict[str, object]]:
    status = row["correspondence_status"]
    if status == "confirmed":
        return [
            {
                "kind": "measure_refactor_locality",
                "reason": (
                    "size/complexity selects an investigation target only; "
                    "decomposition requires explicit pre/post locality evidence"
                ),
            }
        ]
    candidate_paths = sorted({match["test_path"] for match in row["matches"]})
    if candidate_paths:
        return [
            {
                "kind": "verify_test_correspondence",
                "paths": candidate_paths,
                "reason": f"test correspondence is {status}",
            }
        ]
    return [
        {
            "kind": "locate_test_correspondence",
            "reason": "no corresponding test evidence was discovered",
        }
    ]


def _candidate_verification_suggestions(row: FocusRow) -> list[dict[str, object]]:
    suggestions: list[dict[str, object]] = []
    seen: set[str] = set()
    for match in row["confirmed_matches"]:
        path = match["test_path"]
        if path in seen:
            continue
        seen.add(path)
        suggestions.append(
            {
                "kind": "test_file",
                "path": path,
                "reason": f"confirmed ownership via {match['match_type']}",
            }
        )
    return suggestions


def _candidate_from_row(row: FocusRow) -> dict[str, object]:
    uncertainty = _candidate_uncertainty(row)
    required_next_evidence = _candidate_required_next_evidence(row)
    verification_suggestions = _candidate_verification_suggestions(row)
    return {
        "target": row["source_path"],
        "facts": {
            "source_lines": row["source_lines"],
            "corresponding_test_count": row["corresponding_test_count"],
            "supporting_test_count": row["supporting_test_count"],
            "candidate_test_count": row["candidate_test_count"],
            "max_confirmed_test_lines": row["max_test_lines"],
            "oversized_confirmed_test_count": row["oversized_test_count"],
            "dependent_source_count": row["dependent_source_count"],
            "imports_out_count": row["imports_out_count"],
            "function_over_limit_count": row["function_over_limit_count"],
            "largest_function_lines": row["largest_function_lines"],
        },
        "evidence": {
            "confirmed": [dict(match) for match in row["confirmed_matches"]],
            "supporting": [dict(match) for match in row["supporting_matches"]],
            "candidate": [dict(match) for match in row["candidate_matches"]],
        },
        "derived": {
            "correspondence_status": row["correspondence_status"],
            "has_corresponding_tests": row["has_corresponding_tests"],
            "test_sync_required_if_split": row["test_sync_required_if_split"],
        },
        "interpretation": {
            "size_complexity_is_investigation_signal_only": True,
            "decomposition_requires_locality_evidence": True,
        },
        "recommendations": {
            "test_action": row["recommended_test_action"],
            "strategy": row["recommended_strategy"],
        },
        "uncertainty": uncertainty,
        "required_next_evidence": required_next_evidence,
        "verification_suggestions": verification_suggestions,
    }


def build_refactor_focus_contract(
    *,
    generated_at: str,
    repository_root: Path,
    source_root: Path,
    tests_root: Path,
    package_name: str,
    tests_package_name: str,
    file_line_threshold: int,
    function_line_threshold: int,
    top_n: int,
    transitive_max_depth: int,
    helper_max_depth: int,
    pytest_max_depth: int,
    ownership_hints_path: Path | None,
    ownership_hints_sha256: str | None,
    discovery_config: DiscoveryConfig,
    discovery_result: DiscoveryResult,
    analysis_cache: AnalysisCache,
    analyzed_python_files: list[Path],
    source_files_scanned: int,
    test_files_scanned: int,
    test_python_files_scanned: int,
    oversized_source_count: int,
    all_rows: list[FocusRow],
    selected_rows: list[FocusRow],
    economics: dict[str, object],
) -> dict[str, object]:
    repository_entries: list[dict[str, str]] = []
    for path in sorted(analyzed_python_files, key=lambda item: item.as_posix()):
        analysis = analysis_cache.get(path)
        repository_entries.append(
            {
                "path": report_path(path=path, anchor=repository_root),
                "sha256": (
                    analysis.content_sha256
                    if analysis.content_sha256 is not None
                    else f"unavailable:{analysis.parse_error or 'unknown'}"
                ),
            }
        )

    repository = {
        "root": ".",
        "identity": analyzed_input_identity(repository_entries),
        "identity_kind": "analyzed-python-inputs-sha256",
        "input_count": len(repository_entries),
    }
    configuration_values: dict[str, object] = {
        "source_root": report_path(path=source_root, anchor=repository_root),
        "tests_root": report_path(path=tests_root, anchor=repository_root),
        "package_name": package_name,
        "tests_package_name": tests_package_name,
        "file_line_threshold": file_line_threshold,
        "function_line_threshold": function_line_threshold,
        "top_n": top_n,
        "transitive_max_depth": transitive_max_depth,
        "helper_max_depth": helper_max_depth,
        "pytest_max_depth": pytest_max_depth,
        "ownership_hints_path": _portable_optional_path(
            ownership_hints_path, repository_root=repository_root
        ),
        "ownership_hints_sha256": ownership_hints_sha256,
        "discovery_mode": discovery_config.mode,
        "untracked_policy": discovery_config.untracked_policy,
        "ignored_policy": discovery_config.ignored_policy,
        "symlink_policy": discovery_config.symlink_policy,
        "exclude_patterns": list(discovery_config.exclude_patterns),
        "git_timeout_seconds": discovery_config.git_timeout_seconds,
    }

    candidates = [_candidate_from_row(row) for row in selected_rows]
    selected_targets = {row["source_path"] for row in selected_rows}
    deferred_evidence = [
        {
            "target": row["source_path"],
            "reason": "outside configured top_n candidate budget",
        }
        for row in all_rows
        if row["source_path"] not in selected_targets
    ]

    evidence_records: list[dict[str, object]] = []
    required_next_evidence: list[dict[str, object]] = []
    verification_suggestions: list[dict[str, object]] = []
    uncertainty: list[dict[str, object]] = []
    verification_seen: set[tuple[str, str]] = set()
    for row, candidate in zip(selected_rows, candidates, strict=True):
        for match in row["matches"]:
            evidence_records.append(
                {
                    "target": row["source_path"],
                    **dict(match),
                }
            )
        for item in candidate["required_next_evidence"]:  # type: ignore[index]
            required_next_evidence.append({"target": row["source_path"], **dict(item)})
        for item in candidate["uncertainty"]:  # type: ignore[index]
            uncertainty.append({"target": row["source_path"], **dict(item)})
        for item in candidate["verification_suggestions"]:  # type: ignore[index]
            key = (row["source_path"], str(item.get("path", "")))
            if key in verification_seen:
                continue
            verification_seen.add(key)
            verification_suggestions.append(
                {"target": row["source_path"], **dict(item)}
            )

    warnings: list[dict[str, object]] = []
    if int(economics.get("read_failures", 0)):
        warnings.append(
            {
                "code": "python_read_failures",
                "count": int(economics["read_failures"]),
            }
        )
    if int(economics.get("parse_failures", 0)):
        warnings.append(
            {
                "code": "python_parse_failures",
                "count": int(economics["parse_failures"]),
            }
        )

    for message in discovery_result.warnings:
        warnings.append({"code": "discovery_warning", "message": message})
    if discovery_result.symlinks_excluded:
        warnings.append(
            {
                "code": "discovery_symlinks_excluded",
                "count": discovery_result.symlinks_excluded,
            }
        )
    if discovery_result.missing_files:
        warnings.append(
            {"code": "discovery_missing_files", "count": discovery_result.missing_files}
        )

    return build_probe_contract(
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        generated_at=generated_at,
        repository=repository,
        configuration_values=configuration_values,
        evidence={
            "authority_model": dict(MATCH_AUTHORITY),
            "discovery": discovery_result.metrics(),
            "records": evidence_records,
        },
        derived={
            "source_files_scanned": source_files_scanned,
            "test_files_scanned": test_files_scanned,
            "test_python_files_scanned": test_python_files_scanned,
            "oversized_source_count": oversized_source_count,
            "selected_count": len(selected_rows),
        },
        interpretation={
            "ranking_policy": [
                "correspondence_authority",
                "function_over_limit_count_desc",
                "largest_function_lines_desc",
                "source_lines_desc",
                "dependent_source_count_desc",
                "imports_out_count_desc",
                "source_path",
            ],
            "ranking_authority": "investigation-only",
            "decomposition_authority": "refactor-locality-evidence-required",
            "selected_targets": [row["source_path"] for row in selected_rows],
        },
        uncertainty=uncertainty,
        warnings=warnings,
        candidates=candidates,
        required_next_evidence=required_next_evidence,
        deferred_evidence=deferred_evidence,
        verification_suggestions=verification_suggestions,
        economics=economics,
    )
