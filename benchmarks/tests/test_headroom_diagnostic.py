from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

from benchmarks.harness.suite import load_suite
from benchmarks.harness.selection import select_definitions


ROOT = Path(__file__).resolve().parents[2]
SUITE_ROOT = (
    ROOT
    / "benchmarks"
    / "suites"
    / "repository-intelligence"
    / "headroom-v1"
)
BEHAVIORAL_ROOT = (
    ROOT
    / "benchmarks"
    / "suites"
    / "repository-intelligence"
    / "behavioral-v4"
)
TASK_IDS = (
    "change_impact-00",
    "freshness-00",
    "declarations-00",
    "verification-00",
)


def _load_score_module():
    path = SUITE_ROOT / "score.py"
    spec = importlib.util.spec_from_file_location("headroom_score", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_headroom_suite_is_small_repeated_and_subject_neutral() -> None:
    suite = load_suite(SUITE_ROOT)

    assert suite.experiment["id"] == "repository-intelligence-headroom-v1"
    assert tuple(suite.experiment["tasks"]) == TASK_IDS
    assert len(suite.trial_definitions()) == 72

    opencode = select_definitions(
        suite,
        tasks=(),
        agents=("opencode-native",),
        condition=None,
    )
    assert len(opencode) == 36

    for task_id in TASK_IDS:
        prompt = str(suite.tasks[task_id]["prompt"]).lower()
        for forbidden in ("hashmarks", "enola", "mcp", "task_evidence"):
            assert forbidden not in prompt

    assert suite.agents["opencode-native"]["identity"]["version"] == "native-config-v7"
    assert suite.experiment["scoring"]["version"] == 3


def test_headroom_reuses_behavioral_case_truth_without_rewriting_oracles() -> None:
    headroom_cases = json.loads(
        (SUITE_ROOT / "cases.json").read_text(encoding="utf-8")
    )["cases"]
    behavioral_cases = json.loads(
        (BEHAVIORAL_ROOT / "cases.json").read_text(encoding="utf-8")
    )["cases"]

    for task_id in TASK_IDS:
        headroom_task = json.loads(
            (SUITE_ROOT / "tasks" / f"{task_id}.json").read_text(encoding="utf-8")
        )
        behavioral_task = json.loads(
            (BEHAVIORAL_ROOT / "tasks" / f"{task_id}.json").read_text(encoding="utf-8")
        )
        assert headroom_cases[task_id] == behavioral_cases[task_id]
        for key in ("prompt", "repository", "mutation", "oracle", "contamination"):
            assert headroom_task[key] == behavioral_task[key]


def test_headroom_score_honors_exact_assisted_only_selection() -> None:
    suite = load_suite(SUITE_ROOT)
    rows = [
        row
        for row in suite.trial_definitions()
        if row["task_id"] == "freshness-00"
        and row["condition_id"] == "hashmarks-opencode-native"
    ]
    selected = {str(row["definition_id"]) for row in rows}
    assert len(selected) == 3
    score = _load_score_module()

    def report(*, selected_definitions, require_complete, **_kwargs):
        assert require_complete
        assert set(selected_definitions) == selected
        return {
            "expected_trials": 3,
            "observed_trials": 3,
            "status_counts": {"PASS": 3},
            "campaign_qualification": {"status": "QUALIFIED"},
            "stability": [],
            "subject_adoption": [],
            "paired_assistance_summary": [],
            "paired_assistance_usage_summary": [],
            "paired_assistance_exclusions": [],
            "task_assistance_evidence": [],
            "conditions": {
                "hashmarks-opencode-native": {
                    "trials": 3,
                    "valid_outcomes": 3,
                    "statuses": {"PASS": 3},
                }
            },
            "agent_profiles": {},
            "diagnostics": [],
        }

    with mock.patch.object(score, "build_report", side_effect=report):
        payload = score.score(
            suite_root=SUITE_ROOT,
            results=Path("/unused"),
            agents=("opencode-native",),
            definition_ids=tuple(sorted(selected)),
        )

    assert payload["schema"] == "agents-cookbook-repository-intelligence-headroom.v3"
    assert payload["selection"]["definition_count"] == 3
    assert set(payload["selection"]["definition_ids"]) == selected
    assert payload["bare_control_headroom"]["state"] == "unavailable"


def test_headroom_score_reports_bare_control_headroom_without_assuming_it() -> None:
    score = _load_score_module()

    observed = score._bare_headroom(
        {
            "conditions": {
                "none-opencode-native": {
                    "trials": 3,
                    "valid_outcomes": 3,
                    "statuses": {"PASS": 2, "FAIL": 1},
                }
            }
        },
        bare_condition_ids={"none-opencode-native"},
    )
    assert observed == {
        "state": "observed",
        "valid_trials": 3,
        "passes": 2,
        "failures": 1,
        "unresolved_trials": 0,
        "headroom_trials": 1,
        "headroom_rate": 1 / 3,
        "interpretation": "bare control has reproducible semantic headroom",
    }

    no_headroom = score._bare_headroom(
        {
            "conditions": {
                "none-opencode-native": {
                    "trials": 3,
                    "valid_outcomes": 3,
                    "statuses": {"PASS": 3},
                }
            }
        },
        bare_condition_ids={"none-opencode-native"},
    )
    assert no_headroom["state"] == "not-observed"
    assert no_headroom["headroom_trials"] == 0
    assert no_headroom["headroom_rate"] == 0
