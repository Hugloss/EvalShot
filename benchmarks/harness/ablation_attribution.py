"""Controlled Harbor component-ablation analysis.

This layer compares matched bare/full/remove/only quartets. It consumes only
immutable receipts plus observable ATIF tool evidence. It never consumes model
reasoning or upgrades a paired contrast into positive causal proof.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from benchmarks.tool_routing import is_subject_tool, matches_subject_operation
from benchmarks.harness.semantic_outcome_analysis import (
    quartet_information_outcomes,
    summarize_information_outcomes,
)

from .mechanism_attribution import (
    MechanismAttributionError,
    load_harbor_bundle_projection,
)

ABLATION_REPORT_SCHEMA = "agentscookbook.harbor-ablation-report.v2"
ABLATION_ROLES = ("bare", "full", "remove", "only")
LEGACY_TASK_EVIDENCE_CONTRACT = {
    "component": "task_evidence",
    "arms": {
        "bare": "none",
        "full": "hashmarks",
        "remove": "hashmarks-no-task-evidence",
        "only": "hashmarks-task-evidence-only",
    },
}


def _pair_key(receipt: Mapping[str, Any]) -> tuple[object, ...]:
    execution = receipt.get("execution")
    campaign_id = (
        execution.get("campaign_id")
        if isinstance(execution, dict)
        else None
    )
    return (
        campaign_id,
        receipt.get("task_id"),
        receipt.get("harness"),
        receipt.get("model"),
        receipt.get("replicate_id"),
    )


def _status(projection: Mapping[str, Any]) -> str:
    value = projection["receipt"].get("status")
    return str(value) if value in {"PASS", "FAIL"} else "INCOMPLETE"


def _normalize_ablation_contract(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    component = value.get("component")
    arms = value.get("arms")
    if (
        not isinstance(component, str)
        or not component
        or not isinstance(arms, Mapping)
        or set(arms) != set(ABLATION_ROLES)
    ):
        return None
    normalized_arms = {
        role: str(arms[role])
        for role in ABLATION_ROLES
        if isinstance(arms.get(role), str) and arms.get(role)
    }
    if (
        len(normalized_arms) != len(ABLATION_ROLES)
        or len(set(normalized_arms.values())) != len(ABLATION_ROLES)
    ):
        return None
    selector = value.get("selector")
    normalized_selector = None
    if selector is not None:
        if (
            not isinstance(selector, Mapping)
            or set(selector) != {"argument", "value"}
            or selector.get("argument") != "surface_name"
            or not isinstance(selector.get("value"), str)
            or not selector["value"]
        ):
            return None
        normalized_selector = {
            "argument": "surface_name",
            "value": str(selector["value"]),
        }
    return {
        "component": component,
        **({"selector": normalized_selector} if normalized_selector else {}),
        "arms": normalized_arms,
    }


def _receipt_ablation_contract(
    receipt: Mapping[str, Any],
) -> dict[str, Any] | None:
    execution = receipt.get("execution")
    value = execution.get("ablation") if isinstance(execution, Mapping) else None
    return _normalize_ablation_contract(value)


def _component_invoked(
    projection: Mapping[str, Any],
    *,
    component: str,
    selector: Mapping[str, str] | None = None,
) -> bool | None:
    trace = projection.get("trace")
    if not isinstance(trace, dict) or trace.get("available") is not True:
        return None
    if selector is None:
        tools = trace.get("subject_tools")
        if not isinstance(tools, list):
            return None
        return any(
            matches_subject_operation(
                name,
                subject="hashmarks",
                operation=component,
            )
            for name in tools
        )
    calls = trace.get("subject_call_selectors")
    if not isinstance(calls, list):
        return None
    matching_tool_observed = False
    for raw in calls:
        if not isinstance(raw, Mapping):
            continue
        name = raw.get("tool")
        if not matches_subject_operation(
            name,
            subject="hashmarks",
            operation=component,
        ):
            continue
        matching_tool_observed = True
        if raw.get(selector["argument"]) == selector["value"]:
            return True
    return False if matching_tool_observed or calls else False


def _treatment(receipt: Mapping[str, Any]) -> Mapping[str, Any] | None:
    execution = receipt.get("execution")
    value = (
        execution.get("mcp_treatment")
        if isinstance(execution, dict)
        else None
    )
    return value if isinstance(value, Mapping) else None


def _validate_quartet_authority(
    arms: Mapping[str, Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
) -> tuple[bool, str | None]:
    if set(arms) != set(ABLATION_ROLES):
        return False, "missing-ablation-arm"
    component = str(contract["component"])
    full = _treatment(arms["full"]["receipt"])
    removed = _treatment(arms["remove"]["receipt"])
    only = _treatment(arms["only"]["receipt"])
    if not all(isinstance(value, Mapping) for value in (full, removed, only)):
        return False, "missing-frozen-mcp-treatment"
    assert full is not None and removed is not None and only is not None
    identities = {
        value.get("source_contract_identity")
        for value in (full, removed, only)
    }
    if len(identities) != 1 or None in identities:
        return False, "source-contract-mismatch"
    full_tools = full.get("tools")
    removed_tools = removed.get("tools")
    only_tools = only.get("tools")
    if not (
        full.get("full_contract") is True
        and isinstance(full_tools, list)
        and component in full_tools
    ):
        return False, "full-arm-not-canonical"
    selector = contract.get("selector")
    if selector is None:
        if not (
            removed.get("full_contract") is False
            and isinstance(removed_tools, list)
            and component not in removed_tools
            and set(removed_tools) == set(full_tools) - {component}
        ):
            return False, "removal-arm-not-single-tool-ablation"
        if not (
            only.get("full_contract") is False
            and only_tools == [component]
        ):
            return False, "only-arm-not-component-only"
        return True, None

    value = selector.get("value") if isinstance(selector, Mapping) else None
    full_surfaces = full.get("repository_intelligence_query_surfaces")
    removed_surfaces = removed.get("repository_intelligence_query_surfaces")
    only_surfaces = only.get("repository_intelligence_query_surfaces")
    if not (
        isinstance(value, str)
        and isinstance(full_surfaces, list)
        and value in full_surfaces
    ):
        return False, "full-arm-selector-authority-missing"
    if not (
        removed.get("full_contract") is False
        and removed_tools == full_tools
        and isinstance(removed_surfaces, list)
        and set(removed_surfaces) == set(full_surfaces) - {value}
    ):
        return False, "removal-arm-not-single-selector-ablation"
    if not (
        only.get("full_contract") is False
        and only_tools == [component]
        and only_surfaces == [value]
    ):
        return False, "only-arm-not-selector-only"
    return True, None


def _observed_tool_projection(
    arm: Mapping[str, Any],
    *,
    canonical_tools: list[str],
) -> dict[str, Any]:
    """Conservatively check ATIF calls against the frozen arm catalog.

    ATIF records invocations, not the advertised MCP catalog. Missing or
    unrecognized calls must never be promoted to proof of exposed tools.
    """

    receipt = arm["receipt"]
    trace = arm.get("trace")
    treatment = _treatment(receipt)
    allowed = treatment.get("tools") if isinstance(treatment, Mapping) else []
    subject_tools = trace.get("subject_tools") if isinstance(trace, Mapping) else None
    subject_selectors = (
        trace.get("subject_call_selectors")
        if isinstance(trace, Mapping)
        else None
    )
    allowed_surfaces = (
        treatment.get("repository_intelligence_query_surfaces")
        if isinstance(treatment, Mapping)
        else None
    )
    if (
        not isinstance(trace, Mapping)
        or trace.get("available") is not True
        or trace.get("tool_order_complete") is not True
        or not isinstance(subject_tools, list)
        or (
            isinstance(allowed_surfaces, list)
            and not isinstance(subject_selectors, list)
        )
    ):
        return {
            "status": "UNQUALIFIED_TRACE_INCOMPLETE",
            "observed_subject_calls": None,
            "disallowed_calls": [],
            "disallowed_selectors": [],
            "unresolved_calls": [],
            "catalog_advertisement_proven": False,
        }
    if not isinstance(allowed, list) or any(
        not isinstance(name, str) for name in allowed
    ):
        allowed = []
    disallowed: list[str] = []
    disallowed_selectors: list[str] = []
    unresolved: list[str] = []
    observed = 0
    for raw in subject_tools:
        if not is_subject_tool(raw, "hashmarks"):
            unresolved.append(str(raw))
            continue
        observed += 1
        candidates = [
            operation
            for operation in canonical_tools
            if matches_subject_operation(
                raw,
                subject="hashmarks",
                operation=operation,
            )
        ]
        if len(candidates) != 1:
            unresolved.append(str(raw))
        elif candidates[0] not in allowed:
            disallowed.append(str(raw))
    if isinstance(allowed_surfaces, list):
        if not all(isinstance(value, str) and value for value in allowed_surfaces):
            allowed_surfaces = []
        selector_rows = subject_selectors if isinstance(subject_selectors, list) else []
        for raw in selector_rows:
            if not isinstance(raw, Mapping):
                unresolved.append("malformed-subject-selector")
                continue
            name = raw.get("tool")
            if not matches_subject_operation(
                name,
                subject="hashmarks",
                operation="repository_intelligence_query",
            ):
                continue
            surface = raw.get("surface_name")
            if not isinstance(surface, str) or not surface:
                unresolved.append(str(name))
            elif surface not in allowed_surfaces:
                disallowed_selectors.append(surface)

    status = (
        "UNQUALIFIED_DISALLOWED_CALL"
        if disallowed or disallowed_selectors
        else "UNQUALIFIED_UNRESOLVED_CALL"
        if unresolved
        else "NO_DISALLOWED_CALL_OBSERVED"
    )
    return {
        "status": status,
        "observed_subject_calls": observed,
        "disallowed_calls": disallowed,
        "disallowed_selectors": disallowed_selectors,
        "unresolved_calls": unresolved,
        "catalog_advertisement_proven": False,
    }


def _quartet_call_audits(
    arms: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    full = _treatment(arms["full"]["receipt"])
    canonical_tools = (
        full.get("tools")
        if isinstance(full, Mapping)
        else None
    )
    if not isinstance(canonical_tools, list):
        canonical_tools = []
    return {
        role: _observed_tool_projection(
            arms[role],
            canonical_tools=canonical_tools,
        )
        for role in ABLATION_ROLES
    }


def _necessity(
    *,
    full_status: str,
    removed_status: str,
    full_invoked: bool | None,
) -> str:
    if full_status != "PASS":
        return "NOT_TESTABLE_FULL_DID_NOT_PASS"
    if removed_status == "PASS":
        return "NOT_NECESSARY_IN_THIS_REPLICATE"
    if removed_status != "FAIL":
        return "UNKNOWN_INCOMPLETE_REMOVAL_ARM"
    if full_invoked is True:
        return "SUPPORTED_NECESSITY_CONTRAST"
    if full_invoked is False:
        return "UNATTRIBUTABLE_FULL_NEVER_INVOKED_COMPONENT"
    return "UNKNOWN_COMPONENT_INVOCATION"


def _sufficiency(
    *,
    bare_status: str,
    only_status: str,
    only_invoked: bool | None,
) -> str:
    if bare_status != "FAIL":
        return (
            "NOT_TESTABLE_BARE_ALREADY_PASS"
            if bare_status == "PASS"
            else "UNKNOWN_INCOMPLETE_BARE_ARM"
        )
    if only_status == "FAIL":
        return "NOT_SUFFICIENT_IN_THIS_REPLICATE"
    if only_status != "PASS":
        return "UNKNOWN_INCOMPLETE_ONLY_ARM"
    if only_invoked is True:
        return "SUPPORTED_SUFFICIENCY_CONTRAST"
    if only_invoked is False:
        return "UNATTRIBUTABLE_ONLY_ARM_NEVER_INVOKED_COMPONENT"
    return "UNKNOWN_COMPONENT_INVOCATION"


def _combined(necessity: str, sufficiency: str) -> str:
    necessary = necessity == "SUPPORTED_NECESSITY_CONTRAST"
    sufficient = sufficiency == "SUPPORTED_SUFFICIENCY_CONTRAST"
    if necessary and sufficient:
        return "NECESSARY_AND_SUFFICIENT_CONTRAST"
    if necessary:
        return "NECESSITY_SIGNAL"
    if sufficient:
        return "SUFFICIENCY_SIGNAL"
    if necessity == "NOT_NECESSARY_IN_THIS_REPLICATE":
        return "REDUNDANT_OR_OTHER_HASHMARKS_PATH"
    return "NO_ISOLATED_COMPONENT_SIGNAL"


def _normalize_quartet_arms(
    arms: Mapping[str, Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    if set(arms) == set(ABLATION_ROLES):
        return {role: arms[role] for role in ABLATION_ROLES}
    subjects = contract["arms"]
    if set(arms) == set(subjects.values()):
        return {
            role: arms[subjects[role]]
            for role in ABLATION_ROLES
        }
    raise MechanismAttributionError("component-ablation quartet has wrong arm keys")


def quartet_projection(
    arms: Mapping[str, Mapping[str, Any]],
    *,
    contract: Mapping[str, Any] | None = None,
    contract_source: str = "receipt",
) -> dict[str, Any]:
    normalized = (
        _normalize_ablation_contract(contract)
        if contract is not None
        else dict(LEGACY_TASK_EVIDENCE_CONTRACT)
    )
    if normalized is None:
        raise MechanismAttributionError("invalid component-ablation contract")
    component = str(normalized["component"])
    selector = normalized.get("selector")
    normalized_arms = _normalize_quartet_arms(
        arms,
        contract=normalized,
    )
    valid, authority_error = _validate_quartet_authority(
        normalized_arms,
        contract=normalized,
    )
    call_audits = _quartet_call_audits(normalized_arms)
    if valid:
        for role in ABLATION_ROLES:
            if call_audits[role]["status"] != "NO_DISALLOWED_CALL_OBSERVED":
                valid = False
                authority_error = (
                    "observed-mcp-call-not-qualified:"
                    + role
                    + ":"
                    + str(normalized["arms"][role])
                    + ":"
                    + str(call_audits[role]["status"])
                )
                break
    statuses = {
        role: _status(normalized_arms[role])
        for role in ABLATION_ROLES
    }
    full_invoked = _component_invoked(
        normalized_arms["full"],
        component=component,
        selector=selector if isinstance(selector, Mapping) else None,
    )
    only_invoked = _component_invoked(
        normalized_arms["only"],
        component=component,
        selector=selector if isinstance(selector, Mapping) else None,
    )
    necessity = (
        _necessity(
            full_status=statuses["full"],
            removed_status=statuses["remove"],
            full_invoked=full_invoked,
        )
        if valid
        else "UNQUALIFIED_TREATMENT_AUTHORITY"
    )
    sufficiency = (
        _sufficiency(
            bare_status=statuses["bare"],
            only_status=statuses["only"],
            only_invoked=only_invoked,
        )
        if valid
        else "UNQUALIFIED_TREATMENT_AUTHORITY"
    )
    semantic_arms = {}
    for role in ("full", "only"):
        projected = normalized_arms[role].get("component_semantic_information")
        semantic_arms[role] = (
            projected
            if isinstance(projected, Mapping)
            else {
                "qualified": False,
                "reason": "component-semantic-projection-missing",
            }
        )
    semantic_reason = None
    if not valid:
        semantic_reason = "treatment-unqualified"
    elif any(status not in {"PASS", "FAIL"} for status in statuses.values()):
        semantic_reason = "incomplete-quartet-outcomes"
    elif full_invoked is not True:
        semantic_reason = "full-component-not-invoked-or-unknown"
    elif only_invoked is not True:
        semantic_reason = "only-component-not-invoked-or-unknown"
    else:
        for role in ("full", "only"):
            if semantic_arms[role].get("qualified") is not True:
                semantic_reason = (
                    role + ":" + str(semantic_arms[role].get("reason") or "unqualified")
                )
                break
    receipt = normalized_arms["bare"]["receipt"]
    semantic_component = {
        "qualified": semantic_reason is None,
        "reason": semantic_reason,
        "full": dict(semantic_arms["full"]),
        "only": dict(semantic_arms["only"]),
        "positive_causal_proof_claimed": False,
    }
    outcomes = quartet_information_outcomes({
        "semantic_component_evidence": semantic_component,
        "statuses": statuses,
    })
    return {
        "semantic_information_outcomes": outcomes,
        "semantic_component_evidence": {
            "qualified": semantic_reason is None,
            "reason": semantic_reason,
            "full": dict(semantic_arms["full"]),
            "only": dict(semantic_arms["only"]),
            "positive_causal_proof_claimed": False,
        },
        "task": receipt.get("task_id"),
        "harness": receipt.get("harness"),
        "model": receipt.get("model"),
        "replicate_id": receipt.get("replicate_id"),
        "component": component,
        **({"selector": dict(selector)} if isinstance(selector, Mapping) else {}),
        "subjects": dict(normalized["arms"]),
        "contract_source": contract_source,
        "statuses": statuses,
        "treatment_authority_valid": valid,
        "treatment_authority_error": authority_error,
        "observed_call_projection": {
            normalized["arms"][role]: call_audits[role]
            for role in ABLATION_ROLES
        },
        "observed_catalog_advertisement_proven": False,
        "component_invoked": {
            "full": full_invoked,
            "only": only_invoked,
        },
        "necessity": necessity,
        "sufficiency": sufficiency,
        "classification": (
            _combined(necessity, sufficiency)
            if valid
            else "UNQUALIFIED_TREATMENT_AUTHORITY"
        ),
        "positive_causal_proof_claimed": False,
    }


def _qualified_complete_quartet(quartet: Mapping[str, Any]) -> bool:
    """Only qualified, fully observed quartets support component contrasts."""

    statuses = quartet.get("statuses")
    return (
        quartet.get("treatment_authority_valid") is True
        and isinstance(statuses, Mapping)
        and all(
            statuses.get(role) in {"PASS", "FAIL"}
            for role in ABLATION_ROLES
        )
    )


def _qualified_rate(
    quartets: list[dict[str, Any]],
    role: str,
) -> float | None:
    if not quartets:
        return None
    return (
        sum(row["statuses"][role] == "PASS" for row in quartets)
        / len(quartets)
    )


def _contrast(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _has_frozen_factorial(projection: Mapping[str, Any]) -> bool:
    receipt = projection.get("receipt")
    execution = receipt.get("execution") if isinstance(receipt, Mapping) else None
    return isinstance(execution, Mapping) and execution.get("factorial") is not None


def _legacy_contract_needed(
    projections: list[dict[str, Any]],
) -> bool:
    legacy_specific = {
        LEGACY_TASK_EVIDENCE_CONTRACT["arms"]["remove"],
        LEGACY_TASK_EVIDENCE_CONTRACT["arms"]["only"],
    }
    return any(
        not _has_frozen_factorial(projection)
        and _receipt_ablation_contract(projection["receipt"]) is None
        and str(projection["receipt"].get("subject")) in legacy_specific
        for projection in projections
    )


def build_ablation_report(results_root: Path) -> dict[str, Any]:
    projections: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    if results_root.is_dir():
        for directory in sorted(results_root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                projections.append(load_harbor_bundle_projection(directory))
            except (MechanismAttributionError, OSError, ValueError) as exc:
                unavailable.append(
                    {"directory": str(directory), "reason": str(exc)}
                )

    legacy_enabled = _legacy_contract_needed(projections)
    grouped: dict[
        tuple[object, ...],
        dict[str, list[dict[str, Any]]],
    ] = defaultdict(lambda: defaultdict(list))
    contracts: dict[tuple[object, ...], dict[str, Any]] = {}
    contract_sources: dict[tuple[object, ...], set[str]] = defaultdict(set)
    eligible: list[tuple[dict[str, Any], dict[str, Any], str]] = []

    for projection in projections:
        receipt = projection["receipt"]
        contract = _receipt_ablation_contract(receipt)
        source = "receipt"
        if contract is None and legacy_enabled and not _has_frozen_factorial(projection):
            subject = str(receipt.get("subject"))
            if subject in LEGACY_TASK_EVIDENCE_CONTRACT["arms"].values():
                contract = dict(LEGACY_TASK_EVIDENCE_CONTRACT)
                source = "legacy-task-evidence-subjects"
        if contract is None:
            continue
        subject = str(receipt.get("subject"))
        role = next(
            (
                role_name
                for role_name, role_subject in contract["arms"].items()
                if role_subject == subject
            ),
            None,
        )
        if role is None:
            continue
        key = (
            *_pair_key(receipt),
            contract["component"],
            (
                (
                    contract["selector"]["argument"],
                    contract["selector"]["value"],
                )
                if isinstance(contract.get("selector"), Mapping)
                else None
            ),
            tuple(
                (role_name, contract["arms"][role_name])
                for role_name in ABLATION_ROLES
            ),
        )
        grouped[key][role].append(projection)
        contracts[key] = contract
        contract_sources[key].add(source)
        eligible.append((projection, contract, role))

    components = {
        (
            str(contract["component"]),
            (
                (
                    str(contract["selector"]["argument"]),
                    str(contract["selector"]["value"]),
                )
                if isinstance(contract.get("selector"), Mapping)
                else None
            ),
        )
        for contract in contracts.values()
    }
    if len(components) > 1:
        raise MechanismAttributionError(
            "mixed component-ablation contracts in one Harbor result set"
        )

    quartets: list[dict[str, Any]] = []
    incomplete_groups: list[dict[str, Any]] = []
    for key, arms in sorted(
        grouped.items(),
        key=lambda item: tuple(str(value) for value in item[0]),
    ):
        contract = contracts[key]
        if any(len(arms.get(role, [])) != 1 for role in ABLATION_ROLES):
            incomplete_groups.append(
                {
                    "pair_key": [
                        str(value) if value is not None else None
                        for value in key[:5]
                    ],
                    "component": contract["component"],
                    "arm_counts": {
                        role: len(arms.get(role, []))
                        for role in ABLATION_ROLES
                    },
                }
            )
            continue
        sources = contract_sources[key]
        quartets.append(
            quartet_projection(
                {role: arms[role][0] for role in ABLATION_ROLES},
                contract=contract,
                contract_source=(
                    next(iter(sources))
                    if len(sources) == 1
                    else "mixed-compatible"
                ),
            )
        )

    qualified = [
        row
        for row in quartets
        if _qualified_complete_quartet(row)
    ]
    invalid_treatment = sum(
        row["treatment_authority_valid"] is not True
        for row in quartets
    )
    incomplete_outcomes = (
        len(quartets)
        - len(qualified)
        - invalid_treatment
    )
    semantic_qualified = [
        row
        for row in qualified
        if row["semantic_component_evidence"]["qualified"] is True
    ]
    semantic_outcomes = summarize_information_outcomes(quartets)
    semantic_exclusions = Counter(
        str(row["semantic_component_evidence"].get("reason") or "unqualified")
        for row in quartets
        if row["semantic_component_evidence"]["qualified"] is not True
    )
    semantic_cross_tab = Counter(
        "|".join((
            str(row["classification"]),
            str(row["statuses"]["full"]),
            str(row["statuses"]["only"]),
            str(row["semantic_component_evidence"]["full"]["claim_alignment"]),
            str(row["semantic_component_evidence"]["full"]["arrival_timing"]),
            str(row["semantic_component_evidence"]["full"]["final_answer_overlap"]),
            str(row["semantic_component_evidence"]["only"]["claim_alignment"]),
            str(row["semantic_component_evidence"]["only"]["arrival_timing"]),
            str(row["semantic_component_evidence"]["only"]["final_answer_overlap"]),
        ))
        for row in semantic_qualified
    )
    classifications = Counter(
        str(row["classification"])
        for row in quartets
    )
    necessity = Counter(str(row["necessity"]) for row in quartets)
    sufficiency = Counter(str(row["sufficiency"]) for row in quartets)

    component_key = next(iter(components)) if components else None
    component = component_key[0] if component_key is not None else None
    selector = (
        {
            "argument": component_key[1][0],
            "value": component_key[1][1],
        }
        if component_key is not None and component_key[1] is not None
        else None
    )
    arms = (
        dict(next(iter(contracts.values()))["arms"])
        if contracts
        else {}
    )
    by_harness: dict[str, dict[str, Any]] = {}
    harnesses = sorted(
        {
            str(row["harness"])
            for row in quartets
        }
    )
    for harness in harnesses:
        matched = [
            row
            for row in quartets
            if row["harness"] == harness
        ]
        selected = [
            row
            for row in qualified
            if row["harness"] == harness
        ]
        per_role = {
            role: _qualified_rate(selected, role)
            for role in ABLATION_ROLES
        }
        bare = per_role["bare"]
        full = per_role["full"]
        removed = per_role["remove"]
        only = per_role["only"]
        by_harness[harness] = {
            "matched_quartets": len(matched),
            "qualified_complete_quartets": len(selected),
            "semantic_qualified_quartets": sum(
                row["semantic_component_evidence"]["qualified"] is True
                for row in selected
            ),
            "excluded_matched_quartets": len(matched) - len(selected),
            "subjects": arms,
            "success_rate": per_role,
            "contrasts": {
                "full_uplift_vs_bare": _contrast(full, bare),
                "removal_drop": _contrast(full, removed),
                "only_uplift_vs_bare": _contrast(only, bare),
            },
        }

    applicable = bool(contracts)
    return {
        "schema": ABLATION_REPORT_SCHEMA,
        "applicable": applicable,
        "component": component,
        **({"selector": selector} if selector is not None else {}),
        "arms": arms,
        "quartets": quartets,
        "summary": {
            "matched_quartets": len(quartets),
            "qualified_complete_quartets": len(qualified),
            "semantic_qualified_quartets": len(semantic_qualified),
            "semantic_information_outcomes": semantic_outcomes,
            "semantic_exclusion_reasons": dict(sorted(semantic_exclusions.items())),
            "semantic_outcome_cross_tab": dict(sorted(semantic_cross_tab.items())),
            "treatment_unqualified_quartets": invalid_treatment,
            "incomplete_outcome_quartets": incomplete_outcomes,
            "excluded_matched_quartets": len(quartets) - len(qualified),
            "incomplete_groups": len(incomplete_groups),
            "unavailable_bundles": len(unavailable),
            "classification": dict(sorted(classifications.items())),
            "necessity": dict(sorted(necessity.items())),
            "sufficiency": dict(sorted(sufficiency.items())),
            "positive_causal_proof_claimed": False,
        },
        "by_harness": by_harness,
        "incomplete_groups": incomplete_groups,
        "unavailable_bundles": unavailable,
        "method": {
            "design": (
                "same campaign + task + harness + model + replicate across "
                "bare/full/remove/only arms for one declared component or selector"
            ),
            "necessity_contrast": (
                "full Hashmarks versus the declared component/selector removal arm"
            ),
            "sufficiency_contrast": (
                "bare versus the declared component/selector-only Hashmarks arm"
            ),
            "invocation_gate": (
                "positive component attribution requires observable invocation "
                "of the exact declared tool and selector, when present, in the "
                "relevant full or only arm"
            ),
            "call_projection_limit": (
                "ATIF invocation records can disqualify observed calls outside "
                "a frozen MCP projection, but cannot attest to the entire tool "
                "catalog actually advertised by a model host"
            ),
            "aggregate_denominator": (
                "qualified complete matched quartets only; treatment-unqualified "
                "or incomplete outcomes are excluded, never scored as failures or "
                "mixed into per-harness component contrasts"
            ),
            "reasoning_content_consumed": False,
            "semantic_outcome_conditioning_policy": (
                "condition on first aligned/divergent claim observations, not "
                "unrelated returns; compare full to remove and only to bare "
                "within matched complete quartets. Observed evidence is a "
                "post-treatment selected mediator, not a randomized exposure; "
                "all conditional cells report counts and cluster denominators "
                "but no causal uplift or significance claim"
            ),
            "semantic_component_evidence_policy": (                "a post-run projection of observed results from the exact "
                "ablated tool or selector, checked against verifier-owned "
                "frozen semantic atoms; it requires full and only invocation, "
                "qualified complete outcomes and qualified ATIF observations; "
                "it is not proof that returned information changed decisions"
            ),
            "positive_causal_claim_policy": (
                "controlled component ablation strengthens attribution but does not "
                "by itself establish universal causal necessity or sufficiency"
            ),
        },
    }
