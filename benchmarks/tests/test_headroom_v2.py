from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from benchmarks.harness.suite import load_suite

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "benchmarks/suites/repository-intelligence/headroom-v1"
V2 = ROOT / "benchmarks/suites/repository-intelligence/headroom-v2"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, V2 / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v2_freezes_same_cases_with_new_oracle_identity() -> None:
    suite = load_suite(V2)
    assert suite.experiment["id"] == "repository-intelligence-headroom-v2"
    assert len(suite.trial_definitions()) == 72
    assert (
        json.loads((V1 / "cases.json").read_text())["cases"]
        == json.loads((V2 / "cases.json").read_text())["cases"]
    )
    for task_id in suite.tasks:
        old = json.loads((V1 / "tasks" / f"{task_id}.json").read_text())
        new = json.loads((V2 / "tasks" / f"{task_id}.json").read_text())
        assert old["prompt"] == new["prompt"]
        assert old["mutation"] == new["mutation"]
        assert old["repository"] == new["repository"]
        assert old["oracle"]["identity"]["version"] == "1"
        assert new["oracle"]["identity"]["version"] == "2"


def test_v2_separates_semantic_gradeability_from_plain_json_format(
    tmp_path: Path,
) -> None:
    oracle = _module("oracle")
    expected = oracle.CASES["declarations-00"]["expected"]
    payload = json.dumps(expected)
    bare = oracle.observe_answer(payload)
    fenced = oracle.observe_answer(f"```json\n{payload}\n```")
    assert bare["semantic_gradeable"] is True
    assert bare["format_compliant"] is True
    assert fenced["semantic_gradeable"] is True
    assert fenced["format_compliant"] is False
    assert (
        oracle.grade_answer(
            "declarations-00", fenced["actual"], tmp_path, answer_observation=fenced
        )["passed"]
        is True
    )

    for message in (
        f"Here is the answer:\n```json\n{payload}\n```",
        "```json\n{}\n```\n```json\n{}\n```",
        '{"winner":"not-selected","winner":"other"}',
        '[{"winner":"not-selected"}]',
        None,
    ):
        observed = oracle.observe_answer(message)
        assert observed["semantic_gradeable"] is False
        graded = oracle.grade_answer(
            "declarations-00",
            observed["actual"],
            tmp_path,
            answer_observation=observed,
        )
        assert graded["passed"] is False
        assert graded["rubric"]["semantic_success"] is None


def test_v2_headroom_excludes_ungradeable_bare_answers() -> None:
    score = _module("score")
    contract = {
        "conditions": {
            "none-opencode-native": {
                "trials": 3,
                "semantic_gradeable": 2,
                "semantic_passes": 1,
                "semantic_failures": 1,
            }
        }
    }
    result = score._bare_headroom(
        {},
        bare_condition_ids={"none-opencode-native"},
        answer_contract=contract,
    )
    assert result["state"] == "observed"
    assert result["valid_trials"] == 2
    assert result["failures"] == 1
    assert result["unresolved_trials"] == 1
