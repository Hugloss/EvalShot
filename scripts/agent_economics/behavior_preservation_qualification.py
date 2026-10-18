from __future__ import annotations

import argparse
import json
from pathlib import Path

from .behavior_preservation import (
    DEBT_REDISTRIBUTED,
    DEBT_VERIFIED,
    EVIDENCE_REQUIRED,
    LOCALITY_REVIEW_REQUIRED,
    MEASUREMENT_NOT_COMPARABLE,
    POST_EDIT_EVIDENCE_REQUIRED,
    PRESERVED,
    READY,
    TARGET_DEBT_NOT_REDUCED,
    TEST_STRENGTHENING_REQUIRED,
    behavior_preservation_debt_delta,
    behavior_preservation_readiness,
    behavior_preservation_receipt,
    post_edit_change_set_evidence,
    source_set_identity,
    target_test_ownership_evidence,
)
from .refactor_locality import (
    _identity as _locality_identity,
    compare_locality,
    decomposition_decision,
    locality_snapshot,
)


def _boundary(
    identity: str = "public-normalization",
    *,
    classification: str = "direct",
    freshness: str = "fresh",
    test_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "identity": identity,
        "classification": classification,
        "freshness": freshness,
        "provider_reference": {
            "provider": "test-focus",
            "evidence_identity": f"sha256:{identity}",
        },
        "test_evidence_identities": (
            ["sha256:test-core"] if test_ids is None else list(test_ids)
        ),
    }


def _tests() -> list[dict[str, object]]:
    return [{"path": "tests/test_core.py", "evidence_identity": "sha256:test-core"}]


def _ownership(
    *,
    target: str = "src/pkg/core.py",
    source_identity: str = "sha256:source-a",
    tests: list[dict[str, object]] | None = None,
    provider_evidence_identity: str = "sha256:test-focus-a",
) -> dict[str, object]:
    return target_test_ownership_evidence(
        target=target,
        source_identity=source_identity,
        test_references=tests or _tests(),
        provider="test-focus",
        provider_evidence_identity=provider_evidence_identity,
    )


def _post_sources(
    *identities: tuple[str, str],
) -> list[dict[str, str]]:
    rows = identities or (("src/pkg/core.py", "sha256:source-b"),)
    return [
        {"path": path, "evidence_identity": evidence_identity}
        for path, evidence_identity in rows
    ]


def _post_identity(
    source_references: list[dict[str, str]] | None = None,
) -> str:
    return source_set_identity(source_references or _post_sources())


def _independent_locality_snapshot(
    *,
    repository_identity: str,
    source_identity: str,
    include_earned_helper: bool = False,
) -> dict[str, object]:
    symbols: list[dict[str, object]] = [
        {
            "path": "src/pkg/core.py",
            "qualname": "authority",
            "line_start": 1,
            "line_end": 80 if repository_identity.endswith("b") else 100,
            "navigation_depth": 0,
            "structure_kind": "implementation",
            "forwarding_only": False,
            "edit_required": True,
            "evidence_required": True,
            "exact_caller_count": 2,
            "direct_verifier_count": 1,
            "caller_reference_bound_complete": True,
        }
    ]
    if include_earned_helper:
        symbols.append(
            {
                "path": "src/pkg/core.py",
                "qualname": "_normalize",
                "line_start": 82,
                "line_end": 96,
                "navigation_depth": 1,
                "structure_kind": "implementation",
                "forwarding_only": False,
                "edit_required": True,
                "evidence_required": True,
                "value_kind": "semantic_responsibility_owner",
                "value_evidence_identity": "sha256:normalize-responsibility",
                "value_evidence_provider": "qualification",
                "value_repository_identity": repository_identity,
                "exact_caller_count": 1,
                "direct_verifier_count": 0,
                "caller_reference_bound_complete": True,
            }
        )
    snapshot = locality_snapshot(
        repository_identity=repository_identity,
        target="src/pkg/core.py::authority",
        target_path="src/pkg/core.py",
        source_identity=source_identity,
        measurement_configuration_identity="sha256:locality-config-v1",
        provider="hashmarks",
        provider_evidence_identity=f"{repository_identity}:hashmarks-locality",
        symbols=symbols,
        verifier_paths=["tests/test_core.py"],
        responsibilities=["authority"],
        state_kind="observed",
    )
    semantic = {
        str(key): value for key, value in snapshot.items() if key != "evidence_identity"
    }
    claims = dict(semantic["claims"])
    claims["independent_structural_provider"] = True
    semantic["claims"] = claims
    return {**semantic, "evidence_identity": _locality_identity(semantic)}


def _change_set(
    source_references: list[dict[str, str]] | None = None,
    *,
    pre_edit_source_identity: str = "sha256:source-a",
    post_edit_repository_identity: str = "sha256:repo-b",
    provider_evidence_identity: str = "sha256:change-set-a",
) -> dict[str, object]:
    return post_edit_change_set_evidence(
        pre_edit_source_identity=pre_edit_source_identity,
        post_edit_repository_identity=post_edit_repository_identity,
        changed_source_references=source_references or _post_sources(),
        provider="repository-change-set",
        provider_evidence_identity=provider_evidence_identity,
    )


def _receipt(
    *,
    source_identity: str = "sha256:source-a",
    repository_identity: str = "sha256:repo-a",
    status: str = "PASS",
    test_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "source_identity": source_identity,
        "repository_identity": repository_identity,
        "status": status,
        "test_evidence_identities": test_ids or ["sha256:test-core"],
        "execution_identity": "sha256:execution-a",
    }


def _run(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "target": "src/pkg/core.py",
        "source_identity": "sha256:source-a",
        "boundaries": [_boundary()],
        "test_references": _tests(),
        "execution_receipt": _receipt(),
        "repository_gates": [{"name": "full", "command": "uv run pytest"}],
        "test_ownership_evidence": _ownership(),
    }
    args.update(overrides)
    return behavior_preservation_readiness(**args)  # type: ignore[arg-type]


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []

    ready = _run()
    if ready["status"] != READY:
        failures.append("complete fresh direct evidence did not become READY")
    authority = ready.get("authority", {})
    if not isinstance(authority, dict) or authority.get("edit_authorized") is not False:
        failures.append("READY incorrectly authorized an edit")
    if (
        isinstance(authority, dict)
        and authority.get("refactor_safety_proven") is not False
    ):
        failures.append("READY incorrectly claimed refactor safety")

    no_ownership = _run(test_ownership_evidence=None)
    if no_ownership["status"] != EVIDENCE_REQUIRED:
        failures.append("missing target-bound test ownership became READY")

    unrelated_tests = [
        {"path": "tests/test_other.py", "evidence_identity": "sha256:test-other"}
    ]
    unrelated_passing = _run(
        test_references=unrelated_tests,
        execution_receipt=_receipt(test_ids=["sha256:test-other"]),
        test_ownership_evidence=_ownership(
            target="src/pkg/other.py", tests=unrelated_tests
        ),
    )
    if unrelated_passing["status"] != EVIDENCE_REQUIRED:
        failures.append("passing tests owned by a different target became READY")
    if "target-bound-test-ownership" not in unrelated_passing.get(
        "unresolved_evidence", []
    ):
        failures.append("wrong-target ownership mismatch was not explicit")

    wrong_ownership_tests = _run(
        test_ownership_evidence=_ownership(tests=unrelated_tests)
    )
    if wrong_ownership_tests["status"] != EVIDENCE_REQUIRED:
        failures.append("ownership proof for different tests became READY")

    tampered_ownership = _ownership()
    tampered_ownership["provider_evidence_identity"] = "sha256:tampered"
    if _run(test_ownership_evidence=tampered_ownership)["status"] != EVIDENCE_REQUIRED:
        failures.append("tampered ownership proof became READY")

    no_receipt = _run(execution_receipt=None)
    if no_receipt["status"] != EVIDENCE_REQUIRED:
        failures.append("test ownership without execution receipt became READY")

    wrong_source = _run(execution_receipt=_receipt(source_identity="sha256:source-old"))
    if wrong_source["status"] != EVIDENCE_REQUIRED:
        failures.append("receipt bound to different source identity became READY")

    stale = _run(boundaries=[_boundary(freshness="stale")])
    if stale["status"] != EVIDENCE_REQUIRED:
        failures.append("stale provider proof became READY")

    unbound_boundary = _run(boundaries=[_boundary(test_ids=[])])
    if unbound_boundary["status"] != EVIDENCE_REQUIRED:
        failures.append("direct boundary without frozen test binding became READY")
    if "public-normalization:boundary-test-binding" not in unbound_boundary.get(
        "unresolved_evidence", []
    ):
        failures.append("missing boundary-to-test binding was not explicit")

    unknown_boundary_test = _run(
        boundaries=[_boundary(test_ids=["sha256:not-selected"])]
    )
    if unknown_boundary_test["status"] != EVIDENCE_REQUIRED:
        failures.append("boundary bound to an unselected test became READY")

    partially_bound_multi_boundary = _run(
        boundaries=[
            _boundary("public-normalization"),
            _boundary("error-contract", test_ids=[]),
        ]
    )
    if partially_bound_multi_boundary["status"] != EVIDENCE_REQUIRED:
        failures.append(
            "multi-boundary evidence with one unbound boundary became READY"
        )

    indirect = _run(boundaries=[_boundary(classification="indirect")])
    if indirect["status"] != TEST_STRENGTHENING_REQUIRED:
        failures.append("indirect-only proof did not require test strengthening")

    failed = _run(execution_receipt=_receipt(status="FAIL"))
    if failed["status"] != EVIDENCE_REQUIRED:
        failures.append("failed pre-edit execution became READY")

    no_gates = _run(repository_gates=[])
    if no_gates["status"] != EVIDENCE_REQUIRED:
        failures.append("missing broader repository gates became READY")

    coverage_only = _run(
        boundaries=[_boundary(classification="indirect")],
        coverage_evidence={"line_percent": 100, "branch_percent": 100},
    )
    if coverage_only["status"] != TEST_STRENGTHENING_REQUIRED:
        failures.append("coverage percentage promoted indirect evidence to READY")

    wrong_tests = _run(execution_receipt=_receipt(test_ids=["sha256:other-test"]))
    if wrong_tests["status"] != EVIDENCE_REQUIRED:
        failures.append("execution receipt for different selected tests became READY")

    changed_source = _run(
        source_identity="sha256:source-b",
        execution_receipt=_receipt(source_identity="sha256:source-b"),
        test_ownership_evidence=_ownership(source_identity="sha256:source-b"),
    )
    changed_boundary = _run(boundaries=[_boundary("error-contract")])
    changed_test_references = [
        {"path": "tests/test_other.py", "evidence_identity": "sha256:test-other"}
    ]
    changed_tests = _run(
        test_references=changed_test_references,
        execution_receipt=_receipt(test_ids=["sha256:test-other"]),
        test_ownership_evidence=_ownership(tests=changed_test_references),
    )
    changed_ownership = _run(
        test_ownership_evidence=_ownership(
            provider_evidence_identity="sha256:test-focus-b"
        )
    )
    changed_execution = _run(
        execution_receipt={
            **_receipt(),
            "execution_identity": "sha256:execution-b",
        }
    )
    for label, payload in (
        ("source", changed_source),
        ("boundary", changed_boundary),
        ("tests", changed_tests),
        ("ownership", changed_ownership),
        ("execution", changed_execution),
    ):
        if payload["evidence_identity"] == ready["evidence_identity"]:
            failures.append(f"{label} change did not change evidence identity")

    post_tests = _tests()
    post_receipt = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=_post_identity(),
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=_post_sources(),
        post_edit_change_set_evidence=_change_set(),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=_post_identity(), repository_identity="sha256:repo-b"
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate-full-b",
            }
        ],
    )
    if post_receipt["status"] != PRESERVED:
        failures.append(
            "matching frozen post-edit evidence did not verify preservation"
        )

    stale_post_execution = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=_post_identity(),
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=_post_sources(),
        post_edit_change_set_evidence=_change_set(),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=_post_identity(),
            repository_identity="sha256:repo-a",
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate-current",
            }
        ],
    )
    if stale_post_execution["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append(
            "focused execution from a different repository state verified preservation"
        )

    stale_repository_gate = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=_post_identity(),
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=_post_sources(),
        post_edit_change_set_evidence=_change_set(),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=_post_identity(),
            repository_identity="sha256:repo-b",
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-a",
                "execution_identity": "sha256:gate-stale",
            }
        ],
    )
    if stale_repository_gate["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append(
            "repository gate from a different repository state verified preservation"
        )

    missing_post_sources = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=_post_identity(),
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=[],
        post_edit_change_set_evidence=_change_set(),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=_post_identity(), repository_identity="sha256:repo-b"
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate",
            }
        ],
    )
    if missing_post_sources["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append("missing post-edit source set verified preservation")

    split_sources = _post_sources(
        ("src/pkg/core.py", "sha256:source-b"),
        ("src/pkg/core_rules.py", "sha256:source-rules"),
    )
    split_identity = _post_identity(split_sources)
    split_receipt = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=split_identity,
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=split_sources,
        post_edit_change_set_evidence=_change_set(split_sources),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=split_identity, repository_identity="sha256:repo-b"
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate-split",
            }
        ],
    )
    if split_receipt["status"] != PRESERVED:
        failures.append("complete split source set did not verify preservation")

    incomplete_split = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=split_identity,
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=_post_sources(),
        post_edit_change_set_evidence=_change_set(split_sources),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=split_identity, repository_identity="sha256:repo-b"
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate",
            }
        ],
    )
    if incomplete_split["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append("incomplete split source references verified preservation")

    incomplete_sources = _post_sources()
    incomplete_identity = _post_identity(incomplete_sources)
    self_consistent_incomplete_split = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=incomplete_identity,
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=incomplete_sources,
        post_edit_change_set_evidence=_change_set(split_sources),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=incomplete_identity, repository_identity="sha256:repo-b"
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate-incomplete",
            }
        ],
    )
    if self_consistent_incomplete_split["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append(
            "self-consistent incomplete post-edit source set verified preservation"
        )
    if "post-edit-source-set-completeness" not in self_consistent_incomplete_split.get(
        "unresolved_evidence", []
    ):
        failures.append(
            "incomplete post-edit source-set completeness mismatch was not explicit"
        )
    post_authority = post_receipt.get("authority", {})
    if (
        not isinstance(post_authority, dict)
        or post_authority.get("merge_authorized") is not False
    ):
        failures.append("post-edit preservation receipt incorrectly authorized merge")

    changed_post_tests = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=_post_identity(),
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=_post_sources(),
        post_edit_change_set_evidence=_change_set(),
        post_edit_test_references=[
            {"path": "tests/test_core.py", "evidence_identity": "sha256:test-changed"}
        ],
        post_edit_execution_receipt=_receipt(
            source_identity="sha256:source-b",
            repository_identity="sha256:repo-b",
            test_ids=["sha256:test-changed"],
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate",
            }
        ],
    )
    if changed_post_tests["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append("changed frozen test identity verified preservation")

    wrong_post_source = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=_post_identity(),
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=_post_sources(),
        post_edit_change_set_evidence=_change_set(),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity="sha256:source-c", repository_identity="sha256:repo-b"
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate",
            }
        ],
    )
    if wrong_post_source["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append("post-edit receipt bound to wrong source verified preservation")

    failed_post = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=_post_identity(),
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=_post_sources(),
        post_edit_change_set_evidence=_change_set(),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=_post_identity(),
            repository_identity="sha256:repo-b",
            status="FAIL",
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "PASS",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate",
            }
        ],
    )
    if failed_post["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append("failed post-edit focused execution verified preservation")

    missing_gate = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=_post_identity(),
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=_post_sources(),
        post_edit_change_set_evidence=_change_set(),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=_post_identity(), repository_identity="sha256:repo-b"
        ),
        repository_gate_receipts=[],
    )
    if missing_gate["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append("missing post-edit repository gate verified preservation")

    failed_gate = behavior_preservation_receipt(
        pre_edit_evidence=ready,
        post_edit_source_identity=_post_identity(),
        post_edit_repository_identity="sha256:repo-b",
        post_edit_source_references=_post_sources(),
        post_edit_change_set_evidence=_change_set(),
        post_edit_test_references=post_tests,
        post_edit_execution_receipt=_receipt(
            source_identity=_post_identity(), repository_identity="sha256:repo-b"
        ),
        repository_gate_receipts=[
            {
                "name": "full",
                "command": "uv run pytest",
                "status": "FAIL",
                "repository_identity": "sha256:repo-b",
                "execution_identity": "sha256:gate",
            }
        ],
    )
    if failed_gate["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append("failed post-edit repository gate verified preservation")

    locality_pre = _independent_locality_snapshot(
        repository_identity="sha256:repo-a",
        source_identity="sha256:locality-source-a",
    )
    locality_post = _independent_locality_snapshot(
        repository_identity="sha256:repo-b",
        source_identity="sha256:locality-source-b",
    )
    locality_comparison = compare_locality(locality_pre, locality_post)
    locality_decision = decomposition_decision(
        pre_snapshot=locality_pre,
        post_snapshot=locality_post,
        comparison=locality_comparison,
        decomposition_evidence=[],
    )

    delta_args = {
        "target": "src/pkg/core.py",
        "pre_measurement_identity": "sha256:debt-before",
        "post_measurement_identity": "sha256:debt-after",
        "measurement_configuration_identity": "sha256:config",
        "post_measurement_configuration_identity": "sha256:config",
        "pre_repository_excess": 1905,
        "post_repository_excess": 1840,
        "pre_target_excess": 118,
        "post_target_excess": 53,
        "preservation_receipt": post_receipt,
        "locality_decision": locality_decision,
        "post_locality_snapshot": locality_post,
    }
    debt_verified = behavior_preservation_debt_delta(**delta_args)
    if debt_verified["status"] != DEBT_VERIFIED:
        failures.append("matching behavior/locality/debt closure did not verify")

    missing_locality = behavior_preservation_debt_delta(
        **{
            **delta_args,
            "locality_decision": None,
            "post_locality_snapshot": None,
        }
    )
    if missing_locality["status"] != LOCALITY_REVIEW_REQUIRED:
        failures.append("Ruff debt reduction closed without locality qualification")

    extracted_post = _independent_locality_snapshot(
        repository_identity="sha256:repo-b",
        source_identity="sha256:locality-source-extracted",
        include_earned_helper=True,
    )
    extracted_comparison = compare_locality(locality_pre, extracted_post)
    extracted_unreviewed = decomposition_decision(
        pre_snapshot=locality_pre,
        post_snapshot=extracted_post,
        comparison=extracted_comparison,
        decomposition_evidence=[],
    )
    extracted_without_review = behavior_preservation_debt_delta(
        **{
            **delta_args,
            "locality_decision": extracted_unreviewed,
            "post_locality_snapshot": extracted_post,
        }
    )
    if extracted_without_review["status"] != LOCALITY_REVIEW_REQUIRED:
        failures.append(
            "earned one-caller extraction closed debt without decomposition review"
        )

    extracted_justified = decomposition_decision(
        pre_snapshot=locality_pre,
        post_snapshot=extracted_post,
        comparison=extracted_comparison,
        decomposition_evidence=[
            {
                "kind": "mixed_responsibilities",
                "evidence_identity": "sha256:mixed-responsibilities",
                "summary": "normalization owns an independent semantic responsibility",
            },
            {
                "kind": "bounded_locality_tradeoff",
                "evidence_identity": "sha256:bounded-tradeoff",
                "summary": "one additional navigation hop is explicitly accepted",
            },
        ],
    )
    justified_extraction = behavior_preservation_debt_delta(
        **{
            **delta_args,
            "locality_decision": extracted_justified,
            "post_locality_snapshot": extracted_post,
        }
    )
    if justified_extraction["status"] != DEBT_VERIFIED:
        failures.append(
            "evidence-bound good decomposition could not close verified debt reduction"
        )

    redistributed = behavior_preservation_debt_delta(
        **{**delta_args, "post_repository_excess": 1850}
    )
    if redistributed["status"] != DEBT_REDISTRIBUTED:
        failures.append(
            "outside-target debt increase was not rejected as redistribution"
        )

    not_reduced = behavior_preservation_debt_delta(
        **{**delta_args, "post_target_excess": 118, "post_repository_excess": 1905}
    )
    if not_reduced["status"] != TARGET_DEBT_NOT_REDUCED:
        failures.append("unchanged selected-target debt was not rejected")

    incomparable = behavior_preservation_debt_delta(
        **{
            **delta_args,
            "post_measurement_configuration_identity": "sha256:other-config",
        }
    )
    if incomparable["status"] != MEASUREMENT_NOT_COMPARABLE:
        failures.append(
            "mismatched measurement configuration was treated as comparable"
        )

    unverified_preservation = behavior_preservation_debt_delta(
        **{**delta_args, "preservation_receipt": failed_post}
    )
    if unverified_preservation["status"] != POST_EDIT_EVIDENCE_REQUIRED:
        failures.append("debt delta verified without BP2 preservation")

    observations = {
        "ready_status": ready["status"],
        "no_ownership_status": no_ownership["status"],
        "unrelated_passing_status": unrelated_passing["status"],
        "no_receipt_status": no_receipt["status"],
        "wrong_source_status": wrong_source["status"],
        "stale_status": stale["status"],
        "unbound_boundary_status": unbound_boundary["status"],
        "unknown_boundary_test_status": unknown_boundary_test["status"],
        "partially_bound_multi_boundary_status": partially_bound_multi_boundary[
            "status"
        ],
        "indirect_status": indirect["status"],
        "failed_status": failed["status"],
        "no_gates_status": no_gates["status"],
        "coverage_only_status": coverage_only["status"],
        "authority": authority,
        "post_edit_requirements": ready.get("post_edit_requirements"),
        "post_edit_status": post_receipt["status"],
        "debt_closure_status": debt_verified["status"],
        "missing_locality_status": missing_locality["status"],
        "unreviewed_extraction_status": extracted_without_review["status"],
        "justified_extraction_status": justified_extraction["status"],
        "stale_post_execution_status": stale_post_execution["status"],
        "stale_repository_gate_status": stale_repository_gate["status"],
        "post_edit_authority": post_authority,
        "missing_post_sources_status": missing_post_sources["status"],
        "split_source_set_status": split_receipt["status"],
        "incomplete_split_status": incomplete_split["status"],
        "self_consistent_incomplete_split_status": self_consistent_incomplete_split[
            "status"
        ],
        "debt_delta_status": debt_verified["status"],
        "redistributed_status": redistributed["status"],
        "incomparable_status": incomparable["status"],
    }
    result: dict[str, object] = {
        "probe": "behavior-preservation",
        "qualification_phase": "BP1-BP2",
        "passed": not failures,
        "observations": observations,
        "failures": failures,
    }
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(result, indent=2) + "\\n", encoding="utf-8")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Qualify BP1/BP2 behavior-preservation evidence."
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/behavior-preservation-bp1-qualification.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = qualify(args.artifact_path)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
