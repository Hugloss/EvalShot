"""E269: freeze paired native Chat Completions transport before model work.

A successful single text-arm packet cannot make a two-arm study admissible
when its structured control cannot be represented on the same wire. This
read-only admission exposes an explicit diagnostic-only escape hatch, never
relabels an incompatible pair or rewrites frozen source presentations.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .native_chat_transport_parity import inspect_selected_content
from .trusted_treatments import select_treatment, verify_manifest

SCHEMA = "agentscookbook.native-chat-paired-admission.v1"


def admit_native_chat_pair(
    manifest: Mapping[str, Any], *, trial_id: str,
    current: Mapping[str, Any], replaced: Mapping[str, Any] | None,
    allow_unpaired_diagnostic: bool = False,
) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    matches = [c for c in frozen["assignments"] if c["trial_id"] == trial_id]
    if len(matches) != 1 or type(allow_unpaired_diagnostic) is not bool:
        raise ValueError("invalid-frozen-native-pair-admission")
    cell = matches[0]
    if cell["harness"] not in ("opencode", "opencode-native"):
        raise ValueError("non-native-pair-admission")
    paired = [c for c in frozen["assignments"] if c["pair_id"] == cell["pair_id"]]
    if (len(paired) != 2 or {c["arm"] for c in paired} != {"control", "variant"}
            or any((c["task"], c["harness"], c["replicate"]) !=
                   (cell["task"], cell["harness"], cell["replicate"]) for c in paired)):
        raise ValueError("incomplete-or-corrupted-frozen-native-pair")
    observed: dict[str, dict[str, Any]] = {}
    for entry in paired:
        chosen = select_treatment(
            frozen, trial_id=entry["trial_id"],
            harness=entry["harness"], task=entry["task"],
            replicate=entry["replicate"], arm=entry["arm"],
            current=current, replaced=replaced,
        )
        observed[entry["arm"]] = inspect_selected_content(chosen)
    selected = observed[cell["arm"]]
    if not selected["chat_tool_content_wire_compatible"]:
        raise ValueError("selected-native-chat-arm-not-transport-compatible")
    pair_compatible = all(
        observed[arm]["chat_tool_content_wire_compatible"]
        for arm in ("control", "variant")
    )
    if not pair_compatible and not allow_unpaired_diagnostic:
        raise ValueError("native-chat-paired-transport-not-admitted-before-model-work")
    return {
        "schema": SCHEMA,
        "trial_id": cell["trial_id"],
        "pair_id": cell["pair_id"],
        "manifest_sha256": frozen["manifest_sha256"],
        "arm": cell["arm"],
        "representations": {
            arm: observed[arm]["model_input_representation"]
            for arm in ("control", "variant")
        },
        "selected_wire_compatible": True,
        "paired_transport_compatible": pair_compatible,
        "diagnostic_only": not pair_compatible,
        "native_model_runs_performed_by_admission": 0,
        "model_attention_proven": False,
        "causal_uplift_proven": False,
    }


def verify_native_chat_pair_claim(
    receipt: object, *, trial_id: str, manifest_sha256: str,
) -> bool:
    """Check shape/type of host signed report, never trust an upgrade flag."""
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema", "trial_id", "pair_id", "manifest_sha256", "arm",
        "representations", "selected_wire_compatible",
        "paired_transport_compatible", "diagnostic_only",
        "native_model_runs_performed_by_admission",
        "model_attention_proven", "causal_uplift_proven",
    }:
        return False
    reps = receipt["representations"]
    if not isinstance(reps, dict) or set(reps) != {"control", "variant"}:
        return False
    allowed = ("openai-chat-tool-plain-text-v1", "unsupported-nested-json-tool-content")
    if not all(type(reps[a]) is str and reps[a] in allowed for a in reps):
        return False
    compatible = all(reps[arm] == "openai-chat-tool-plain-text-v1" for arm in reps)
    return (
        receipt["schema"] == SCHEMA
        and receipt["trial_id"] == trial_id
        and receipt["manifest_sha256"] == manifest_sha256
        and type(receipt["pair_id"]) is str and len(receipt["pair_id"]) == 32
        and receipt["arm"] in ("control", "variant")
        and reps[receipt["arm"]] == "openai-chat-tool-plain-text-v1"
        and receipt["selected_wire_compatible"] is True
        and receipt["paired_transport_compatible"] is compatible
        and receipt["diagnostic_only"] is (not compatible)
        and receipt["native_model_runs_performed_by_admission"] == 0
        and type(receipt["native_model_runs_performed_by_admission"]) is int
        and receipt["model_attention_proven"] is False
        and receipt["causal_uplift_proven"] is False
    )
