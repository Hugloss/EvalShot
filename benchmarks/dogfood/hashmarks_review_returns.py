"""Bound first-pass reviewer returns to blinded questions without approving oracles.

This is an *untrusted-claim intake* for review coordination, NOT the EvalShot
oracle-review validator. It cannot attest the reviewer, verify semantic truth,
resolve ambiguities, or permit a model-backed benchmark.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

from benchmarks.dogfood.hashmarks_blind_review import (
    STAGED_RELATIVE, prepare_blind_review,
)
from benchmarks.dogfood.hashmarks_review_return_contract import ReturnError, validate_returns
from benchmarks.harness.identity import digest

SCHEMA = "evalshot.hashmarks-first-pass-return-status.v1"
RETURNS_SCHEMA = "evalshot.hashmarks-external-first-pass-returns.v1"
_MAX_FILE_BYTES = 250_000


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_FILE_BYTES:
        raise ReturnError("review-return file is missing, unsafe, or oversized")

    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        if len(pairs) != len(dict(pairs)):
            raise ReturnError("duplicate JSON key in review-return file")
        return dict(pairs)

    try:
        parsed = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=strict_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ReturnError("non-finite JSON")),
        )
    except (ValueError, UnicodeError) as exc:
        raise ReturnError("unreadable review-return JSON") from exc
    if not isinstance(parsed, dict):
        raise ReturnError("review-return root must be an object")
    return parsed



def project_returns(
    packet: Mapping[str, Any],
    returns: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Produce sanitized diagnostic status, never re-emit private source answers."""
    if (
        packet.get("schema") != "evalshot.hashmarks-blind-semantic-review-packet.v1"
        or packet.get("qualification") != "SOURCE_BLINDED_QUESTION_ONLY"
        or packet.get("experiment_admitted") is not False
        or packet.get("independent_oracles_approved") != 0
        or packet.get("trial_executions") != 0
        or not isinstance(packet.get("packets"), list)
    ):
        raise ReturnError("unrecognized blinded question authority")

    questions = packet["packets"]
    if len(questions) != 2 or len({
        entry.get("packet_id") for entry in questions if isinstance(entry, dict)
    }) != 2:
        raise ReturnError("blinded question population differs from preregistration")
    questions_by_id = {
        entry["packet_id"]: entry for entry in questions
    }
    packet_identity = "sha256:" + digest(dict(packet))
    incoming: dict[str, dict[str, Any]] = {}

    if returns is not None:
        if (
            not isinstance(returns, Mapping)
            or set(returns) != {"schema", "blinded_packet_identity", "reviews"}
            or returns.get("schema") != RETURNS_SCHEMA
            or returns.get("blinded_packet_identity") != packet_identity
            or not isinstance(returns.get("reviews"), list)
            or len(returns["reviews"]) > len(questions)
        ):
            raise ReturnError("review returns do not match the exact blinded packet")
        incoming = validate_returns(packet, returns)

    cases: list[dict[str, Any]] = []
    for question in questions:
        response = incoming.get(question["packet_id"])
        if response is None:
            state = "AWAITING_FIRST_PASS"
            decision = "NOT_REPORTED"
            exposure = "UNKNOWN"
        else:
            decision = response["decision"]
            exposure = (
                "DECLARED_PRIOR_ACCESS"
                if response["prior_expected_answer_access"]
                else "CLAIMED_NO_PRIOR_ACCESS_NOT_VERIFIED"
            )
            if response["prior_expected_answer_access"]:
                state = "PRIOR_ANSWER_EXPOSURE_DECLARED"
            elif decision == "ambiguous":
                state = "AMBIGUITY_REPORTED"
            elif decision == "invalid":
                state = "INVALIDITY_REPORTED"
            elif decision == "unable_to_review":
                state = "UNABLE_TO_REVIEW"
            else:
                state = "UNVERIFIED_UNIQUE_CLAIM_RECORDED"
        cases.append({
            "task_id": question["task_id"],
            "packet_id": question["packet_id"],
            "task_digest": question["task_digest"],
            "status": state,
            "declared_decision": decision,
            "exposure_status": exposure,
            "source_claim_authenticated": False,
            "reviewer_identity_verified": False,
            "semantic_truth_verified": False,
            "canonical_oracle_approved": False,
            "native_experiment_admitted": False,
            "exact_task_baseline_established": False,
        })

    return {
        "schema": SCHEMA,
        "qualification": "UNTRUSTED_FIRST_PASS_CLAIM_STATUS_ONLY",
        "blinded_packet_identity": packet_identity,
        "source_review_returns_present": returns is not None,
        "summary": {
            "expected_questions": len(cases),
            "responses_received": len(incoming),
            "awaiting_responses": sum(x["status"] == "AWAITING_FIRST_PASS" for x in cases),
            "unverified_unique_claims": sum(
                x["status"] == "UNVERIFIED_UNIQUE_CLAIM_RECORDED" for x in cases
            ),
            "ambiguous_or_invalid_claims": sum(
                x["status"] in {"AMBIGUITY_REPORTED", "INVALIDITY_REPORTED"} for x in cases
            ),
            "approved_oracles": 0,
            "native_experiments_admitted": 0,
            "measured_headroom": 0,
        },
        "tasks": cases,
        "next_owner": "benchmarks.harness.oracle_reviews",
        "data_classification": (
            "SANITIZED_STATUS_ONLY_NO_SOURCE_PATHS_NO_REVIEW_RATIONALES"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--suite", type=Path, default=None)
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    from benchmarks.dogfood.hashmarks_blind_review import STAGED_RELATIVE
    try:
        root = args.repository_root.resolve()
        staged = root / (args.suite or STAGED_RELATIVE)
        if args.responses and args.responses.resolve().is_relative_to(root):
            raise ReturnError("private reviewer returns must remain outside repository")
        if args.output:
            output = args.output.resolve()
            if output.is_relative_to(root / "benchmarks"):
                raise ReturnError("review status must not overwrite benchmark sources")
            if args.responses and output == args.responses.resolve():
                raise ReturnError("review status must not overwrite private returns")
        packet = prepare_blind_review(staged=staged, repository_root=root)
        report = project_returns(
            packet, _read_json(args.responses) if args.responses else None
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
