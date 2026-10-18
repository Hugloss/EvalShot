"""Read-only score projections over verified benchmark execution bundles."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.adapters.oracles import (
    RepositoryLocationOracle,
    score_repository_location,
)
from benchmarks.adapters.registry import build_oracle
from benchmarks.harness.bundle import verify_bundle
from benchmarks.harness.campaign_authority import (
    CampaignAuthorityError,
    read_launch_claims,
    read_campaign,
)
from benchmarks.harness.identity import (
    EXECUTION_EVIDENCE_CONTRACT,
    REPLICATE_EVIDENCE_CONTRACT,
    REPLICATE_SCORE_CONTRACT,
    canonical_json,
    definition_id,
    execution_task_contract,
    score_projection_id,
)
from benchmarks.harness.suite import SuiteDefinition


class RegradeError(ValueError):
    pass


def _verified_receipt(directory: Path) -> tuple[dict[str, Any], str]:
    valid, reason = verify_bundle(directory)
    if not valid:
        raise RegradeError(f"invalid source bundle {directory}: {reason}")
    payload = (directory / "result.json").read_bytes()
    return json.loads(payload), hashlib.sha256(payload).hexdigest()


def _location_projection(
    receipt: dict[str, Any], oracle: RepositoryLocationOracle
) -> dict[str, Any]:
    execution = receipt.get("execution", {})
    observed = execution.get("location_observation")
    contract = execution.get("evidence_contract")
    if contract not in {EXECUTION_EVIDENCE_CONTRACT, REPLICATE_EVIDENCE_CONTRACT}:
        raise RegradeError("source receipt lacks current execution evidence")
    if not isinstance(observed, dict):
        raise RegradeError("source receipt lacks frozen location observation")
    policy = oracle.identity().provenance["normalization_policy"]
    if observed.get("normalization_policy") != policy:
        raise RegradeError("source normalization policy differs; run a new trial")
    expected = oracle.expected
    expected_path = expected.get("path")
    expected_symbol = expected.get("symbol")
    if (
        set(expected) != {"path", "symbol"}
        or not isinstance(expected_path, str)
        or not expected_path
        or Path(expected_path).is_absolute()
        or Path(expected_path).as_posix() != expected_path
        or ".." in Path(expected_path).parts
        or not isinstance(expected_symbol, str)
        or not expected_symbol
        or "." in expected_symbol
    ):
        raise RegradeError("current repository-location oracle truth is not canonical")
    declared = dataclasses.asdict(oracle.identity())
    evidence_identity = execution["evidence_identity"]
    return {
        "execution_evidence_id": evidence_identity,
        "projection_identity": score_projection_id(
            execution_evidence=evidence_identity,
            oracle_identity=declared,
            **(
                {"contract": REPLICATE_SCORE_CONTRACT}
                if contract == REPLICATE_EVIDENCE_CONTRACT
                else {}
            ),
        ),
        "oracle": declared,
        "oracle_grade": score_repository_location(observed, expected=oracle.expected),
    }


def regrade_repository_location_bundle(
    bundle: Path, oracle: RepositoryLocationOracle
) -> dict[str, Any]:
    """Regrade one sealed bundle without reading a former trial workspace."""
    receipt, _digest = _verified_receipt(bundle)
    return _location_projection(receipt, oracle)


def project_campaign_receipts(
    *,
    suite: SuiteDefinition,
    source_results: Path,
    selected_definitions: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Map a complete compatible source campaign to current score definitions."""
    if not source_results.is_dir():
        raise RegradeError(f"source results directory is missing: {source_results}")
    conditions = {
        str(row["id"]): suite.expanded_condition(row)
        for row in suite.experiment["conditions"]
    }
    all_definitions = suite.trial_definitions()
    known = {
        (
            str(row["task_id"]),
            str(row["condition_id"]),
            row["trial"],
            row.get("replicate_id", row.get("seed")),
        )
        for row in all_definitions
    }
    expected = {
        (
            str(row["task_id"]),
            str(row["condition_id"]),
            row["trial"],
            row.get("replicate_id", row.get("seed")),
        ): row
        for row in all_definitions
        if row["definition_id"] in selected_definitions
    }
    if len(expected) != len(selected_definitions):
        raise RegradeError("regrade selection is outside the current suite")
    projected: list[dict[str, Any]] = []
    lineage: list[dict[str, str]] = []
    found: set[tuple[str, str, int, int]] = set()
    source_campaign = None
    source_claims: dict[str, str] = {}
    for bundle in sorted(source_results.iterdir()):
        if not bundle.is_dir() or bundle.name.startswith("."):
            continue
        source, source_sha = _verified_receipt(bundle)
        if (
            source.get("execution", {}).get("evidence_contract")
            == REPLICATE_EVIDENCE_CONTRACT
        ):
            if source_campaign is None:
                try:
                    source_campaign = read_campaign(source_results)
                    source_claims = read_launch_claims(
                        source_results, source_campaign["campaign_id"]
                    )
                    if not set(source_claims).issubset(
                        set(source_campaign["selected_definitions"])
                    ):
                        raise RegradeError(
                            "source launch claim exceeds campaign selection"
                        )
                except CampaignAuthorityError as exc:
                    raise RegradeError(str(exc)) from exc
            if (
                source.get("execution", {}).get("campaign_id")
                != source_campaign["campaign_id"]
                or source.get("definition_id")
                not in source_campaign["selected_definitions"]
            ):
                raise RegradeError(
                    "source receipt is outside frozen campaign authority"
                )
            if source_claims.get(source.get("definition_id")) != source.get("trial_id"):
                raise RegradeError("source receipt has no matching launch claim")
        task = source.get("task", {})
        condition = source.get("condition", {})
        execution = source.get("execution", {})
        experiment = source.get("experiment", {})
        if (
            experiment != suite.experiment
            or task.get("id") not in suite.tasks
            or condition.get("id") not in conditions
        ):
            raise RegradeError(f"foreign source receipt: {bundle}")
        key = (
            task["id"],
            condition["id"],
            execution.get("trial_index"),
            execution.get("replicate_id", execution.get("seed")),
        )
        if source.get("definition_id") != definition_id(
            experiment=experiment,
            task=task,
            condition=condition,
            trial=key[2],
            **({"seed": key[3]} if "seed" in execution else {"replicate_id": key[3]}),
        ):
            raise RegradeError(f"source definition identity mismatch: {bundle}")
        if key not in known:
            raise RegradeError(f"foreign source trial: {bundle}")
        if key not in expected:
            continue
        if key in found:
            raise RegradeError(f"duplicate source execution for {key}")
        found.add(key)
        current_task = suite.tasks[task["id"]]
        if (
            execution_task_contract(task) != execution_task_contract(current_task)
            or condition != conditions[condition["id"]]
        ):
            raise RegradeError(f"source execution inputs changed for {key}")
        if source.get("status") not in {"PASS", "FAIL", "NO_QUALIFYING_DEFECT"}:
            raise RegradeError(f"source execution is not qualified for {key}")
        if execution.get("evidence_contract") not in {
            EXECUTION_EVIDENCE_CONTRACT,
            REPLICATE_EVIDENCE_CONTRACT,
        }:
            raise RegradeError(f"source execution lacks current evidence for {key}")

        oracle = build_oracle(
            current_task["oracle"],
            timeout_seconds=int(current_task["budgets"]["timeout_seconds"]),
            suite_root=suite.root,
        )
        current = copy.deepcopy(source)
        current["experiment"] = suite.experiment
        current["task"] = current_task
        current["condition"] = conditions[condition["id"]]
        current["definition_id"] = expected[key]["definition_id"]
        if isinstance(oracle, RepositoryLocationOracle):
            if source["status"] not in {"PASS", "FAIL"}:
                raise RegradeError(f"unexpected localization outcome for {key}")
            projection = _location_projection(source, oracle)
            current["authority"]["oracle"] = {
                "declared": projection["oracle"],
                "healthy": True,
            }
            current["scoring"] = {
                "projection_identity": projection["projection_identity"],
                "oracle_grade": projection["oracle_grade"],
            }
            current["status"] = (
                "PASS" if projection["oracle_grade"]["passed"] else "FAIL"
            )
            current["reason"] = projection["oracle_grade"]["reason"]
        elif canonical_json(
            source["authority"]["oracle"]["declared"]
        ) != canonical_json(dataclasses.asdict(oracle.identity())):
            raise RegradeError(f"non-localization oracle changed for {key}")

        projected.append(current)
        lineage.append(
            {
                "source_trial_id": source["trial_id"],
                "source_result_sha256": source_sha,
                "current_definition_id": current["definition_id"],
                "projection_identity": current["scoring"]["projection_identity"],
            }
        )
    missing = set(expected) - found
    if missing:
        raise RegradeError(
            f"source campaign is incomplete: {len(missing)} trial(s) missing"
        )
    return projected, sorted(lineage, key=lambda row: row["current_definition_id"])
