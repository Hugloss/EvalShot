"""Score complete multidomain agent trials without a release verdict."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from benchmarks.evidence import load_cases
from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.selection import (
    SelectionError,
    parse_agent_arguments,
    select_scoring_definitions,
)
from benchmarks.harness.suite import load_suite


FAMILIES = ("logs", "splunk", "dependencies", "semantics", "identities", "code_owners")


def score(
    *,
    suite_root: Path,
    results: Path,
    agents: tuple[str, ...],
    definition_ids: tuple[str, ...] = (),
) -> dict:
    suite = load_suite(suite_root)
    evidence_cases = load_cases(suite_root / "evidence.json")
    reviewed = sum(case["review"]["state"] == "approved" for case in evidence_cases)
    if not agents or set(agents) - set(suite.agents):
        raise ValueError("select one or both frozen native agents explicitly")
    try:
        selected_rows = select_scoring_definitions(
            suite,
            agents=agents,
            definition_ids=definition_ids,
        )
    except SelectionError as exc:
        raise ValueError(str(exc)) from exc
    all_selected = {str(row["definition_id"]) for row in selected_rows}
    whole = build_report(
        suite=suite,
        results_root=results,
        selected_definitions=all_selected,
        require_complete=True,
    )
    families = {}
    for family in FAMILIES:
        family_rows = [
            row
            for row in selected_rows
            if suite.tasks[str(row["task_id"])].get("family") == family
        ]
        if not family_rows:
            continue
        selected = {str(row["definition_id"]) for row in family_rows}
        report = build_report(
            suite=suite,
            results_root=results,
            selected_definitions=selected,
            require_complete=True,
        )
        families[family] = {
            "task_ids": sorted({str(row["task_id"]) for row in family_rows}),
            "expected_trials": report["expected_trials"],
            "observed_trials": report["observed_trials"],
            "status_counts": report["status_counts"],
            "conditions": report["conditions"],
            "paired_assistance": report["paired_assistance"],
        }
    valid_outcomes = {"PASS", "FAIL", "NO_QUALIFYING_DEFECT"}
    complete_outcomes = all(
        set(row["status_counts"]).issubset(valid_outcomes) for row in families.values()
    )
    return {
        "schema": "agents-cookbook-multidomain-observer-outcomes.v1",
        "selection": {
            "agents": sorted(agents),
            "definition_count": len(all_selected),
            "definition_ids": sorted(all_selected),
        },
        "expected_trials": whole["expected_trials"],
        "observed_trials": whole["observed_trials"],
        "families": families,
        "authority": {
            "overall_winner": None,
            "release_authority": False,
            "corpus_review_state": "approved" if reviewed == 60 else "draft",
            "reviewed_cases": reviewed,
            "complete_valid_outcomes": complete_outcomes,
            "paired_assistance_scope": "within-agent-valid-outcomes-only",
            "cross_agent_comparison": "descriptive-only",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agent", action="append", required=True)
    parser.add_argument("--definition-id", action="append", default=[])
    args = parser.parse_args()
    try:
        agents = parse_agent_arguments(args.agent)
        payload = score(
            suite_root=Path(__file__).resolve().parent,
            results=args.results,
            agents=agents,
            definition_ids=tuple(args.definition_id),
        )
    except (SelectionError, ValueError, ReportError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{args.output.name}.",
        dir=args.output.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
