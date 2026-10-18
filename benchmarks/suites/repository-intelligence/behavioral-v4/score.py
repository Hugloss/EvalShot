"""Lexigram report for behavioral-v4."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.selection import (
    SelectionError,
    parse_agent_arguments,
    select_scoring_definitions,
)
from benchmarks.harness.suite import load_suite

FAMILIES = (
    "post_change",
    "change_impact",
    "verification",
    "dependency_delta",
    "correlation",
    "declarations",
    "freshness",
    "negative_bounds",
)
LEXIGRAM = (
    (
        "admissibility",
        "ADMISSIBLE",
        "The trial has a valid sealed receipt and comparable authority.",
    ),
    ("authority", "BOUNDED", "No claim exceeds the admitted evidence."),
    (
        "resolution",
        "COMPLETE",
        "All required semantic claims and edited bytes are correct.",
    ),
    (
        "evidence",
        "PRESERVED",
        "Freshness, provenance, ambiguity, and reuse facts survive.",
    ),
    (
        "paired_benefit",
        "RESCUED",
        "The assisted run succeeds where its paired bare run fails.",
    ),
)


def _paired_grade(row: dict[str, Any]) -> str:
    bare = row["bare_status"] == "PASS"
    assisted = row["assisted_status"] == "PASS"
    if not assisted:
        return "REGRESSED" if bare else "NO_OBSERVED_GAIN"
    if not bare:
        return "RESCUED"
    if row.get("tool_calls_delta") is not None and row["tool_calls_delta"] < 0:
        return "LEANER"
    return "NO_OBSERVED_GAIN"


def _lexigram(report: dict[str, Any]) -> dict[str, Any]:
    statuses = report["status_counts"]
    pairs = report["paired_assistance"]
    labels = [_paired_grade(row) for row in pairs]
    grades = [
        {
            "dimension": "admissibility",
            "grade": "ADMISSIBLE"
            if report["observed_trials"] == report["expected_trials"]
            else "UNCOMPARABLE",
            "meaning": LEXIGRAM[0][2],
        },
        {
            "dimension": "authority",
            "grade": "BOUNDED"
            if not statuses.get("INVALID") and not statuses.get("CONTAMINATED")
            else "UNSAFE",
            "meaning": LEXIGRAM[1][2],
        },
        {
            "dimension": "resolution",
            "grade": "COMPLETE" if not statuses.get("FAIL") else "PARTIAL",
            "meaning": LEXIGRAM[2][2],
        },
        {
            "dimension": "evidence",
            "grade": "PRESERVED" if not statuses.get("INVALID") else "THIN",
            "meaning": LEXIGRAM[3][2],
        },
        {
            "dimension": "paired_benefit",
            "grade": "RESCUED"
            if "RESCUED" in labels
            else "LEANER"
            if "LEANER" in labels
            else "NO_OBSERVED_GAIN",
            "meaning": LEXIGRAM[4][2],
            "observations": labels,
        },
    ]
    return {
        "ordering": [row[0] for row in LEXIGRAM],
        "grades": grades,
        "policy": "lexicographic-words; unknown-is-not-zero; hard-authority-is-non-compensatory",
    }


def score(
    *,
    suite_root: Path,
    results: Path,
    agents: tuple[str, ...],
    definition_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    suite = load_suite(suite_root)
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
    selected = {str(row["definition_id"]) for row in selected_rows}
    whole = build_report(
        suite=suite,
        results_root=results,
        selected_definitions=selected,
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
        family_selected = {str(row["definition_id"]) for row in family_rows}
        family_report = build_report(
            suite=suite,
            results_root=results,
            selected_definitions=family_selected,
            require_complete=True,
        )
        families[family] = {
            "task_ids": sorted({str(row["task_id"]) for row in family_rows}),
            "expected_trials": family_report["expected_trials"],
            "observed_trials": family_report["observed_trials"],
            "status_counts": family_report["status_counts"],
            "paired_assistance": family_report["paired_assistance"],
            "lexigram": _lexigram(family_report),
        }
    return {
        "schema": "agents-cookbook-behavioral-outcomes.v2",
        "selection": {
            "agents": sorted(agents),
            "definition_count": len(selected),
            "definition_ids": sorted(selected),
        },
        "expected_trials": whole["expected_trials"],
        "observed_trials": whole["observed_trials"],
        "families": families,
        "lexigram": _lexigram(whole),
        "authority": {
            "overall_winner": None,
            "release_authority": False,
            "comparison": "lexicographic descriptive grades only",
            "numeric_aggregate": None,
            "unknown_policy": "unknown-not-zero",
            "hard_authority_veto": True,
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
        payload = score(
            suite_root=Path(__file__).resolve().parent,
            results=args.results,
            agents=parse_agent_arguments(args.agent),
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
