"""Discovery-only score for the frozen eighteen-assignment native pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.harness.availability_study import (
    build_availability_study,
    inspect_native_trace,
)
from benchmarks.harness.campaign_authority import CampaignAuthorityError
from benchmarks.harness.report import _receipts, build_report
from benchmarks.harness.selection import parse_agent_arguments
from benchmarks.harness.suite import load_suite
from benchmarks.harness.treatment_attribution import contracted_treatment_evidence


SCHEMA = "evalshot.native-evidence-discovery-pilot.v1"
BASELINE = "none-codex-native"
AVAILABLE = "hashmarks-codex-native"
REQUIRED = "hashmarks-codex-required"
AGENTS = {"codex-native", "codex-required"}


def score(
    *, suite_root: Path, results: Path, agents: tuple[str, ...],
    definition_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    suite = load_suite(suite_root)
    expected = suite.trial_definitions()
    selected = {row["definition_id"] for row in expected}
    if set(agents) != AGENTS or len(agents) != len(AGENTS):
        raise ValueError("pilot score requires both native and instructed-policy configurations")
    if definition_ids and (
        len(set(definition_ids)) != len(definition_ids) or set(definition_ids) != selected
    ):
        raise ValueError("pilot score population must retain all eighteen frozen assignments")
    report = build_report(
        suite=suite, results_root=results, require_complete=False,
        selected_definitions=selected,
    )
    availability = build_availability_study(
        suite=suite, results_root=results,
        baseline_condition=BASELINE, available_condition=AVAILABLE,
    )
    receipts = {row["definition_id"]: row for row in _receipts(results)}
    diagnostic = []
    for assignment in expected:
        if assignment["condition_id"] != REQUIRED:
            continue
        receipt = receipts.get(assignment["definition_id"])
        observation = inspect_native_trace(
            "", adapter="codex", subject="hashmarks", terminal_complete=False,
        )
        issues = []
        success = None
        if receipt is None:
            issues.append("missing-assigned-trial")
        else:
            execution = receipt["execution"]
            if receipt.get("authority", {}).get("subject", {}).get("available") is not True:
                issues.append("subject-availability-unproven")
            if receipt.get("measurements", {}).get("agent", {}).get("subject_tool_configured") is not True:
                issues.append("subject-configuration-unproven")
            trace = execution.get("artifacts", {}).get("agent_trace")
            if isinstance(trace, dict):
                terminal = execution.get("agent_terminal")
                observation = inspect_native_trace(
                    (results / receipt["trial_id"] / trace["path"]).read_text(encoding="utf-8"),
                    adapter="codex", subject="hashmarks",
                    terminal_complete=isinstance(terminal, dict) and terminal.get("type") == "turn.completed",
                )
            grade = receipt.get("scoring", {}).get("oracle_grade", {})
            if receipt.get("status") in {"PASS", "FAIL"} and grade.get("valid") is not False:
                success = grade.get("semantic_success", grade.get("passed"))
                success = success if type(success) is bool else None
            if observation["trace_state"] != "COMPLETE_MCP_TELEMETRY":
                issues.append("native-mcp-telemetry-incomplete")
            if observation["final_answer"] is None or observation["final_answer"] != execution.get("agent_answer"):
                issues.append("native-answer-receipt-join-unproven")
        if success is None:
            issues.append("oracle-outcome-unknown")
        diagnostic.append({
            **assignment, "success": success,
            **{key: value for key, value in observation.items() if key != "final_answer"},
            "required_operation_evidence": contracted_treatment_evidence(receipt or {}),
            "issues": issues,
        })
    qualified = (
        availability["study_measurement_qualified"]
        and all(not row["issues"] for row in diagnostic)
        and not report["campaign_qualification"]["evidence_tainted"]
    )
    return {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "pilot_measurement_qualified": qualified,
        "protocol_sha256": hashlib.sha256((suite_root / "protocol.json").read_bytes()).hexdigest(),
        "frozen_execution_order": suite.experiment["execution_order"],
        "actual_launch_order_state": "NOT_INDEPENDENTLY_AUDITED",
        "expected_trials": len(expected),
        "observed_trials": report["observed_trials"],
        "status_counts": report["status_counts"],
        "cost_measurement_scope": {
            "agent_duration_ms": "native agent execution only",
            "admission_phase_timings": "retained in receipt measurements.admission; total includes its component phases",
            "full_trial_wall_time": "NOT_ESTABLISHED; publication and cleanup are outside recorded phase totals",
        },
        "availability_study": availability,
        "instructed_policy_diagnostic": {
            "assigned_trials": len(diagnostic), "trials": diagnostic,
            "claim_scope": "explicit-invocation-policy-descriptive-only",
            "isolated_evidence_information_effect": "NOT_ESTABLISHED",
            "model_request_inclusion": "UNKNOWN",
        },
        "conditions": report["conditions"],
        "diagnostics": report["diagnostics"],
        "historical_subject_use_qualification": report["campaign_qualification"],
        "authority": {
            "release_authority": False, "heldout_confirmation": False,
            "current_codex_headroom_assumed": False,
            "overall_winner": None, "causal_effect_proven": False,
            "noninvokers_retained_in_primary_comparison": True,
            "replicates_are_rng_seeds": False,
            "random_assignment_proven": False,
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
            results=args.results, agents=parse_agent_arguments(args.agent),
            definition_ids=tuple(args.definition_id),
        )
    except (OSError, ValueError, CampaignAuthorityError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=args.output.parent)
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
