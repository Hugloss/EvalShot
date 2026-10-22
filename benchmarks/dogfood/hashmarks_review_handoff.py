"""Independently reviewable packets; never evaluator admission or oracle approval.

Inputs are the existing immutable source audit and its deterministic coverage
projection. We recompute the latter rather than accepting a modified queue.
This module produces only requests for independent evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from benchmarks.dogfood.hashmarks_coverage import build_coverage
from benchmarks.harness.identity import digest

SCHEMA = "evalshot.hashmarks-dogfood-review-handoff.v1"
_MAX_INPUT_BYTES = 4_000_000

# Requirements belong to an independent reviewer, not to an automatic scorer.
_COMMON_REQUIREMENTS = (
    "independent_source_and_scope_review",
    "independent_oracle_and_ambiguity_review",
    "native_control_headroom_evidence",
    "frozen_prompt_oracle_and_contamination_check",
    "runtime_comparability_and_delivery_qualification",
)

_KIND_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "DORMANT_TASK_NEEDS_REVIEW": (
        "resolve_historical_source_discrepancy_without_rewriting_evidence",
    ),
    "CROSS_SUITE_SEMANTIC_VARIANT": (
        "reconcile_competing_task_meanings_without_promoting_an_oracle",
    ),
    "UNASSIGNED_CASE_FOR_TRIAGE": (
        "establish_task_eligibility_and_distinction_from_assigned_cases",
    ) + _COMMON_REQUIREMENTS,
    "FAMILY_WITHOUT_ASSIGNED_TASK": (
        "independently_design_a_concrete_task_before_oracle_review",
    ) + _COMMON_REQUIREMENTS,
    "SINGLE_ASSIGNED_TASK_FAMILY": (
        "establish_a_new_nonduplicated_behavior_boundary",
    ) + _COMMON_REQUIREMENTS,
}


class HandoffError(ValueError):
    """The existing audit and coverage cannot support this review packet."""


def _source_rows(audit: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    records = audit.get("tasks")
    if not isinstance(records, list):
        raise HandoffError("source audit tasks missing")
    rows: dict[tuple[str, str], Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise HandoffError("source audit task record is not an object")
        key = (record.get("suite"), record.get("task"))
        if not all(isinstance(value, str) for value in key) or key in rows:
            raise HandoffError("invalid or duplicated source audit task identity")
        rows[key] = record
    return rows


def build_handoff(audit: Mapping[str, Any], coverage: Mapping[str, Any]) -> dict[str, Any]:
    """Bind reviewer requests to frozen facts, without making review decisions."""
    try:
        expected = build_coverage(audit)
    except (ValueError, TypeError, KeyError) as exc:
        raise HandoffError("invalid source audit for review handoff") from exc
    if not isinstance(coverage, Mapping) or dict(coverage) != expected:
        raise HandoffError("coverage does not match its exact source audit")
    rows = _source_rows(audit)
    packets: list[dict[str, Any]] = []

    for candidate in expected["candidate_review_queue"]:
        kind = candidate["kind"]
        requirements = _KIND_REQUIREMENTS.get(kind)
        if requirements is None:
            raise HandoffError("unknown candidate review category")
        suite, task = candidate["suite"], candidate["task"]

        if suite is not None and task is not None:
            matched = [rows[(suite, task)]]
        elif task is not None:
            # Cross-suite divergence is bound to all corresponding frozen
            # definitions, not a handpicked "winning" implementation.
            matched = [
                record for record in rows.values()
                if record["task"] == task and record["commit"] == candidate["commit"]
            ]
        else:
            # Family-level suggestions describe coverage, not a task oracle.
            matched = [
                record for record in rows.values()
                if record["family"] == candidate["family"]
            ]
        matched.sort(key=lambda r: (r["suite"], r["task"]))
        if not matched:
            raise HandoffError("candidate has no corresponding frozen source evidence")

        references = [
            {
                "suite": record["suite"],
                "task": record["task"],
                "commit": record["commit"],
                "membership": record["experiment_membership"],
                "source": record["source"],
                "source_problems": sorted(set(record["problems"])),
                "semantic_task_digest": record.get("task_semantic_digest"),
                "review_transcription": record["review"],
            }
            for record in matched
        ]

        packets.append({
            "packet_id": "sha256:" + digest({
                "source_audit_identity": expected["source_audit_identity"],
                "candidate_id": candidate["id"],
            }),
            "candidate_id": candidate["id"],
            "kind": kind,
            "family": candidate["family"],
            "review_status": "REQUEST_ONLY",
            "experiment_ready": False,
            "headroom_status": "UNKNOWN_NOT_MEASURED",
            "oracle_status": "NOT_ESTABLISHED",
            "source_evidence": references,
            "independent_evidence_required": [
                {"evidence": requirement, "status": "NOT_PROVIDED"}
                for requirement in requirements
            ],
            "reviewer_instruction": (
                "Inspect pinned sources independently; do not expose oracle "
                "answers or review rationales to the agent. Preserve ambiguity "
                "and negative findings. Return any proposed experiments to "
                "EvalShot's existing independent admission and grading owners."
            ),
        })

    if len({row["packet_id"] for row in packets}) != len(packets):
        raise HandoffError("non-unique review packet identities")
    return {
        "schema": SCHEMA,
        "qualification": "REVIEW_REQUESTS_ONLY",
        "source_audit_identity": expected["source_audit_identity"],
        "coverage_identity": "sha256:" + digest(expected),
        "source_audit_gate_passed": expected["source_audit_passed"],
        "summary": {
            "review_requests": len(packets),
            "experiment_ready": 0,
            "headroom_measured": 0,
            "independent_oracles_established": 0,
        },
        "review_packets": packets,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise HandoffError("duplicate input JSON key")
    return result


def _read_input(path: Path) -> dict[str, Any]:
    if path.stat().st_size > _MAX_INPUT_BYTES:
        raise HandoffError("review handoff input exceeds 4,000,000 bytes")
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(HandoffError("non-finite JSON value")),
    )
    if not isinstance(value, dict):
        raise HandoffError("review handoff input must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.resolve() in {
            args.audit.resolve(), args.coverage.resolve()
        }:
            raise HandoffError("review packet output cannot overwrite an input")
        handoff = build_handoff(_read_input(args.audit), _read_input(args.coverage))
    except (OSError, ValueError, UnicodeError, RecursionError, TypeError) as exc:
        parser.error(str(exc))
    report = json.dumps(handoff, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
