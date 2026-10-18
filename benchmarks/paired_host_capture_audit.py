#!/usr/bin/env python3
"""Audit exact host-observed payload bytes and task binding before paired scoring.

The capture root must be trusted independently. This program does not authenticate
the host, prove model consumption, or manufacture delivery receipts.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from paired_outcome_gate import evaluate


def _text(value, where):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected nonempty string")
    return value


def _sha(value, where):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{where}: expected lowercase SHA-256")
    return value


def audit(campaign, manifest, capture_root):
    if not isinstance(manifest, dict) or manifest.get("schema") != "agentscookbook.host-capture.v1":
        raise ValueError("unsupported host capture manifest")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("empty host capture manifest")
    if not capture_root.is_dir() or capture_root.is_symlink():
        raise ValueError("capture root must be a real directory")
    lookup = {}
    paths = set()
    for idx, entry in enumerate(entries):
        where = f"capture.entries[{idx}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where}: invalid entry")
        key = (_text(entry.get("task_id"), where), _text(entry.get("arm"), where))
        if key[1] not in ("baseline", "treatment") or key in lookup:
            raise ValueError(f"{where}: duplicate/invalid task arm")
        trial = _text(entry.get("host_trial_id"), where)
        delivery = _text(entry.get("delivery_id"), where)
        digest = _sha(entry.get("payload_sha256"), where)
        rel = _text(entry.get("payload_path"), where)
        path = Path(rel)
        if path.is_absolute() or any(p in (".", "..") for p in path.parts) or rel in paths:
            raise ValueError(f"{where}: unsafe or duplicate payload path")
        paths.add(rel)
        full = capture_root / path
        if full.is_symlink() or not full.is_file() or not full.resolve().is_relative_to(capture_root.resolve()):
            raise ValueError(f"{where}: payload missing, symlink or path escape")
        actual = hashlib.sha256(full.read_bytes()).hexdigest()
        if digest != actual:
            raise ValueError(f"{where}: recorded digest differs from captured bytes")
        lookup[key] = (trial, delivery, digest)
    if not isinstance(campaign, dict) or not isinstance(campaign.get("pairs"), list):
        raise ValueError("invalid campaign")
    used = set()
    for index, pair in enumerate(campaign["pairs"]):
        if not isinstance(pair, dict):
            raise ValueError(f"pairs[{index}]: invalid pair")
        task = _text(pair.get("task_id"), f"pairs[{index}]")
        for arm in ("baseline", "treatment"):
            key = (task, arm)
            if key not in lookup or key in used:
                raise ValueError(f"{key}: missing/duplicate host capture")
            used.add(key)
            receipt = pair.get(arm, {}).get("delivery", {})
            trial, delivery, digest = lookup[key]
            if not isinstance(receipt, dict) or receipt.get("host_trial_id") != trial or receipt.get("delivery_id") != delivery:
                raise ValueError(f"{key}: task/arm delivery identity mismatch")
            if receipt.get("payload_sha256") != digest or receipt.get("visible_payload_sha256") != digest:
                raise ValueError(f"{key}: byte-level model-visible payload identity mismatch")
    if used != set(lookup):
        raise ValueError("capture manifest has extra unmatched entries")
    outcome = evaluate(campaign)
    outcome["host_capture_audit"] = {
        "schema": "agentscookbook.host-capture-audit.v1",
        "byte_verified_deliveries": len(used),
        "capture_manifest_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "capture_root_trust": "EXTERNAL_VERIFICATION_REQUIRED",
    }
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--capture-manifest", required=True, type=Path)
    parser.add_argument("--capture-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        campaign = json.loads(args.campaign.read_text(encoding="utf-8"))
        manifest = json.loads(args.capture_manifest.read_text(encoding="utf-8"))
        result = audit(campaign, manifest, args.capture_root)
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        print(f"INADMISSIBLE: {exc}", file=sys.stderr)
        return 2
    content = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")
    return 0 if result["qualification"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
