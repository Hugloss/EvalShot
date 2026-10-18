"""Verify externally held host-model-input receipts without trusting ATIF prose.

The independent model-invoking host must MAC its *model request input* binding
using a key unavailable to the agent, MCP server, and evaluated workspace.
These checks prove a key-holder's assertion, not provider consumption,
model attention, or causal application. No host emitter is shipped here.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import stat
from pathlib import Path
from typing import Any, Mapping

from .evidence_lifecycle import project_evidence_lifecycle

SCHEMA = "agentscookbook.host-model-input-attestation.v1"
REPORT_SCHEMA = "agentscookbook.host-input-verification.v1"
DOMAIN = b"agentscookbook:host-model-input-attestation:v1\x00"
MAX_RECEIPT_BYTES = 2_097_152
MAX_RECORDS = 10_000
FIELDS = frozenset((
    "host_identity", "host_build_sha256", "model_request_sha256",
    "model_input_sha256", "model_message_sha256", "packet_sha256",
    "call_id_sha256", "model_request_sequence", "boundary",
))
HEX = frozenset("0123456789abcdef")
TRANSPORT_FIELDS = frozenset((
    "boundary", "endpoint_sha256", "http_status",
    "response_sha256", "response_bytes",
))
INTERVENTION_FIELDS = frozenset((
    "study", "arm", "design_sha256", "presentation", "generation_sha256",
    "current_generation_sha256", "semantic_sha256", "catalog_sha256",
    "prompt_sha256", "oracle_sha256", "workspace_sha256", "surface_sha256",
))
TIMING_FIELDS = frozenset((
    "delivery_request_sequence", "prior_request_sha256", "prior_transport_sha256",
))


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def load_host_key(path: Path) -> bytes:
    """Verify key file type and owner-only access.

    The caller remains responsible for keeping it outside evaluated workspaces
    and inaccessible to agents and MCP servers.
    """
    if path.is_symlink():
        raise ValueError("host-key-symlink")
    try:
        st = path.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
            raise ValueError("host-key-permissions-or-type")
        key = path.read_bytes()
    except OSError as exc:
        raise ValueError("host-key-unavailable") from exc
    if not 32 <= len(key) <= 4096:
        raise ValueError("host-key-length")
    return key


def _load_receipt(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise ValueError("attestation-file-unavailable-or-symlink")
    if path.stat().st_size > MAX_RECEIPT_BYTES:
        raise ValueError("attestation-file-oversized")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("attestation-file-invalid") from exc


def _bound_mac(key: bytes, context: Mapping[str, Any], delivery: Mapping[str, Any]) -> str:
    return hmac.new(key, DOMAIN + canonical({
        "context": context, "delivery": delivery,
    }), hashlib.sha256).hexdigest()


def verify_host_attestations(
    trajectory: Path,
    attestation: Path,
    key: bytes,
    *,
    campaign_id: str,
    trial_id: str,
) -> dict[str, Any]:
    """Never accept an attestation embedded in ATIF/tool output or agent text.

    The expected campaign and trial IDs must come from independently chosen
    run authority. A complete set covers every usable Hashmarks result.
    """
    lifecycle = project_evidence_lifecycle(trajectory)
    output: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "subject_invocation": lifecycle["invocation_state"],
        "subject_return": lifecycle["return_state"],
        "returned_packets": lifecycle["returned_packets"],
        "verified_model_input_packets": 0,
        "verified_provider_submission_packets": 0,
        "provider_submission_state": "UNKNOWN",
        "delivery_state": "UNKNOWN",
        "attestation_state": "UNAVAILABLE",
        "reason": None,
        "model_attention_proven": False,
        "provider_consumption_proven": False,
        "causal_influence_proven": False,
        "trusted_host_boundary_required": True,
    }
    if not lifecycle["qualified"] or lifecycle["return_state"] != "RETURNED":
        output["reason"] = "no-qualified-subject-packets"
        return output
    if not isinstance(key, bytes) or not 32 <= len(key) <= 4096:
        output.update(attestation_state="REJECTED", reason="invalid-independent-host-key")
        return output
    if not all(isinstance(x, str) and 0 < len(x) <= 256 for x in (campaign_id, trial_id)):
        output.update(attestation_state="REJECTED", reason="invalid-expected-run-identity")
        return output
    try:
        contents = trajectory.read_bytes()
        if len(contents) > 64 * 1024 * 1024:
            raise ValueError("trajectory-oversized")
        envelope = _load_receipt(attestation)
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema", "campaign_id", "trial_id", "trajectory_sha256", "deliveries",
        }:
            raise ValueError("invalid-attestation-envelope")
        if (envelope["schema"] != SCHEMA or envelope["campaign_id"] != campaign_id
                or envelope["trial_id"] != trial_id
                or envelope["trajectory_sha256"] != hashlib.sha256(contents).hexdigest()):
            raise ValueError("stale-or-foreign-attestation")
        deliveries = envelope["deliveries"]
        if not isinstance(deliveries, list) or not 0 < len(deliveries) <= MAX_RECORDS:
            raise ValueError("invalid-attestation-records")
        context = {field: envelope[field] for field in (
            "schema", "campaign_id", "trial_id", "trajectory_sha256",
        )}
        packets = {
            (row["call_id_sha256"], row["packet_sha256"])
            for row in lifecycle["packet_refs"]
        }
        matched: set[tuple[str, str]] = set()
        submitted: set[tuple[str, str]] = set()
        requests: set[tuple[str, str, str]] = set()
        for row in deliveries:
            if not isinstance(row, dict) or not (
                FIELDS | {"mac_sha256"} <= set(row)
                and set(row) - (FIELDS | {"mac_sha256"}) <= {"intervention", "transport"}
            ):
                raise ValueError("invalid-delivery-fields")
            delivery = {field: row[field] for field in FIELDS}
            if "intervention" in row:
                intervention = row["intervention"]
                if not isinstance(intervention, dict):
                    raise ValueError("invalid-intervention-binding")
                timing = intervention.get("study") == "timing"
                expected_facets = INTERVENTION_FIELDS | (TIMING_FIELDS if timing else frozenset())
                if (set(intervention) != expected_facets
                        or intervention["study"] not in ("presentation", "freshness", "timing", "text_presentation")
                        or intervention["arm"] not in ("control", "variant")
                        or intervention["presentation"] not in (
                            "structured", "text", "compact-json-text",
                            "indented-json-text",
                        )
                        or intervention["presentation"] != {
                            "presentation": {
                                "control": "structured", "variant": "text",
                            },
                            "text_presentation": {
                                "control": "compact-json-text",
                                "variant": "indented-json-text",
                            },
                            "freshness": {
                                "control": "structured", "variant": "structured",
                            },
                            "timing": {
                                "control": "structured", "variant": "structured",
                            },
                        }[intervention["study"]][intervention["arm"]]
                        or not all(_hex64(intervention[name]) for name in
                                   expected_facets - {
                                       "study", "arm", "presentation",
                                       "delivery_request_sequence",
                                   })
                        or (timing and (
                            type(intervention["delivery_request_sequence"]) is not int
                            or intervention["delivery_request_sequence"] != (
                                1 if intervention["arm"] == "control" else 2
                            )
                            or (intervention["arm"] == "control" and (
                                intervention["prior_request_sha256"] != "0" * 64
                                or intervention["prior_transport_sha256"] != "0" * 64
                            ))
                            or (intervention["arm"] == "variant" and (
                                intervention["prior_request_sha256"] == "0" * 64
                                or intervention["prior_transport_sha256"] == "0" * 64
                            ))
                        ))):
                    raise ValueError("invalid-intervention-binding")
                if timing and delivery["model_request_sequence"] != intervention["delivery_request_sequence"]:
                    raise ValueError("timing-attested-request-sequence-mismatch")
                delivery["intervention"] = intervention
            if "transport" in row:
                transport = row["transport"]
                if (not isinstance(transport, dict)
                        or set(transport) != TRANSPORT_FIELDS
                        or transport["boundary"] != "https-response"
                        or not _hex64(transport["endpoint_sha256"])
                        or not _hex64(transport["response_sha256"])
                        or type(transport["http_status"]) is not int
                        or not 200 <= transport["http_status"] < 300
                        or type(transport["response_bytes"]) is not int
                        or not 0 < transport["response_bytes"] <= 2_097_152):
                    raise ValueError("invalid-provider-transport-record")
                delivery["transport"] = transport
            if not all(_hex64(delivery[name]) for name in (
                "host_build_sha256", "model_request_sha256", "model_input_sha256",
                "model_message_sha256", "packet_sha256", "call_id_sha256",
            )):
                raise ValueError("invalid-delivery-digest")
            if (not isinstance(delivery["host_identity"], str)
                    or not 0 < len(delivery["host_identity"]) <= 128
                    or delivery["boundary"] != "host-model-request-input"
                    or type(delivery["model_request_sequence"]) is not int
                    or delivery["model_request_sequence"] < 1):
                raise ValueError("invalid-delivery-boundary")
            binding = (delivery["call_id_sha256"], delivery["packet_sha256"])
            request = (delivery["host_identity"], delivery["model_request_sha256"],
                       delivery["call_id_sha256"])
            if binding not in packets:
                raise ValueError("packet-not-in-immutable-trace")
            if request in requests:
                raise ValueError("duplicate-model-input-binding")
            requests.add(request)
            if not _hex64(row["mac_sha256"]) or not hmac.compare_digest(
                row["mac_sha256"], _bound_mac(key, context, delivery)
            ):
                raise ValueError("host-authentication-failed")
            matched.add(binding)
            if "transport" in delivery:
                submitted.add(binding)
        state = "PROVEN" if matched == packets else "PARTIAL"
        output.update(
            delivery_state=state,
            attestation_state="VERIFIED",
            verified_model_input_packets=len(matched),
            verified_provider_submission_packets=len(submitted),
            provider_submission_state=(
                "SUBMITTED" if submitted == packets else
                "PARTIAL" if submitted else "UNKNOWN"
            ),
            reason=None if state == "PROVEN" else "some-returned-packets-not-attested",
        )
        return output
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        output.update(
            attestation_state="REJECTED",
            reason=str(exc) if isinstance(exc, ValueError) else "invalid-attestation",
        )
        return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--host-key-file", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--require-delivery", action="store_true")
    args = parser.parse_args()
    try:
        key = load_host_key(args.host_key_file)
        result = verify_host_attestations(
            args.trajectory, args.attestation, key,
            campaign_id=args.campaign_id, trial_id=args.trial_id,
        )
    except ValueError as exc:
        result = {"schema": REPORT_SCHEMA, "attestation_state": "REJECTED",
                  "delivery_state": "UNKNOWN", "reason": str(exc),
                  "model_attention_proven": False, "causal_influence_proven": False}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if args.require_delivery and result["delivery_state"] != "PROVEN" else 0


if __name__ == "__main__":
    raise SystemExit(main())
