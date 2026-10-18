#!/usr/bin/env python3
"""Admit paired trial exports only with independently MAC-verified host submissions.

This boundary verifies existing host-input attestation; it does not infer
which visible response bytes the provider consumed, nor grade agent outcomes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmarks.harness.host_input_attestation import load_host_key, verify_host_attestations
from native_host_trial_projection import project
from paired_host_capture_audit import audit


def _path(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"{label}: invalid path")
    parts = relative.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"{label}: path traversal")
    path = Path(relative)
    if path.is_absolute():
        raise ValueError(f"{label}: absolute path")
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label}: symlink component")
    if not cursor.is_file() or not cursor.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"{label}: missing/outside root")
    return cursor


def admit(export: dict, provenance: dict, root: Path, key: bytes, capture_root: Path) -> dict:
    if not isinstance(provenance, dict) or provenance.get("schema") != "agentscookbook.native-host-signed-receipts.v1":
        raise ValueError("invalid signed-receipt map")
    campaign_id = provenance.get("campaign_id")
    if not isinstance(campaign_id, str) or not 0 < len(campaign_id) <= 256:
        raise ValueError("invalid campaign ID")
    records = provenance.get("records")
    if not isinstance(records, list) or not records or not root.is_dir() or root.is_symlink():
        raise ValueError("empty receipt map or invalid receipt root")
    paired, manifest = project(export)
    expected = {row["host_trial_id"] for row in export["trials"]}
    observed = set()
    for i, row in enumerate(records):
        if not isinstance(row, dict) or set(row) != {"host_trial_id", "trajectory_path", "attestation_path"}:
            raise ValueError(f"records[{i}]: invalid record")
        trial = row["host_trial_id"]
        if not isinstance(trial, str) or trial not in expected or trial in observed:
            raise ValueError(f"records[{i}]: foreign/duplicate trial")
        observed.add(trial)
        trajectory = _path(root, row["trajectory_path"], f"records[{i}].trajectory")
        receipt = _path(root, row["attestation_path"], f"records[{i}].attestation")
        verification = verify_host_attestations(
            trajectory, receipt, key, campaign_id=campaign_id, trial_id=trial
        )
        if (verification["attestation_state"] != "VERIFIED"
                or verification["delivery_state"] != "PROVEN"
                or verification["provider_submission_state"] != "SUBMITTED"
                or verification["returned_packets"] < 1
                or verification["verified_provider_submission_packets"] != verification["returned_packets"]):
            raise ValueError(f"{trial}: no complete signed host provider submission ({verification['reason']})")
    if observed != expected:
        raise ValueError("incomplete host signed-receipt coverage")
    result = audit(paired, manifest, capture_root)
    result["signed_host_submission_admission"] = {
        "schema": "agentscookbook.signed-host-submission-admission.v1",
        "verified_trial_receipts": len(observed),
        "provider_submission_attested": True,
        "payload_capture_equivalence_proven": False,
        "provider_consumption_proven": False,
        "model_attention_proven": False,
        "causal_influence_proven": False,
    }
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_export", type=Path)
    parser.add_argument("--receipt-map", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--host-key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        key = load_host_key(args.host_key_file)
        export = json.loads(args.native_export.read_text(encoding="utf-8"))
        provenance = json.loads(args.receipt_map.read_text(encoding="utf-8"))
        result = admit(export, provenance, args.receipt_root, key, args.capture_root)
        content = json.dumps(result, sort_keys=True, indent=2) + "\n"
        if args.output:
            args.output.write_text(content, encoding="utf-8")
        else:
            print(content, end="")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"INADMISSIBLE: {exc}", file=sys.stderr)
        return 2
    return 0 if result["qualification"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
