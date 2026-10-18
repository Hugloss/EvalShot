"""E271/E272: independently check frozen text-only presentation provider parity.

Require the *actual host-attested* Chat Completions tool message digest to
equal the one reconstructed from a real native session call ID and the
new frozen textual source treatment. The original native export, signed
delivery and provider transport are validated by the separate E256 audit.

No raw provider request is invented here. Equality of a host MAC and
canonical message digest is evidence of the host-observed field, NOT
independent origin of the native request, SDK version effectiveness, model
attention, human grading or causal improvement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import _load_receipt, canonical, load_host_key
from .native_chat_transport_parity import (
    audit_frozen_chat_treatments, require_chat_tool_content,
)
from .native_netns_pilot_readiness import evaluate_native_netns_pilot
from .opencode_native_session import SUBJECT_TOOLS
from .trusted_treatments import load_manifest, select_treatment, verify_manifest

SCHEMA = "agentscookbook.native-text-presentation-paired-readback.v1"
MAX_EXPORT_BYTES = 16_777_216


def _read_json(path: Path, *, max_bytes: int) -> object:
    if (path.is_symlink() or not path.is_file()
            or not 0 < path.stat().st_size <= max_bytes):
        raise ValueError("unsafe-text-presentation-evidence-file")
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("unreadable-text-presentation-evidence-file") from exc


def _native_subject_call(native: object) -> str:
    if not isinstance(native, dict) or not isinstance(native.get("messages"), list):
        raise ValueError("invalid-native-text-presentation-session")
    calls = []
    for message in native["messages"]:
        if not isinstance(message, dict):
            raise ValueError("malformed-native-text-presentation-message")
        if not isinstance(message.get("info"), dict):
            raise ValueError("missing-native-text-presentation-message-identity")
        if message["info"].get("role") != "assistant":
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            raise ValueError("missing-native-text-presentation-parts")
        for part in parts:
            if not isinstance(part, dict):
                raise ValueError("malformed-native-text-presentation-part")
            if part.get("type") == "tool" and part.get("tool") in SUBJECT_TOOLS:
                state = part.get("state")
                if not isinstance(state, dict) or state.get("status") != "completed":
                    raise ValueError("unfinished-native-text-presentation-tool")
                calls.append(part.get("callID"))
    if len(calls) != 1 or not isinstance(calls[0], str) or not calls[0]:
        raise ValueError("ambiguous-native-text-presentation-subject-call")
    return calls[0]


def assess_native_text_presentation(
    manifest: Mapping[str, Any], *, results_root: Path,
    sources_root: Path, workspace: Path, host_key: bytes,
    host_identity: str, approved_endpoint_sha256: str,
    expected_bwrap_sha256: str, expected_hashmarks_sha256: str,
) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    if frozen["design"]["study"] != "text_presentation":
        raise ValueError("not-a-new-text-presentation-study")
    preflight = audit_frozen_chat_treatments(
        frozen, sources_root=sources_root,
    )
    if not preflight["fully_compatible_for_paired_study"]:
        raise ValueError("frozen-text-presentation-wire-incompatible")
    netns = evaluate_native_netns_pilot(
        frozen, results_root=results_root, sources_root=sources_root,
        workspace=workspace, host_key=host_key,
        host_identity=host_identity,
        approved_endpoint_sha256=approved_endpoint_sha256,
        expected_bwrap_sha256=expected_bwrap_sha256,
        expected_hashmarks_sha256=expected_hashmarks_sha256,
    )
    issues: Counter[str] = Counter()
    if not netns["paired_pilot_observations_ready_for_independent_grading"]:
        issues["prior-native-isolation-and-session-not-qualified"] += 1
    observed = 0
    for cell in frozen["assignments"]:
        trial = cell["trial_id"]
        try:
            payload = _read_json(
                sources_root / (trial + ".json"), max_bytes=524_288,
            )
            if not isinstance(payload, dict) or set(payload) != {"current", "replaced"}:
                raise ValueError("invalid-independent-text-source")
            selection = select_treatment(
                frozen, trial_id=trial, harness=cell["harness"],
                task=cell["task"], replicate=cell["replicate"],
                arm=cell["arm"],
                current=payload["current"], replaced=payload["replaced"],
            )
            require_chat_tool_content(
                selection, actual_tool_content=selection["selected_content"],
            )
            native = _read_json(
                results_root / trial / "native-session.json",
                max_bytes=MAX_EXPORT_BYTES,
            )
            call_id = _native_subject_call(native)
            receipt = _load_receipt(results_root / trial / "host-attestation.json")
            if (not isinstance(receipt, dict)
                    or not isinstance(receipt.get("deliveries"), list)
                    or len(receipt["deliveries"]) != 1
                    or not isinstance(receipt["deliveries"][0], dict)):
                raise ValueError("unqualified-text-presentation-host-receipt")
            delivery = receipt["deliveries"][0]
            expected = {
                "role": "tool", "tool_call_id": call_id,
                "content": selection["selected_content"],
            }
            message_sha256 = hashlib.sha256(canonical(expected)).hexdigest()
            call_sha256 = hashlib.sha256(call_id.encode("utf-8")).hexdigest()
            if (delivery.get("call_id_sha256") != call_sha256
                    or delivery.get("model_message_sha256") != message_sha256
                    or not isinstance(delivery.get("intervention"), dict)
                    or delivery["intervention"].get("presentation")
                    != selection["presentation"]
                    or delivery["intervention"].get("surface_sha256")
                    != selection["surface_sha256"]):
                raise ValueError("host-observed-native-text-message-parity-failed")
            observed += 1
        except (ValueError, OSError, TypeError, KeyError, RecursionError):
            issues["native-text-wire-parity-unverified"] += 1
    if observed != len(frozen["assignments"]):
        issues["incomplete-frozen-text-presentation-population"] += 1
    return {
        "schema": SCHEMA,
        "campaign_id": frozen["design"]["campaign_id"],
        "manifest_sha256": frozen["manifest_sha256"],
        "expected_trials": len(frozen["assignments"]),
        "host_attested_exact_text_messages": observed,
        "prior_native_session_netns": netns,
        "text_presentation_pairs_host_transport_verified": not issues,
        "blockers": dict(sorted(issues.items())),
        "native_process_origin_independently_attested": False,
        "native_sdk_behavior_independently_attested": False,
        "model_attention_proven": False,
        "independent_grading_completed": False,
        "randomized_causal_uplift_proven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--sources-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--host-key-file", type=Path, required=True)
    parser.add_argument("--host-identity", required=True)
    parser.add_argument("--approved-endpoint-sha256", required=True)
    parser.add_argument("--bwrap-sha256", required=True)
    parser.add_argument("--hashmarks-sha256", required=True)
    parser.add_argument("--require-host-transport", action="store_true")
    args = parser.parse_args()
    try:
        report = assess_native_text_presentation(
            load_manifest(args.manifest),
            results_root=args.results_root, sources_root=args.sources_root,
            workspace=args.workspace, host_key=load_host_key(args.host_key_file),
            host_identity=args.host_identity,
            approved_endpoint_sha256=args.approved_endpoint_sha256,
            expected_bwrap_sha256=args.bwrap_sha256,
            expected_hashmarks_sha256=args.hashmarks_sha256,
        )
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({
            "schema": SCHEMA,
            "text_presentation_pairs_host_transport_verified": False,
            "reason": str(exc), "randomized_causal_uplift_proven": False,
        }, sort_keys=True, indent=2))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 2 if (args.require_host_transport and
                 not report["text_presentation_pairs_host_transport_verified"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
