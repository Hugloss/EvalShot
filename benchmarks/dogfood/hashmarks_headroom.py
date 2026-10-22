"""Fail-closed baseline-headroom eligibility for frozen Hashmarks dogfood.

Read-only: this never executes models, scores historical trials, adjudicates
oracles, or promotes a distinct-prompt task using another task's outcomes.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from benchmarks.dogfood.hashmarks_coverage import build_coverage
from benchmarks.dogfood.hashmarks_review_handoff import _read_input
from benchmarks.harness.identity import digest

SCHEMA = "evalshot.hashmarks-dogfood-headroom-readiness.v1"
HISTORICAL_SCHEMA = "evalshot.native-evidence-pilot-execution-summary.v1"

def _historical_pilot(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    if summary is None:
        return {
            "status": "NOT_PROVIDED", "pilot_id": None, "task_ids": [],
            "observed_trials": None, "qualified_outcomes": None,
            "headroom_evidence": "UNKNOWN",
        }
    if summary.get("schema") != HISTORICAL_SCHEMA:
        raise ValueError("unsupported historical pilot summary schema")
    qualified = summary.get("pilot_measurement_qualified")
    if type(qualified) is not bool:
        raise ValueError("historical pilot qualification must be an explicit boolean")
    records = summary.get("trials")
    if not isinstance(records, list) or any(
        not isinstance(row, Mapping) or not isinstance(row.get("task_id"), str)
        for row in records
    ):
        raise ValueError("malformed historical pilot trial roster")
    expected, observed = summary.get("expected_trials"), summary.get("observed_trials")
    if (
        type(expected) is not int or type(observed) is not int
        or expected < 0 or observed < 0 or observed != len(records)
    ):
        raise ValueError("historical pilot trial count does not match roster")
    task_ids = sorted({row["task_id"] for row in records})
    return {
        "status": (
            "QUALIFIED_CLAIM_REQUIRES_CANONICAL_REPORT"
            if qualified else "UNQUALIFIED_EXECUTION_DIAGNOSTIC_ONLY"
        ),
        "pilot_id": summary.get("campaign_id"),
        "task_ids": task_ids,
        "observed_trials": observed,
        # A standalone aggregate summary is not a task-level qualified
        # baseline source, irrespective of its self-declared boolean.
        "qualified_outcomes": None,
        "headroom_evidence": "UNKNOWN",
    }


def build_readiness(
    audit: Mapping[str, Any],
    *,
    historical_pilot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Preserve task meaning, source identity and unsupported headroom claims."""
    build_coverage(audit)  # canonical audit/shape validation
    pilot = _historical_pilot(historical_pilot)
    rows = audit["tasks"]
    assigned: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["experiment_membership"] != "ASSIGNED":
            continue
        path, symbol, commit, tree = (
            row.get("oracle_path"), row.get("oracle_symbol"),
            row.get("commit"), row.get("tree"),
        )
        if not all(isinstance(x, str) and x for x in (path, symbol, commit, tree)):
            continue
        assigned[(commit, tree, path, symbol)].append(row)

    observations: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: (r["suite"], r["task"])):
        if row["experiment_membership"] != "UNASSIGNED":
            continue
        commit, tree = row.get("commit"), row.get("tree")
        path, symbol = row.get("oracle_path"), row.get("oracle_symbol")
        key = (commit, tree, path, symbol)
        source_ok = row["source"] == "VERIFIED_PATH" and not row["problems"]
        analogous = (
            assigned.get(key, [])
            if source_ok and all(isinstance(x, str) and x for x in key)
            else []
        )
        references = [{
            "suite": reference["suite"],
            "task": reference["task"],
            "task_semantic_digest": reference.get("task_semantic_digest"),
            "source_status": reference["source"],
            "source_problems": sorted(reference["problems"]),
            "relation": "SAME_PINNED_EXPECTATION_NOT_EQUIVALENT_TASK",
            "same_task_identity": (
                row.get("task_semantic_digest") is not None
                and reference.get("task_semantic_digest") is not None
                and row["task_semantic_digest"] == reference["task_semantic_digest"]
            ),
            "historical_pilot_task_present": reference["task"] in pilot["task_ids"],
        } for reference in sorted(analogous, key=lambda x: (x["suite"], x["task"]))]

        if not source_ok:
            state = "SOURCE_PRECONDITION_FAILED"
        elif references:
            state = "RELATED_ASSIGNED_TARGET_NOT_BASELINE_EVIDENCE"
        else:
            state = "NO_ASSIGNED_SAME_EXPECTATION_ANALOG"
        observations.append({
            "suite": row["suite"],
            "task": row["task"],
            "family": row["family"],
            "commit": commit,
            "tree": tree,
            "task_semantic_digest": row.get("task_semantic_digest"),
            "candidate_source": row["source"],
            "source_problems": sorted(row["problems"]),
            "state": state,
            "related_assigned_expectations": references,
            "candidate_has_qualified_native_baseline": False,
            "native_baseline_status": "UNKNOWN_NOT_MEASURED_FOR_EXACT_TASK",
            "headroom_status": "UNKNOWN",
            "agent_tool_effectiveness": "NOT_ESTABLISHED",
            "oracle_independently_approved": False,
            "next_independent_action": (
                "Review the frozen source discrepancy before any headroom study"
                if not source_ok else
                "Qualify independent oracle and run an exact-task native control "
                "under EvalShot campaign admission before estimating headroom"
            ),
        })
    return {
        "schema": SCHEMA,
        "qualification": "DESCRIPTIVE_READINESS_ONLY",
        "source_audit_identity": "sha256:" + digest(audit),
        "historical_pilot": pilot,
        "summary": {
            "candidate_definitions": len(observations),
            "source_blocked": sum(r["state"] == "SOURCE_PRECONDITION_FAILED" for r in observations),
            "same_expectation_analogs": sum(
                r["state"] == "RELATED_ASSIGNED_TARGET_NOT_BASELINE_EVIDENCE"
                for r in observations
            ),
            "qualified_exact_task_baselines": 0,
            "headroom_established": 0,
            "effectiveness_claims": 0,
        },
        "candidates": observations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--historical-pilot", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = [args.audit, *([args.historical_pilot] if args.historical_pilot else [])]
        if args.output and args.output.resolve() in {path.resolve() for path in inputs}:
            raise ValueError("output must not overwrite input evidence")
        report = build_readiness(
            _read_input(args.audit),
            historical_pilot=(
                _read_input(args.historical_pilot)
                if args.historical_pilot else None
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        parser.error(str(exc))
    content = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
