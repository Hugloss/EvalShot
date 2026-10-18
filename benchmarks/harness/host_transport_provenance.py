"""E246–E247: strict read-back of cross-trial, host-MAC provider provenance.

Read-only; no model, provider request, key or review work is generated.
All evidence is bound to the same frozen campaign/source/host assignment.
Transport attestation remains a key-holder assertion, not network telemetry
from a third-party agent or proof of cognition.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import (
    _load_receipt, canonical, load_host_key, verify_host_attestations,
)
from .trusted_treatments import load_manifest, verify_manifest

SCHEMA = "agentscookbook.host-transport-provenance.v1"
HEX = set("0123456789abcdef")


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def audit_transport_provenance(
    manifest: Mapping[str, Any],
    bundles_root: Path,
    attestations_root: Path,
    key: bytes,
    *,
    approved_endpoint_sha256: str,
    expected_host_identity: str,
) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    if not _hex64(approved_endpoint_sha256):
        raise ValueError("invalid-approved-provider-endpoint-sha256")
    if not isinstance(expected_host_identity, str) or not 0 < len(expected_host_identity) <= 128:
        raise ValueError("invalid-expected-host-identity")
    problems: Counter[str] = Counter()
    if (not bundles_root.is_dir() or bundles_root.is_symlink()
            or not attestations_root.is_dir() or attestations_root.is_symlink()):
        problems["unsafe-or-unavailable-results-root"] += 1
    provenance: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for cell in frozen["assignments"]:
        if problems.get("unsafe-or-unavailable-results-root"):
            break
        trial = cell["trial_id"]
        trace = bundles_root / trial / "trajectory.json"
        attestation = attestations_root / (trial + ".json")
        if (trace.is_symlink() or attestation.is_symlink()
                or (bundles_root / trial).is_symlink()):
            problems["symlinked-trial-source"] += 1
            continue
        verified = verify_host_attestations(
            trace, attestation, key,
            campaign_id=frozen["design"]["campaign_id"], trial_id=trial,
        )
        if (verified.get("attestation_state") != "VERIFIED"
                or verified.get("delivery_state") != "PROVEN"
                or verified.get("provider_submission_state") != "SUBMITTED"
                or type(verified.get("returned_packets")) is not int
                or verified["returned_packets"] <= 0
                or verified.get("verified_provider_submission_packets")
                != verified["returned_packets"]):
            problems["trial-missing-independent-signed-provider-submission"] += 1
            continue
        try:
            envelope = _load_receipt(attestation)
            if not isinstance(envelope, dict) or not isinstance(envelope.get("deliveries"), list):
                raise ValueError("invalid-transport-envelope")
            records = envelope["deliveries"]
            # One controlled tool call/response per trial is the present
            # provider-host integration contract; aggregate subject traces
            # are deliberately not silently reduced to one delivered packet.
            if len(records) != 1 or not isinstance(records[0], dict):
                raise ValueError("unsupported-multiple-deliveries")
            row = records[0]
            meta = row.get("intervention")
            transport = row.get("transport")
            study = frozen["design"]["study"]
            expected_sequence = (
                2 if study == "timing" and cell["arm"] == "variant" else 1
            )
            if (not isinstance(meta, dict) or not isinstance(transport, dict)
                    or row.get("host_identity") != expected_host_identity
                    or row.get("host_build_sha256") != frozen["host_build_sha256"]
                    or transport.get("endpoint_sha256") != approved_endpoint_sha256
                    or row.get("model_request_sequence") != expected_sequence
                    or meta.get("arm") != cell["arm"]
                    or meta.get("study") != study
                    or meta.get("design_sha256") != frozen["design_sha256"]):
                raise ValueError("host-build-endpoint-assignment-or-sequence-mismatch")
            if study == "timing":
                prior_request = meta.get("prior_request_sha256")
                prior_transport = meta.get("prior_transport_sha256")
                if (meta.get("delivery_request_sequence") != expected_sequence
                        or not _hex64(prior_request)
                        or not _hex64(prior_transport)
                        or (cell["arm"] == "control" and (
                            prior_request != "0" * 64
                            or prior_transport != "0" * 64
                        ))
                        or (cell["arm"] == "variant" and (
                            prior_request == "0" * 64
                            or prior_transport == "0" * 64
                        ))):
                    raise ValueError("timing-prior-submission-or-sequence-unqualified")
            key_cell = (cell["harness"], cell["task"], cell["replicate"], cell["arm"])
            provenance[key_cell] = {
                "trial_id": trial,
                "pair_id": cell["pair_id"],
                "host_build_sha256": row["host_build_sha256"],
                "host_identity": row["host_identity"],
                "endpoint_sha256": transport["endpoint_sha256"],
                "request_sha256": row["model_request_sha256"],
                "response_sha256": transport["response_sha256"],
                "request_sequence": row["model_request_sequence"],
                "prior_request_sha256": (
                    meta["prior_request_sha256"] if study == "timing" else None
                ),
                "prior_transport_sha256": (
                    meta["prior_transport_sha256"] if study == "timing" else None
                ),
                "catalog_sha256": meta["catalog_sha256"],
                "prompt_sha256": meta["prompt_sha256"],
                "oracle_sha256": meta["oracle_sha256"],
                "workspace_sha256": meta["workspace_sha256"],
            }
        except (ValueError, TypeError, KeyError, OSError):
            problems["trial-invalid-host-transport-provenance"] += 1
    expected_pairs: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    observed_pairs: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for cell in frozen["assignments"]:
        key_pair = (cell["harness"], cell["task"], cell["replicate"])
        expected_pairs[key_pair].add(cell["arm"])
        observed = provenance.get((*key_pair, cell["arm"]))
        if observed:
            observed_pairs[key_pair][cell["arm"]] = observed
    for key_pair, arms in expected_pairs.items():
        observed = observed_pairs.get(key_pair, {})
        if len(observed) != len(arms):
            problems["incomplete-paired-transport"] += 1
            continue
        left, right = observed["control"], observed["variant"]
        if any(left[facet] != right[facet] for facet in (
            "host_build_sha256", "host_identity", "endpoint_sha256",
            "catalog_sha256", "prompt_sha256", "oracle_sha256", "workspace_sha256",
        )):
            problems["cross-arm-host-or-context-transport-drift"] += 1
        if frozen["design"]["study"] == "timing" and (
            left["request_sequence"] != 1 or right["request_sequence"] != 2
            or left["prior_request_sha256"] != "0" * 64
            or left["prior_transport_sha256"] != "0" * 64
            or right["prior_request_sha256"] in (None, "0" * 64)
            or right["prior_transport_sha256"] in (None, "0" * 64)
        ):
            problems["cross-arm-timing-transport-sequence-unqualified"] += 1
    if len(provenance) != len(frozen["assignments"]):
        problems["missing-planned-trial-transport"] = len(frozen["assignments"]) - len(provenance)
    return {
        "schema": SCHEMA,
        "campaign_id": frozen["design"]["campaign_id"],
        "manifest_sha256": frozen["manifest_sha256"],
        "expected_cells": len(frozen["assignments"]),
        "verified_cells": len(provenance),
        "expected_pairs": len(expected_pairs),
        "timing_arm_transport_protocol": (
            "DIRECT_REQUEST_1_VS_DEFERRED_REQUEST_2"
            if frozen["design"]["study"] == "timing" else None
        ),
        "timing_extra_model_request_confound": (
            frozen["design"]["study"] == "timing"
        ),
        "cross_trial_transport_qualified": not problems,
        "issues": dict(sorted(problems.items())),
        # No raw prompts/secret transport payloads or token-level answers.
        "provider_consumption_proven": False,
        "model_attention_proven": False,
        "native_harness_request_capture_proven": False,
        "causal_influence_proven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--bundles-root", required=True, type=Path)
    parser.add_argument("--attestations-root", required=True, type=Path)
    parser.add_argument("--host-key-file", required=True, type=Path)
    parser.add_argument("--approved-endpoint-sha256", required=True)
    parser.add_argument("--expected-host-identity", required=True)
    parser.add_argument("--require-qualified", action="store_true")
    args = parser.parse_args()
    try:
        report = audit_transport_provenance(
            load_manifest(args.manifest), args.bundles_root,
            args.attestations_root, load_host_key(args.host_key_file),
            approved_endpoint_sha256=args.approved_endpoint_sha256,
            expected_host_identity=args.expected_host_identity,
        )
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"schema": SCHEMA, "cross_trial_transport_qualified": False,
                          "reason": str(exc)}, sort_keys=True, indent=2))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 2 if args.require_qualified and not report["cross_trial_transport_qualified"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
