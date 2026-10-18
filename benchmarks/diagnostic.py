"""Freeze a separate ten-replicate diagnostic suite from qualified source tasks."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.harness.campaign_authority import CampaignAuthorityError, read_campaign
from benchmarks.harness.identity import canonical_json, digest
from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.suite import load_suite


class DiagnosticError(ValueError):
    pass


_SCORE_REPORT_FIELDS = (
    "expected_trials",
    "observed_trials",
    "status_counts",
    "campaign_qualification",
    "conditions",
    "paired_assistance",
    "paired_assistance_summary",
    "paired_assistance_exclusions",
    "expected_assistance_pairs",
    "stability",
    "diagnostics",
    "agent_profiles",
    "cross_agent_observations",
    "task_agent_authority",
)


def _selected_tasks(score: dict[str, Any], explicit: set[str]) -> set[str]:
    tasks = set(explicit)
    for language in score.get("languages", {}).values():
        for row in language.get("stability", []):
            if row.get("state") in {"unstable", "execution-unstable", "not-gradeable"}:
                tasks.add(str(row["task_id"]))
        for row in language.get("diagnostics", []):
            if row.get("primary") in {"timeout", "runtime", "agent-terminal"} or (
                row.get("flags") or {}
            ).get("output_contract"):
                tasks.add(str(row["task_id"]))
    return tasks


def _copy_artifact(source_root: Path, destination: Path, relative: str) -> None:
    source = (source_root / relative).resolve()
    try:
        source.relative_to(source_root.resolve())
    except ValueError as exc:
        raise DiagnosticError(f"artifact escapes source suite: {relative}") from exc
    if not source.is_file():
        raise DiagnosticError(f"missing source artifact: {relative}")
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def prepare_diagnostic_suite(
    *,
    source_suite: Path,
    source_results: Path,
    score_path: Path,
    destination: Path,
    include_tasks: set[str],
) -> dict[str, Any]:
    suite = load_suite(source_suite)
    if not all("replicate_ids" in row for row in suite.experiment["conditions"]):
        raise DiagnosticError("diagnostic source suite needs replicate identities")
    try:
        score = json.loads(score_path.read_text(encoding="utf-8"))
        campaign = read_campaign(source_results)
    except (OSError, ValueError, CampaignAuthorityError) as exc:
        raise DiagnosticError(
            "diagnostic source score or campaign cannot be read"
        ) from exc
    qualification = (
        score.get("campaign_qualification") if isinstance(score, dict) else None
    )
    if (
        not isinstance(score, dict)
        or score.get("schema") != "agents-cookbook-heldout-observer-outcomes.v7"
        or score.get("projection_mode") != "live"
        or not isinstance(qualification, dict)
        or qualification.get("status") != "QUALIFIED"
    ):
        raise DiagnosticError(
            "diagnostic selection requires a qualified live official score"
        )
    selection = score.get("selection")
    agents = selection.get("agents") if isinstance(selection, dict) else None
    if (
        not isinstance(agents, list)
        or not agents
        or any(not isinstance(agent, str) for agent in agents)
        or len(agents) != len(set(agents))
        or not set(agents).issubset(suite.agents)
    ):
        raise DiagnosticError("diagnostic score has invalid selected agents")
    languages = score.get("languages")
    if not isinstance(languages, dict) or set(languages) != {"python", "typescript"}:
        raise DiagnosticError("diagnostic score has incomplete language coverage")
    conditions = {row["id"]: row for row in suite.experiment["conditions"]}
    totals = {"expected_trials": 0, "observed_trials": 0}
    for language in ("python", "typescript"):
        task_ids = sorted(
            task_id
            for task_id, task in suite.tasks.items()
            if str(task.get("family", "")).startswith(language + "-")
        )
        selected = {
            str(row["definition_id"])
            for row in suite.trial_definitions()
            if row["task_id"] in task_ids
            and conditions[row["condition_id"]]["agent"] in agents
        }
        try:
            report = build_report(
                suite=suite,
                results_root=source_results,
                selected_definitions=selected,
                require_complete=True,
            )
        except ReportError as exc:
            raise DiagnosticError(f"source campaign is not qualified: {exc}") from exc
        source_language = languages[language]
        if (
            report["campaign_qualification"]["status"] != "QUALIFIED"
            or not isinstance(source_language, dict)
            or set(source_language) != {"task_ids", *_SCORE_REPORT_FIELDS}
            or source_language.get("task_ids") != task_ids
            or any(
                source_language.get(field) != report[field]
                for field in _SCORE_REPORT_FIELDS
            )
        ):
            raise DiagnosticError("score disagrees with verified source campaign")
        totals["expected_trials"] += report["expected_trials"]
        totals["observed_trials"] += report["observed_trials"]
    if any(score.get(field) != value for field, value in totals.items()):
        raise DiagnosticError("score trial totals differ from source campaign")
    if qualification.get("languages") != {
        language: languages[language]["campaign_qualification"]
        for language in ("python", "typescript")
    }:
        raise DiagnosticError(
            "score qualification summary differs from source campaign"
        )
    selected = _selected_tasks(score, include_tasks)
    if not selected or not selected.issubset(suite.tasks):
        raise DiagnosticError("diagnostic selection is empty or has unknown tasks")
    destination = destination.resolve()
    if destination.exists():
        raise DiagnosticError("diagnostic suite destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".diagnostic-suite-", dir=destination.parent)
    )
    try:
        experiment = dict(suite.experiment)
        experiment["id"] = str(experiment["id"]) + "-diagnostic-10"
        experiment["version"] = int(experiment["version"]) + 1
        experiment["tasks"] = sorted(selected)
        experiment["conditions"] = [
            {**condition, "trials": 10, "replicate_ids": list(range(7001, 7011))}
            for condition in experiment["conditions"]
        ]
        experiment["scoring"] = {
            **experiment["scoring"],
            "id": str(experiment["scoring"]["id"]) + "-diagnostic",
        }
        (temporary / "experiment.json").write_bytes(canonical_json(experiment))
        for kind, values in (
            ("tasks", {key: suite.tasks[key] for key in selected}),
            ("agents", suite.agents),
            ("subjects", suite.subjects),
        ):
            (temporary / kind).mkdir()
            for key, value in values.items():
                (temporary / kind / f"{key}.json").write_bytes(canonical_json(value))
        for task_id in selected:
            task = suite.tasks[task_id]
            if task.get("mutation"):
                _copy_artifact(suite.root, temporary, task["mutation"]["artifact"])
            for fixture in task.get("fixtures", []):
                _copy_artifact(suite.root, temporary, fixture["artifact"])
        source_reviews = suite.root / "qualification" / "oracle-reviews.json"
        if source_reviews.is_file():
            reviews = json.loads(source_reviews.read_text(encoding="utf-8"))
            reviews["tasks"] = {
                task_id: reviews["tasks"][task_id] for task_id in selected
            }
            (temporary / "qualification").mkdir()
            (temporary / "qualification" / "oracle-reviews.json").write_bytes(
                canonical_json(reviews)
            )
        evidence = {
            "source_suite": str(suite.root),
            "source_campaign_id": campaign["campaign_id"],
            "source_score_sha256": digest(score),
            "selected_tasks": sorted(selected),
            "purpose": "diagnostic-only; excluded from heldout score",
        }
        (temporary / "diagnostic-source.json").write_bytes(canonical_json(evidence))
        load_suite(temporary)
        os.rename(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return evidence
