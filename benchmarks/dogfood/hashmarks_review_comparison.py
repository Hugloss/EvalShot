"""Offline comparison of TWO private first-pass reviewer returns.

This compares untrusted, source-bound *claims*, never qualifies independence,
semantic owner uniqueness, oracle approval, native trials or headroom. Reuse
the kit's one return-shape and source-reference validation owner rather than
reimplementing those decisions. Do not write reviewer answers into the kit.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from verify import KitVerificationError, _read_object
from check_review import check
from review_contract import ReturnError

SCHEMA = "evalshot.hashmarks-private-review-disagreement.v1"
_ACCEPTABLE_SOURCE = "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF"
_UNABLE_SOURCE = "UNABLE_TO_REVIEW_NO_SOURCE_CHECK"


def _bundle(
    *, kit: Path, responses: Path, expected_digest: str,
) -> dict[str, dict]:
    # check() already authenticates the manifest bytes, uses the shared
    # return-shape contract and validates source references from that
    # manifest-verified snapshot. No duplicate validator is introduced.
    report = check(kit, responses, expected_digest)
    payload, _ = _read_object(responses)
    questions, _ = _read_object(kit / "questions.json")
    by_packet = {row["packet_id"]: row["task_id"] for row in questions["packets"]}
    statuses = {row["task_id"]: row["source_reference_status"]
                for row in report["review_statuses"]}
    reviewers = {row["reviewer_id"] for row in payload["reviews"]}
    if len(reviewers) > 1:
        raise ReturnError("one private response bundle must declare one reviewer")
    return {
        by_packet[row["packet_id"]]: {
            "declared_reviewer": row["reviewer_id"],
            "decision": row["decision"],
            "prior_access": row["prior_expected_answer_access"],
            "source_status": statuses[by_packet[row["packet_id"]]],
            # Keep candidate content in memory only; never serialize it.
            "candidate": tuple(sorted(
                (entry["path"], entry["symbol"])
                for entry in row["candidate_locations"]
            )),
        }
        for row in payload["reviews"]
    }


def compare_private_reviews(
    *, kit: Path, response_a: Path, response_b: Path,
    expected_manifest_sha256: str,
) -> dict:
    kit = kit.resolve()
    if response_a.resolve() == response_b.resolve():
        raise ReturnError("two distinct private review files are required")
    first = _bundle(
        kit=kit, responses=response_a,
        expected_digest=expected_manifest_sha256,
    )
    second = _bundle(
        kit=kit, responses=response_b,
        expected_digest=expected_manifest_sha256,
    )
    questions, _ = _read_object(kit / "questions.json")
    cases = []
    for question in questions["packets"]:
        task = question["task_id"]
        a, b = first.get(task), second.get(task)
        if a is None or b is None:
            state = "INCOMPLETE_TWO_REVIEWER_COVERAGE"
        elif a["declared_reviewer"] == b["declared_reviewer"]:
            state = "DUPLICATE_DECLARED_REVIEWER"
        elif a["prior_access"] or b["prior_access"]:
            state = "PRIOR_EXPECTED_ANSWER_ACCESS_DECLARED"
        elif any(
            row["source_status"] not in {_ACCEPTABLE_SOURCE, _UNABLE_SOURCE}
            for row in (a, b)
        ):
            state = "SOURCE_REFERENCE_DISPUTED"
        elif any(row["decision"] == "ambiguous" for row in (a, b)):
            state = "AMBIGUITY_REPORTED_NO_AUTOMATIC_RESOLUTION"
        elif any(row["decision"] == "invalid" for row in (a, b)):
            state = "INVALIDITY_REPORTED_NO_AUTOMATIC_RESOLUTION"
        elif any(row["decision"] == "unable_to_review" for row in (a, b)):
            state = "UNABLE_TO_REVIEW_NO_AUTOMATIC_RESOLUTION"
        elif a["decision"] == b["decision"] == "unique" and (
            a["source_status"] == b["source_status"] == _ACCEPTABLE_SOURCE
        ):
            state = (
                "SAME_CANDIDATE_UNVERIFIED"
                if a["candidate"] == b["candidate"]
                else "COMPETING_CANDIDATES_DISAGREE"
            )
        else:
            state = "INCOMPATIBLE_FIRST_PASS_CLAIMS"
        cases.append({
            "task_id": task,
            "state": state,
            "returned_claims": int(a is not None) + int(b is not None),
            "reviewer_independence_verified": False,
            "semantic_owner_proven": False,
            "canonical_oracle_approved": False,
            "native_baseline_established": False,
        })
    return {
        "schema": SCHEMA,
        "qualification": "TWO_UNTRUSTED_SOURCE_BOUND_REVIEWS_ONLY",
        "cases": cases,
        "summary": {
            "tasks": len(cases),
            "complete_claim_pairs": sum(row["returned_claims"] == 2 for row in cases),
            "same_candidate_unverified": sum(
                row["state"] == "SAME_CANDIDATE_UNVERIFIED" for row in cases
            ),
            "conflicts_or_incomplete": sum(
                row["state"] != "SAME_CANDIDATE_UNVERIFIED" for row in cases
            ),
            "approved_oracles": 0,
            "verified_independent_reviewers": 0,
            "measured_native_headroom": 0,
        },
        "next_owner": "benchmarks.harness.oracle_reviews",
        "data_classification": "SANITIZED_NO_PRIVATE_ANSWER_OR_REVIEWER_ID",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--response-a", required=True, type=Path)
    parser.add_argument("--response-b", required=True, type=Path)
    parser.add_argument("--expect-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        kit = args.root.resolve()
        for response in (args.response_a, args.response_b):
            if response.resolve().is_relative_to(kit):
                raise ReturnError("private reviewer responses must remain outside kit")
        if args.output is not None:
            target = args.output.resolve()
            if (
                target.is_relative_to(kit)
                or target in {args.response_a.resolve(), args.response_b.resolve()}
                or args.output.exists() or args.output.is_symlink()
                or not args.output.parent.is_dir()
            ):
                raise ReturnError("comparison output must be a new external file")
        result = compare_private_reviews(
            kit=kit,
            response_a=args.response_a,
            response_b=args.response_b,
            expected_manifest_sha256=args.expect_manifest_sha256,
        )
        rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
        if args.output is not None:
            fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(rendered.encode("utf-8"))
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.error(str(exc))
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
