from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from pathlib import Path
from typing import Iterable

from .probe_contract import analyzed_input_identity, build_probe_contract
from .refactor_focus_analysis import AnalysisCache
from .refactor_focus_discovery import (
    DEFAULT_EXCLUDE_PATTERNS,
    DiscoveryConfig,
    DiscoveryError,
    discover_python_roots,
    normalize_exclude_patterns,
)
from .refactor_focus_imports import build_import_index
from .refactor_focus_paths import iso_utc_now, module_path_for_file, report_path

TOOL_NAME = "change-impact"
TOOL_VERSION = "0.8.0"


class ChangeImpactError(ValueError):
    pass


def _portable_path(value: Path | str, *, repository_root: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repository_root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(repository_root)
    except ValueError as exc:
        raise ChangeImpactError(
            f"changed path escapes repository root: {value}"
        ) from exc
    return resolved


def _module_for_changed_path(
    *, path: Path, source_root: Path, package_name: str
) -> str | None:
    try:
        path.relative_to(source_root)
    except ValueError:
        return None
    if path.suffix.lower() != ".py":
        return None
    return module_path_for_file(path=path, root=source_root, package_name=package_name)


def _source_entries(
    *, files: list[Path], repository_root: Path, cache: AnalysisCache
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in files:
        record = cache.get(path)
        entries.append(
            {
                "path": report_path(path=path, anchor=repository_root),
                "sha256": record.content_sha256
                or f"unavailable:{record.parse_error or 'unknown'}",
            }
        )
    return entries


def _impact_paths(
    *,
    seed_modules: list[str],
    seed_paths: set[Path],
    import_index: dict[str, set[Path]],
    module_by_path: dict[Path, str],
    max_depth: int,
    max_sources: int,
) -> tuple[dict[Path, tuple[int, tuple[str, ...]]], list[dict[str, object]]]:
    # Store the shortest deterministic module chain from a changed module to
    # each reverse importer. A path can be reachable from multiple seeds; the
    # shortest path wins, then lexical chain order breaks ties.
    impacted: dict[Path, tuple[int, tuple[str, ...]]] = {}
    deferred: list[dict[str, object]] = []
    queue: deque[tuple[str, int, tuple[str, ...]]] = deque(
        (module, 0, (module,)) for module in sorted(set(seed_modules))
    )
    seen_depth: dict[str, int] = {module: 0 for module in seed_modules}

    while queue:
        current_module, depth, chain = queue.popleft()
        if depth >= max_depth:
            continue
        for importer in sorted(
            import_index.get(current_module, set()), key=lambda p: p.as_posix()
        ):
            importer_module = module_by_path.get(importer)
            if importer_module is None:
                continue
            next_depth = depth + 1
            next_chain = (*chain, importer_module)
            if importer not in seed_paths:
                previous = impacted.get(importer)
                candidate = (next_depth, next_chain)
                if previous is None:
                    if len(impacted) >= max_sources:
                        deferred.append(
                            {
                                "target": importer.as_posix(),
                                "reason": "outside configured impact_max_sources budget",
                                "depth": next_depth,
                            }
                        )
                    else:
                        impacted[importer] = candidate
                elif candidate < previous:
                    impacted[importer] = candidate
            prior_depth = seen_depth.get(importer_module)
            if next_depth < max_depth and (
                prior_depth is None or next_depth < prior_depth
            ):
                seen_depth[importer_module] = next_depth
                queue.append((importer_module, next_depth, next_chain))

    # Deduplicate deferred records by target/depth after traversal.
    unique: dict[tuple[str, int], dict[str, object]] = {}
    for item in deferred:
        unique[(str(item["target"]), int(item["depth"]))] = item
    return impacted, [unique[key] for key in sorted(unique)]


def change_impact_audit(
    *,
    repository_root: Path,
    source_root: Path,
    changed_paths: Iterable[Path | str],
    package_name: str | None = None,
    artifact_path: Path | None = None,
    impact_max_depth: int = 3,
    impact_max_sources: int = 100,
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
        (repository_root / source_root).resolve()
        if not source_root.is_absolute()
        else source_root.resolve()
    )
    if impact_max_depth < 0:
        raise ChangeImpactError("impact_max_depth must be >= 0")
    if impact_max_sources < 1:
        raise ChangeImpactError("impact_max_sources must be >= 1")
    if not source_root.exists():
        raise ChangeImpactError(f"source root does not exist: {source_root}")
    try:
        source_root.relative_to(repository_root)
    except ValueError as exc:
        raise ChangeImpactError("source root must be inside repository root") from exc

    effective_package = package_name or source_root.name
    changed_preview: list[Path] = []
    seen_preview: set[Path] = set()
    for raw in changed_paths:
        path = _portable_path(raw, repository_root=repository_root)
        if path not in seen_preview:
            seen_preview.add(path)
            changed_preview.append(path)
    if not changed_preview:
        raise ChangeImpactError("at least one changed path is required")
    preview_modules = [
        module
        for path in changed_preview
        if (
            module := _module_for_changed_path(
                path=path, source_root=source_root, package_name=effective_package
            )
        )
        is not None
    ]

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
        if preview_modules:
            discovery = discover_python_roots(
                roots={"source": source_root},
                repository_root=repository_root,
                config=discovery_config,
            )
        else:
            discovery = None
    except DiscoveryError as exc:
        raise ChangeImpactError(str(exc)) from exc

    if discovery is None:
        changed_labels = [
            report_path(path=path, anchor=repository_root) for path in changed_preview
        ]
        uncertainty = [
            {
                "target": label,
                "code": "static_impact_unsupported",
                "message": (
                    "Changed path is outside the configured Python source root "
                    "or is not Python."
                ),
            }
            for label in changed_labels
        ]
        required_next = [
            {
                "target": label,
                "kind": "obtain_provider_or_repository_impact_evidence",
                "reason": (
                    "fallback static impact only models Python imports under source_root"
                ),
            }
            for label in changed_labels
        ]
        repository_entries = [
            {"path": label, "sha256": "unsupported-static-impact"}
            for label in changed_labels
        ]
        repository = {
            "root": ".",
            "identity": analyzed_input_identity(repository_entries),
            "identity_kind": "unsupported-change-path-set-sha256",
            "input_count": len(repository_entries),
        }
        configuration_values = {
            "source_root": report_path(path=source_root, anchor=repository_root),
            "package_name": effective_package,
            "changed_paths": changed_labels,
            "impact_max_depth": impact_max_depth,
            "impact_max_sources": impact_max_sources,
            "discovery_mode": discovery_config.mode,
            "untracked_policy": discovery_config.untracked_policy,
            "ignored_policy": discovery_config.ignored_policy,
            "symlink_policy": discovery_config.symlink_policy,
            "exclude_patterns": list(discovery_config.exclude_patterns),
            "git_timeout_seconds": discovery_config.git_timeout_seconds,
        }
        economics = {
            "files_read": 0,
            "bytes_read": 0,
            "ast_parses": 0,
            "read_failures": 0,
            "parse_failures": 0,
            "cache_hits": 0,
            "unique_files_cached": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "source_files_discovered": 0,
            "changed_paths": len(changed_preview),
            "seed_modules": 0,
            "impacted_sources_selected": 0,
            "impacted_sources_deferred": 0,
            "impact_max_depth": impact_max_depth,
            "impact_max_sources": impact_max_sources,
            "discovery": {
                "backend": "not_run",
                "reason": "no supported Python changed paths",
            },
        }
        payload = build_probe_contract(
            tool_name=TOOL_NAME,
            tool_version=TOOL_VERSION,
            generated_at=iso_utc_now(),
            repository=repository,
            configuration_values=configuration_values,
            evidence={
                "changed": [
                    {"path": label, "kind": "unsupported", "discovered": False}
                    for label in changed_labels
                ],
                "records": [],
                "authority": "static Python import reachability only",
            },
            derived={"selected_impacted_source_count": 0, "direct_dependent_count": 0},
            interpretation={
                "relationship_boundary": (
                    "static reachability is not runtime behavior or edit authority"
                )
            },
            uncertainty=uncertainty,
            warnings=[],
            candidates=[],
            required_next_evidence=required_next,
            deferred_evidence=[],
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
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return payload

    source_files = list(discovery.files_for("source"))
    source_set = set(source_files)
    cache = AnalysisCache()
    cache.prewarm(source_files)
    module_by_path = {
        path: module_path_for_file(
            path=path, root=source_root, package_name=effective_package
        )
        for path in source_files
    }
    import_index = build_import_index(
        files=source_files,
        root=source_root,
        current_package_name=effective_package,
        package_names={effective_package},
        analysis_cache=cache,
    )

    changed = list(changed_preview)

    seed_modules: list[str] = []
    seed_paths: set[Path] = set()
    changed_records: list[dict[str, object]] = []
    uncertainty: list[dict[str, object]] = []
    required_next: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = [
        {"code": "discovery_warning", "message": message}
        for message in discovery.warnings
    ]
    if cache.read_failures:
        warnings.append({"code": "python_read_failures", "count": cache.read_failures})
    if cache.parse_failures:
        warnings.append(
            {"code": "python_parse_failures", "count": cache.parse_failures}
        )

    for path in changed:
        label = report_path(path=path, anchor=repository_root)
        module = _module_for_changed_path(
            path=path,
            source_root=source_root,
            package_name=effective_package,
        )
        if module is None:
            changed_records.append(
                {"path": label, "kind": "unsupported", "discovered": False}
            )
            uncertainty.append(
                {
                    "target": label,
                    "code": "static_impact_unsupported",
                    "message": "Changed path is outside the configured Python source root or is not Python.",
                }
            )
            required_next.append(
                {
                    "target": label,
                    "kind": "obtain_provider_or_repository_impact_evidence",
                    "reason": "fallback static impact only models Python imports under source_root",
                }
            )
            continue
        discovered = path in source_set
        seed_modules.append(module)
        if discovered:
            seed_paths.add(path)
        else:
            uncertainty.append(
                {
                    "target": label,
                    "code": "changed_source_not_discovered",
                    "message": "Module identity was inferred from the changed path, but the source file is absent from current discovery.",
                }
            )
            required_next.append(
                {
                    "target": label,
                    "kind": "verify_deleted_or_renamed_source",
                    "reason": "current repository bytes cannot confirm the changed source module",
                }
            )
        changed_records.append(
            {
                "path": label,
                "kind": "python_source",
                "module": module,
                "discovered": discovered,
            }
        )

    impacted_raw, deferred_raw = _impact_paths(
        seed_modules=seed_modules,
        seed_paths=seed_paths,
        import_index=import_index,
        module_by_path=module_by_path,
        max_depth=impact_max_depth,
        max_sources=impact_max_sources,
    )
    impacted = sorted(
        impacted_raw.items(),
        key=lambda item: (
            item[1][0],
            report_path(path=item[0], anchor=repository_root),
        ),
    )
    candidates: list[dict[str, object]] = []
    evidence_records: list[dict[str, object]] = []
    for path, (depth, chain) in impacted:
        label = report_path(path=path, anchor=repository_root)
        portable_chain = list(chain)
        evidence = {
            "kind": "python_reverse_import_chain",
            "depth": depth,
            "module_chain": portable_chain,
        }
        evidence_records.append({"target": label, **evidence})
        candidates.append(
            {
                "target": label,
                "facts": {
                    "impact_depth": depth,
                    "module": module_by_path[path],
                    "source_lines": cache.get(path).line_count,
                },
                "evidence": {"structural": [evidence]},
                "derived": {"direct_dependent": depth == 1},
                "interpretation": {
                    "relationship": "static import reachability",
                    "runtime_effect_not_proven": True,
                },
                "recommendations": {
                    "next_action": "inspect_before_edit_or_verification_planning"
                },
                "uncertainty": [],
                "required_next_evidence": [],
                "verification_suggestions": [],
            }
        )

    deferred: list[dict[str, object]] = []
    for item in deferred_raw:
        raw_target = Path(str(item["target"]))
        target = (
            report_path(path=raw_target, anchor=repository_root)
            if raw_target.is_absolute()
            else str(item["target"])
        )
        deferred.append({**item, "target": target})
    if deferred:
        uncertainty.append(
            {
                "code": "impact_budget_exhausted",
                "message": "Some reachable source dependents were omitted by impact_max_sources.",
                "count": len(deferred),
            }
        )

    entries = _source_entries(
        files=source_files, repository_root=repository_root, cache=cache
    )
    # Missing changed source paths still affect decision identity even though no bytes exist.
    for item in changed_records:
        if item.get("kind") == "python_source" and not item.get("discovered"):
            entries.append(
                {
                    "path": str(item["path"]),
                    "sha256": f"missing-module:{item.get('module')}",
                }
            )
    repository = {
        "root": ".",
        "identity": analyzed_input_identity(entries),
        "identity_kind": "analyzed-python-impact-inputs-sha256",
        "input_count": len(entries),
    }
    configuration_values = {
        "source_root": report_path(path=source_root, anchor=repository_root),
        "package_name": effective_package,
        "changed_paths": [report_path(path=p, anchor=repository_root) for p in changed],
        "impact_max_depth": impact_max_depth,
        "impact_max_sources": impact_max_sources,
        "discovery_mode": discovery_config.mode,
        "untracked_policy": discovery_config.untracked_policy,
        "ignored_policy": discovery_config.ignored_policy,
        "symlink_policy": discovery_config.symlink_policy,
        "exclude_patterns": list(discovery_config.exclude_patterns),
        "git_timeout_seconds": discovery_config.git_timeout_seconds,
    }
    economics = {
        **cache.metrics(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "source_files_discovered": len(source_files),
        "changed_paths": len(changed),
        "seed_modules": len(set(seed_modules)),
        "impacted_sources_selected": len(candidates),
        "impacted_sources_deferred": len(deferred),
        "impact_max_depth": impact_max_depth,
        "impact_max_sources": impact_max_sources,
        "discovery": discovery.metrics(),
    }
    payload = build_probe_contract(
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        generated_at=iso_utc_now(),
        repository=repository,
        configuration_values=configuration_values,
        evidence={
            "changed": changed_records,
            "records": evidence_records,
            "authority": "static Python import reachability only",
        },
        derived={
            "selected_impacted_source_count": len(candidates),
            "direct_dependent_count": sum(
                1 for c in candidates if c["facts"]["impact_depth"] == 1
            ),
        },
        interpretation={
            "relationship_boundary": "static reachability is not runtime behavior or edit authority"
        },
        uncertainty=uncertainty,
        warnings=warnings,
        candidates=candidates,
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
