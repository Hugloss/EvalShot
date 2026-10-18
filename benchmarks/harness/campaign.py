"""Resumable campaign identities, paths, and status."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bundle import verify_bundle
from .campaign_authority import (
    CampaignAuthorityError,
    authority_epoch_projection,
    read_campaign,
    read_interrupted_attempts,
    read_launch_details,
)
from .identity import definition_id, execution_id
from .receipt import is_complete_receipt
from .report import ReportError, validate_comparability
from .subject_exposure import (
    subject_exposure_issues,
    subject_exposure_qualification,
)
from .suite import SuiteDefinition


class CampaignError(ValueError):
    pass


@dataclass(frozen=True)
class TrialSpec:
    experiment: dict[str, Any]
    task: dict[str, Any]
    condition: dict[str, Any]
    trial: int
    seed: int
    subject_identity: dict[str, Any]
    agent_identity: dict[str, Any]
    oracle_identity: dict[str, Any]
    harness_identity: dict[str, Any]
    environment_identity: dict[str, Any]
    mutation_identity: dict[str, Any] | None = None

    @property
    def definition_id(self) -> str:
        return definition_id(
            experiment=self.experiment,
            task=self.task,
            condition=self.condition,
            trial=self.trial,
            seed=self.seed,
        )

    @property
    def id(self) -> str:
        return execution_id(
            definition=self.definition_id,
            subject_identity=self.subject_identity,
            agent_identity=self.agent_identity,
            oracle_identity=self.oracle_identity,
            harness_identity=self.harness_identity,
            environment_identity=self.environment_identity,
            mutation_identity=self.mutation_identity,
        )


@dataclass(frozen=True)
class CampaignPaths:
    root: Path | None
    cache: Path | None
    work: Path | None
    results: Path | None
    run_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "root": str(self.root) if self.root is not None else None,
            "cache": str(self.cache) if self.cache is not None else None,
            "work": str(self.work) if self.work is not None else None,
            "results": str(self.results) if self.results is not None else None,
            "run_id": self.run_id,
        }


def pending(specs: list[TrialSpec], results_root: Path) -> list[TrialSpec]:
    return [spec for spec in specs if not is_complete_receipt(results_root / spec.id)]


def resolve_campaign_paths(
    *,
    root: Path | None,
    cache: Path | None,
    work: Path | None,
    results: Path | None,
    need_execution: bool,
    results_optional: bool = False,
) -> CampaignPaths:
    if root is not None:
        if any(value is not None for value in (cache, work, results)):
            raise CampaignError(
                "--root cannot be combined with --cache, --work, or --results"
            )
        root = root.resolve()
        return CampaignPaths(
            root=root,
            cache=root / "cache" if need_execution else None,
            work=root / "work" if need_execution else None,
            results=root / "results",
        )

    if results is None and not results_optional:
        raise CampaignError("provide --root or --results")
    if need_execution and (cache is None or work is None):
        raise CampaignError("execution requires --root or both --cache and --work")
    return CampaignPaths(
        root=None,
        cache=cache.resolve() if cache is not None else None,
        work=work.resolve() if work is not None else None,
        results=results.resolve() if results is not None else None,
    )


def campaign_status(
    *,
    suite: SuiteDefinition,
    results_root: Path,
    selected_definitions: set[str],
    active_definition: str | None = None,
) -> dict[str, Any]:
    all_rows = suite.trial_definitions()
    all_definitions = {str(row["definition_id"]): row for row in all_rows}
    definitions = {
        key: row for key, row in all_definitions.items() if key in selected_definitions
    }
    if set(definitions) != selected_definitions:
        raise CampaignError(
            "status selection contains definitions outside frozen suite"
        )
    new_contract = any("replicate_id" in row for row in definitions.values())
    backend = "native"
    campaign_error = None
    claims: set[str] = set()
    launch_claims: dict[str, str] = {}
    launch_details: dict[str, dict[str, Any]] = {}
    interrupted_attempts: dict[str, list[dict[str, Any]]] = {}
    authority_epoch_rows: list[dict[str, Any]] = []
    if new_contract:
        try:
            manifest = read_campaign(results_root)
            backend = str(manifest.get("backend", "native"))
            if not selected_definitions.issubset(set(manifest["selected_definitions"])):
                raise CampaignAuthorityError(
                    "status selection exceeds campaign selection"
                )
            launch_details = read_launch_details(results_root, manifest["campaign_id"])
            launch_claims = {
                definition: claim["trial_id"]
                for definition, claim in launch_details.items()
            }
            interrupted_attempts = read_interrupted_attempts(
                results_root,
                manifest["campaign_id"],
            )
            selected_pairs = {
                (str(row["task_id"]), str(row["condition_id"]))
                for row in definitions.values()
            }
            epoch_projection = authority_epoch_projection(
                results_root=results_root,
                campaign_id=manifest["campaign_id"],
                selected_pairs=selected_pairs,
            )
            authority_epoch_rows = list(epoch_projection["authority_epochs"])
            claims = set(launch_claims)
            if not claims.issubset(set(manifest["selected_definitions"])):
                raise CampaignAuthorityError("launch claim exceeds campaign selection")
            if not set(interrupted_attempts).issubset(set(manifest["selected_definitions"])):
                raise CampaignAuthorityError("interruption attempt exceeds campaign selection")
            for definition, claim in launch_details.items():
                if claim["attempt"] != len(interrupted_attempts.get(definition, [])) + 1:
                    raise CampaignAuthorityError(
                        f"launch attempt lineage is invalid: {definition}"
                    )
        except CampaignAuthorityError as exc:
            campaign_error = str(exc)

    receipts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    corrupt: list[dict[str, str]] = []
    foreign: list[dict[str, str]] = []
    if results_root.exists():
        for directory in sorted(
            path for path in results_root.iterdir() if path.is_dir()
        ):
            if directory.name.startswith("."):
                continue
            valid, reason = verify_bundle(directory)
            if not valid:
                corrupt.append({"directory": str(directory), "reason": str(reason)})
                continue
            value = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            definition = value.get("definition_id")
            if not isinstance(definition, str):
                corrupt.append(
                    {
                        "directory": str(directory),
                        "reason": "verified bundle has no definition_id",
                    }
                )
                continue
            if definition in definitions:
                if backend == "harbor":
                    expected = definitions[definition]
                    if any(
                        value.get(field) != expected.get(expected_field)
                        for field, expected_field in (
                            ("task_id", "task_id"),
                            ("condition_id", "condition_id"),
                            ("trial", "trial"),
                            ("replicate_id", "replicate_id"),
                        )
                    ):
                        corrupt.append(
                            {
                                "directory": str(directory),
                                "reason": "Harbor receipt row differs from frozen definition",
                            }
                        )
                        continue
                if (
                    new_contract
                    and campaign_error is None
                    and value.get("execution", {}).get("campaign_id")
                    != manifest["campaign_id"]
                ):
                    corrupt.append(
                        {
                            "directory": str(directory),
                            "reason": "receipt campaign authority mismatch",
                        }
                    )
                    continue
                if (
                    new_contract
                    and campaign_error is None
                    and launch_claims.get(definition) != value.get("trial_id")
                ):
                    corrupt.append(
                        {
                            "directory": str(directory),
                            "reason": "receipt has no matching launch claim",
                        }
                    )
                    continue
                receipts[definition].append(value)
            elif definition not in all_definitions:
                foreign.append(
                    {
                        "directory": str(directory),
                        "definition_id": definition,
                    }
                )

    rows: list[dict[str, Any]] = []
    state_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    for definition, row in definitions.items():
        found = receipts.get(definition, [])
        if not found:
            state = (
                "RUNNING" if definition == active_definition else "INTERRUPTED"
            ) if definition in claims else "PENDING"
            outcome = None
            trial_ids: list[str] = []
            diagnostic = None
        elif len(found) == 1:
            state = "COMPLETE"
            outcome = str(found[0].get("status"))
            outcome_counts[outcome] += 1
            trial_ids = [str(found[0].get("trial_id"))]
            source_diagnostic = found[0].get("diagnostic")
            diagnostic = (
                {
                    "stage": source_diagnostic.get("stage"),
                    "reason_code": source_diagnostic.get("reason_code"),
                }
                if isinstance(source_diagnostic, dict)
                else None
            )
        else:
            state = "CONFLICT"
            outcome = None
            trial_ids = sorted(str(value.get("trial_id")) for value in found)
            diagnostic = None
        state_counts[state] += 1
        rows.append(
            {
                "definition_id": definition,
                "task_id": row["task_id"],
                "condition_id": row["condition_id"],
                "trial": row["trial"],
                "state": state,
                "outcome": outcome,
                "trial_ids": trial_ids,
                "diagnostic": diagnostic,
                "interrupted_attempts": len(
                    interrupted_attempts.get(definition, [])
                ),
            }
        )

    completed_receipts = [values[0] for values in receipts.values() if len(values) == 1]
    comparability_error = None
    if backend != "harbor":
        try:
            validate_comparability(completed_receipts)
        except ReportError as exc:
            comparability_error = str(exc)

    valid_outcomes = {"PASS", "FAIL", "NO_QUALIFYING_DEFECT"}
    unresolved_outcomes = sum(
        count
        for outcome, count in outcome_counts.items()
        if outcome not in valid_outcomes
    )
    integrity_ok = (
        not corrupt and not foreign and state_counts["CONFLICT"] == 0
        and campaign_error is None
    )
    completeness_ok = state_counts["COMPLETE"] == len(definitions)
    exposure_qualification = (
        {
            "status": "NOT_APPLICABLE",
            "qualified": True,
            "policy": "Harbor reward does not certify subject invocation",
            "conditions": [],
            "failed_conditions": 0,
            "pending_conditions": 0,
        }
        if backend == "harbor"
        else subject_exposure_qualification(
            suite=suite,
            expected_rows=definitions.values(),
            receipts=completed_receipts,
        )
    )
    evidence_tainted = bool(authority_epoch_rows)
    qualification_ok = (
        integrity_ok
        and completeness_ok
        and unresolved_outcomes == 0
        and comparability_error is None
        and exposure_qualification["qualified"]
        and not evidence_tainted
    )

    return {
        "expected_trials": len(definitions),
        "complete_trials": state_counts["COMPLETE"],
        "pending_trials": state_counts["PENDING"],
        "interrupted_trials": state_counts["INTERRUPTED"],
        "running_trials": state_counts["RUNNING"],
        "conflicting_trials": state_counts["CONFLICT"],
        "outcomes": dict(sorted(outcome_counts.items())),
        "corrupt_bundles": corrupt,
        "foreign_bundles": foreign,
        "complete": (
            state_counts["COMPLETE"] == len(definitions)
            and not corrupt
            and state_counts["CONFLICT"] == 0
            and state_counts["INTERRUPTED"] == 0
            and state_counts["RUNNING"] == 0
            and campaign_error is None
        ),
        "qualified": qualification_ok,
        "unresolved_outcome_trials": unresolved_outcomes,
        "evidence_tainted": evidence_tainted,
        "authority_epoch_transitions": len(authority_epoch_rows),
        "authority_epochs": authority_epoch_rows,
        "subject_exposure_qualification": exposure_qualification,
        "recovered_interruption_attempts": sum(
            len(values)
            for definition, values in interrupted_attempts.items()
            if definition in selected_definitions
        ),
        "health": {
            "integrity": "PASS" if integrity_ok else "FAIL",
            "completeness": completeness_ok,
            "qualification": qualification_ok,
            "issues": [
                *([campaign_error] if campaign_error else []),
                *[f"corrupt bundle: {item['directory']}: {item['reason']}" for item in corrupt],
                *[f"foreign bundle: {item['directory']}" for item in foreign],
                *[f"conflicting receipts: {item['definition_id']}" for item in rows if item["state"] == "CONFLICT"],
                *([comparability_error] if comparability_error else []),
                *(
                    [
                        "participant authority changed across "
                        f"{len(authority_epoch_rows)} immutable epoch "
                        f"{'transition' if len(authority_epoch_rows) == 1 else 'transitions'}; "
                        "evidence remains preserved but cannot qualify as one stable campaign"
                    ]
                    if authority_epoch_rows
                    else []
                ),
                *subject_exposure_issues(exposure_qualification),
            ],
        },
        "comparability_error": comparability_error,
        "campaign_authority_error": campaign_error,
        "rows": sorted(
            rows,
            key=lambda value: (
                str(value["task_id"]),
                str(value["condition_id"]),
                int(value["trial"]),
            ),
        ),
    }
