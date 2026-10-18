"""Descriptive score projection for context-invariance-v1."""

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
    report = build_report(
        suite=suite,
        results_root=results,
        selected_definitions=selected,
        require_complete=True,
    )
    return {
        "schema": "agents-cookbook-context-invariance-outcomes.v1",
        "selection": {
            "agents": sorted(agents),
            "definition_count": len(selected),
            "definition_ids": sorted(selected),
        },
        "expected_trials": report["expected_trials"],
        "observed_trials": report["observed_trials"],
        "campaign_qualification": report["campaign_qualification"],
        "analysis_evidence": report["analysis_evidence"],
        "context_invariance_summary": report["context_invariance_summary"],
        "stability": report["stability"],
        "authority": {
            "overall_winner": None,
            "release_authority": False,
            "comparison": "within-agent-subject paired contexts",
            "cross_agent_ranking": False,
            "unknown_policy": "unknown-not-zero",
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
