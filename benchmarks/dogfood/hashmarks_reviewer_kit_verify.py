"""Standalone, standard-library-only verification of a received reviewer kit.

This module is copied to verify.py inside the handoff. A reviewer can execute
it WITHOUT installing or checking out EvalShot. It verifies transport bytes
and inventory, not the origin of the uploader, semantic oracle correctness,
or the independence of the reviewer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

SCHEMA = "evalshot.hashmarks-blinded-reviewer-kit.v1"
QUESTION_SCHEMA = "evalshot.hashmarks-blind-semantic-review-packet.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_MAX_SOURCE_FILES = 1200
_MAX_SOURCE_BYTES = 32 * 1024 * 1024
_MAX_METADATA_BYTES = 200_000
_ROOT_ITEMS = {
    "manifest.json", "questions.json", "source",
    "verify.py", "start_review.py", "REVIEW.md",
    "review_contract.py", "check_review.py",
}


class KitVerificationError(ValueError):
    """A transported handoff failed independent byte and inventory checks."""


def _regular_bytes(path: Path, *, max_size: int) -> bytes:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > max_size
    ):
        raise KitVerificationError("missing, unsafe or oversized kit member")
    return path.read_bytes()


def _read_object(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _regular_bytes(path, max_size=_MAX_METADATA_BYTES)

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise KitVerificationError("duplicate JSON key")
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise KitVerificationError("kit metadata is not a valid JSON object") from exc
    if not isinstance(value, dict):
        raise KitVerificationError("kit metadata must be an object")
    return value, raw


def _path_safe(name: str) -> bool:
    if not isinstance(name, str) or not name or name.startswith("/"):
        return False
    parts = name.split("/")
    return (
        all(part not in {"", ".", "..", ".git"} for part in parts)
        and not any(ch in name for ch in "\\:*?[]")
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
    )


def verify_kit(root: Path, *, expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    """Verify after transport; never execute or import checked source files."""
    if root.is_symlink() or not root.is_dir():
        raise KitVerificationError("review kit root is missing or unsafe")
    if {child.name for child in root.iterdir()} != _ROOT_ITEMS:
        raise KitVerificationError("review kit contains unexpected or missing root items")
    if (root / "source").is_symlink() or not (root / "source").is_dir():
        raise KitVerificationError("review kit source directory missing or unsafe")

    manifest, raw_manifest = _read_object(root / "manifest.json")
    manifest_hash = hashlib.sha256(raw_manifest).hexdigest()
    if expected_manifest_sha256 is not None and (
        not _SHA256.fullmatch(expected_manifest_sha256)
        or manifest_hash != expected_manifest_sha256
    ):
        raise KitVerificationError("received manifest differs from producer-supplied digest")
    hashes = manifest.get("source_sha256_by_path")
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("qualification") != "HANDOFF_SOURCE_SNAPSHOT_ONLY"
        or manifest.get("workspace_members") != [
            "manifest.json", "questions.json", "source/",
            "verify.py", "start_review.py", "REVIEW.md",
            "review_contract.py", "check_review.py"
        ]
        or type(manifest.get("reviewer_independence_attested")) is not bool
        or manifest["reviewer_independence_attested"] is not False
        or manifest.get("canonical_oracle_approved") is not False
        or manifest.get("native_trial_executed") is not False
        or not isinstance(hashes, dict)
        or type(manifest.get("source_files")) is not int
        or not 0 < manifest["source_files"] <= _MAX_SOURCE_FILES
        or len(hashes) != manifest["source_files"]
        or not _SHA40.fullmatch(str(manifest.get("pinned_commit")))
        or not _SHA40.fullmatch(str(manifest.get("pinned_tree")))
        or not _SHA256.fullmatch(str(manifest.get("questions_sha256")))
        or not _SHA256.fullmatch(str(manifest.get("verifier_sha256")))
        or not _SHA256.fullmatch(str(manifest.get("review_starter_sha256")))
        or not _SHA256.fullmatch(str(manifest.get("review_protocol_sha256")))
        or not _SHA256.fullmatch(str(manifest.get("review_contract_sha256")))
        or not _SHA256.fullmatch(str(manifest.get("offline_checker_sha256")))
        or not isinstance(manifest.get("packet_digest"), str)
        or not manifest["packet_digest"].startswith("sha256:")

    ):
        raise KitVerificationError("unsupported or unsafe kit manifest")
    if any(
        not _path_safe(path) or not isinstance(expected, str)
        or not _SHA256.fullmatch(expected)
        for path, expected in hashes.items()
    ):
        raise KitVerificationError("unsafe or malformed source inventory")

    verifier = _regular_bytes(root / "verify.py", max_size=_MAX_METADATA_BYTES)
    if hashlib.sha256(verifier).hexdigest() != manifest["verifier_sha256"]:
        raise KitVerificationError("review kit verifier changed after source export")

    starter = _regular_bytes(root / "start_review.py", max_size=_MAX_METADATA_BYTES)
    if hashlib.sha256(starter).hexdigest() != manifest["review_starter_sha256"]:
        raise KitVerificationError("review starter changed after source export")

    review_protocol = _regular_bytes(root / "REVIEW.md", max_size=_MAX_METADATA_BYTES)
    if hashlib.sha256(review_protocol).hexdigest() != manifest["review_protocol_sha256"]:
        raise KitVerificationError("reviewer protocol differs from manifest")
    # This is deliberately content-integrity validation, not a semantic
    # approval or an interpretation of the instructions.

    for member, field in (
        ("review_contract.py", "review_contract_sha256"),
        ("check_review.py", "offline_checker_sha256"),
    ):
        raw = _regular_bytes(root / member, max_size=_MAX_METADATA_BYTES)
        if hashlib.sha256(raw).hexdigest() != manifest[field]:
            raise KitVerificationError("offline review checker differs from manifest")

    questions, raw_questions = _read_object(root / "questions.json")
    if hashlib.sha256(raw_questions).hexdigest() != manifest["questions_sha256"]:
        raise KitVerificationError("blinded questions bytes differ from manifest")
    packet_canonical = (
        json.dumps(questions, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    if manifest["packet_digest"] != "sha256:" + hashlib.sha256(packet_canonical).hexdigest():
        raise KitVerificationError("blinded packet identity differs from manifest")
    records = questions.get("packets")
    if (
        questions.get("schema") != QUESTION_SCHEMA
        or questions.get("qualification") != "SOURCE_BLINDED_QUESTION_ONLY"
        or questions.get("experiment_admitted") is not False
        or questions.get("independent_oracles_approved") != 0
        or questions.get("trial_executions") != 0
        or not isinstance(records, list)
        or len(records) != 2
    ):
        raise KitVerificationError("blinded question authority changed")
    packet_ids: set[str] = set()
    for row in records:
        if (
            not isinstance(row, dict)
            or row.get("review_status") != "FIRST_PASS_UNREVIEWED"
            or row.get("proposed_owner") != "WITHHELD"
            or row.get("semantic_decision") != "NOT_MADE"
            or not isinstance(row.get("packet_id"), str)
            or row["packet_id"] in packet_ids
            or not isinstance(row.get("frozen_prompt"), str)
            or not row["frozen_prompt"].strip()
            or row.get("repository", {}).get("commit") != manifest["pinned_commit"]
            or row.get("repository", {}).get("tree") != manifest["pinned_tree"]
            or "oracle" in row
            or "expected" in row
        ):
            raise KitVerificationError("blinded question contents are unqualified")
        packet_ids.add(row["packet_id"])

    observed: set[str] = set()
    total = 0
    source = root / "source"
    for item in source.rglob("*"):
        relative = item.relative_to(source).as_posix()
        if not _path_safe(relative) or item.is_symlink():
            raise KitVerificationError("unsafe material in received source tree")
        if item.is_dir():
            continue
        if not item.is_file() or relative not in hashes or relative in observed:
            raise KitVerificationError("unexpected source material in reviewer kit")
        raw = _regular_bytes(item, max_size=_MAX_SOURCE_BYTES)
        total += len(raw)
        if total > _MAX_SOURCE_BYTES:
            raise KitVerificationError("reviewer source exceeds bounded total size")
        if hashlib.sha256(raw).hexdigest() != hashes[relative]:
            raise KitVerificationError("received source does not match inventory")
        observed.add(relative)
    if observed != set(hashes):
        raise KitVerificationError("received reviewer kit omitted tracked source files")

    return {
        "qualification": "TRANSPORT_BYTES_VERIFIED_ONLY",
        "manifest_sha256": manifest_hash,
        "source_files": len(observed),
        "blinded_questions": len(records),
        "reviewer_independence_attested": False,
        "oracle_approved": False,
        "native_trials_executed": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--expect-manifest-sha256")
    args = parser.parse_args(argv)
    try:
        result = verify_kit(args.root, expected_manifest_sha256=args.expect_manifest_sha256)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
