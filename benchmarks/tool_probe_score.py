"""Descriptive scoring for separate required-tool diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.harness.report import build_report
from benchmarks.harness.suite import load_suite
from benchmarks.harness.trace_diagnostics import build_trace_diagnostics
from benchmarks.harness.subject_exposure import exposure_probe_required_tool
from benchmarks.tool_routing import (
    first_native_discovery,
    required_before_native_discovery,
    required_call_result,
)

def smoke_gate(
    score: dict[str, object],
    *,
    subject: str,
    expected_trials: int = 3,
    required_tool: str | None = None,
) -> None:
    if score.get("schema") != "agents-cookbook-tool-probe-score.v3":
        raise ValueError("tool-probe smoke score v3 is required")
    if score.get("subject") != subject:
        raise ValueError("smoke score subject differs from selection")
    observed_required_tool = score.get("required_tool")
    if not isinstance(observed_required_tool, str) or not observed_required_tool:
        raise ValueError("smoke score has no required tool")
    if required_tool is not None and observed_required_tool != required_tool:
        raise ValueError("smoke score required tool differs from selection")
    rows = score.get("required_tool_results")
    if not isinstance(rows, list) or len(rows) != expected_trials or (
        score.get("expected_trials") != expected_trials or
        score.get("observed_trials") != expected_trials
    ):
        raise ValueError("smoke requires exactly three observed trials")
    if len({row.get("trial_id") for row in rows}) != expected_trials or len({
        row.get("task_id") for row in rows
    }) != 1 or any(
        not isinstance(row.get("trial_id"), str) or not row["trial_id"] or
        not isinstance(row.get("task_id"), str) or not row["task_id"]
        for row in rows
    ):
        raise ValueError("smoke trial identities or task selection are inconsistent")
    for row in rows:
        if row.get("status") not in {"PASS", "FAIL"} or row.get("required_call_succeeded") is not True or (
            row.get("required_before_native_discovery") is not True
        ):
            raise ValueError(
                "smoke requires completed nonempty subject calls before native "
                "repository discovery in all trials"
            )


def main(suite_root: Path) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agent", action="append", required=True)
    parser.add_argument("--definition-id", action="append", default=[])
    args = parser.parse_args()
    suite = load_suite(suite_root)
    if args.agent != ["opencode-native"]:
        parser.error("tool-probe score requires only opencode-native")
    selected = set(args.definition_id) or {
        str(row["definition_id"]) for row in suite.trial_definitions()
    }
    report = build_report(
        suite=suite, results_root=args.results, selected_definitions=selected,
        require_complete=True,
    )
    traces = build_trace_diagnostics(args.results)
    subject = suite.experiment["conditions"][0]["subject"]
    required = exposure_probe_required_tool(suite, subject)
    rows = []
    for trial in traces["trials"]:
        if trial["definition_id"] not in selected:
            continue
        calls = trial["calls"]
        trace_observed = calls is not None
        attempted, successful = required_call_result(
            calls,
            subject=subject,
            required_tool=required,
        )
        first_class, first_tool = first_native_discovery(calls)
        rows.append({
            "trial_id": trial["trial_id"],
            "task_id": trial["task_id"],
            "status": trial["status"],
            "required_tool": required,
            "trace_observed": trace_observed,
            "required_call_attempted": attempted,
            "required_call_succeeded": successful,
            "required_before_native_discovery": required_before_native_discovery(
                calls,
                subject=subject,
                required_tool=required,
            ),
            "first_native_discovery_class": first_class,
            "first_native_discovery_tool": first_tool,
        })
    score = {
        "schema": "agents-cookbook-tool-probe-score.v3",
        "authority": {"diagnostic_only": True, "heldout_comparable": False},
        "subject": subject,
        "required_tool": required,
        "expected_trials": report["expected_trials"],
        "observed_trials": report["observed_trials"],
        "semantic_status_counts": report["status_counts"],
        "required_tool_results": sorted(rows, key=lambda row: row["trial_id"]),
        "required_call_successes": sum(row["required_call_succeeded"] is True for row in rows),
        "required_call_failures": sum(row["required_call_succeeded"] is False for row in rows),
        "required_call_unknown": sum(row["required_call_succeeded"] is None for row in rows),
        "required_before_native_discovery_successes": sum(
            row["required_before_native_discovery"] is True for row in rows
        ),
        "required_before_native_discovery_failures": sum(
            row["required_before_native_discovery"] is False for row in rows
        ),
        "required_before_native_discovery_unknown": sum(
            row["required_before_native_discovery"] is None for row in rows
        ),
    }
    args.output.write_text(json.dumps(score, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0
