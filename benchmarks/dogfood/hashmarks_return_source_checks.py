"""Pin first-pass reviewer source references to historical Git objects.

This is a private, optional *source existence* backstop for the untrusted
return intake. It cannot verify a reviewer's identity, source interpretation,
semantic ownership, or independent oracle truth. Never publish private
candidate paths, reviewer IDs, or rationales in the resulting report.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Mapping

from benchmarks.dogfood.hashmarks_blind_review import STAGED_RELATIVE, prepare_blind_review
from benchmarks.dogfood.hashmarks_collisions import _commit_tree
from benchmarks.dogfood.hashmarks_corpus import _safe_file
from benchmarks.dogfood.hashmarks_declarations import (
    _Declarations, _git_bytes, _source_bytes,
)
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
    """No observable return contains sensitive reviewer-supplied details."""
    cache: dict[str, bytes | None] = {}
    count = 0

    def source(path: str) -> bytes | None:
        if path not in cache:
            cache[path] = _frozen_regular_source(checkout, commit, path)
        return cache[path]

    for location in response["candidate_locations"]:
        content = source(location["path"])
        count += 1
        if content is None:
            return "PINNED_CANDIDATE_FILE_UNAVAILABLE", count
        if location["path"].endswith(".py"):
            try:
                parsed = ast.parse(content.decode("utf-8"), filename=location["path"])
                inspector = _Declarations(location["symbol"])
                inspector.visit(parsed)
            except (SyntaxError, UnicodeError, ValueError, RecursionError):
                return "CANDIDATE_PYTHON_NOT_PARSEABLE", count
            if not inspector.matches:
                return "CANDIDATE_DECLARATION_NOT_OBSERVED", count

    for evidence in response["source_evidence"]:
        content = source(evidence["path"])
        count += 1
        if content is None:
            return "PINNED_EVIDENCE_FILE_UNAVAILABLE", count
        # Byte lines are sufficient for existence, without pretending a
        # natural-language "observation" is verified by its line number.
        if evidence["line"] > len(content.splitlines()):
            return "PINNED_EVIDENCE_LINE_ABSENT", count
    return "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF", count


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
    verified_commits: dict[str, bool] = {}
    cases: list[dict[str, Any]] = []
    for row in sanitized["tasks"]:
        question = questions[row["packet_id"]]
        commit = question["repository"]["commit"]
        tree = question["repository"]["tree"]
        if commit not in verified_commits:
            verified_commits[commit] = _commit_tree(checkout, commit) == tree
        elif _commit_tree(checkout, commit) != tree:
            verified_commits[commit] = False
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
            if out.is_relative_to(root) or (
                args.responses and out == args.responses.resolve()
            ):
                raise ReturnError("source status must not overwrite repository or private inputs")
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
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return 0 if report["summary"]["unavailable_or_disputed_source"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
