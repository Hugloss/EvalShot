"""Two-component factorial contrasts from immutable, matched Harbor receipts.

The four cells are full-minus-both, full-minus-B, full-minus-A, and full.
These are descriptive observed contrasts. They are not proof of causal use,
tool advertisement, or general component necessity.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from benchmarks.tool_routing import is_subject_tool, matches_subject_operation

from .evaluation_assurance import _bootstrap
from .mechanism_attribution import (
    MechanismAttributionError,
    load_harbor_bundle_projection,
)

SCHEMA = "agentscookbook.harbor-tool-factorial.v1"
ROLES = ("neither", "a_only", "b_only", "both")


def _contract(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != {"components", "arms"}:
        return None
    components, arms = value.get("components"), value.get("arms")
    if (
        not isinstance(components, list) or len(components) != 2
        or not all(isinstance(x, str) and x for x in components)
        or len(set(components)) != 2
        or not isinstance(arms, Mapping) or set(arms) != set(ROLES)
        or not all(isinstance(arms[r], str) and arms[r] for r in ROLES)
        or len({arms[r] for r in ROLES}) != 4
        or arms["both"] != "hashmarks"
        or "none" in arms.values()
    ):
        return None
    return {"components": list(components), "arms": {r: arms[r] for r in ROLES}}


def _execution(projection: Mapping[str, Any]) -> Mapping[str, Any]:
    receipt = projection.get("receipt")
    execution = receipt.get("execution") if isinstance(receipt, Mapping) else None
    return execution if isinstance(execution, Mapping) else {}


def _treatment(projection: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = _execution(projection).get("mcp_treatment")
    return value if isinstance(value, Mapping) else None


def _audit_tools(
    projection: Mapping[str, Any], allowed: set[str], canonical: list[str],
) -> tuple[bool, str | None, set[str]]:
    trace = projection.get("trace")
    if (
        not isinstance(trace, Mapping) or trace.get("available") is not True
        or trace.get("tool_order_complete") is not True
        or not isinstance(trace.get("subject_tools"), list)
    ):
        return False, "incomplete-tool-order", set()
    observed: set[str] = set()
    for name in trace["subject_tools"]:
        if not isinstance(name, str) or not is_subject_tool(name, "hashmarks"):
            return False, "unknown-subject-call", observed
        mapped = [
            operation for operation in canonical
            if matches_subject_operation(name, subject="hashmarks", operation=operation)
        ]
        if len(mapped) != 1:
            return False, "unknown-operation-spelling", observed
        operation = mapped[0]
        if operation not in allowed:
            return False, "forbidden-arm-operation", observed
        observed.add(operation)
    return True, None, observed


def factorial_quartet(arms: Mapping[str, Mapping[str, Any]], contract: object) -> dict[str, Any]:
    normalized = _contract(contract)
    if normalized is None:
        raise MechanismAttributionError("invalid factorial contract")
    if set(arms) != set(ROLES):
        raise MechanismAttributionError("invalid factorial arm roles")
    a, b = normalized["components"]
    receipt = arms["both"].get("receipt")
    if not isinstance(receipt, Mapping):
        raise MechanismAttributionError("factorial arm has no receipt")
    row: dict[str, Any] = {
        "task": receipt.get("task_id"),
        "harness": receipt.get("harness"),
        "model": receipt.get("model"),
        "replicate_id": receipt.get("replicate_id"),
        "components": [a, b],
        "statuses": {},
        "treatment_qualified": False,
        "qualification_reason": None,
        "interaction": None,
        "observed_component_use": {},
        "positive_causal_proof_claimed": False,
    }
    expected_tools = {
        "neither": {a, b},
        "a_only": {b},
        "b_only": {a},
        "both": set(),
    }
    treatments = {role: _treatment(arms[role]) for role in ROLES}
    full = treatments["both"]
    reason: str | None = None
    canonical: list[str] = []
    if any(value is None for value in treatments.values()):
        reason = "missing-frozen-treatment"
    elif (
        not isinstance(full.get("tools"), list)  # type: ignore[union-attr]
        or not all(isinstance(t, str) and t for t in full["tools"])  # type: ignore[index]
        or len(set(full["tools"])) != len(full["tools"])  # type: ignore[index]
    ):
        reason = "invalid-canonical-tools"
    else:
        assert full is not None
        canonical = list(full["tools"])
        identity = full.get("source_contract_identity")
        if not isinstance(identity, str) or not identity:
            reason = "missing-source-contract-identity"
        elif full.get("full_contract") is not True or not {a, b}.issubset(canonical):
            reason = "both-arm-not-full-catalog"
        else:
            for role in ROLES:
                treatment = treatments[role]
                assert treatment is not None
                role_receipt = arms[role].get("receipt")
                if (
                    not isinstance(role_receipt, Mapping)
                    or role_receipt.get("subject") != normalized["arms"][role]
                    or treatment.get("source_contract_identity") != identity
                ):
                    reason = "arm-source-or-subject-mismatch"
                    break
                allowed = set(canonical) - expected_tools[role]
                tools = treatment.get("tools")
                if (
                    not isinstance(tools, list) or len(tools) != len(allowed)
                    or not all(isinstance(tool, str) and tool for tool in tools)
                    or set(tools) != allowed
                    or treatment.get("full_contract") is not (role == "both")
                    or treatment.get("repository_intelligence_query_surfaces")
                    != full.get("repository_intelligence_query_surfaces")
                ):
                    reason = "arm-projection-not-exact"
                    break
                valid, issue, observed = _audit_tools(arms[role], allowed, canonical)
                row["observed_component_use"][role] = {
                    a: a in observed, b: b in observed,
                }
                if not valid:
                    reason = role + ":" + str(issue)
                    break
    statuses = {}
    for role in ROLES:
        value = arms[role].get("receipt")
        status = value.get("status") if isinstance(value, Mapping) else None
        statuses[role] = status if status in {"PASS", "FAIL"} else "INCOMPLETE"
    row["statuses"] = statuses
    if reason is None and "INCOMPLETE" in statuses.values():
        reason = "incomplete-factorial-outcome"
    row["qualification_reason"] = reason
    if reason is None:
        row["treatment_qualified"] = True
        success = {role: int(statuses[role] == "PASS") for role in ROLES}
        row["interaction"] = (
            success["both"] - success["a_only"]
            - success["b_only"] + success["neither"]
        )
    return row


def build_factorial_report(results_root: Path) -> dict[str, Any]:
    groups: dict[tuple[object, ...], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    contracts: dict[tuple[object, ...], dict[str, Any]] = {}
    exclusions: Counter[str] = Counter()
    if results_root.is_dir():
        for directory in sorted(results_root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                item = load_harbor_bundle_projection(directory)
            except (MechanismAttributionError, OSError, ValueError):
                exclusions["unavailable-or-corrupt-bundle"] += 1
                continue
            receipt = item["receipt"]
            execution = _execution(item)
            raw = execution.get("factorial")
            if raw is None:
                continue
            contract = _contract(raw)
            if contract is None:
                exclusions["malformed-factorial-contract"] += 1
                continue
            roles = contract["arms"]
            role = next((r for r in ROLES if roles[r] == receipt.get("subject")), None)
            if role is None:
                continue
            key = (
                execution.get("campaign_id"), receipt.get("task_id"),
                receipt.get("harness"), receipt.get("model"),
                receipt.get("replicate_id"), tuple(contract["components"]),
                tuple((r, roles[r]) for r in ROLES),
            )
            groups[key][role].append(item)
            contracts[key] = contract
    quartets: list[dict[str, Any]] = []
    for key, values in sorted(groups.items(), key=lambda item: str(item[0])):
        if any(len(values.get(role, [])) != 1 for role in ROLES):
            exclusions["missing-or-duplicate-factorial-arm"] += 1
            continue
        result = factorial_quartet(
            {role: values[role][0] for role in ROLES}, contracts[key],
        )
        quartets.append(result)
        if not result["treatment_qualified"]:
            exclusions[str(result["qualification_reason"])] += 1
    qualified = [q for q in quartets if q["treatment_qualified"] is True]
    task_clusters: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    by_harness: dict[str, list[float]] = defaultdict(list)
    for q in qualified:
        task_clusters[(str(q["task"]), str(q["harness"]), str(q["model"]))].append(float(q["interaction"]))
        by_harness[str(q["harness"])].append(float(q["interaction"]))
    cross_harness = len(by_harness) > 1
    unpooled = {
        "state": "NOT_COMPARABLE_ACROSS_HARNESSES",
        "reason": "different-harness-execution-and-observability",
        "task_clusters": None, "point_estimate": None, "interval_95": None,
        "probability_gain": None,
    }
    return {
        "schema": SCHEMA,
        "applicable": bool(groups),
        "matched_quartets": len(quartets),
        "qualified_complete_quartets": len(qualified),
        "exclusion_reasons": dict(sorted(exclusions.items())),
        "quartets": quartets,
        "interaction_effect": unpooled if cross_harness else _bootstrap(task_clusters),
        "by_harness": {
            name: {
                "qualified_quartets": len(values),
                "mean_interaction": sum(values) / len(values),
                "task_cluster_effect": _bootstrap({
                    key: rows for key, rows in task_clusters.items()
                    if key[1] == name
                }),
            }
            for name, values in sorted(by_harness.items())
        },
        "catalog_advertisement_proven": False,
        "positive_causal_proof_claimed": False,
        "interpretation": (
            "Observed difference-in-differences between four frozen tool catalogs "
            "(both, A-only, B-only, neither), conditioned on qualified matching "
            "and allowed calls. Subject invocation is reported separately; "
            "no proof of attention or causal efficacy."
        ),
    }
