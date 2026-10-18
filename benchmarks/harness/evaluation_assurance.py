"""Conservative decision diagnostics over immutable Harbor pair projections.

This projection never reruns a model, changes scoring, infers tool delivery from
a return, pools incomparable harnesses, or calls a small sample significant.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from statistics import mean
from typing import Any, Mapping

SCHEMA = "agentscookbook.harbor-evaluation-assurance.v1"
MIN_TASK_CLUSTERS = 8
BOOTSTRAP_DRAWS = 1200


def _outcome(value: object) -> tuple[int, int] | None:
    return {
        "FAIL_TO_FAIL": (0, 0),
        "FAIL_TO_PASS": (0, 1),
        "PASS_TO_FAIL": (1, 0),
        "PASS_TO_PASS": (1, 1),
    }.get(value) if isinstance(value, str) else None


def _pair_identity(pair: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(pair.get("task", "")),
        str(pair.get("harness", "")),
        str(pair.get("model", "")),
    )


def _bootstrap(groups: Mapping[tuple[str, str, str], list[float]]) -> dict[str, Any]:
    """Cluster on task/harness/model, preserving within-task replicate dependence."""
    ordered = sorted(groups.items())
    cluster_values = [mean(values) for _, values in ordered]
    n = len(cluster_values)
    if not n:
        return {
            "state": "UNAVAILABLE",
            "reason": "no-qualified-task-clusters",
            "task_clusters": 0, "interval_95": None,
            "point_estimate": None, "probability_gain": None,
        }
    point = mean(cluster_values)
    if n < MIN_TASK_CLUSTERS:
        return {
            "state": "INSUFFICIENT_EVIDENCE",
            "reason": "too-few-independent-task-clusters",
            "task_clusters": n, "interval_95": None,
            "point_estimate": round(point, 6),
            "probability_gain": None,
        }
    source = json.dumps(ordered, sort_keys=True, separators=(",", ":"))
    seed = int.from_bytes(hashlib.sha256(source.encode("utf-8")).digest()[:8], "big")
    generator = random.Random(seed)
    draws = sorted(
        mean(cluster_values[generator.randrange(n)] for _ in range(n))
        for _ in range(BOOTSTRAP_DRAWS)
    )
    return {
        "state": "DESCRIPTIVE_INTERVAL",
        "reason": None,
        "task_clusters": n,
        "point_estimate": round(point, 6),
        "interval_95": [round(draws[29], 6), round(draws[1169], 6)],
        "probability_gain": None,  # Not a posterior or a hypothesis test.
        "method": "deterministic-task-cluster-bootstrap-1200",
    }


def _numeric_summary(values: list[int]) -> dict[str, Any]:
    return {
        "observations": len(values),
        "mean": round(mean(values), 3) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _observable_use(pair: Mapping[str, Any]) -> str:
    packet = pair.get("delivery_evidence")
    if not isinstance(packet, Mapping) or packet.get("qualified") is not True:
        return "PACKET_UNQUALIFIED"
    if packet.get("return_state") != "RETURNED":
        return "NO_USABLE_PACKET"
    # Nothing in an ATIF linked return proves model attention.
    if packet.get("delivery_state") != "PROVEN":
        return "RETURNED_DELIVERY_UNKNOWN"
    return "DELIVERY_PROVEN_USE_UNKNOWN"


def build_assurance_summary(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    qualified: list[dict[str, Any]] = []
    exclusions: Counter[str] = Counter()
    by_harness: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        outcome = _outcome(pair.get("outcome_transition"))
        if outcome is None:
            exclusions["incomplete-or-invalid-pair-outcome"] += 1
            continue
        if pair.get("tool_order_qualified") is not True:
            exclusions["tool-order-unqualified"] += 1
            continue
        qualified.append(pair)
        by_harness[str(pair.get("harness", "UNKNOWN"))].append(pair)
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    treatment_groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    changes: Counter[str] = Counter()
    lifecycle: Counter[str] = Counter()
    parity: Counter[str] = Counter()
    native_deltas: list[int] = []
    native_search_exclusions: Counter[str] = Counter()
    token_deltas: list[int] = []
    for pair in qualified:
        before, after = _outcome(pair["outcome_transition"]) or (0, 0)
        groups[_pair_identity(pair)].append(float(after - before))
        changes[str(pair["outcome_transition"])] += 1
        if pair.get("treatment") == "OBSERVED_SUCCESSFUL_RESULT":
            treatment_groups[_pair_identity(pair)].append(float(after - before))
        lifecycle[_observable_use(pair)] += 1
        delivery = pair.get("delivery_evidence")
        if isinstance(delivery, Mapping):
            parity[str(delivery.get("presentation_parity", "UNKNOWN"))] += 1
        comparison = pair.get("native_search_comparison")
        if not isinstance(comparison, Mapping):
            native_search_exclusions["comparison-unavailable"] += 1
        elif comparison.get("qualified") is not True:
            native_search_exclusions[str(comparison.get("reason") or "unknown")] += 1
        elif type(comparison.get("qualified_delta")) is int:
            native_deltas.append(comparison["qualified_delta"])
        else:
            native_search_exclusions["qualified-count-unavailable"] += 1
        value = pair.get("token_delta")
        if type(value) is int:
            token_deltas.append(value)
    comparisons: dict[str, dict[str, Any]] = {}
    for harness, rows in sorted(by_harness.items()):
        harness_groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        for pair in rows:
            before, after = _outcome(pair["outcome_transition"]) or (0, 0)
            harness_groups[_pair_identity(pair)].append(float(after - before))
        comparisons[harness] = {
            "qualified_pairs": len(rows),
            "paired_effect": _bootstrap(harness_groups),
        }
    bare_failures = sum(
        _outcome(p["outcome_transition"])[0] == 0 for p in qualified
    )
    cannot_pool = len(by_harness) > 1
    unpooled = {
        "state": "NOT_COMPARABLE_ACROSS_HARNESSES",
        "reason": "different-harness-execution-and-observability",
        "task_clusters": None,
        "point_estimate": None,
        "interval_95": None,
        "probability_gain": None,
    }
    report = {
        "schema": SCHEMA,
        "qualified_pairs": len(qualified),
        "excluded_pairs": len(pairs) - len(qualified),
        "exclusion_reasons": dict(sorted(exclusions.items())),
        "outcome_transitions": dict(sorted(changes.items())),
        "bare_control": {
            "qualified_trials": len(qualified),
            "failures": bare_failures,
            "headroom_state": (
                "UNAVAILABLE" if not qualified
                else "OBSERVED" if bare_failures else "NOT_OBSERVED"
            ),
            "caution": "Observed failure does not establish sufficient task difficulty.",
        },
        "paired_effect": unpooled if cannot_pool else _bootstrap(groups),
        "contracted_treatment_effect": unpooled if cannot_pool else _bootstrap(treatment_groups),
        "per_harness": comparisons,
        "evidence_delivery": {
            "states": dict(sorted(lifecycle.items())),
            "presentation_parity": dict(sorted(parity.items())),
            "attention_proven": False,
            "causal_influence_proven": False,
        },
        "economics": {
            "native_search_delta": _numeric_summary(native_deltas),
            "native_search_comparable_pairs": len(native_deltas),
            "native_search_comparison_exclusions": dict(
                sorted(native_search_exclusions.items())
            ),
            "native_search_delta_scope": "explicit-ATIF-native-search-calls-only",
            "native_search_savings_proven": False,
            "model_token_delta": _numeric_summary(token_deltas),
            "catalog_overhead_isolated": False,
            "monetary_cost_available": False,
        },
        "interactions": {
            "state": "NOT_IDENTIFIABLE",
            "reason": "single-component four-arm contrasts cannot identify joint-component interactions",
        },
        "context_and_freshness": {
            "state": "NOT_ASSESSED",
            "reason": "no matched context/freshness intervention bound to this pair population",
        },
        "presentation_experiment": {
            "state": "NOT_ASSESSED",
            "reason": "parity of two returned views is not a randomized presentation intervention",
        },
        "interpretation": (
            "No causal proof, model attention, cross-harness product winner, "
            "or statistical significance is asserted. Native search economics "
            "excludes opaque and unobserved tool boundaries. Unqualified and "
            "incomplete pairs are excluded from all numerical effects."
        ),
    }
    return report
