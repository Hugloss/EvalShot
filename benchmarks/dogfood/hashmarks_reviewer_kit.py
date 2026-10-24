"""Prepare a source-bound, answer-blinded reviewer kit without model execution.

The output contains only the validated blinded questions, a byte-verified
snapshot of the exact pinned Hashmarks Git tree, and a content manifest.
This is an offline handoff, NOT a reviewer identity attestation, isolation
sandbox, independent semantic review, or EvalShot oracle approval.
"""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
import shutil
from pathlib import Path
import subprocess
import tarfile
import tempfile
from typing import Any

from benchmarks.dogfood.hashmarks_blind_review import (
    STAGED_RELATIVE, prepare_blind_review,
)
from benchmarks.dogfood.hashmarks_collisions import _commit_tree
from benchmarks.dogfood.hashmarks_corpus import _safe_file
from benchmarks.harness.identity import digest

SCHEMA = "evalshot.hashmarks-blinded-reviewer-kit.v1"
_MAX_FILES = 1200
_MAX_SOURCE_BYTES = 32 * 1024 * 1024
_MAX_TAR_BYTES = 40 * 1024 * 1024


class ReviewerKitError(ValueError):
    """A blinded source kit cannot be safely materialized."""


def _git(checkout: Path, *args: str, data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "--no-optional-locks", *args],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewerKitError("historical Git source is unavailable") from exc
    if result.returncode:
        raise ReviewerKitError("historical Git source is unavailable")
    return result.stdout


def _tracked_objects(checkout: Path, commit: str) -> dict[str, tuple[str, str]]:
    raw = _git(checkout, "ls-tree", "-r", "--full-tree", "-z", commit)
    entries: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            path = path_bytes.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReviewerKitError("invalid tracked source tree entry") from exc
        if (
            not _safe_file(path)
            or path.startswith(".git/")
            or path == ".git"
            or kind != "blob"
            or mode not in {"100644", "100755"}
            or path in entries
        ):
            raise ReviewerKitError("unsupported or unsafe historical source entry")
        entries[path] = (object_id, mode)
    if not entries or len(entries) > _MAX_FILES:
        raise ReviewerKitError("historical source exceeds reviewer kit file limit")
    return entries


def _extract_snapshot(
    *, checkout: Path, commit: str, destination: Path,
) -> dict[str, str]:
    expected = _tracked_objects(checkout, commit)
    archive = _git(checkout, "archive", "--format=tar", commit)
    if len(archive) > _MAX_TAR_BYTES:
        raise ReviewerKitError("historical source archive exceeds bound")
    seen: dict[str, str] = {}
    total = 0
    try:
        with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream:
            for member in stream:
                name = member.name.removesuffix("/") if member.isdir() else member.name
                if member.isdir():
                    if not _safe_file(name) or name.startswith(".git/"):
                        raise ReviewerKitError("unsafe directory inside historical archive")
                    continue
                if not member.isfile() or not _safe_file(name) or name in seen:
                    raise ReviewerKitError("unsupported archive source entry")
                if name not in expected:
                    raise ReviewerKitError("archive contains untracked source content")
                if member.size > _MAX_SOURCE_BYTES or member.size < 0:
                    raise ReviewerKitError("source file exceeds bounded size")
                total += member.size
                if total > _MAX_SOURCE_BYTES:
                    raise ReviewerKitError("reviewer kit source exceeds total bound")
                source = stream.extractfile(member)
                if source is None:
                    raise ReviewerKitError("archive member source unavailable")
                raw = source.read(_MAX_SOURCE_BYTES + 1)
                if len(raw) != member.size:
                    raise ReviewerKitError("archive member byte count mismatch")
                # Git itself owns blob identity; do not invent an alternate
                # implementation of its SHA-1/SHA-256 object semantics.
                object_id = _git(checkout, "hash-object", "--stdin", data=raw).decode(
                    "ascii", errors="strict"
                ).strip()
                original_id, original_mode = expected[name]
                if object_id != original_id:
                    raise ReviewerKitError("archive content differs from frozen Git blob")
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                target.chmod(0o755 if original_mode == "100755" else 0o644)
                seen[name] = hashlib.sha256(raw).hexdigest()
    except (tarfile.TarError, OSError, UnicodeError) as exc:
        raise ReviewerKitError("invalid historical source archive") from exc
    if set(seen) != set(expected):
        raise ReviewerKitError("archive omitted tracked files from frozen source")
    return dict(sorted(seen.items()))


def _safe_destination(*, output: Path, repository_root: Path, checkout: Path) -> None:
    root = repository_root.resolve()
    source = checkout.resolve()
    target = output.resolve()
    if (
        target == root
        or target.is_relative_to(root)
        or target == source
        or target.is_relative_to(source)
        or root.is_relative_to(target)
        or source.is_relative_to(target)
        or output.exists()
        or output.is_symlink()
    ):
        raise ReviewerKitError("reviewer kit must be a new external destination")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ReviewerKitError("reviewer kit parent directory must already exist")


def prepare_reviewer_kit(
    *, repository_root: Path, checkout: Path, output: Path,
) -> dict[str, Any]:
    """Produce a verifiable external directory; no EvalShot files transported."""
    root = repository_root.resolve()
    checkout = checkout.resolve()
    if not checkout.is_dir():
        raise ReviewerKitError("pinned Hashmarks checkout is missing")
    _safe_destination(output=output, repository_root=root, checkout=checkout)
    questions = prepare_blind_review(
        staged=root / STAGED_RELATIVE, repository_root=root
    )
    if (
        questions.get("qualification") != "SOURCE_BLINDED_QUESTION_ONLY"
        or questions.get("experiment_admitted") is not False
        or questions.get("independent_oracles_approved") != 0
        or questions.get("trial_executions") != 0
    ):
        raise ReviewerKitError("blinded questions were not rejected by admission")
    requested = {
        (row["repository"]["commit"], row["repository"]["tree"])
        for row in questions["packets"]
    }
    if len(requested) != 1:
        raise ReviewerKitError("blinded questions reference different source trees")
    commit, tree = next(iter(requested))
    if _commit_tree(checkout, commit) != tree:
        raise ReviewerKitError("pinned historical commit/tree does not match questions")

    packet_bytes = (
        json.dumps(questions, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    working = Path(tempfile.mkdtemp(
        prefix=".reviewer-kit-staging-", dir=output.parent
    ))
    try:
        source_root = working / "source"
        source_root.mkdir()
        hashes = _extract_snapshot(
            checkout=checkout, commit=commit, destination=source_root
        )
        (working / "questions.json").write_bytes(packet_bytes)
        # Give the reviewer a standalone verifier. They must not need to
        # checkout EvalShot or execute any untrusted pinned source module.
        verifier_bytes = Path(__file__).with_name(
            "hashmarks_reviewer_kit_verify.py"
        ).read_bytes()
        (working / "verify.py").write_bytes(verifier_bytes)
        starter_bytes = Path(__file__).with_name(
            "hashmarks_reviewer_return_starter.py"
        ).read_bytes()
        (working / "start_review.py").write_bytes(starter_bytes)
        protocol_bytes = Path(__file__).with_name(
            "hashmarks_reviewer_protocol.md"
        ).read_bytes()
        (working / "REVIEW.md").write_bytes(protocol_bytes)
        contract_bytes = Path(__file__).with_name("hashmarks_review_return_contract.py").read_bytes()
        checker_bytes = Path(__file__).with_name("hashmarks_review_return_offline.py").read_bytes()
        (working / "review_contract.py").write_bytes(contract_bytes)
        (working / "check_review.py").write_bytes(checker_bytes)
        source_contract_bytes = Path(__file__).with_name(
            "hashmarks_review_source_contract.py"
        ).read_bytes()
        (working / "source_contract.py").write_bytes(source_contract_bytes)
        comparison_bytes = Path(__file__).with_name(
            "hashmarks_review_comparison.py"
        ).read_bytes()
        (working / "compare_reviews.py").write_bytes(comparison_bytes)
        manifest = {
            "schema": SCHEMA,
            "qualification": "HANDOFF_SOURCE_SNAPSHOT_ONLY",
            "pinned_commit": commit,
            "pinned_tree": tree,
            "packet_digest": "sha256:" + digest(questions),
            "questions_sha256": hashlib.sha256(packet_bytes).hexdigest(),
            "source_files": len(hashes),
            "source_sha256_by_path": hashes,
            "workspace_members": [
                "manifest.json", "questions.json", "source/",
                "verify.py", "start_review.py", "REVIEW.md",
                "review_contract.py", "check_review.py", "source_contract.py",
                "compare_reviews.py",
            ],
            "verifier_sha256": hashlib.sha256(verifier_bytes).hexdigest(),
            "review_starter_sha256": hashlib.sha256(starter_bytes).hexdigest(),
            "review_protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
            "review_contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
            "offline_checker_sha256": hashlib.sha256(checker_bytes).hexdigest(),
            "source_contract_sha256": hashlib.sha256(source_contract_bytes).hexdigest(),
            "comparison_sha256": hashlib.sha256(comparison_bytes).hexdigest(),
            "reviewer_independence_attested": False,
            "canonical_oracle_approved": False,
            "native_trial_executed": False,
            "instruction": (
                "Review only questions.json and source/. No EvalShot oracle, "
                "prior reviews, historical grading, or other workspace files "
                "are included. Declare any prior answer exposure independently."
            ),
        }
        manifest_bytes = (
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        (working / "manifest.json").write_bytes(manifest_bytes)
        try:
            working.rename(output)
        except OSError as exc:
            raise ReviewerKitError("cannot publish external reviewer kit") from exc
    finally:
        if working.exists():
            shutil.rmtree(working)
    return {
        "schema": SCHEMA,
        "qualification": "HANDOFF_PREPARED_NOT_INDEPENDENT_REVIEWED",
        "pinned_commit": commit,
        "pinned_tree": tree,
        "source_files": len(hashes),
        "questions": len(questions["packets"]),
        "owner_oracle_approved": False,
        "reviewer_independence_attested": False,
        "native_trials_executed": 0,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare_reviewer_kit(
            repository_root=args.repository_root,
            checkout=args.checkout,
            output=args.output_dir,
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
