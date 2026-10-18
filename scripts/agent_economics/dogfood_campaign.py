from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from .agent_outcome_benchmark import BenchmarkError, Outcome, compare, load_outcomes


class DogfoodCampaignError(BenchmarkError):
    pass


SCHEMA = "agent-economics-dogfood-campaign"
RESULT_SCHEMA = "agent-economics-dogfood-campaign-result"
_TASK_FIELDS = {
    "task_id",
    "treatment_id",
    "scenario_id",
    "repository_id",
    "measurement_contract_id",
    "baseline_session_id",
    "bridge_session_id",
    "baseline_isolation_evidence_id",
    "bridge_isolation_evidence_id",
    "baseline_freeze_receipt_id",
    "bridge_freeze_receipt_id",
    "baseline_oracle_access_receipt_id",
    "bridge_oracle_access_receipt_id",
}
_ROOT_FIELDS = {
    "schema",
    "campaign_id",
    "implementation_id",
    "corpus_id",
    "measurement_contract_id",
    "scenario_requirements",
    "minimum_distinct_repositories",
    "tasks",
    "consumer_cleanup_evidence",
    "producer_qualification_authority_id",
    "producer_qualification_evidence_id",
    "producer_qualification_implementation_id",
}
_SCENARIO_REQUIREMENT_FIELDS = {"scenario_id", "required_pair_count"}


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DogfoodCampaignError(f"{field} must be a non-empty string")
    return value.strip()


def _required_positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DogfoodCampaignError(f"{field} must be an integer >= 1")
    return value


def _campaign_identity(value: Mapping[str, object]) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def load_campaign(path: Path) -> dict[str, object]:
    if path.stat().st_size > 1_000_000:
        raise DogfoodCampaignError("campaign manifest exceeds 1,000,000 byte bound")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DogfoodCampaignError("invalid UTF-8 JSON campaign manifest") from exc
    if not isinstance(value, dict):
        raise DogfoodCampaignError("campaign manifest root must be an object")
    return value


def _paired(outcomes: Sequence[Outcome]) -> dict[tuple[str, str], dict[str, Outcome]]:
    pairs: dict[tuple[str, str], dict[str, Outcome]] = {}
    for outcome in outcomes:
        key = (outcome.task_id, outcome.treatment_id)
        pairs.setdefault(key, {})[outcome.mode] = outcome
    return pairs


def _scenario_requirements(
    raw_requirements: object,
) -> dict[str, int]:
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise DogfoodCampaignError("scenario_requirements must be a non-empty list")
    requirements: dict[str, int] = {}
    for index, raw in enumerate(raw_requirements):
        if not isinstance(raw, Mapping):
            raise DogfoodCampaignError(
                f"scenario_requirements[{index}] must be an object"
            )
        unknown = set(raw) - _SCENARIO_REQUIREMENT_FIELDS
        missing = _SCENARIO_REQUIREMENT_FIELDS - set(raw)
        if unknown:
            raise DogfoodCampaignError(
                f"scenario_requirements[{index}] has unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise DogfoodCampaignError(
                f"scenario_requirements[{index}] missing fields: {sorted(missing)}"
            )
        scenario_id = _required_text(
            raw.get("scenario_id"),
            field=f"scenario_requirements[{index}].scenario_id",
        )
        pair_count = _required_positive_int(
            raw.get("required_pair_count"),
            field=f"scenario_requirements[{index}].required_pair_count",
        )
        if scenario_id in requirements:
            raise DogfoodCampaignError(f"duplicate scenario requirement: {scenario_id}")
        requirements[scenario_id] = pair_count
    return requirements


def validate_campaign(
    outcomes: Sequence[Outcome],
    campaign: Mapping[str, object],
) -> dict[str, object]:
    benchmark = compare(list(outcomes), strict_dogfood=True)

    unknown_root = set(campaign) - _ROOT_FIELDS
    if unknown_root:
        raise DogfoodCampaignError(f"unknown campaign fields: {sorted(unknown_root)}")
    schema = campaign.get("schema")
    if (
        not isinstance(schema, Mapping)
        or schema.get("name") != SCHEMA
        or schema.get("version") != 2
    ):
        raise DogfoodCampaignError(
            "campaign schema must be agent-economics-dogfood-campaign v2"
        )

    campaign_id = _required_text(campaign.get("campaign_id"), field="campaign_id")
    implementation_id = _required_text(
        campaign.get("implementation_id"),
        field="implementation_id",
    )
    corpus_id = _required_text(campaign.get("corpus_id"), field="corpus_id")
    measurement_contract_id = _required_text(
        campaign.get("measurement_contract_id"),
        field="measurement_contract_id",
    )
    minimum_distinct_repositories = _required_positive_int(
        campaign.get("minimum_distinct_repositories"),
        field="minimum_distinct_repositories",
    )
    required_scenarios = _scenario_requirements(campaign.get("scenario_requirements"))

    producer_authority = _required_text(
        campaign.get("producer_qualification_authority_id"),
        field="producer_qualification_authority_id",
    )
    producer_evidence = _required_text(
        campaign.get("producer_qualification_evidence_id"),
        field="producer_qualification_evidence_id",
    )
    producer_implementation = _required_text(
        campaign.get("producer_qualification_implementation_id"),
        field="producer_qualification_implementation_id",
    )
    if producer_implementation != implementation_id:
        raise DogfoodCampaignError(
            "producer qualification must bind the exact campaign implementation identity"
        )

    tasks = campaign.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise DogfoodCampaignError("campaign tasks must be a non-empty list")

    rows: dict[tuple[str, str], Mapping[str, object]] = {}
    scenario_ids: list[str] = []
    expected_run_ids: set[str] = set()
    repositories: set[str] = set()
    evidence_receipt_ids: set[str] = set()

    for index, raw in enumerate(tasks):
        if not isinstance(raw, Mapping):
            raise DogfoodCampaignError(f"tasks[{index}] must be an object")
        unknown = set(raw) - _TASK_FIELDS
        missing = _TASK_FIELDS - set(raw)
        if unknown:
            raise DogfoodCampaignError(
                f"tasks[{index}] has unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise DogfoodCampaignError(
                f"tasks[{index}] missing fields: {sorted(missing)}"
            )

        task_id = _required_text(
            raw.get("task_id"),
            field=f"tasks[{index}].task_id",
        )
        treatment_id = _required_text(
            raw.get("treatment_id"),
            field=f"tasks[{index}].treatment_id",
        )
        scenario_id = _required_text(
            raw.get("scenario_id"),
            field=f"tasks[{index}].scenario_id",
        )
        if scenario_id not in required_scenarios:
            raise DogfoodCampaignError(
                f"tasks[{index}] uses undeclared scenario_id {scenario_id!r}"
            )
        repository_id = _required_text(
            raw.get("repository_id"),
            field=f"tasks[{index}].repository_id",
        )
        if raw.get("measurement_contract_id") != measurement_contract_id:
            raise DogfoodCampaignError(
                f"tasks[{index}] does not bind the campaign measurement contract"
            )

        baseline_session = _required_text(
            raw.get("baseline_session_id"),
            field=f"tasks[{index}].baseline_session_id",
        )
        bridge_session = _required_text(
            raw.get("bridge_session_id"),
            field=f"tasks[{index}].bridge_session_id",
        )
        if baseline_session == bridge_session:
            raise DogfoodCampaignError(
                f"tasks[{index}] baseline and bridge session identities must differ"
            )
        for session_id in (baseline_session, bridge_session):
            if session_id in expected_run_ids:
                raise DogfoodCampaignError(
                    f"session/run identity reused across campaign: {session_id}"
                )
            expected_run_ids.add(session_id)

        receipt_fields = (
            "baseline_isolation_evidence_id",
            "bridge_isolation_evidence_id",
            "baseline_freeze_receipt_id",
            "bridge_freeze_receipt_id",
            "baseline_oracle_access_receipt_id",
            "bridge_oracle_access_receipt_id",
        )
        for field in receipt_fields:
            receipt_id = _required_text(
                raw.get(field),
                field=f"tasks[{index}].{field}",
            )
            if receipt_id in evidence_receipt_ids:
                raise DogfoodCampaignError(
                    f"evidence receipt identity reused across campaign: {receipt_id}"
                )
            evidence_receipt_ids.add(receipt_id)

        key = (task_id, treatment_id)
        if key in rows:
            raise DogfoodCampaignError(f"duplicate campaign task pair: {key}")
        rows[key] = raw
        scenario_ids.append(scenario_id)
        repositories.add(repository_id)

    actual_scenario_counts = Counter(scenario_ids)
    missing_or_wrong: list[str] = []
    for scenario_id, required_count in sorted(required_scenarios.items()):
        observed = actual_scenario_counts.get(scenario_id, 0)
        if observed != required_count:
            missing_or_wrong.append(
                f"{scenario_id}: required={required_count} observed={observed}"
            )
    if missing_or_wrong:
        raise DogfoodCampaignError(
            "scenario cardinality mismatch: " + "; ".join(missing_or_wrong)
        )

    if len(repositories) < minimum_distinct_repositories:
        raise DogfoodCampaignError(
            "campaign repository diversity below declared minimum: "
            f"required={minimum_distinct_repositories} observed={len(repositories)}"
        )

    pairs = _paired(outcomes)
    if set(pairs) != set(rows):
        missing_pairs = sorted(set(rows) - set(pairs))
        unregistered_pairs = sorted(set(pairs) - set(rows))
        raise DogfoodCampaignError(
            "campaign manifest and outcome pair membership differ; "
            f"missing={missing_pairs} unregistered={unregistered_pairs}"
        )

    actual_run_ids: list[str] = []
    qualification_authorities: set[str] = set()
    for key, row in rows.items():
        pair = pairs[key]
        baseline = pair["baseline"]
        bridge = pair["bridge"]
        repository_id = str(row["repository_id"])
        if (
            baseline.repository_id != repository_id
            or bridge.repository_id != repository_id
        ):
            raise DogfoodCampaignError(f"repository identity mismatch for pair {key}")
        if baseline.corpus_id != corpus_id or bridge.corpus_id != corpus_id:
            raise DogfoodCampaignError(f"corpus identity mismatch for pair {key}")
        if bridge.bridge_implementation_id != implementation_id:
            raise DogfoodCampaignError(
                f"bridge implementation identity mismatch for pair {key}"
            )
        if baseline.run_id != row["baseline_session_id"]:
            raise DogfoodCampaignError(
                f"baseline run/session identity mismatch for pair {key}"
            )
        if bridge.run_id != row["bridge_session_id"]:
            raise DogfoodCampaignError(
                f"bridge run/session identity mismatch for pair {key}"
            )
        if baseline.run_id is None or bridge.run_id is None:
            raise DogfoodCampaignError(f"missing run identity for pair {key}")
        actual_run_ids.extend((baseline.run_id, bridge.run_id))
        assert baseline.independent_qualification_authority_id is not None
        assert bridge.independent_qualification_authority_id is not None
        qualification_authorities.add(baseline.independent_qualification_authority_id)

    if len(actual_run_ids) != len(set(actual_run_ids)):
        raise DogfoodCampaignError(
            "run identities must be globally unique across campaign"
        )
    if set(actual_run_ids) != expected_run_ids:
        raise DogfoodCampaignError(
            "campaign session identities do not match outcome run identities"
        )

    cleanup = campaign.get("consumer_cleanup_evidence")
    if not isinstance(cleanup, list) or not cleanup:
        raise DogfoodCampaignError("consumer_cleanup_evidence must be a non-empty list")
    cleanup_by_repo: dict[str, str] = {}
    for index, row in enumerate(cleanup):
        if not isinstance(row, Mapping) or set(row) != {"repository_id", "evidence_id"}:
            raise DogfoodCampaignError(
                f"consumer_cleanup_evidence[{index}] must contain "
                "repository_id and evidence_id"
            )
        repository_id = _required_text(
            row.get("repository_id"),
            field=f"consumer_cleanup_evidence[{index}].repository_id",
        )
        evidence_id = _required_text(
            row.get("evidence_id"),
            field=f"consumer_cleanup_evidence[{index}].evidence_id",
        )
        if repository_id in cleanup_by_repo:
            raise DogfoodCampaignError(
                f"duplicate cleanup evidence for repository {repository_id}"
            )
        cleanup_by_repo[repository_id] = evidence_id
    if set(cleanup_by_repo) != repositories:
        raise DogfoodCampaignError(
            "cleanup evidence must cover exactly every campaign repository"
        )

    campaign_identity = _campaign_identity(campaign)
    return {
        "schema": {"name": RESULT_SCHEMA, "version": 2},
        "campaign_id": campaign_id,
        "campaign_identity": campaign_identity,
        "implementation_id": implementation_id,
        "corpus_id": corpus_id,
        "measurement_contract_id": measurement_contract_id,
        "scenario_requirements": [
            {
                "scenario_id": scenario_id,
                "required_pair_count": required_scenarios[scenario_id],
            }
            for scenario_id in sorted(required_scenarios)
        ],
        "scenario_counts": dict(sorted(actual_scenario_counts.items())),
        "minimum_distinct_repositories": minimum_distinct_repositories,
        "repositories": sorted(repositories),
        "qualification_authorities": sorted(qualification_authorities),
        "paired_tasks": len(rows),
        "run_identities": len(actual_run_ids),
        "consumer_cleanup_evidence": cleanup_by_repo,
        "producer_qualification_authority_id": producer_authority,
        "producer_qualification_evidence_id": producer_evidence,
        "producer_qualification_implementation_id": producer_implementation,
        "benchmark": benchmark,
        "authority": {
            "automatic_promotion": False,
            "empirical_evidence_is_not_a_release_verdict": True,
            "repository_qualification_authority_is_consumer_owned": True,
        },
    }


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Validate a repository-neutral fail-closed Agent Economics "
            "empirical campaign."
        )
    )
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args(argv)

    outcomes = load_outcomes(args.outcomes)
    campaign = load_campaign(args.campaign)
    payload = validate_campaign(outcomes, campaign)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.artifact:
        args.artifact.parent.mkdir(parents=True, exist_ok=True)
        args.artifact.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
