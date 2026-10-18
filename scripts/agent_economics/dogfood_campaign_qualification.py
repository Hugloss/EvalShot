from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path

from .agent_outcome_benchmark import BenchmarkError, Outcome
from .dogfood_campaign import load_campaign, validate_campaign


def _expect_error(fn) -> None:
    try:
        fn()
    except BenchmarkError:
        return
    raise AssertionError("expected BenchmarkError")


def _pair(
    task_id: str,
    scenario_id: str,
    repository_id: str,
    *,
    qualification_authority_id: str,
    index: int,
) -> tuple[list[Outcome], dict[str, object]]:
    treatment_id = "bridge-v1"
    shared = dict(
        task_id=task_id,
        treatment_id=treatment_id,
        correct=True,
        repository_id=repository_id,
        task_fixture_id=f"fixture:{task_id}",
        corpus_id="corpus:v1",
        initial_source_id=f"source:{task_id}:initial",
        agent_profile="agent:v1",
        execution_environment_id="env:v1",
        independent_qualification_authority_id=qualification_authority_id,
        oracle_opened_after_freeze=True,
    )
    baseline_run = f"session:{index}:baseline"
    bridge_run = f"session:{index}:bridge"
    baseline = Outcome(
        mode="baseline",
        run_id=baseline_run,
        final_source_id=f"source:{task_id}:baseline-final",
        independent_qualification_evidence_id=f"qualification:{task_id}:baseline",
        **shared,
    )
    bridge = Outcome(
        mode="bridge",
        run_id=bridge_run,
        bridge_implementation_id="implementation:v1",
        manifest_id=f"manifest:{task_id}",
        local_qualification_id=f"local:{task_id}",
        final_source_id=f"source:{task_id}:bridge-final",
        independent_qualification_evidence_id=f"qualification:{task_id}:bridge",
        local_independent_qualification_agree=True,
        **shared,
    )
    row = {
        "task_id": task_id,
        "treatment_id": treatment_id,
        "scenario_id": scenario_id,
        "repository_id": repository_id,
        "measurement_contract_id": "measurement:v1",
        "baseline_session_id": baseline_run,
        "bridge_session_id": bridge_run,
        "baseline_isolation_evidence_id": f"isolation:{task_id}:baseline",
        "bridge_isolation_evidence_id": f"isolation:{task_id}:bridge",
        "baseline_freeze_receipt_id": f"freeze:{task_id}:baseline",
        "bridge_freeze_receipt_id": f"freeze:{task_id}:bridge",
        "baseline_oracle_access_receipt_id": f"oracle:{task_id}:baseline",
        "bridge_oracle_access_receipt_id": f"oracle:{task_id}:bridge",
    }
    return [baseline, bridge], row


def _fixture() -> tuple[list[Outcome], dict[str, object]]:
    specs = [
        ("task-a1", "scenario:alpha", "repo:a", "authority:a"),
        ("task-b", "scenario:beta", "repo:a", "authority:a"),
        ("task-c", "scenario:gamma", "repo:a", "authority:a"),
        ("task-d", "scenario:delta", "repo:a", "authority:a"),
        ("task-a2", "scenario:alpha", "repo:b", "authority:b"),
    ]
    outcomes: list[Outcome] = []
    tasks: list[dict[str, object]] = []
    for index, (task_id, scenario_id, repository_id, authority_id) in enumerate(
        specs,
        start=1,
    ):
        pair, row = _pair(
            task_id,
            scenario_id,
            repository_id,
            qualification_authority_id=authority_id,
            index=index,
        )
        outcomes.extend(pair)
        tasks.append(row)

    campaign: dict[str, object] = {
        "schema": {
            "name": "agent-economics-dogfood-campaign",
            "version": 2,
        },
        "campaign_id": "campaign:v2",
        "implementation_id": "implementation:v1",
        "corpus_id": "corpus:v1",
        "measurement_contract_id": "measurement:v1",
        "scenario_requirements": [
            {"scenario_id": "scenario:alpha", "required_pair_count": 2},
            {"scenario_id": "scenario:beta", "required_pair_count": 1},
            {"scenario_id": "scenario:gamma", "required_pair_count": 1},
            {"scenario_id": "scenario:delta", "required_pair_count": 1},
        ],
        "minimum_distinct_repositories": 2,
        "tasks": tasks,
        "consumer_cleanup_evidence": [
            {"repository_id": "repo:a", "evidence_id": "cleanup:repo-a"},
            {"repository_id": "repo:b", "evidence_id": "cleanup:repo-b"},
        ],
        "producer_qualification_authority_id": "authority:producer",
        "producer_qualification_evidence_id": "qualification:producer",
        "producer_qualification_implementation_id": "implementation:v1",
    }
    return outcomes, campaign


def main() -> None:
    outcomes, campaign = _fixture()
    result = validate_campaign(outcomes, campaign)

    assert result["schema"]["version"] == 2
    assert result["paired_tasks"] == 5
    assert result["run_identities"] == 10
    assert result["repositories"] == ["repo:a", "repo:b"]
    assert result["minimum_distinct_repositories"] == 2
    assert result["scenario_counts"] == {
        "scenario:alpha": 2,
        "scenario:beta": 1,
        "scenario:delta": 1,
        "scenario:gamma": 1,
    }
    assert result["qualification_authorities"] == ["authority:a", "authority:b"]
    assert result["benchmark"]["promotion_evidence"]["strict_dogfood_protocol"] is True
    assert result["authority"]["automatic_promotion"] is False
    assert (
        result["authority"]["repository_qualification_authority_is_consumer_owned"]
        is True
    )

    missing = deepcopy(campaign)
    missing["tasks"] = [
        row for row in campaign["tasks"] if row["scenario_id"] != "scenario:gamma"
    ]
    missing_outcomes = [outcome for outcome in outcomes if outcome.task_id != "task-c"]
    _expect_error(lambda: validate_campaign(missing_outcomes, missing))

    too_many_repositories_required = deepcopy(campaign)
    too_many_repositories_required["minimum_distinct_repositories"] = 3
    _expect_error(lambda: validate_campaign(outcomes, too_many_repositories_required))

    undeclared_scenario = deepcopy(campaign)
    undeclared_scenario["tasks"][0]["scenario_id"] = "scenario:undeclared"
    _expect_error(lambda: validate_campaign(outcomes, undeclared_scenario))

    duplicate_requirement = deepcopy(campaign)
    duplicate_requirement["scenario_requirements"].append(
        {"scenario_id": "scenario:alpha", "required_pair_count": 1}
    )
    _expect_error(lambda: validate_campaign(outcomes, duplicate_requirement))

    duplicate_run = deepcopy(campaign)
    duplicate_run["tasks"][1]["baseline_session_id"] = campaign["tasks"][0][
        "baseline_session_id"
    ]
    _expect_error(lambda: validate_campaign(outcomes, duplicate_run))

    bad_measurement = deepcopy(campaign)
    bad_measurement["tasks"][0]["measurement_contract_id"] = "measurement:other"
    _expect_error(lambda: validate_campaign(outcomes, bad_measurement))

    reused_receipt = deepcopy(campaign)
    reused_receipt["tasks"][1]["baseline_isolation_evidence_id"] = campaign["tasks"][0][
        "baseline_isolation_evidence_id"
    ]
    _expect_error(lambda: validate_campaign(outcomes, reused_receipt))

    wrong_producer_source = deepcopy(campaign)
    wrong_producer_source["producer_qualification_implementation_id"] = (
        "implementation:other"
    )
    _expect_error(lambda: validate_campaign(outcomes, wrong_producer_source))

    no_cleanup = deepcopy(campaign)
    no_cleanup["consumer_cleanup_evidence"] = [
        {"repository_id": "repo:a", "evidence_id": "cleanup:repo-a"}
    ]
    _expect_error(lambda: validate_campaign(outcomes, no_cleanup))

    mismatched_repo = list(outcomes)
    mismatched_repo[0] = Outcome(
        **{**mismatched_repo[0].__dict__, "repository_id": "repo:other"}
    )
    _expect_error(lambda: validate_campaign(mismatched_repo, campaign))

    mismatched_qualification_authority = list(outcomes)
    mismatched_qualification_authority[1] = Outcome(
        **{
            **mismatched_qualification_authority[1].__dict__,
            "independent_qualification_authority_id": "authority:other",
        }
    )
    _expect_error(
        lambda: validate_campaign(
            mismatched_qualification_authority,
            campaign,
        )
    )

    reused_outcome_run = list(outcomes)
    reused_outcome_run[2] = Outcome(
        **{**reused_outcome_run[2].__dict__, "run_id": outcomes[0].run_id}
    )
    campaign_reused = deepcopy(campaign)
    campaign_reused["tasks"][1]["baseline_session_id"] = outcomes[0].run_id
    _expect_error(lambda: validate_campaign(reused_outcome_run, campaign_reused))

    unregistered = list(outcomes)
    extra_pair, _ = _pair(
        "extra",
        "scenario:alpha",
        "repo:a",
        qualification_authority_id="authority:a",
        index=99,
    )
    unregistered.extend(extra_pair)
    _expect_error(lambda: validate_campaign(unregistered, campaign))

    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "campaign.json"
        path.write_text(json.dumps(campaign), encoding="utf-8")
        assert load_campaign(path) == campaign

    print(
        json.dumps(
            {
                "status": "PASS",
                "paired_tasks": 5,
                "declared_scenarios": 4,
                "repositories": 2,
                "strict_campaign": True,
                "schema": 2,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
