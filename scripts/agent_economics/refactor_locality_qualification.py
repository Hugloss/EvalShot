from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from . import refactor_locality as refactor_locality_module
from .refactor_locality import (
    DECOMPOSITION_JUSTIFIED,
    DECOMPOSITION_LOCALITY_RISK,
    INSUFFICIENT_LOCALITY_EVIDENCE,
    KEEP_COHESIVE_AUTHORITY,
    LOCALITY_REGRESSED,
    LOCALITY_TRADEOFF_REVIEW_REQUIRED,
    compare_locality,
    decomposition_decision,
    _locality_snapshot_from_observed_hashmarks,
    locality_snapshot,
    locality_snapshot_from_hashmarks,
)


def _hashmarks_identity(payload: dict[str, object]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )


def _symbol(
    path: str,
    qualname: str,
    start: int,
    end: int,
    *,
    depth: int = 0,
    forwarding: bool = False,
    structure_kind: str = "implementation",
    value_kind: str | None = None,
    value_identity: str | None = None,
    callers: int = 0,
    direct_verifiers: int = 0,
) -> dict[str, object]:
    return {
        "path": path,
        "qualname": qualname,
        "line_start": start,
        "line_end": end,
        "navigation_depth": depth,
        "forwarding_only": forwarding,
        "edit_required": True,
        "evidence_required": True,
        "structure_kind": structure_kind,
        "value_kind": value_kind or "",
        "value_evidence_identity": value_identity or "",
        "exact_caller_count": callers,
        "caller_reference_bound_complete": True,
        "direct_verifier_count": direct_verifiers,
    }


def _snapshot(
    repo: str,
    symbols: list[dict[str, object]],
    *,
    lines: int,
    branches: int,
    nesting: int,
    responsibilities: list[str] | None = None,
    state_kind: str = "observed",
    provider: str = "manual",
) -> dict[str, object]:
    normalized: list[dict[str, object]] = []
    for row in symbols:
        current = dict(row)
        if current.get("value_kind"):
            current["value_evidence_provider"] = "qualification"
            current["value_repository_identity"] = repo
        normalized.append(current)
    return locality_snapshot(
        repository_identity=repo,
        target="src/pkg/core.py::authority",
        target_path="src/pkg/core.py",
        source_identity=f"sha256:{repo[-1]}-source",
        measurement_configuration_identity="sha256:locality-config-v1",
        provider=provider,
        provider_evidence_identity=f"sha256:{repo[-1]}-provider",
        symbols=normalized,
        verifier_paths=["tests/test_core.py"],
        responsibilities=responsibilities or ["authority"],
        authority_lines=lines,
        branch_points=branches,
        nesting_depth=nesting,
        state_kind=state_kind,
    )


def _hm_node(
    path: str,
    qualname: str,
    start: int,
    end: int,
    *,
    depth: int = 0,
    forwarding: bool | None = False,
    kind: str = "function",
    callers: int = 0,
    caller_paths: list[str] | None = None,
    complete: bool = True,
) -> dict[str, object]:
    name = qualname.rsplit(".", 1)[-1]
    source_semantic = {
        "path": path,
        "qualname": qualname,
        "file_digest": f"sha256:{path}:{end}",
        "lines": [start, end],
    }
    caller_paths = caller_paths or ["src/pkg/core.py"] * callers
    if len(caller_paths) != callers:
        raise ValueError("caller_paths length must match callers")
    semantic = {
        "path": path,
        "qualname": qualname,
        "name": name,
        "kind": kind,
        "signature": (f"class {name}:" if kind == "class" else f"def {name}(...):"),
        "lines": [start, end],
        "navigation_depth": depth,
        "file_digest": source_semantic["file_digest"],
        "forwarding_only": forwarding,
        "forwarding_provider": (
            "not-applicable-symbol-kind"
            if kind == "class" and forwarding is None
            else "python-ast"
        ),
        "exact_callers": [
            {
                "path": caller_path,
                "source": f"caller_{index}",
                "line": start + index,
                "confidence": "static-name",
            }
            for index, caller_path in enumerate(caller_paths)
        ],
        "exact_caller_count": callers,
        "caller_reference_bound_complete": complete,
        "unresolved_caller_candidates": [],
    }
    return {
        "symbol_id": f"{path}::{qualname}",
        **semantic,
        "symbol_source_identity": _hashmarks_identity(source_semantic),
        "symbol_evidence_identity": _hashmarks_identity(semantic),
    }


def _union_lines(nodes: list[dict[str, object]]) -> int:
    by_path: dict[str, list[tuple[int, int]]] = {}
    for row in nodes:
        start, end = row["lines"]
        by_path.setdefault(str(row["path"]), []).append((int(start), int(end)))
    total = 0
    for spans in by_path.values():
        current_start: int | None = None
        current_end: int | None = None
        for start, end in sorted(spans):
            if current_start is None:
                current_start, current_end = start, end
                continue
            assert current_end is not None
            if start <= current_end + 1:
                current_end = max(current_end, end)
                continue
            total += current_end - current_start + 1
            current_start, current_end = start, end
        if current_start is not None and current_end is not None:
            total += current_end - current_start + 1
    return total


def _hm_packet(
    repo: str,
    nodes: list[dict[str, object]],
    *,
    unresolved_calls: list[dict[str, object]] | None = None,
    freshness: str = "current",
) -> dict[str, object]:
    unresolved = unresolved_calls or []
    target = nodes[0]
    verifiers = ["tests/test_core.py"]
    dimensions = {
        "symbol_count": len(nodes),
        "file_count": len({str(row["path"]) for row in nodes}),
        "max_navigation_depth": max(int(row["navigation_depth"]) for row in nodes),
        "forwarding_only_symbol_count": sum(
            1 for row in nodes if row["forwarding_only"] is True
        ),
        "forwarding_unknown_symbol_count": 0,
        "context_lines": _union_lines(nodes),
        "verifier_file_count": len(verifiers),
        "cross_file_symbol_count": sum(
            1 for row in nodes if row["path"] != target["path"]
        ),
        "unresolved_call_count": len(unresolved),
        "target_exact_caller_count": int(target["exact_caller_count"]),
    }
    semantic: dict[str, object] = {
        "schema": "hashmarks.structural-locality.v1",
        "provider": "hashmarks",
        "provider_version": "qualification",
        "provider_implementation_identity": "sha256:qualification-hashmarks-implementation",
        "repository_identity": repo,
        "source_identity": target["symbol_source_identity"],
        "measurement_configuration_identity": "sha256:locality-config-v1",
        "target": "src/pkg/core.py::authority",
        "target_symbol_id": "src/pkg/core.py::authority",
        "freshness": {
            "generation": 1,
            "identity_generation": 1,
            "stale": False if freshness == "current" else None,
            "state": freshness,
            "basis": "explicit-sync" if freshness == "current" else "observer-status",
        },
        "bounds": {
            "max_depth": 2,
            "call_limit_per_symbol": 64,
            "ref_limit_per_symbol": 256,
            "verification_max_depth": 3,
            "target_resolution": "exact-path-qualname",
            "call_resolution": "unambiguous-indexed-symbol-only",
            "refresh": freshness == "current",
        },
        "nodes": nodes,
        "edges": [],
        "unresolved_calls": unresolved,
        "verification_paths": verifiers,
        "dimensions": dimensions,
        "claims": {
            "refactor_recommendation": False,
            "semantic_responsibility_inferred": False,
            "ambiguous_calls_promoted_to_exact": False,
            "execution_authority": False,
        },
    }
    return {**semantic, "evidence_identity": _hashmarks_identity(semantic)}


def _command_identity(argv: list[str]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {"argv": argv, "cwd": "."},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )


def _receipt(packet: dict[str, object]) -> dict[str, object]:
    bounds = packet["bounds"]
    assert isinstance(bounds, dict)
    executable = "hashmarks"
    argv = [
        executable,
        "--workspace",
        ".",
        "structural-locality",
        str(packet["target"]),
        "--max-depth",
        str(bounds["max_depth"]),
        "--call-limit",
        str(bounds["call_limit_per_symbol"]),
        "--ref-limit",
        str(bounds["ref_limit_per_symbol"]),
    ]
    semantic = {
        "schema": "agentscookbook-hashmarks-locality-observation/v1",
        "target": packet["target"],
        "executable": executable,
        "argv": argv,
        "command_identity": _command_identity(argv),
        "packet_evidence_identity": packet["evidence_identity"],
        "packet_repository_identity": packet["repository_identity"],
        "provider_implementation_identity": packet["provider_implementation_identity"],
        "status": "PASS",
        "classification": "pass",
        "workspace_before_identity": "sha256:tracked-workspace",
        "workspace_after_identity": "sha256:tracked-workspace",
        "changed_tracked_paths": [],
        "stdout_sha256": "0" * 64,
        "stderr_sha256": "0" * 64,
        "return_code": 0,
        "timed_out": False,
        "executable_missing": False,
        "output_truncated": False,
    }
    return {**semantic, "evidence_identity": _hashmarks_identity(semantic)}


def _value(
    repo: str,
    symbol_id: str,
    value_kind: str,
    evidence_identity: str,
    *,
    structure_kind: str = "helper",
) -> dict[str, object]:
    return {
        "symbol_id": symbol_id,
        "structure_kind": structure_kind,
        "value_kind": value_kind,
        "repository_identity": repo,
        "evidence_provider": "repository-contract",
        "evidence_identity": evidence_identity,
        "edit_required": True,
    }


def _decision(
    pre: dict[str, object],
    post: dict[str, object],
    evidence: list[dict[str, str]],
) -> tuple[dict[str, object], dict[str, object]]:
    comparison = compare_locality(pre, post)
    decision = decomposition_decision(
        pre_snapshot=pre,
        post_snapshot=post,
        comparison=comparison,
        decomposition_evidence=evidence,
    )
    return comparison, decision


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []

    manual_pre = _snapshot(
        "sha256:repo-a",
        [_symbol("src/pkg/core.py", "authority", 1, 225)],
        lines=225,
        branches=32,
        nesting=4,
    )
    manual_size_only = _snapshot(
        "sha256:repo-b",
        [_symbol("src/pkg/core.py", "authority", 1, 16)],
        lines=16,
        branches=1,
        nesting=1,
        state_kind="proposed",
    )
    _, size_only_decision = _decision(
        manual_pre,
        manual_size_only,
        [
            {
                "kind": "size_only",
                "evidence_identity": "sha256:loc",
                "summary": "225 lines to 16",
            }
        ],
    )
    if size_only_decision["status"] != KEEP_COHESIVE_AUTHORITY:
        failures.append(
            "size-only reduction was treated as decomposition justification"
        )

    manual_semantic = _snapshot(
        "sha256:repo-c",
        [
            _symbol("src/pkg/core.py", "authority", 1, 36),
            _symbol(
                "src/pkg/core.py",
                "validate",
                38,
                90,
                depth=1,
                value_kind="validation_boundary",
                value_identity="sha256:validation-boundary",
            ),
        ],
        lines=36,
        branches=5,
        nesting=2,
        responsibilities=["validation"],
        state_kind="proposed",
    )
    _, manual_semantic_decision = _decision(
        manual_pre,
        manual_semantic,
        [
            {
                "kind": "mixed_responsibilities",
                "evidence_identity": "sha256:mixed-manual",
                "summary": "claimed validation responsibility",
            },
            {
                "kind": "bounded_locality_tradeoff",
                "evidence_identity": "sha256:tradeoff-manual",
                "summary": "claimed bounded tradeoff",
            },
        ],
    )
    if manual_semantic_decision["status"] != INSUFFICIENT_LOCALITY_EVIDENCE:
        failures.append("caller-authored structural counts justified decomposition")

    hm_pre_packet = _hm_packet(
        "sha256:hm-pre",
        [_hm_node("src/pkg/core.py", "authority", 1, 225)],
    )
    hm_pre_diagnostic = locality_snapshot_from_hashmarks(packet=hm_pre_packet)
    if (
        hm_pre_diagnostic.get("claims", {}).get("independent_structural_provider")
        is not False
    ):
        failures.append(
            "packet-only Hashmarks input claimed independent execution authority"
        )
    hm_pre = _locality_snapshot_from_observed_hashmarks(
        packet=hm_pre_packet,
        observation_receipt=_receipt(hm_pre_packet),
    )

    hm_post_packet = _hm_packet(
        "sha256:hm-post",
        [
            _hm_node("src/pkg/core.py", "authority", 1, 36),
            _hm_node("src/pkg/core.py", "validate", 38, 90, depth=1, callers=1),
            _hm_node("src/pkg/core.py", "persist", 92, 140, depth=1, callers=1),
        ],
    )
    hm_post = _locality_snapshot_from_observed_hashmarks(
        packet=hm_post_packet,
        observation_receipt=_receipt(hm_post_packet),
        structural_values=[
            _value(
                "sha256:hm-post",
                "src/pkg/core.py::validate",
                "validation_boundary",
                "sha256:validation-contract",
            ),
            _value(
                "sha256:hm-post",
                "src/pkg/core.py::persist",
                "side_effect_isolation",
                "sha256:persistence-contract",
            ),
        ],
        responsibilities=["validation", "persistence"],
        state_kind="observed",
    )
    hm_comparison, hm_without_tradeoff = _decision(
        hm_pre,
        hm_post,
        [
            {
                "kind": "mixed_responsibilities",
                "evidence_identity": "sha256:mixed-hm",
                "summary": "validation and persistence are independently owned",
            }
        ],
    )
    if hm_comparison["status"] != LOCALITY_TRADEOFF_REVIEW_REQUIRED:
        failures.append(
            "Hashmarks-backed same-file extraction hid its locality tradeoff"
        )
    if hm_comparison.get("limited_observed_reuse_structures") != [
        "src/pkg/core.py::persist",
        "src/pkg/core.py::validate",
    ]:
        failures.append(
            "one-caller introduced helpers were not surfaced as limited observed reuse"
        )
    if (
        hm_comparison.get("claims", {}).get("limited_observed_reuse_proves_single_use")
        is not False
    ):
        failures.append("limited observed reuse was promoted into a single-use claim")
    if hm_without_tradeoff["status"] != INSUFFICIENT_LOCALITY_EVIDENCE:
        failures.append(
            "Hashmarks-backed tradeoff was accepted without explicit evidence"
        )
    _, hm_decision = _decision(
        hm_pre,
        hm_post,
        [
            {
                "kind": "mixed_responsibilities",
                "evidence_identity": "sha256:mixed-hm",
                "summary": "validation and persistence are independently owned",
            },
            {
                "kind": "bounded_locality_tradeoff",
                "evidence_identity": "sha256:tradeoff-hm",
                "summary": "same-file semantic ownership adds one bounded navigation hop",
            },
        ],
    )
    if hm_decision["status"] != DECOMPOSITION_JUSTIFIED:
        failures.append(
            "independent Hashmarks facts could not justify earned decomposition"
        )

    with TemporaryDirectory() as temporary_directory:
        temporary = Path(temporary_directory)
        values_path = temporary / "structural-values.json"
        observed_path = temporary / "observed.json"
        values_path.write_text(
            json.dumps(
                [
                    _value(
                        "sha256:hm-post",
                        "src/pkg/core.py::validate",
                        "validation_boundary",
                        "sha256:validation-contract",
                    ),
                    _value(
                        "sha256:hm-post",
                        "src/pkg/core.py::persist",
                        "side_effect_isolation",
                        "sha256:persistence-contract",
                    ),
                ]
            ),
            encoding="utf-8",
        )
        observed = {
            "packet": hm_post_packet,
            "observation_receipt": _receipt(hm_post_packet),
        }
        with patch.object(
            refactor_locality_module,
            "observe_hashmarks_locality",
            return_value=observed,
        ):
            refactor_locality_module.main(
                [
                    "observe-hashmarks",
                    "src/pkg/core.py::authority",
                    "--repository-root",
                    str(temporary),
                    "--values",
                    str(values_path),
                    "--artifact",
                    str(observed_path),
                ]
            )
        observed_payload = json.loads(observed_path.read_text(encoding="utf-8"))
        observed_snapshot = observed_payload.get("snapshot")
        observed_symbols = (
            observed_snapshot.get("symbols", [])
            if isinstance(observed_snapshot, dict)
            else []
        )
        observed_values = {
            f"{row.get('path')}::{row.get('qualname')}": row.get("value_kind")
            for row in observed_symbols
            if isinstance(row, dict) and row.get("value_kind")
        }
        if observed_values != {
            "src/pkg/core.py::persist": "side_effect_isolation",
            "src/pkg/core.py::validate": "validation_boundary",
        }:
            failures.append(
                "live observe-hashmarks CLI did not bind supplied structural values"
            )
        if (
            not isinstance(observed_snapshot, dict)
            or observed_snapshot.get("claims", {}).get(
                "independent_structural_provider"
            )
            is not True
        ):
            failures.append(
                "live observe-hashmarks values path lost independent provider authority"
            )

    fragmented_packet = _hm_packet(
        "sha256:hm-fragmented",
        [
            _hm_node("src/pkg/core.py", "authority", 1, 16),
            _hm_node(
                "src/pkg/a.py", "check_a", 1, 8, depth=1, forwarding=True, callers=1
            ),
            _hm_node(
                "src/pkg/b.py", "check_b", 1, 8, depth=1, forwarding=True, callers=1
            ),
        ],
    )
    fragmented = _locality_snapshot_from_observed_hashmarks(
        packet=fragmented_packet,
        observation_receipt=_receipt(fragmented_packet),
    )
    fragmented_comparison, fragmented_decision = _decision(
        hm_pre,
        fragmented,
        [
            {
                "kind": "mixed_responsibilities",
                "evidence_identity": "sha256:fragmented-claim",
                "summary": "claimed decomposition",
            }
        ],
    )
    if fragmented_comparison["status"] != LOCALITY_REGRESSED:
        failures.append(
            "Hashmarks-backed wrapper fragmentation did not regress locality"
        )
    if fragmented_decision["status"] != DECOMPOSITION_LOCALITY_RISK:
        failures.append(
            "Hashmarks-backed wrapper fragmentation escaped risk classification"
        )

    reuse_packet = _hm_packet(
        "sha256:hm-reuse",
        [
            _hm_node("src/pkg/core.py", "authority", 1, 180),
            _hm_node("src/pkg/core.py", "_shared", 182, 210, depth=1, callers=1),
        ],
    )
    reuse_snapshot = _locality_snapshot_from_observed_hashmarks(
        packet=reuse_packet,
        observation_receipt=_receipt(reuse_packet),
        structural_values=[
            _value(
                "sha256:hm-reuse",
                "src/pkg/core.py::_shared",
                "shared_reuse",
                "sha256:claimed-reuse",
            )
        ],
    )
    reuse_comparison = compare_locality(hm_pre, reuse_snapshot)
    if reuse_comparison["status"] != LOCALITY_REGRESSED:
        failures.append(
            "provider-observed single caller laundered itself as shared reuse"
        )
    if reuse_comparison.get("limited_observed_reuse_structures") != [
        "src/pkg/core.py::_shared"
    ]:
        failures.append("limited observed reuse evidence omitted the one-caller helper")

    verifier_inflated_reuse_packet = _hm_packet(
        "sha256:hm-verifier-inflated-reuse",
        [
            _hm_node("src/pkg/core.py", "authority", 1, 180),
            _hm_node(
                "src/pkg/core.py",
                "_shared",
                182,
                210,
                depth=1,
                callers=3,
                caller_paths=[
                    "src/pkg/core.py",
                    "tests/test_core.py",
                    "tests/test_core.py",
                ],
            ),
        ],
    )
    verifier_inflated_reuse = _locality_snapshot_from_observed_hashmarks(
        packet=verifier_inflated_reuse_packet,
        observation_receipt=_receipt(verifier_inflated_reuse_packet),
        structural_values=[
            _value(
                "sha256:hm-verifier-inflated-reuse",
                "src/pkg/core.py::_shared",
                "shared_reuse",
                "sha256:claimed-verifier-inflated-reuse",
            )
        ],
    )
    verifier_inflated_comparison = compare_locality(hm_pre, verifier_inflated_reuse)
    if verifier_inflated_comparison["status"] != LOCALITY_REGRESSED:
        failures.append(
            "verifier-only callers laundered one source caller into shared reuse"
        )
    introduced = verifier_inflated_comparison.get("introduced_structures", [])
    if not any(
        isinstance(row, dict)
        and row.get("qualname") == "_shared"
        and row.get("exact_caller_count") == 3
        and row.get("exact_non_verifier_caller_count") == 1
        and row.get("earned_structural_value") is False
        for row in introduced
    ):
        failures.append(
            "verifier-inflated reuse evidence did not retain total/non-verifier caller facts"
        )

    incomplete_reuse_packet = _hm_packet(
        "sha256:hm-incomplete-reuse",
        [
            _hm_node("src/pkg/core.py", "authority", 1, 180),
            _hm_node(
                "src/pkg/core.py",
                "_shared",
                182,
                210,
                depth=1,
                callers=3,
                complete=False,
            ),
        ],
    )
    incomplete_reuse = _locality_snapshot_from_observed_hashmarks(
        packet=incomplete_reuse_packet,
        observation_receipt=_receipt(incomplete_reuse_packet),
        structural_values=[
            _value(
                "sha256:hm-incomplete-reuse",
                "src/pkg/core.py::_shared",
                "shared_reuse",
                "sha256:claimed-reuse-incomplete",
            )
        ],
    )
    expected_reference_bound_gap = (
        "hashmarks-caller-reference-bound:src/pkg/core.py::_shared"
    )
    if expected_reference_bound_gap not in incomplete_reuse["unresolved_evidence"]:
        failures.append(
            "incomplete caller reference bound did not surface as unresolved locality evidence"
        )
    incomplete_reuse_status = compare_locality(hm_pre, incomplete_reuse)["status"]
    if incomplete_reuse_status != INSUFFICIENT_LOCALITY_EVIDENCE:
        failures.append(
            "incomplete caller reference bound was allowed to justify locality"
        )

    direct_test_packet = _hm_packet(
        "sha256:hm-test-seam",
        [
            _hm_node("src/pkg/core.py", "authority", 1, 180),
            _hm_node("src/pkg/core.py", "_test_seam", 182, 205, depth=1, callers=1),
        ],
    )
    direct_test_snapshot = _locality_snapshot_from_observed_hashmarks(
        packet=direct_test_packet,
        observation_receipt=_receipt(direct_test_packet),
        structural_values=[
            _value(
                "sha256:hm-test-seam",
                "src/pkg/core.py::_test_seam",
                "direct_test_seam",
                "sha256:claimed-direct-seam",
            )
        ],
    )
    if compare_locality(hm_pre, direct_test_snapshot)["status"] != LOCALITY_REGRESSED:
        failures.append("related verifier paths were promoted into a direct test seam")

    non_callable_forwarding_packet = _hm_packet(
        "sha256:hm-non-callable-forwarding",
        [
            _hm_node("src/pkg/core.py", "authority", 1, 180),
            _hm_node(
                "src/pkg/model.py",
                "EvidenceVisibility",
                1,
                12,
                depth=1,
                forwarding=None,
                kind="class",
            ),
        ],
    )
    non_callable_forwarding = _locality_snapshot_from_observed_hashmarks(
        packet=non_callable_forwarding_packet,
        observation_receipt=_receipt(non_callable_forwarding_packet),
    )
    if any(
        str(value).startswith("forwarding-shape:")
        for value in non_callable_forwarding["unresolved_evidence"]
    ):
        failures.append(
            "non-callable class forwarding shape was incorrectly required for locality"
        )

    callable_forwarding_packet = _hm_packet(
        "sha256:hm-callable-forwarding-unknown",
        [
            _hm_node(
                "src/pkg/core.py",
                "authority",
                1,
                180,
                forwarding=None,
            )
        ],
    )
    callable_forwarding = _locality_snapshot_from_observed_hashmarks(
        packet=callable_forwarding_packet,
        observation_receipt=_receipt(callable_forwarding_packet),
    )
    if (
        "forwarding-shape:src/pkg/core.py::authority"
        not in callable_forwarding["unresolved_evidence"]
    ):
        failures.append(
            "unknown callable forwarding shape did not remain incomplete locality evidence"
        )

    unresolved_packet = _hm_packet(
        "sha256:hm-unresolved",
        [_hm_node("src/pkg/core.py", "authority", 1, 200)],
        unresolved_calls=[
            {
                "source_symbol_id": "src/pkg/core.py::authority",
                "line": 50,
                "target_text": "helper",
                "candidate_symbol_ids": [
                    "src/pkg/a.py::helper",
                    "src/pkg/b.py::helper",
                ],
            }
        ],
    )
    unresolved_snapshot = _locality_snapshot_from_observed_hashmarks(
        packet=unresolved_packet,
        observation_receipt=_receipt(unresolved_packet),
    )
    if not unresolved_snapshot["unresolved_evidence"]:
        failures.append(
            "Hashmarks unresolved call did not make locality evidence incomplete"
        )
    if (
        compare_locality(hm_pre, unresolved_snapshot)["status"]
        != INSUFFICIENT_LOCALITY_EVIDENCE
    ):
        failures.append(
            "ambiguous Hashmarks call remained sufficient for locality decision"
        )

    stale_packet = _hm_packet(
        "sha256:hm-stale",
        [_hm_node("src/pkg/core.py", "authority", 1, 200)],
        freshness="unknown",
    )
    stale_snapshot = _locality_snapshot_from_observed_hashmarks(
        packet=stale_packet,
        observation_receipt=_receipt(stale_packet),
    )
    if (
        compare_locality(hm_pre, stale_snapshot)["status"]
        != INSUFFICIENT_LOCALITY_EVIDENCE
    ):
        failures.append("non-current Hashmarks evidence remained sufficient")

    tampered = dict(hm_post_packet)
    tampered["repository_identity"] = "sha256:tampered"
    try:
        locality_snapshot_from_hashmarks(packet=tampered)
    except ValueError:
        pass
    else:
        failures.append("tampered Hashmarks evidence identity was accepted")

    forged_snapshot = dict(manual_semantic)
    forged_claims = dict(forged_snapshot["claims"])
    forged_claims["independent_structural_provider"] = True
    forged_snapshot["claims"] = forged_claims
    forged_comparison = compare_locality(manual_pre, forged_snapshot)
    if forged_comparison["status"] != INSUFFICIENT_LOCALITY_EVIDENCE:
        failures.append("tampered locality claims retained comparison authority")
    if "post-snapshot-integrity" not in forged_comparison.get(
        "unresolved_evidence", []
    ):
        failures.append("tampered locality claims did not invalidate snapshot identity")

    wrong_target_receipt = _receipt(hm_post_packet)
    wrong_target_semantic = {
        key: value
        for key, value in wrong_target_receipt.items()
        if key != "evidence_identity"
    }
    wrong_target_semantic["target"] = "src/pkg/core.py::different"
    wrong_target_receipt = {
        **wrong_target_semantic,
        "evidence_identity": _hashmarks_identity(wrong_target_semantic),
    }
    wrong_target_snapshot = _locality_snapshot_from_observed_hashmarks(
        packet=hm_post_packet,
        observation_receipt=wrong_target_receipt,
    )
    if (
        wrong_target_snapshot.get("claims", {}).get("independent_structural_provider")
        is not False
    ):
        failures.append("wrong-target execution receipt promoted structural authority")

    mutated_receipt = _receipt(hm_post_packet)
    mutated_semantic = {
        key: value
        for key, value in mutated_receipt.items()
        if key != "evidence_identity"
    }
    mutated_semantic["changed_tracked_paths"] = ["src/pkg/core.py"]
    mutated_semantic["workspace_after_identity"] = "sha256:mutated-workspace"
    mutated_receipt = {
        **mutated_semantic,
        "evidence_identity": _hashmarks_identity(mutated_semantic),
    }
    mutated_snapshot = _locality_snapshot_from_observed_hashmarks(
        packet=hm_post_packet,
        observation_receipt=mutated_receipt,
    )
    if (
        mutated_snapshot.get("claims", {}).get("independent_structural_provider")
        is not False
    ):
        failures.append(
            "tracked-mutating Hashmarks execution promoted structural authority"
        )

    wrong_repo_value = _value(
        "sha256:wrong-repository",
        "src/pkg/core.py::validate",
        "validation_boundary",
        "sha256:wrong-binding",
    )
    try:
        _locality_snapshot_from_observed_hashmarks(
            packet=hm_post_packet,
            observation_receipt=_receipt(hm_post_packet),
            structural_values=[wrong_repo_value],
        )
    except ValueError:
        pass
    else:
        failures.append(
            "semantic value evidence from another repository state was accepted"
        )

    if hm_pre.get("claims", {}).get("independent_structural_provider") is not True:
        failures.append(
            "observed Hashmarks snapshot did not retain independent provider authority"
        )
    if manual_pre.get("claims", {}).get("independent_structural_provider") is not False:
        failures.append(
            "manual snapshot incorrectly claimed independent structural authority"
        )
    if any("score" in key for key in hm_pre.get("dimensions", {})):
        failures.append(
            "Hashmarks-backed locality dimensions introduced an opaque score"
        )

    result = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "cases": {
            "size_only": size_only_decision["status"],
            "manual_semantic": manual_semantic_decision["status"],
            "hashmarks_semantic": hm_decision["status"],
            "live_observe_values": ("BOUND" if observed_values else "MISSING"),
            "fragmented_wrappers": fragmented_decision["status"],
            "provider_single_caller_reuse": reuse_comparison["status"],
            "unresolved_hashmarks": compare_locality(hm_pre, unresolved_snapshot)[
                "status"
            ],
            "stale_hashmarks": compare_locality(hm_pre, stale_snapshot)["status"],
        },
    }
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args(argv)
    result = qualify(args.artifact)
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
