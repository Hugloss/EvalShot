"""Score behavioral-v3 without producing a release verdict."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.selection import (
    SelectionError,
    parse_agent_arguments,
    select_scoring_definitions,
)
from benchmarks.harness.suite import load_suite

FAMILIES = ("post_change", "change_impact", "verification", "dependency_delta")


def score(
    *,
    suite_root: Path,
    results: Path,
    agents: tuple[str, ...],
    definition_ids: tuple[str, ...] = (),
) -> dict:
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

    selected_all = {
        str(row["definition_id"])
        for row in selected_rows
    }
    whole = build_report(
        suite=suite,
        results_root=results,
        selected_definitions=selected_all,
        require_complete=True,
    )
    families = {}
    for family in FAMILIES:
        selected_family_rows = [
            row
            for row in selected_rows
            if suite.tasks[str(row["task_id"])].get("family") == family
        ]
        if not selected_family_rows:
            continue
        selected = {
            str(row["definition_id"])
            for row in selected_family_rows
        }
        report = build_report(
            suite=suite,
            results_root=results,
            selected_definitions=selected,
            require_complete=True,
        )
        families[family] = {
            "task_ids": sorted(
                {str(row["task_id"]) for row in selected_family_rows}
            ),
            "expected_trials": report["expected_trials"],
            "observed_trials": report["observed_trials"],
            "status_counts": report["status_counts"],
            "conditions": report["conditions"],
            "paired_assistance": report["paired_assistance"],
        }
    return {
        "schema": "agents-cookbook-behavioral-outcomes.v1",
        "selection": {
            "agents": sorted(agents),
            "definition_count": len(selected_all),
            "definition_ids": sorted(selected_all),
        },
        "expected_trials": whole["expected_trials"],
        "observed_trials": whole["observed_trials"],
        "families": families,
        "authority": {
            "overall_winner": None,
            "release_authority": False,
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
