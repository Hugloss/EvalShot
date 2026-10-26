"""Descriptive released-Hashmarks outcomes from existing verified reports."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.selection import parse_agent_arguments
from benchmarks.harness.suite import load_suite


def score(
    *,
    suite_root: Path,
    results: Path,
    agents: tuple[str, ...],
    definition_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    suite = load_suite(suite_root)
    if len(agents) != len(suite.agents) or set(agents) != set(suite.agents):
        raise ValueError("release-pair scoring requires both frozen runtime arms")
    selected = {str(row["definition_id"]) for row in suite.trial_definitions()}
    if definition_ids and (
        len(set(definition_ids)) != len(definition_ids)
        or set(definition_ids) != selected
    ):
        raise ValueError("release-pair scoring requires all six frozen assignments")
    report = build_report(
        suite=suite,
        results_root=results,
        selected_definitions=selected,
        require_complete=True,
    )
    return {
        "schema": "evalshot.oh-goon-hashmarks-release-pair.v1",
        "selection": {"agents": sorted(agents), "definition_ids": sorted(selected)},
        "expected_trials": report["expected_trials"],
        "observed_trials": report["observed_trials"],
        "status_counts": report["status_counts"],
        "conditions": report["conditions"],
        "diagnostics": report["diagnostics"],
        "campaign_qualification": report["campaign_qualification"],
        "runtime_arm_observations": report["cross_agent_observations"],
        "authority": {
            "comparison": "matched released-Hashmarks runtime arms; descriptive only",
            "repository_evidence": "mandatory Hashmarks in both arms; no bare control",
            "overall_winner": None,
            "release_authority": False,
            "causal_quality_effect_proven": False,
            "unknown_policy": "unknown-not-zero",
            "model_sampling_seed_proven": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
    except (ValueError, ReportError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{args.output.name}.", dir=args.output.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
