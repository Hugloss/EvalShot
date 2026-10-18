"""E265-E268: OpenAI Chat Completions tool-result wire parity (model-free).

Do not equate the JSON *value* of a Hashmarks result with the bytes and
type actually carried inside a native provider tool message. OpenAI Chat
Completions tool-message content is text/typed text parts, not an arbitrary
nested JSON object. A presentation intervention using JSON objects as
tool content is therefore UNSUPPORTED by this transport, even if coercing
it into a JSON string would preserve semantics. That coercion would erase
the arm distinction and invalidate an apparent paired result.

This is an admission/diagnostic owner, never a synthetic model request or
an upgrade to provider attention, process isolation or causal improvement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import canonical
from .trusted_treatments import load_manifest, select_treatment, verify_manifest

SCHEMA = "agentscookbook.native-chat-tool-transport-parity.v1"
SUPPORTED_MODE = "openai-chat-tool-plain-text-v1"
MAX_CONTENT_BYTES = 1_048_576


def inspect_selected_content(selected: Mapping[str, Any]) -> dict[str, Any]:
    """Decide exact transport representation *before* provider or agent work.

    Never reinterpret JSON text as a structured native model-input object.
    If control/variant cannot retain the frozen declared surface shape, no
    paired presentation study is admissible on this transport.
    """
    if not isinstance(selected, Mapping) or selected.get("study") not in (
        "presentation", "freshness", "text_presentation",
    ) or selected.get("presentation") not in (
        "structured", "text", "compact-json-text", "indented-json-text",
    ):
        raise ValueError("invalid-frozen-intervention-for-chat-transport")
    if (selected["study"] == "freshness"
            and selected["presentation"] != "structured"):
        raise ValueError("invalid-freshness-presentation-authority")
    if (selected["study"] == "text_presentation"
            and selected["presentation"] != (
                "compact-json-text" if selected.get("arm") == "control"
                else "indented-json-text"
            )):
        raise ValueError("invalid-native-text-presentation-assignment")
    surface = selected.get("selected_content")
    if isinstance(surface, str):
        encoded = surface.encode("utf-8")
        if not 0 < len(encoded) <= MAX_CONTENT_BYTES:
            raise ValueError("invalid-native-chat-tool-text-size")
        if selected["presentation"] not in (
            "text", "compact-json-text", "indented-json-text",
        ):
            raise ValueError("declared-structured-presentation-has-text-surface")
        mode = SUPPORTED_MODE
        eligible = True
        reason = None
    else:
        # Nested JSON values are legitimate *sources*, but NOT legitimate
        # OpenAI Chat Completions role=tool content on the wire. A JSON
        # string is not an equivalent structured-model-input surface.
        canonical(surface)  # Refuse non-JSON source values.
        mode = "unsupported-nested-json-tool-content"
        eligible = False
        reason = "native-chat-role-tool-requires-text-not-json-object"
    return {
        "schema": SCHEMA,
        "trial_id": selected["trial_id"],
        "arm": selected["arm"],
        "study": selected["study"],
        "presentation": selected["presentation"],
        "selected_surface_sha256": selected["surface_sha256"],
        "model_input_representation": mode,
        "chat_tool_content_wire_compatible": eligible,
        "reason": reason,
        "semantic_equivalence_is_surface_parity": False,
        "provider_consumption_proven": False,
        "causal_influence_proven": False,
    }


def require_chat_tool_content(selected: Mapping[str, Any],
                              *, actual_tool_content: object) -> None:
    """Strict observed request boundary; no json.loads(content) coercion."""
    report = inspect_selected_content(selected)
    if not report["chat_tool_content_wire_compatible"]:
        raise ValueError("native-chat-tool-content-shape-not-supported")
    expected = selected["selected_content"]
    if type(actual_tool_content) is not str or actual_tool_content != expected:
        raise ValueError("native-chat-tool-content-not-byte-identical-to-treatment")
    if hashlib.sha256(canonical(actual_tool_content)).hexdigest() != (
        selected["surface_sha256"]
    ):
        raise ValueError("native-chat-tool-surface-digest-drift")


def audit_frozen_chat_treatments(
    manifest: Mapping[str, Any], *,
    sources_root: Path,
) -> dict[str, Any]:
    """Read-only preflight; no model execution or attestation is fabricated."""
    frozen = verify_manifest(dict(manifest))
    if sources_root.is_symlink() or not sources_root.is_dir():
        raise ValueError("untrusted-native-chat-source-root")
    planned = {cell["trial_id"] for cell in frozen["assignments"]}
    existing: set[str] = set()
    for path in sources_root.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ValueError("unadmitted-native-chat-source-entry")
        if path.stem not in planned or path.stem in existing:
            raise ValueError("foreign-or-duplicated-native-chat-source")
        existing.add(path.stem)
    if existing != planned:
        raise ValueError("incomplete-native-chat-frozen-sources")
    rows: list[dict[str, Any]] = []
    for cell in frozen["assignments"]:
        path = sources_root / (cell["trial_id"] + ".json")
        if path.stat().st_size > 524_288:
            raise ValueError("native-chat-source-oversized")
        try:
            payload = json.loads(path.read_bytes())
        except (ValueError, UnicodeError) as exc:
            raise ValueError("native-chat-source-not-json") from exc
        if not isinstance(payload, dict) or set(payload) != {"current", "replaced"}:
            raise ValueError("invalid-native-chat-source-envelope")
        selected = select_treatment(
            frozen, trial_id=cell["trial_id"], harness=cell["harness"],
            task=cell["task"], replicate=cell["replicate"],
            arm=cell["arm"], current=payload["current"],
            replaced=payload["replaced"],
        )
        rows.append(inspect_selected_content(selected))
    groups: dict[tuple[str, str, int], dict[str, bool]] = {}
    for cell, row in zip(frozen["assignments"], rows, strict=True):
        key = (cell["harness"], cell["task"], cell["replicate"])
        groups.setdefault(key, {})[cell["arm"]] = (
            row["chat_tool_content_wire_compatible"]
        )
    paired = sum(
        set(arms) == {"control", "variant"} and all(arms.values())
        for arms in groups.values()
    )
    kinds = dict(sorted(Counter(row["model_input_representation"]
                                for row in rows).items()))
    return {
        "schema": SCHEMA,
        "manifest_sha256": frozen["manifest_sha256"],
        "trials": len(rows),
        "pairs": len(groups),
        "transport_admissible_pairs": paired,
        "fully_compatible_for_paired_study": (
            len(groups) > 0 and paired == len(groups)
        ),
        "representation_counts": kinds,
        "rows": rows,
        "model_backed_runs_performed": 0,
        "model_attention_proven": False,
        "causal_hashmarks_uplift_proven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sources-root", type=Path, required=True)
    parser.add_argument("--require-paired-compatible", action="store_true")
    args = parser.parse_args()
    try:
        output = audit_frozen_chat_treatments(
            load_manifest(args.manifest), sources_root=args.sources_root,
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        print(json.dumps({
            "schema": SCHEMA, "fully_compatible_for_paired_study": False,
            "reason": str(exc), "causal_hashmarks_uplift_proven": False,
        }, sort_keys=True, indent=2))
        return 2
    print(json.dumps(output, sort_keys=True, indent=2))
    if args.require_paired_compatible and not output["fully_compatible_for_paired_study"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
