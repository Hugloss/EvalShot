"""Post-treatment semantic exposure diagnostics for frozen Harbor quartets.

These are conditional *descriptions* of what a tool returned and the matched
control outcomes. Stratifying on an agent-selected tool observation is NOT a
randomized intervention on the information and cannot establish causation.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

SCHEMA = "agentscookbook.harbor-semantic-outcome-diagnostics.v1"
ARRIVALS = frozenset({
    "BEFORE_NATIVE_DISCOVERY",
    "AFTER_NATIVE_DISCOVERY",
    "NO_NATIVE_DISCOVERY",
    "UNKNOWN_SAME_STEP",
    "NOT_OBSERVED",
})
ALIGNMENTS = frozenset({
    "ALIGNED_ONLY", "DIVERGENT_ONLY", "MIXED_OR_CONFLICTING",
    "NO_COMPARABLE_CLAIMS",
})
CONTRAST_ROLES = (("full", "remove", "full_vs_remove"), ("only", "bare", "only_vs_bare"))


def _field_names(value: object) -> set[str] | None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ) or len(value) != len(set(value)):
        return None
    return set(value)


def exposure_profile(semantic: object) -> dict[str, Any]:
    """Return only categories and counts; never oracle values or message text."""
    if not isinstance(semantic, Mapping) or semantic.get("qualified") is not True:
        return {"qualified": False, "reason": (
            str(semantic.get("reason") or "unqualified")
            if isinstance(semantic, Mapping) else "missing-semantic-projection"
        )}
    alignment = semantic.get("claim_alignment")
    aligned_time = semantic.get("aligned_claim_arrival")
    divergent_time = semantic.get("divergent_claim_arrival")
    order = semantic.get("claim_arrival_order")
    field_groups = (
        "aligned_fields", "divergent_fields", "conflicted_fields",
        "aligned_repeated_in_final", "divergent_repeated_in_final",
    )
    fields = {key: _field_names(semantic.get(key)) for key in field_groups}
    if (
        alignment not in ALIGNMENTS
        or aligned_time not in ARRIVALS
        or divergent_time not in ARRIVALS
        or order not in {
            "NO_UNCONFLICTED_CLAIMS", "DIVERGENT_ONLY", "ALIGNED_ONLY",
            "UNKNOWN_SAME_STEP", "ALIGNED_FIRST", "DIVERGENT_FIRST",
        }
        or any(value is None for value in fields.values())
    ):
        return {"qualified": False, "reason": "semantic-profile-fields-incomplete"}
    a, d, c, ae, de = (fields[key] for key in field_groups)
    assert all(x is not None for x in (a, d, c, ae, de))
    if (
        a & d or a & c or d & c or
        not ae <= a or not de <= d or
        bool(a) != (aligned_time != "NOT_OBSERVED") or
        bool(d) != (divergent_time != "NOT_OBSERVED") or
        (alignment == "ALIGNED_ONLY" and (not a or d or c)) or
        (alignment == "DIVERGENT_ONLY" and (not d or a or c)) or
        (alignment == "NO_COMPARABLE_CLAIMS" and (a or d or c)) or
        (alignment == "MIXED_OR_CONFLICTING" and not (c or (a and d)))
    ):
        return {"qualified": False, "reason": "semantic-profile-inconsistent"}
    if not a and not d and order != "NO_UNCONFLICTED_CLAIMS":
        return {"qualified": False, "reason": "semantic-claim-order-inconsistent"}
    if a and not d and order != "ALIGNED_ONLY":
        return {"qualified": False, "reason": "semantic-claim-order-inconsistent"}
    if d and not a and order != "DIVERGENT_ONLY":
        return {"qualified": False, "reason": "semantic-claim-order-inconsistent"}
    if a and d and order not in {
        "UNKNOWN_SAME_STEP", "ALIGNED_FIRST", "DIVERGENT_FIRST"
    }:
        return {"qualified": False, "reason": "semantic-claim-order-inconsistent"}
    # An echoed atom is co-occurrence in the verifier-observed answer. It
    # does not establish that the answer was generated *because* of the tool.
    echo = (
        "BOTH_REPEATED" if ae and de
        else "DIVERGENT_REPEATED" if de
        else "ALIGNED_REPEATED" if ae
        else "NEITHER_REPEATED"
    )
    signal = (
        "CONFLICTED_FIELDS" if c
        else "DIVERGENT_CLAIM_REPEATED" if de
        else "ALIGNED_CLAIM_REPEATED" if ae
        else "ALIGNED_AVAILABLE_NOT_REPEATED" if a
        else "DIVERGENT_AVAILABLE_NOT_REPEATED" if d
        else "NO_COMPARABLE_CLAIMS"
    )
    return {
        "qualified": True,
        "reason": None,
        "claim_alignment": alignment,
        "aligned_claim_arrival": aligned_time,
        "divergent_claim_arrival": divergent_time,
        "claim_arrival_order": order,
        "final_answer_echo": echo,
        "descriptive_signal": signal,
        "aligned_field_count": len(a),
        "divergent_field_count": len(d),
        "conflicted_field_count": len(c),
        "aligned_echo_field_count": len(ae),
        "divergent_echo_field_count": len(de),
        "agent_attention_proven": False,
        "causal_influence_claimed": False,
    }


def quartet_information_outcomes(quartet: Mapping[str, Any]) -> dict[str, Any]:
    semantic = quartet.get("semantic_component_evidence")
    reason = None
    if not isinstance(semantic, Mapping) or semantic.get("qualified") is not True:
        reason = (
            str(semantic.get("reason") or "semantic-component-unqualified")
            if isinstance(semantic, Mapping) else "semantic-component-missing"
        )
    statuses = quartet.get("statuses")
    if reason is None and (
        not isinstance(statuses, Mapping)
        or any(statuses.get(role) not in {"PASS", "FAIL"} for role in (
            "bare", "full", "remove", "only",
        ))
    ):
        reason = "incomplete-matched-outcomes"
    arms: dict[str, dict[str, Any]] = {}
    if reason is None:
        for role, _, _ in CONTRAST_ROLES:
            profile = exposure_profile(semantic.get(role))
            arms[role] = profile
            if profile.get("qualified") is not True and reason is None:
                reason = role + ":" + str(profile.get("reason") or "invalid-profile")
    if reason is not None:
        return {
            "schema": SCHEMA,
            "qualified": False,
            "reason": reason,
            "comparisons": [],
            "causal_influence_claimed": False,
        }
    comparisons = []
    for role, control, label in CONTRAST_ROLES:
        comparisons.append({
            "contrast": label,
            "control_status": statuses[control],
            "observed_status": statuses[role],
            "outcome_transition": statuses[control] + "_TO_" + statuses[role],
            "exposure": arms[role],
        })
    return {
        "schema": SCHEMA,
        "qualified": True,
        "reason": None,
        "comparisons": comparisons,
        "causal_influence_claimed": False,
    }


def summarize_information_outcomes(
    quartets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize complete quartets without pseudoreplication as independent proof.

    Counts are per matched replicate; distinct task/harness/model clusters are
    reported for transparency. No conditional uplift/rate or p-value is inferred.
    """
    qualified = []
    exclusions: dict[str, int] = defaultdict(int)
    for row in quartets:
        diag = row.get("semantic_information_outcomes")
        if not isinstance(diag, Mapping) or diag.get("qualified") is not True:
            reason = (
                str(diag.get("reason") or "unqualified")
                if isinstance(diag, Mapping) else "missing-information-outcomes"
            )
            exclusions[reason] += 1
            continue
        qualified.append((row, diag))
    bucket: dict[tuple[str, ...], dict[str, Any]] = {}
    for row, diag in qualified:
        for comparison in diag["comparisons"]:
            exposed = comparison["exposure"]
            key = (
                str(comparison["contrast"]),
                str(exposed["claim_alignment"]),
                str(exposed["aligned_claim_arrival"]),
                str(exposed["divergent_claim_arrival"]),
                str(exposed["claim_arrival_order"]),
                str(exposed["final_answer_echo"]),
                str(exposed["descriptive_signal"]),
                str(comparison["outcome_transition"]),
            )
            if key not in bucket:
                bucket[key] = {"replicates": 0, "clusters": set()}
            bucket[key]["replicates"] += 1
            bucket[key]["clusters"].add((
                str(row.get("task")),
                str(row.get("harness")),
                str(row.get("model")),
            ))
    keys = (
        "contrast", "claim_alignment", "aligned_claim_arrival",
        "divergent_claim_arrival", "claim_arrival_order",
        "final_answer_echo", "descriptive_signal", "outcome_transition",
    )
    cells = [
        {
            **dict(zip(keys, key)),
            "replicate_count": data["replicates"],
            "independent_task_harness_model_clusters": len(data["clusters"]),
        }
        for key, data in sorted(bucket.items())
    ]
    return {
        "schema": SCHEMA,
        "qualified_quartets": len(qualified),
        "excluded_quartets": len(quartets) - len(qualified),
        "exclusion_reasons": dict(sorted(exclusions.items())),
        "conditional_outcome_cells": cells,
        "causal_influence_claimed": False,
        "conditioning_warning": (
            "information is observed after treatment and agent selection; "
            "conditional outcome cells are descriptive, not randomized "
            "information-timing effects"
        ),
    }
