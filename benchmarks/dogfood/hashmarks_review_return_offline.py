"""Offline check of private reviewer responses; no approval or source-meaning proof."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
from verify import verify_kit, _read_object, KitVerificationError
from review_contract import validate_returns, ReturnError


def check(root: Path, responses: Path, digest: str) -> dict:
    verify_kit(root, expected_manifest_sha256=digest)
    if responses.resolve().is_relative_to(root.resolve()):
        raise ReturnError("private reviewer returns must remain outside the kit")
    if responses.is_symlink() or not responses.is_file() or responses.stat().st_size > 250_000:
        raise ReturnError("private response unavailable or unsafe")
    payload, _ = _read_object(responses)
    question, _ = _read_object(root / "questions.json")
    manifest, _ = _read_object(root / "manifest.json")
    if payload.get("blinded_packet_identity") != manifest.get("packet_digest"):
        raise ReturnError("review returns do not match the exact blinded packet")
    checked = validate_returns(question, payload)
    return {
        "status": "UNTRUSTED_FIRST_PASS_SHAPE_VALIDATED_ONLY",
        "responses_received": len(checked),
        "approved_oracles": 0,
        "reviewer_independence_verified": False,
        "semantic_owner_proven": False,
        "native_baseline_established": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--expect-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = check(args.root, args.responses, args.expect_manifest_sha256)
    except (ValueError, OSError, TypeError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
