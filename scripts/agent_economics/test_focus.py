from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .inherited_method_ownership import build_inherited_method_ownership_evidence
from .probe_contract import analyzed_input_identity, build_probe_contract
from .refactor_focus_analysis import AnalysisCache
from .refactor_focus_discovery import (
    DEFAULT_EXCLUDE_PATTERNS,
    DiscoveryConfig,
    DiscoveryError,
    discover_python_roots,
    normalize_exclude_patterns,
)
from .refactor_focus_hints import OwnershipHintsError, load_declared_ownership_hints
from .refactor_focus_imports import build_import_index
from .refactor_focus_matching import (
    build_test_ownership_evidence,
    direct_name_test_candidates,
    mirrored_test_candidates,
)
from .refactor_focus_paths import iso_utc_now, module_path_for_file, report_path
from .refactor_focus_pytest import build_pytest_ownership_evidence

TOOL_NAME = "test-focus"
TOOL_VERSION = "0.7.3"


class TestFocusError(ValueError):
    pass


@dataclass(frozen=True)
class GateSpec:
    name: str
    command: str


@dataclass(frozen=True)
class OwnershipRecord:
    source_path: Path
    test_path: Path
    match_type: str
    provenance: str


@dataclass(frozen=True)
class DependentRecord:
    source_path: Path
    depth: int
    via_module: str


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _portable_changed_path(value: Path, repository_root: Path) -> Path:
    root = repository_root.resolve()
    candidate = value if value.is_absolute() else root / value
    # Do not require existence: deleted/renamed paths must remain representable.
    absolute = candidate.resolve(strict=False)
    try:
        absolute.relative_to(root)
    except ValueError as exc:
        raise TestFocusError(f"changed path escapes repository_root: {value}") from exc
    return absolute


def _source_module_for_changed(
    path: Path,
    *,
    source_root: Path,
    package_name: str,
) -> str | None:
    if path.suffix != ".py" or not _inside(path, source_root):
        return None
    try:
        return module_path_for_file(
            path=path, root=source_root, package_name=package_name
        )
    except ValueError:
        return None


def _test_module_for_changed(
    path: Path,
    *,
    tests_root: Path,
    tests_package_name: str,
) -> str | None:
    if path.suffix != ".py" or not _inside(path, tests_root):
        return None
    try:
        return module_path_for_file(
            path=path, root=tests_root, package_name=tests_package_name
        )
    except ValueError:
        return None


def _dependent_sources(
    *,
    start_module: str,
    source_import_index: dict[str, set[Path]],
    source_module_by_path: dict[Path, str],
    max_depth: int,
    max_sources: int,
) -> tuple[list[DependentRecord], bool]:
    if max_depth <= 0 or max_sources <= 0:
        return [], False
    queue: deque[tuple[str, int]] = deque([(start_module, 0)])
    seen_modules = {start_module}
    results: list[DependentRecord] = []
    truncated = False
    while queue:
        module, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for importer in sorted(
            source_import_index.get(module, set()), key=lambda p: p.as_posix()
        ):
            next_module = source_module_by_path.get(importer)
            if next_module is None or next_module in seen_modules:
                continue
            seen_modules.add(next_module)
            if len(results) >= max_sources:
                truncated = True
                continue
            record = DependentRecord(
                source_path=importer,
                depth=depth + 1,
                via_module=module,
            )
            results.append(record)
            queue.append((next_module, depth + 1))
    return results, truncated


def _dedupe_ownership(records: Iterable[OwnershipRecord]) -> list[OwnershipRecord]:
    best: dict[tuple[Path, Path], OwnershipRecord] = {}
    priority = {
        "declared_owner": 0,
        "import_exact": 1,
        "inherited_method_call": 2,
        "dynamic_import_literal": 3,
        "conftest_fixture": 3,
        "pytest_fixture": 4,
        "pytest_plugin_fixture": 5,
        "pytest_plugin": 6,
        "support_loader": 7,
    }
    for record in records:
        key = (record.source_path, record.test_path)
        existing = best.get(key)
        if existing is None or priority.get(record.match_type, 100) < priority.get(
            existing.match_type, 100
        ):
            best[key] = record
    return sorted(
        best.values(),
        key=lambda item: (
            item.source_path.as_posix(),
            item.test_path.as_posix(),
            item.match_type,
        ),
    )


def _append_test_suggestion(
    suggestions: dict[str, dict[str, object]],
    *,
    test_path: Path,
    repository_root: Path,
    stage: str,
    stage_number: int,
    reason: str,
    changed_target: str,
    test_lines: int,
    impact_depth: int | None = None,
) -> None:
    key = report_path(path=test_path, anchor=repository_root)
    candidate = {
        "kind": "test_file",
        "stage": stage,
        "stage_number": stage_number,
        "path": key,
        "reason": reason,
        "changed_target": changed_target,
        "test_lines": test_lines,
    }
    if impact_depth is not None:
        candidate["impact_depth"] = impact_depth
    existing = suggestions.get(key)
    if existing is None or int(candidate["stage_number"]) < int(
        existing["stage_number"]
    ):
        suggestions[key] = candidate


def _read_changed_identity(
    path: Path,
    *,
    repository_root: Path,
    max_bytes: int,
) -> tuple[dict[str, str], int, int, str | None]:
    label = report_path(path=path, anchor=repository_root)
    if not path.exists():
        return {"path": label, "sha256": "missing"}, 0, 0, "changed path does not exist"
    if not path.is_file():
        return (
            {"path": label, "sha256": "not-a-file"},
            0,
            0,
            "changed path is not a regular file",
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        return (
            {"path": label, "sha256": f"stat-error:{type(exc).__name__}"},
            0,
            0,
            "changed path could not be stat'ed",
        )
    if size > max_bytes:
        return (
            {"path": label, "sha256": f"unhashed-oversized:{size}"},
            0,
            0,
            f"changed path exceeds identity read budget ({size} > {max_bytes} bytes)",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return (
            {"path": label, "sha256": f"read-error:{type(exc).__name__}"},
            0,
            0,
            "changed path could not be read",
        )
    return {"path": label, "sha256": hashlib.sha256(raw).hexdigest()}, 1, len(raw), None


def test_focus_audit(
    *,
    repository_root: Path,
    source_root: Path,
    tests_root: Path,
    changed_paths: list[Path],
    package_name: str | None = None,
    tests_package_name: str | None = None,
    artifact_path: Path | None = None,
    helper_max_depth: int = 2,
    pytest_max_depth: int = 2,
    impact_max_depth: int = 1,
    impact_max_sources: int = 100,
    max_tests_per_stage: int = 50,
    max_changed_identity_bytes: int = 1_048_576,
    ownership_hints_path: Path | None = None,
    gates: tuple[GateSpec, ...] = (),
    discovery_mode: str = "auto",
    untracked_policy: str = "include",
    ignored_policy: str = "exclude",
    symlink_policy: str = "exclude",
    exclude_patterns: tuple[str, ...] | None = None,
    use_default_excludes: bool = True,
    git_timeout_seconds: float = 5.0,
) -> dict[str, object]:
    started = time.perf_counter()
    repository_root = repository_root.resolve()
    source_root = (
        source_root if source_root.is_absolute() else repository_root / source_root
    ).resolve()
    tests_root = (
        tests_root if tests_root.is_absolute() else repository_root / tests_root
    ).resolve()
    if not changed_paths:
        raise TestFocusError("at least one changed path is required")
    if helper_max_depth < 0 or pytest_max_depth < 0 or impact_max_depth < 0:
        raise TestFocusError("depth bounds must be non-negative")
    if (
        impact_max_sources < 1
        or max_tests_per_stage < 1
        or max_changed_identity_bytes < 1
    ):
        raise TestFocusError("source/test/identity bounds must be positive")
    for label, root in (("source_root", source_root), ("tests_root", tests_root)):
        if not _inside(root, repository_root):
            raise TestFocusError(f"{label} must be inside repository_root")
    effective_package_name = package_name or source_root.name
    effective_tests_package_name = tests_package_name or tests_root.name

    requested_excludes = list(DEFAULT_EXCLUDE_PATTERNS if use_default_excludes else ())
    requested_excludes.extend(exclude_patterns or ())
    try:
        discovery_config = DiscoveryConfig(
            mode=discovery_mode,
            untracked_policy=untracked_policy,
            ignored_policy=ignored_policy,
            symlink_policy=symlink_policy,
            exclude_patterns=normalize_exclude_patterns(requested_excludes),
            git_timeout_seconds=git_timeout_seconds,
        )
        discovery = discover_python_roots(
            roots={"source": source_root, "tests": tests_root},
            repository_root=repository_root,
            config=discovery_config,
        )
    except DiscoveryError as exc:
        raise TestFocusError(str(exc)) from exc

    source_files = list(discovery.files_for("source"))
    all_test_python_files = list(discovery.files_for("tests"))
    test_files = [
        path
        for path in all_test_python_files
        if path.name.startswith("test_") or path.name.endswith("_test.py")
    ]
    source_file_set = set(source_files)
    test_file_set = set(test_files)

    analysis_cache = AnalysisCache()
    analyzed_python_files = sorted(source_file_set | set(all_test_python_files))
    analysis_cache.prewarm(analyzed_python_files)

    source_module_by_path = {
        path: module_path_for_file(
            path=path, root=source_root, package_name=effective_package_name
        )
        for path in source_files
    }
    test_module_by_path = {
        path: module_path_for_file(
            path=path, root=tests_root, package_name=effective_tests_package_name
        )
        for path in all_test_python_files
    }
    module_to_path = {
        **{module: path for path, module in source_module_by_path.items()},
        **{module: path for path, module in test_module_by_path.items()},
    }
    source_import_index = build_import_index(
        files=source_files,
        root=source_root,
        current_package_name=effective_package_name,
        package_names={effective_package_name},
        analysis_cache=analysis_cache,
    )

    ownership: list[OwnershipRecord] = []
    for item in build_test_ownership_evidence(
        test_files=test_files,
        all_test_python_files=all_test_python_files,
        module_to_path=module_to_path,
        source_root=source_root,
        tests_root=tests_root,
        package_name=effective_package_name,
        tests_package_name=effective_tests_package_name,
        analysis_cache=analysis_cache,
        repository_root=repository_root,
        helper_max_depth=helper_max_depth,
    ):
        ownership.append(
            OwnershipRecord(
                item.source_path, item.test_path, item.match_type, item.provenance
            )
        )
    for item in build_inherited_method_ownership_evidence(
        source_files=source_files,
        test_files=test_files,
        source_root=source_root,
        tests_root=tests_root,
        package_name=effective_package_name,
        tests_package_name=effective_tests_package_name,
        analysis_cache=analysis_cache,
        repository_root=repository_root,
    ):
        ownership.append(
            OwnershipRecord(
                item.source_path, item.test_path, item.match_type, item.provenance
            )
        )
    for item in build_pytest_ownership_evidence(
        test_files=test_files,
        all_test_python_files=all_test_python_files,
        module_to_path=module_to_path,
        source_root=source_root,
        tests_root=tests_root,
        package_name=effective_package_name,
        tests_package_name=effective_tests_package_name,
        analysis_cache=analysis_cache,
        repository_root=repository_root,
        pytest_max_depth=pytest_max_depth,
    ):
        ownership.append(
            OwnershipRecord(
                item.source_path, item.test_path, item.match_type, item.provenance
            )
        )

    ownership_hints_sha256: str | None = None
    auxiliary_files_read = 0
    auxiliary_bytes_read = 0
    if ownership_hints_path is not None:
        hint_path = (
            ownership_hints_path
            if ownership_hints_path.is_absolute()
            else repository_root / ownership_hints_path
        )
        try:
            hints = load_declared_ownership_hints(
                hints_path=hint_path,
                repository_root=repository_root,
                source_files=source_file_set,
                test_files=test_file_set,
            )
        except OwnershipHintsError as exc:
            raise TestFocusError(str(exc)) from exc
        ownership_hints_sha256 = hints.content_sha256
        auxiliary_files_read += 1
        auxiliary_bytes_read += hints.bytes_read
        for item in hints.relationships:
            ownership.append(
                OwnershipRecord(
                    item.source_path, item.test_path, "declared_owner", item.provenance
                )
            )
    ownership = _dedupe_ownership(ownership)
    ownership_by_source: dict[Path, list[OwnershipRecord]] = {}
    for item in ownership:
        ownership_by_source.setdefault(item.source_path, []).append(item)

    changed = sorted(
        {_portable_changed_path(path, repository_root) for path in changed_paths},
        key=lambda item: report_path(path=item, anchor=repository_root),
    )

    repository_entries: list[dict[str, str]] = []
    cached_paths = set(analyzed_python_files)
    for path in analyzed_python_files:
        record = analysis_cache.get(path)
        repository_entries.append(
            {
                "path": report_path(path=path, anchor=repository_root),
                "sha256": record.content_sha256
                or f"unavailable:{record.parse_error or 'unknown'}",
            }
        )
    changed_identity_reads = 0
    changed_identity_bytes = 0
    identity_warnings: list[dict[str, object]] = []
    for path in changed:
        if path.resolve() in {cached.resolve() for cached in cached_paths}:
            continue
        entry, reads, bytes_read, warning = _read_changed_identity(
            path,
            repository_root=repository_root,
            max_bytes=max_changed_identity_bytes,
        )
        repository_entries.append(entry)
        changed_identity_reads += reads
        changed_identity_bytes += bytes_read
        if warning:
            identity_warnings.append(
                {
                    "code": "changed_identity_incomplete",
                    "path": entry["path"],
                    "message": warning,
                }
            )

    direct_suggestions: dict[str, dict[str, object]] = {}
    affected_suggestions: dict[str, dict[str, object]] = {}
    candidates: list[dict[str, object]] = []
    required_next_evidence: list[dict[str, object]] = []
    uncertainty: list[dict[str, object]] = []
    deferred_evidence: list[dict[str, object]] = []
    impact_truncated_any = False

    for path in changed:
        target = report_path(path=path, anchor=repository_root)
        candidate_evidence = {"confirmed": [], "supporting": [], "candidate": []}
        candidate_required: list[dict[str, object]] = []
        candidate_uncertainty: list[dict[str, object]] = []
        candidate_verification: list[dict[str, object]] = []
        kind = "other"
        source_module = _source_module_for_changed(
            path, source_root=source_root, package_name=effective_package_name
        )
        test_module = _test_module_for_changed(
            path, tests_root=tests_root, tests_package_name=effective_tests_package_name
        )

        if path in test_file_set:
            kind = "test"
            lines = analysis_cache.get(path).line_count
            _append_test_suggestion(
                direct_suggestions,
                test_path=path,
                repository_root=repository_root,
                stage="direct",
                stage_number=1,
                reason="the test file itself changed",
                changed_target=target,
                test_lines=lines,
            )
            candidate_evidence["confirmed"].append(
                {
                    "kind": "changed_test",
                    "test_path": target,
                    "provenance": "user-supplied changed path",
                }
            )
        elif path in source_file_set:
            kind = "source"
            direct_records = ownership_by_source.get(path, [])
            for record in direct_records:
                test_label = report_path(path=record.test_path, anchor=repository_root)
                candidate_evidence["confirmed"].append(
                    {
                        "test_path": test_label,
                        "match_type": record.match_type,
                        "provenance": record.provenance,
                    }
                )
                _append_test_suggestion(
                    direct_suggestions,
                    test_path=record.test_path,
                    repository_root=repository_root,
                    stage="direct",
                    stage_number=1,
                    reason=f"confirmed ownership via {record.match_type}",
                    changed_target=target,
                    test_lines=analysis_cache.get(record.test_path).line_count,
                )
            supporting_paths = set(
                direct_name_test_candidates(source_file=path, test_files=test_files)
            )
            for mirrored in mirrored_test_candidates(
                source_file=path, source_root=source_root, tests_root=tests_root
            ):
                if mirrored in test_file_set:
                    supporting_paths.add(mirrored)
            confirmed_test_paths = {record.test_path for record in direct_records}
            for support in sorted(supporting_paths - confirmed_test_paths):
                candidate_evidence["supporting"].append(
                    {
                        "test_path": report_path(path=support, anchor=repository_root),
                        "match_type": "naming_or_mirrored_convention",
                        "provenance": "path/name convention only; inspect before treating as ownership",
                    }
                )
            if not direct_records:
                candidate_required.append(
                    {
                        "kind": "verify_test_correspondence",
                        "reason": "changed source has no confirmed owning test",
                        "candidate_test_paths": [
                            item["test_path"]
                            for item in candidate_evidence["supporting"]
                        ],
                    }
                )
                candidate_uncertainty.append(
                    {
                        "code": "direct_test_ownership_missing",
                        "message": "No confirmed direct owning test was recovered for this changed source file.",
                    }
                )

            if source_module is not None and impact_max_depth > 0:
                dependents, truncated = _dependent_sources(
                    start_module=source_module,
                    source_import_index=source_import_index,
                    source_module_by_path=source_module_by_path,
                    max_depth=impact_max_depth,
                    max_sources=impact_max_sources,
                )
                impact_truncated_any = impact_truncated_any or truncated
                for dependent in dependents:
                    for record in ownership_by_source.get(dependent.source_path, []):
                        test_label = report_path(
                            path=record.test_path, anchor=repository_root
                        )
                        candidate_evidence["supporting"].append(
                            {
                                "test_path": test_label,
                                "match_type": "affected_dependent_owner",
                                "dependent_source": report_path(
                                    path=dependent.source_path, anchor=repository_root
                                ),
                                "impact_depth": dependent.depth,
                                "ownership_match_type": record.match_type,
                                "provenance": record.provenance,
                            }
                        )
                        _append_test_suggestion(
                            affected_suggestions,
                            test_path=record.test_path,
                            repository_root=repository_root,
                            stage="affected",
                            stage_number=2,
                            reason=(
                                "confirmed owner of a source file that depends on the changed module: "
                                f"{report_path(path=dependent.source_path, anchor=repository_root)}"
                            ),
                            changed_target=target,
                            test_lines=analysis_cache.get(record.test_path).line_count,
                            impact_depth=dependent.depth,
                        )
        else:
            if test_module is not None or source_module is not None:
                kind = "python_not_discovered"
                candidate_uncertainty.append(
                    {
                        "code": "changed_python_not_discovered",
                        "message": "Changed Python path is under a configured root but was not admitted by discovery or does not exist.",
                    }
                )
                candidate_required.append(
                    {"kind": "inspect_discovery_or_deleted_path", "path": target}
                )
            elif not path.exists():
                kind = "missing"
                candidate_uncertainty.append(
                    {
                        "code": "changed_path_missing",
                        "message": "Changed path does not exist in this checkout.",
                    }
                )
                candidate_required.append(
                    {"kind": "recover_deleted_or_renamed_impact", "path": target}
                )
            else:
                kind = "non_python"
                candidate_uncertainty.append(
                    {
                        "code": "focused_test_mapping_unsupported",
                        "message": "This probe has no structural ownership model for the changed non-Python path.",
                    }
                )
                candidate_required.append(
                    {"kind": "use_repository_gate_or_provider_impact", "path": target}
                )

        # Candidate-level suggestions are filled after global stage bounding.
        candidates.append(
            {
                "target": target,
                "facts": {
                    "changed_kind": kind,
                    "exists": path.exists(),
                    "is_discovered_source": path in source_file_set,
                    "is_discovered_test": path in test_file_set,
                },
                "evidence": candidate_evidence,
                "derived": {
                    "confirmed_direct_test_count": len(candidate_evidence["confirmed"]),
                    "supporting_relationship_count": len(
                        candidate_evidence["supporting"]
                    ),
                },
                "interpretation": {
                    "focused_verification_available": bool(
                        path in test_file_set
                        or (path in source_file_set and ownership_by_source.get(path))
                    )
                },
                "recommendations": {
                    "verification_order": ["direct", "affected", "broader_gate"]
                },
                "uncertainty": candidate_uncertainty,
                "required_next_evidence": candidate_required,
                "verification_suggestions": candidate_verification,
            }
        )
        for item in candidate_required:
            required_next_evidence.append({"target": target, **item})
        for item in candidate_uncertainty:
            uncertainty.append({"target": target, **item})

    # Remove stage-2 duplicates already covered by a direct test.
    for key in set(affected_suggestions) & set(direct_suggestions):
        del affected_suggestions[key]

    def bounded_stage(
        stage_items: dict[str, dict[str, object]],
        stage: str,
    ) -> list[dict[str, object]]:
        ordered = [stage_items[key] for key in sorted(stage_items)]
        selected = ordered[:max_tests_per_stage]
        for item in ordered[max_tests_per_stage:]:
            deferred_evidence.append(
                {
                    "kind": "test_file",
                    "stage": stage,
                    "path": item["path"],
                    "reason": "outside configured max_tests_per_stage budget",
                }
            )
        return selected

    direct_selected = bounded_stage(direct_suggestions, "direct")
    affected_selected = bounded_stage(affected_suggestions, "affected")
    verification_suggestions: list[dict[str, object]] = [
        *direct_selected,
        *affected_selected,
    ]
    gate_suggestions = [
        {
            "kind": "command",
            "stage": "broader_gate",
            "stage_number": 3,
            "name": gate.name,
            "command": gate.command,
            "reason": "repository-supplied broader verification gate; focused tests do not prove broader verification unnecessary",
        }
        for gate in gates
    ]
    verification_suggestions.extend(gate_suggestions)
    if not gates:
        item = {
            "kind": "determine_repository_gates",
            "reason": "no broader repository verification gates were supplied to the probe",
        }
        required_next_evidence.append(item)
        uncertainty.append(
            {
                "code": "broader_verification_unspecified",
                "message": "Focused test suggestions are not evidence that broader repository gates can be skipped.",
            }
        )
    if impact_truncated_any:
        uncertainty.append(
            {
                "code": "impact_scan_truncated",
                "message": "Reverse source dependency traversal hit impact_max_sources; affected-test evidence is incomplete.",
            }
        )

    # Attach selected global verification items back to candidate records by target.
    by_target: dict[str, list[dict[str, object]]] = {}
    for item in [*direct_selected, *affected_selected]:
        target = str(item.get("changed_target", ""))
        if target:
            by_target.setdefault(target, []).append(dict(item))
    for candidate in candidates:
        candidate["verification_suggestions"] = by_target.get(
            str(candidate["target"]), []
        )

    repository = {
        "root": ".",
        "identity": analyzed_input_identity(repository_entries),
        "identity_kind": "test-focus-analyzed-inputs-sha256",
        "input_count": len(repository_entries),
    }
    hint_label: str | None = None
    if ownership_hints_path is not None:
        hint_abs = (
            ownership_hints_path
            if ownership_hints_path.is_absolute()
            else repository_root / ownership_hints_path
        )
        try:
            hint_label = hint_abs.resolve().relative_to(repository_root).as_posix()
        except ValueError:
            hint_label = f"<external>/{hint_abs.name}"
    configuration_values = {
        "source_root": report_path(path=source_root, anchor=repository_root),
        "tests_root": report_path(path=tests_root, anchor=repository_root),
        "package_name": effective_package_name,
        "tests_package_name": effective_tests_package_name,
        "changed_paths": [
            report_path(path=path, anchor=repository_root) for path in changed
        ],
        "helper_max_depth": helper_max_depth,
        "pytest_max_depth": pytest_max_depth,
        "impact_max_depth": impact_max_depth,
        "impact_max_sources": impact_max_sources,
        "max_tests_per_stage": max_tests_per_stage,
        "max_changed_identity_bytes": max_changed_identity_bytes,
        "ownership_hints_path": hint_label,
        "ownership_hints_sha256": ownership_hints_sha256,
        "gates": [{"name": gate.name, "command": gate.command} for gate in gates],
        "discovery_mode": discovery_config.mode,
        "untracked_policy": discovery_config.untracked_policy,
        "ignored_policy": discovery_config.ignored_policy,
        "symlink_policy": discovery_config.symlink_policy,
        "exclude_patterns": list(discovery_config.exclude_patterns),
        "git_timeout_seconds": discovery_config.git_timeout_seconds,
    }
    warnings: list[dict[str, object]] = [*identity_warnings]
    for message in discovery.warnings:
        warnings.append({"code": "discovery_warning", "message": message})
    if analysis_cache.read_failures:
        warnings.append(
            {"code": "python_read_failures", "count": analysis_cache.read_failures}
        )
    if analysis_cache.parse_failures:
        warnings.append(
            {"code": "python_parse_failures", "count": analysis_cache.parse_failures}
        )

    test_lines_selected = sum(
        int(item.get("test_lines", 0))
        for item in [*direct_selected, *affected_selected]
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    economics = {
        **analysis_cache.metrics(),
        "auxiliary_files_read": auxiliary_files_read + changed_identity_reads,
        "auxiliary_bytes_read": auxiliary_bytes_read + changed_identity_bytes,
        "total_files_read": analysis_cache.files_read
        + auxiliary_files_read
        + changed_identity_reads,
        "total_bytes_read": analysis_cache.bytes_read
        + auxiliary_bytes_read
        + changed_identity_bytes,
        "changed_path_count": len(changed),
        "direct_tests_selected": len(direct_selected),
        "affected_tests_selected": len(affected_selected),
        "broader_gates_supplied": len(gates),
        "deferred_test_count": len(deferred_evidence),
        "selected_test_lines": test_lines_selected,
        "elapsed_ms": elapsed_ms,
        "discovery": discovery.metrics(),
    }

    payload = build_probe_contract(
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        generated_at=iso_utc_now(),
        repository=repository,
        configuration_values=configuration_values,
        evidence={
            "ownership_authority": "confirmed import/loader/pytest/declaration relationships only",
            "confirmed_ownership_relationships": [
                {
                    "source": report_path(
                        path=item.source_path, anchor=repository_root
                    ),
                    "test": report_path(path=item.test_path, anchor=repository_root),
                    "match_type": item.match_type,
                    "provenance": item.provenance,
                }
                for item in ownership
            ],
            "discovery": discovery.metrics(),
        },
        derived={
            "changed_count": len(changed),
            "direct_test_count": len(direct_selected),
            "affected_test_count": len(affected_selected),
            "broader_gate_count": len(gates),
        },
        interpretation={
            "verification_ladder": [
                {
                    "stage": 1,
                    "name": "direct",
                    "meaning": "changed tests and confirmed owning tests",
                },
                {
                    "stage": 2,
                    "name": "affected",
                    "meaning": "confirmed owners of bounded reverse source dependents",
                },
                {
                    "stage": 3,
                    "name": "broader_gate",
                    "meaning": "repository-supplied broader verification",
                },
            ],
            "sufficiency_rule": "focused suggestions never prove broader verification unnecessary",
        },
        uncertainty=uncertainty,
        warnings=warnings,
        candidates=candidates,
        required_next_evidence=required_next_evidence,
        deferred_evidence=deferred_evidence,
        verification_suggestions=verification_suggestions,
        economics=economics,
    )
    if artifact_path is not None:
        output = (
            artifact_path
            if artifact_path.is_absolute()
            else repository_root / artifact_path
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        import json

        output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return payload
