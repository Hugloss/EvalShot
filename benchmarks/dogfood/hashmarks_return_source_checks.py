"""Pin first-pass reviewer source references to historical Git objects.

This is a private, optional *source existence* backstop for the untrusted
return intake. It cannot verify a reviewer's identity, source interpretation,
semantic ownership, or independent oracle truth. Never publish private
candidate paths, reviewer IDs, or rationales in the resulting report.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from benchmarks.dogfood.hashmarks_blind_review import STAGED_RELATIVE, prepare_blind_review
from benchmarks.dogfood.hashmarks_collisions import _commit_tree
from benchmarks.dogfood.hashmarks_corpus import _safe_file
from benchmarks.dogfood.hashmarks_declarations import _git_bytes, _source_bytes
from benchmarks.dogfood.hashmarks_review_source_contract import check_references
from benchmarks.dogfood.hashmarks_review_returns import (
    ReturnError, _read_json, project_returns,
)

SCHEMA = "evalshot.hashmarks-first-pass-source-reference-check.v1"
_MAX_REFERENCES = 60


def _frozen_regular_source(checkout: Path, commit: str, path: str) -> bytes | None:
    """Verify one *exact* regular tracked blob, not a matching Git pathspec."""
    if not _safe_file(path):
        return None
    listing = _git_bytes(checkout, "ls-tree", "--full-tree", "-z", commit, "--", path)
    if listing is None:
        return None
    records = [record for record in listing.split(b"\0") if record]
    if len(records) != 1 or b"\t" not in records[0]:
        return None
    meta, returned_path = records[0].split(b"\t", 1)
    try:
        mode, object_type, _oid = meta.decode("ascii").split(" ", 2)
        actual_path = returned_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    if (
        actual_path != path
        or object_type != "blob"
        or mode not in {"100644", "100755"}
    ):
        return None
    return _source_bytes(checkout, commit, path)


def _validate_one_return(
    *, checkout: Path, commit: str, response: Mapping[str, Any],
) -> tuple[str, int]:
    """Use the portable check with Git-verified regular historical blobs."""
    return check_references(
        response,
        lambda path: _frozen_regular_source(checkout, commit, path),
    )


def verify_source_references(
    packet: Mapping[str, Any], returns: Mapping[str, Any] | None,
    *, checkout: Path,
) -> dict[str, Any]:
    """Check private citations only after the existing return-shape authority."""
    sanitized = project_returns(packet, returns)
    if not checkout.is_dir() or _git_bytes(
        checkout, "rev-parse", "--is-inside-work-tree"
    ) != b"true\n":
        raise ReturnError("source checkout must be a readable Git working tree")

    questions = {
        row["packet_id"]: row for row in packet["packets"]
    }
    private = {
        row["packet_id"]: row
        for row in returns["reviews"]
    } if returns is not None else {}

    # Do not blindly trust the returned commit/tree. The untrusted reviewer
    # must match the blinded packet, and Git commit/tree identity must agree
    # with the historical objects without refs/replace.
    # Resolve the whole commit/tree authority once before examining any
    # private citation. Otherwise a later conflicting task could invalidate
    # the commit after an earlier task had already been marked verified.
    expected_trees: dict[str, str | None] = {}
    for question in questions.values():
        commit = question["repository"]["commit"]
        tree = question["repository"]["tree"]
        if commit not in expected_trees:
            expected_trees[commit] = tree
        elif expected_trees[commit] != tree:
            expected_trees[commit] = None
    verified_commits = {
        commit: expected_tree is not None and _commit_tree(checkout, commit) == expected_tree
        for commit, expected_tree in expected_trees.items()
    }
    cases: list[dict[str, Any]] = []
    for row in sanitized["tasks"]:
        question = questions[row["packet_id"]]
        commit = question["repository"]["commit"]
        claim = private.get(row["packet_id"])
        if not verified_commits[commit]:
            status = "PINNED_SOURCE_IDENTITY_UNAVAILABLE"
            tested = 0
        elif claim is None:
            status = "AWAITING_FIRST_PASS"
            tested = 0
        else:
            if len(claim["candidate_locations"]) + len(claim["source_evidence"]) > _MAX_REFERENCES:
                raise ReturnError("bounded first-pass source references exceeded")
            status, tested = _validate_one_return(
                checkout=checkout, commit=commit, response=claim
            )
        cases.append({
            "task_id": row["task_id"],
            "packet_id": row["packet_id"],
            "source_reference_status": status,
            "source_references_examined": tested,
            "reviewer_identity_verified": False,
            "claim_content_verified": False,
            "semantic_owner_proven": False,
            "independent_oracle_approved": False,
            "native_baseline_established": False,
        })
    statuses = [row["source_reference_status"] for row in cases]
    return {
        "schema": SCHEMA,
        "qualification": "PINNED_SOURCE_EXISTENCE_ONLY",
        "blinded_packet_identity": sanitized["blinded_packet_identity"],
        "summary": {
            "tasks": len(cases),
            "responses_received": sanitized["summary"]["responses_received"],
            "pinned_references_exist": statuses.count(
                "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF"
            ),
            "awaiting_first_pass": statuses.count("AWAITING_FIRST_PASS"),
            "unavailable_or_disputed_source": sum(
                status not in {
                    "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
                    "AWAITING_FIRST_PASS",
                } for status in statuses
            ),
            "approved_oracles": 0,
            "verified_independent_reviewers": 0,
            "measured_native_headroom": 0,
        },
        "tasks": cases,
        "data_classification": "SANITIZED_PRIVATE_SOURCE_CHECK_STATUS_ONLY",
        "next_owner": "benchmarks.harness.oracle_reviews",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--suite", type=Path, default=STAGED_RELATIVE)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.repository_root.resolve()
        if args.responses and args.responses.resolve().is_relative_to(root):
            raise ReturnError("private reviewer returns must remain outside repository")
        if args.output:
            out = args.output.resolve()
            if (
                out.is_relative_to(root)
                or (args.responses and out == args.responses.resolve())
                or args.output.exists()
                or args.output.is_symlink()
            ):
                raise ReturnError(
                    "source status must be a new file outside the repository "
                    "and distinct from private returns"
                )
        packet = prepare_blind_review(
            staged=(root / args.suite).resolve(), repository_root=root
        )
        report = verify_source_references(
            packet,
            _read_json(args.responses) if args.responses else None,
            checkout=args.checkout,
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        parser.error(str(exc))
    raw = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive publication is authoritative even if another writer
            # creates the name after preflight. Never follow a final symlink
            # or truncate private/previously qualified review material.
            fd = os.open(
                args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(raw)
        except OSError as exc:
            parser.error(str(exc))
    print(raw, end="")
    return 0 if report["summary"]["unavailable_or_disputed_source"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
