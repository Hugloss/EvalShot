"""Offline, answer-free, INCOMPLETE review-return starter for a blinded kit.

This program runs only from the downloaded reviewer kit. It DOES NOT perform
a review, choose a source owner, execute agents, or approve an EvalShot oracle.
All human/agent observations must be entered independently. The returned
starter intentionally FAILS EvalShot's private return-shape validator until a
reviewer replaces all placeholders and supplies genuine source evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

# A received kit is an exact, manifest-bound allowlist. Importing verify.py
# must not create __pycache__ and invalidate its own transport verification.
sys.dont_write_bytecode = True
from verify import KitVerificationError, verify_kit

RETURNS_SCHEMA = "evalshot.hashmarks-external-first-pass-returns.v1"
NOT_REVIEWED = "NOT_REVIEWED_REPLACE_BEFORE_SUBMISSION"


def make_unreviewed_starter(root: Path, expected_digest: str) -> dict:
    public: dict = {}
    verified = verify_kit(
        root, expected_manifest_sha256=expected_digest, verified_public=public
    )
    if verified.get("qualification") != "TRANSPORT_BYTES_VERIFIED_ONLY":
        raise KitVerificationError("review kit transport is unverified")
    questions = public["questions"]
    manifest = public["manifest"]
    identity = manifest.get("packet_digest")
    if not isinstance(identity, str) or not identity.startswith("sha256:"):
        raise KitVerificationError("blinded question identity missing from manifest")
    rows = []
    for question in questions["packets"]:
        rows.append({
            "packet_id": question["packet_id"],
            "task_digest": question["task_digest"],
            "reviewer_id": "",
            "source_commit": question["repository"]["commit"],
            "source_tree": question["repository"]["tree"],
            "decision": NOT_REVIEWED,
            "prior_expected_answer_access": None,
            "source_inspected": None,
            "candidate_locations": [],
            "reason": "",
            "source_evidence": [],
        })
    return {
        "schema": RETURNS_SCHEMA,
        "blinded_packet_identity": identity,
        "reviews": rows,
    }


def _private_destination(root: Path, output: Path) -> None:
    target = output.resolve()
    kit = root.resolve()
    if (
        target == kit
        or target.is_relative_to(kit)
        or kit.is_relative_to(target)
        or output.exists()
        or output.is_symlink()
        or not output.parent.is_dir()
        or output.parent.is_symlink()
    ):
        raise KitVerificationError(
            "private response starter must be a new file outside the reviewer kit"
        )


def write_starter(*, root: Path, output: Path, expected_digest: str) -> dict:
    _private_destination(root, output)
    report = make_unreviewed_starter(root, expected_digest)
    body = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    # O_EXCL prevents overwriting an existing response; 0600 keeps this
    # private by default. No private reviewer answers are printed or uploaded.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(output, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {
        "status": "INCOMPLETE_PRIVATE_REVIEW_STARTER_ONLY",
        "tasks": len(report["reviews"]),
        "output": str(output),
        "reviewer_identity_verified": False,
        "semantic_owner_proven": False,
        "oracle_approved": False,
        "native_baseline_established": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--expect-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        status = write_starter(
            root=args.root,
            output=args.output,
            expected_digest=args.expect_manifest_sha256,
        )
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(status, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
