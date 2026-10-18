from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .agent_outcome_benchmark import (
    BenchmarkError,
    Outcome,
    compare,
    load_outcomes,
    outcome_template,
)


def _expect_error(fn) -> None:
    try:
        fn()
    except BenchmarkError:
        return
    raise AssertionError("expected BenchmarkError")


def main() -> None:
    common = dict(
        repository_id="repo:fixture",
        task_fixture_id="fixture:v1",
        agent_profile="agent:v1",
        independent_qualification_authority_id="authority:repo",
    )
    outcomes = [
        Outcome(
            task_id="localized",
            treatment_id="bridge-v1",
            mode="baseline",
            correct=True,
            independent_qualification_activations=3,
            files_opened=12,
            evidence_bytes=20_000,
            context_tokens_estimate=9_000,
            tool_calls=20,
            repair_iterations=3,
            seconds_to_first_correct_edit=40.0,
            **common,
        ),
        Outcome(
            task_id="localized",
            treatment_id="bridge-v1",
            mode="bridge",
            correct=True,
            independent_qualification_activations=1,
            files_opened=5,
            evidence_bytes=8_000,
            context_tokens_estimate=3_500,
            tool_calls=9,
            local_commands=4,
            repair_iterations=1,
            seconds_to_first_correct_edit=18.0,
            bridge_elapsed_ms=120.0,
            local_independent_qualification_agree=True,
            **common,
        ),
        Outcome(
            task_id="broader-gate",
            treatment_id="bridge-v1",
            mode="baseline",
            correct=True,
            independent_qualification_activations=2,
            files_opened=10,
            evidence_bytes=15_000,
            context_tokens_estimate=7_000,
            tool_calls=16,
            repair_iterations=2,
            **common,
        ),
        Outcome(
            task_id="broader-gate",
            treatment_id="bridge-v1",
            mode="bridge",
            correct=True,
            independent_qualification_activations=1,
            files_opened=6,
            evidence_bytes=9_000,
            context_tokens_estimate=4_000,
            tool_calls=10,
            local_commands=5,
            repair_iterations=2,
            local_independent_qualification_agree=True,
            **common,
        ),
    ]
    result = compare(outcomes)
    assert result["schema"]["version"] == 3
    assert result["correctness"]["paired_tasks"] == 2
    assert result["metrics"]["independent_qualification_activations"]["delta"] == -3.0
    assert result["timing"]["paired_measurements"] == 1
    assert result["authority"]["automatic_promotion"] is False
    assert result["authority"]["promotion_evidence_is_not_a_verdict"] is True
    assert (
        result["promotion_evidence"][
            "local_independent_qualification_disagreement_zero"
        ]
        is True
    )
    assert result["experiment_protocol"]["bridge_receipts_bound"] == 0
    assert result["experiment_protocol"]["oracle_protocol_unknown"] == 2
    assert len(outcome_template(task_id="new-task", treatment_id="bridge-v1")) == 2

    unknown_qualification = compare(
        [
            Outcome(
                task_id="unknown-qualification",
                treatment_id="bridge-v1",
                mode="baseline",
                correct=True,
                **common,
            ),
            Outcome(
                task_id="unknown-qualification",
                treatment_id="bridge-v1",
                mode="bridge",
                correct=True,
                local_independent_qualification_agree=None,
                **common,
            ),
        ]
    )
    assert (
        unknown_qualification["correctness"]["local_independent_qualification_unknown"]
        == 1
    )
    assert (
        unknown_qualification["promotion_evidence"][
            "local_independent_qualification_disagreement_zero"
        ]
        is False
    )

    bound_common = dict(
        common,
        bridge_implementation_id="implementation:v1",
        manifest_id="manifest:v1",
        local_qualification_id="local:v1",
        final_source_id="source:v1",
        independent_qualification_evidence_id="qualification:v1",
        oracle_opened_after_freeze=True,
    )
    strict_shared = dict(
        repository_id="repo:strict",
        task_fixture_id="fixture:strict",
        corpus_id="corpus:v1",
        initial_source_id="source:initial",
        agent_profile="agent:v1",
        execution_environment_id="env:v1",
        independent_qualification_authority_id="authority:repo",
    )
    strict_baseline = Outcome(
        task_id="strict",
        treatment_id="bridge-v1",
        mode="baseline",
        correct=True,
        run_id="run:baseline",
        final_source_id="source:baseline-final",
        independent_qualification_evidence_id="qualification:baseline",
        oracle_opened_after_freeze=True,
        **strict_shared,
    )
    strict_bridge = Outcome(
        task_id="strict",
        treatment_id="bridge-v1",
        mode="bridge",
        correct=True,
        run_id="run:bridge",
        bridge_implementation_id="implementation:v1",
        manifest_id="manifest:v1",
        local_qualification_id="local:v1",
        final_source_id="source:bridge-final",
        independent_qualification_evidence_id="qualification:bridge",
        local_independent_qualification_agree=True,
        oracle_opened_after_freeze=True,
        **strict_shared,
    )
    strict = compare(
        [strict_baseline, strict_bridge],
        strict_dogfood=True,
    )
    assert strict["promotion_evidence"]["strict_dogfood_protocol"] is True
    assert (
        strict["promotion_evidence"][
            "local_independent_qualification_disagreement_zero"
        ]
        is True
    )

    _expect_error(
        lambda: compare(
            [
                Outcome(
                    **{
                        **strict_baseline.__dict__,
                        "initial_source_id": None,
                    }
                ),
                strict_bridge,
            ],
            strict_dogfood=True,
        )
    )
    _expect_error(
        lambda: compare(
            [
                strict_baseline,
                Outcome(
                    **{
                        **strict_bridge.__dict__,
                        "run_id": "run:baseline",
                    }
                ),
            ],
            strict_dogfood=True,
        )
    )
    _expect_error(
        lambda: compare(
            [
                strict_baseline,
                Outcome(
                    **{
                        **strict_bridge.__dict__,
                        "local_independent_qualification_agree": None,
                    }
                ),
            ],
            strict_dogfood=True,
        )
    )
    _expect_error(
        lambda: compare(
            [
                strict_baseline,
                Outcome(
                    **{
                        **strict_bridge.__dict__,
                        "independent_qualification_evidence_id": None,
                    }
                ),
            ],
            strict_dogfood=True,
        )
    )
    _expect_error(
        lambda: compare(
            [
                strict_baseline,
                Outcome(
                    **{
                        **strict_bridge.__dict__,
                        "independent_qualification_authority_id": None,
                    }
                ),
            ],
            strict_dogfood=True,
        )
    )
    _expect_error(
        lambda: compare(
            [
                strict_baseline,
                Outcome(
                    **{
                        **strict_bridge.__dict__,
                        "independent_qualification_authority_id": "authority:other",
                    }
                ),
            ],
            strict_dogfood=True,
        )
    )

    bound = compare(
        [
            Outcome(
                task_id="bound",
                treatment_id="bridge-v1",
                mode="baseline",
                correct=True,
                **common,
            ),
            Outcome(
                task_id="bound",
                treatment_id="bridge-v1",
                mode="bridge",
                correct=True,
                **bound_common,
            ),
        ]
    )
    assert bound["experiment_protocol"]["bridge_receipts_bound"] == 1
    assert bound["experiment_protocol"]["independent_qualification_evidence_bound"] == 1

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "outcomes.jsonl"
        path.write_text(
            "\n".join(json.dumps(o.__dict__) for o in outcomes) + "\n",
            encoding="utf-8",
        )
        assert len(load_outcomes(path)) == 4

    _expect_error(lambda: compare([outcomes[0]]))
    _expect_error(lambda: compare(outcomes + [outcomes[0]]))

    mismatch = Outcome(
        task_id="localized",
        treatment_id="bridge-v1",
        mode="bridge",
        correct=True,
        repository_id="repo:other",
        task_fixture_id="fixture:v1",
        agent_profile="agent:v1",
        independent_qualification_authority_id="authority:repo",
    )
    _expect_error(lambda: compare([outcomes[0], mismatch]))

    print(
        json.dumps(
            {
                "status": "PASS",
                "paired_tasks": 2,
                "schema": 3,
                "automatic_promotion": False,
                "strict_dogfood": True,
                "qualification_authority": "repository-owned",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
