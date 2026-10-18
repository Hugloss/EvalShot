"""Report complete held-out trials separately for Python and TypeScript."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from benchmarks.harness.regrade import RegradeError, project_campaign_receipts
from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.selection import (
    SelectionError,
    parse_agent_arguments,
    select_scoring_definitions,
)
from benchmarks.harness.suite import load_suite


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--results", type=Path)
    source.add_argument("--regrade-source-results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agent", action="append", required=True)
    parser.add_argument("--definition-id", action="append", default=[])
    args = parser.parse_args()
    if args.regrade_source_results is not None:
        try:
            args.output.resolve().relative_to(args.regrade_source_results.resolve())
        except ValueError:
            pass
        else:
            parser.error("regraded score output must be outside source results")
    suite = load_suite(Path(__file__).resolve().parent)
    try:
        agents = parse_agent_arguments(args.agent)
    except SelectionError as exc:
        raise SystemExit(str(exc)) from exc
    unknown = sorted(set(agents) - set(suite.agents))
    if unknown:
        raise SystemExit("unknown benchmark agent(s): " + ", ".join(unknown))
    selected_agents = set(agents)
    conditions = {
        str(condition["id"]): condition for condition in suite.experiment["conditions"]
    }
    try:
        selected_rows = select_scoring_definitions(
            suite,
            agents=agents,
            definition_ids=tuple(args.definition_id),
        )
    except SelectionError as exc:
        raise SystemExit(str(exc)) from exc
    selected_definition_ids = {
        str(row["definition_id"])
        for row in selected_rows
    }
    languages = {}
    all_lineage = []
    for language in ("python", "typescript"):
        task_ids = {
            task_id
            for task_id, task in suite.tasks.items()
            if str(task.get("family", "")).startswith(language + "-")
        }
        selected = {
            str(row["definition_id"])
            for row in selected_rows
            if row["task_id"] in task_ids
        }
        if not selected:
            continue
        selected_task_ids = {
            str(row["task_id"])
            for row in selected_rows
            if str(row["definition_id"]) in selected
        }
        try:
            projected = None
            if args.regrade_source_results is not None:
                projected, lineage = project_campaign_receipts(
                    suite=suite,
                    source_results=args.regrade_source_results,
                    selected_definitions=selected,
                )
                all_lineage.extend(lineage)
            report = build_report(
                suite=suite,
                results_root=args.results or args.regrade_source_results,
                selected_definitions=selected,
                require_complete=True,
                projected_receipts=projected,
            )
        except (RegradeError, ReportError) as exc:
            raise SystemExit(f"heldout score unavailable: {exc}") from exc
        languages[language] = {
            "task_ids": sorted(selected_task_ids),
            "expected_trials": report["expected_trials"],
            "observed_trials": report["observed_trials"],
            "status_counts": report["status_counts"],
            "campaign_qualification": report["campaign_qualification"],
            "decision_summary": report.get("decision_summary", {}),
            "conditions": report["conditions"],
            "paired_assistance": report["paired_assistance"],
            "paired_assistance_summary": report["paired_assistance_summary"],
            "paired_assistance_usage_summary": report.get(
                "paired_assistance_usage_summary",
                [],
            ),
            "paired_assistance_treatment_summary": report.get(
                "paired_assistance_treatment_summary",
                [],
            ),
            "paired_assistance_exclusions": report["paired_assistance_exclusions"],
            "expected_assistance_pairs": report["expected_assistance_pairs"],
            "task_assistance_evidence": report.get(
                "task_assistance_evidence",
                [],
            ),
            "stability": report["stability"],
            "task_agent_authority": report["task_agent_authority"],
            "subject_adoption": report["subject_adoption"],
            "diagnostics": report["diagnostics"],
            "agent_profiles": report["agent_profiles"],
            "cross_agent_observations": report["cross_agent_observations"],
        }
    if not languages:
        raise SystemExit("heldout score selection contains no language tasks")

    payload = {
        "schema": "agents-cookbook-heldout-observer-outcomes.v20",
        "projection_mode": (
            "offline-regrade" if args.regrade_source_results is not None else "live"
        ),
        "expected_trials": sum(row["expected_trials"] for row in languages.values()),
        "observed_trials": sum(row["observed_trials"] for row in languages.values()),
        "languages": languages,
        "selection": {
            "agents": sorted(agents),
            "definition_count": len(selected_definition_ids),
            "definition_ids": sorted(selected_definition_ids),
        },
        "campaign_qualification": {
            "status": (
                "QUALIFIED"
                if all(
                    row["campaign_qualification"]["status"] == "QUALIFIED"
                    for row in languages.values()
                )
                else "NOT_QUALIFIED"
            ),
            "languages": {
                language: row["campaign_qualification"]
                for language, row in sorted(languages.items())
            },
        },
        "decision_summary": {
            "languages": {
                language: row.get("decision_summary", {})
                for language, row in sorted(languages.items())
            },
            "claim_guardrails": {
                "overall_winner": "not-permitted",
                "cross_language_ranking": "not-performed",
                "subject_effect_attribution": (
                    "exact-required-operation-with-successful-nonempty-result"
                ),
                "generic_subject_invocation": "routing-and-adoption-descriptive-only",
            },
        },
        "authority": {
            "overall_winner": None,
            "cross_agent_comparison": (
                "descriptive-only"
                if len(agents) > 1
                else "not-applicable-single-agent-selection"
            ),
            "assistance_comparison": "within-agent-paired-trials",
        },
    }
    if args.regrade_source_results is not None:
        payload["source_lineage"] = sorted(
            all_lineage, key=lambda row: row["current_definition_id"]
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{args.output.name}.", dir=args.output.parent
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
