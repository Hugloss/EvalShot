"""Diagnostic report with separate semantic and answer-format accounting."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.harness.report import ReportError, _receipts, build_report
from benchmarks.harness.selection import (
    SelectionError,
    parse_agent_arguments,
    select_scoring_definitions,
)
from benchmarks.harness.suite import load_suite


def _bare_headroom(
    report: dict[str, Any],
    *,
    bare_condition_ids: set[str],
    answer_contract: dict[str, Any],
) -> dict[str, Any]:
    summaries = [
        answer_contract["conditions"][condition_id]
        for condition_id in sorted(bare_condition_ids)
    ]
    valid = sum(row["semantic_gradeable"] for row in summaries)
    passes = sum(row["semantic_passes"] for row in summaries)
    failures = sum(row["semantic_failures"] for row in summaries)
    unresolved = sum(row["trials"] - row["semantic_gradeable"] for row in summaries)
    per_task = []
    for task_id, conditions in sorted(answer_contract.get("tasks", {}).items()):
        for condition_id in sorted(bare_condition_ids):
            row = conditions.get(condition_id)
            if not isinstance(row, dict):
                continue
            repeated = (
                row["semantic_gradeable"] >= 3
                and row["semantic_failures"] >= 2
            )
            per_task.append({
                "task_id": task_id,
                "condition_id": condition_id,
                "valid": row["semantic_gradeable"],
                "failures": row["semantic_failures"],
                "repeated_failure": repeated,
            })
    repeated_count = sum(row["repeated_failure"] for row in per_task)
    if not valid:
        state = "unavailable"
    elif failures:
        state = "observed"
    else:
        state = "not-observed"
    return {
        "state": state,
        "valid_trials": valid,
        "passes": passes,
        "failures": failures,
        "unresolved_trials": unresolved,
        "headroom_trials": failures,
        "headroom_rate": failures / valid if valid else None,
        "per_task_bare_headroom": per_task,
        "repeated_failure_task_conditions": repeated_count,
        "reproducibility_state": (
            "unavailable" if not valid
            else "observed" if repeated_count
            else "not-demonstrated"
        ),
        "interpretation": (
            "bare control has observed failures; inspect repeated task-level evidence separately"
            if state == "observed"
            else "bare control solved every valid diagnostic trial; this corpus does not yet demonstrate headroom"
            if state == "not-observed"
            else "bare-control headroom cannot be assessed without valid outcomes"
        ),
    }


def _answer_contract(results: Path, selected: set[str]) -> dict[str, Any]:
    """Count format and semantic outcomes from verified, selected receipts."""
    by_condition: dict[str, dict[str, Any]] = {}
    by_task: dict[str, dict[str, dict[str, int]]] = {}
    seen: set[str] = set()
    for receipt in _receipts(results):
        definition = receipt.get("definition_id")
        if definition not in selected:
            continue
        if definition in seen:
            raise ReportError(f"duplicate selected definition: {definition}")
        seen.add(definition)
        condition = str(receipt["condition"]["id"])
        row = by_condition.setdefault(
            condition,
            {
                "trials": 0,
                "semantic_gradeable": 0,
                "semantic_passes": 0,
                "semantic_failures": 0,
                "format_compliant": 0,
                "answer_shapes": {},
            },
        )
        rubric = receipt.get("scoring", {}).get("oracle_grade", {}).get("rubric")
        if not isinstance(rubric, dict):
            raise ReportError(f"missing answer rubric: {definition}")
        shape = rubric.get("answer_shape")
        gradeable = rubric.get("semantic_gradeable")
        compliant = rubric.get("format_compliant")
        success = rubric.get("semantic_success")
        if (
            not isinstance(shape, str)
            or not isinstance(gradeable, bool)
            or not isinstance(compliant, bool)
            or (gradeable and not isinstance(success, bool))
            or (not gradeable and success is not None)
        ):
            raise ReportError(f"invalid answer rubric: {definition}")
        task_id = str(receipt["task_id"])
        task_row = by_task.setdefault(task_id, {}).setdefault(condition, {
            "trials": 0,
            "semantic_gradeable": 0,
            "semantic_failures": 0,
        })
        task_row["trials"] += 1
        task_row["semantic_gradeable"] += gradeable
        task_row["semantic_failures"] += success is False
        row["trials"] += 1
        row["semantic_gradeable"] += gradeable
        row["semantic_passes"] += success is True
        row["semantic_failures"] += success is False
        row["format_compliant"] += compliant
        row["answer_shapes"][shape] = row["answer_shapes"].get(shape, 0) + 1
    if seen != selected:
        raise ReportError(f"missing selected answer receipts: {len(selected - seen)}")
    return {
        "conditions": by_condition,
        "tasks": by_task,
        "totals": {
            key: sum(row[key] for row in by_condition.values())
            for key in (
                "trials",
                "semantic_gradeable",
                "semantic_passes",
                "semantic_failures",
                "format_compliant",
            )
        },
    }


def score(
    *,
    suite_root: Path,
    results: Path,
    agents: tuple[str, ...],
    definition_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    suite = load_suite(suite_root)
    if not agents:
        raise ValueError("select at least one frozen native agent")
    unknown = sorted(set(agents) - set(suite.agents))
    if unknown:
        raise ValueError("unknown benchmark agent(s): " + ", ".join(unknown))

    conditions = {
        str(condition["id"]): condition for condition in suite.experiment["conditions"]
    }
    try:
        selected_rows = select_scoring_definitions(
            suite,
            agents=agents,
            definition_ids=definition_ids,
        )
    except SelectionError as exc:
        raise ValueError(str(exc)) from exc
    selected = {str(row["definition_id"]) for row in selected_rows}
    report = build_report(
        suite=suite,
        results_root=results,
        selected_definitions=selected,
        require_complete=True,
        selection={
            "tasks": [],
            "agents": sorted(agents),
            "subjects": [],
            "condition": None,
            "bare_control_included": True,
        },
    )
    answer_contract = _answer_contract(results, selected)
    selected_condition_ids = {str(row["condition_id"]) for row in selected_rows}
    bare_condition_ids = {
        condition_id
        for condition_id in selected_condition_ids
        if conditions[condition_id]["subject"] == "none"
    }
    return {
        "schema": "agents-cookbook-repository-intelligence-headroom.v4",
        "diagnostic_only": True,
        "selection": {
            "agents": sorted(agents),
            "definition_count": len(selected),
            "definition_ids": sorted(selected),
        },
        "expected_trials": report["expected_trials"],
        "observed_trials": report["observed_trials"],
        "status_counts": report["status_counts"],
        "campaign_qualification": report["campaign_qualification"],
        "answer_contract": answer_contract,
        "bare_control_headroom": _bare_headroom(
            report,
            bare_condition_ids=bare_condition_ids,
            answer_contract=answer_contract,
        ),
        "bare_stability": [
            row for row in report["stability"] if row["subject_id"] == "none"
        ],
        "subject_adoption": report["subject_adoption"],
        "paired_assistance_summary": report["paired_assistance_summary"],
        "paired_assistance_usage_summary": report.get(
            "paired_assistance_usage_summary",
            [],
        ),
        "paired_assistance_exclusions": report["paired_assistance_exclusions"],
        "task_assistance_evidence": report.get("task_assistance_evidence", []),
        "conditions": report["conditions"],
        "agent_profiles": report["agent_profiles"],
        "diagnostics": report["diagnostics"],
        "authority": {
            "release_authority": False,
            "heldout_replacement": False,
            "overall_winner": None,
            "comparison": "within-agent paired diagnostic only",
            "headroom_is_observed_not_assumed": True,
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
