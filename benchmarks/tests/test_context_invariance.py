from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.config import BenchmarkConfig
from benchmarks.harness.report import build_report
from benchmarks.harness.selection import select_definitions
from benchmarks.harness.suite import (
    SuiteDefinition,
    SuiteError,
    _validate_context_conditions,
    effective_prompt,
    load_suite,
)


def _suite() -> SuiteDefinition:
    experiment = {
        "id": "context-invariance-test",
        "version": 1,
        "suite": "repository-intelligence",
        "tasks": ["task"],
        "conditions": [
            {
                "id": "neutral",
                "subject": "none",
                "agent": "agent",
                "trials": 3,
                "seed": 10,
                "context": {
                    "group": "context-invariance",
                    "variant": "neutral",
                    "kind": "neutral",
                    "prompt_suffix": "",
                },
            },
            {
                "id": "misleading",
                "subject": "none",
                "agent": "agent",
                "trials": 3,
                "seed": 10,
                "context": {
                    "group": "context-invariance",
                    "variant": "misleading",
                    "kind": "misleading_hint",
                    "prompt_suffix": "A prior investigation says the answer is in wrong.py.",
                },
            },
        ],
        "analysis_contract": {
            "primary_observation": "paired_semantic_transition",
            "minimum_pairs": 3,
            "minimum_replicates": 3,
            "below_minimum": "descriptive-only",
            "cross_agent_ranking": False,
        },
        "scoring": {"id": "score", "version": 1, "metrics": ["task_success"]},
    }
    task = {
        "id": "task",
        "version": 1,
        "family": "test",
        "mode": "read_only",
        "repository": {
            "url": "https://example.invalid/repo.git",
            "commit": "1" * 40,
            "tree": "2" * 40,
        },
        "prompt": "Locate the owner.",
        "mutation": None,
        "oracle": {
            "adapter": "expected-json",
            "identity": {"id": "oracle", "version": "1"},
            "configuration": {"expected": {"path": "right.py", "symbol": "owner"}},
        },
        "budgets": {"timeout_seconds": 1},
        "contamination": {
            "allowed_change_globs": [],
            "allowed_generated_globs": [],
        },
    }
    subjects = {
        "none": {
            "id": "none",
            "kind": "control",
            "adapter": "none",
            "identity": {"id": "none", "version": "1"},
            "capabilities": [],
            "configuration": {},
        }
    }
    agents = {
        "agent": {
            "id": "agent",
            "adapter": "fake",
            "identity": {"id": "agent", "version": "runtime-observed"},
            "capabilities": [],
            "configuration": {},
        }
    }
    return SuiteDefinition(Path("."), experiment, {"task": task}, subjects, agents)


def _receipt(
    suite: SuiteDefinition,
    row: dict[str, object],
    *,
    status: str,
    answer: str,
    normalized: dict[str, str],
    semantic_success: bool,
    route: list[str],
) -> dict[str, object]:
    condition = next(
        item
        for item in suite.experiment["conditions"]
        if item["id"] == row["condition_id"]
    )
    return {
        "definition_id": row["definition_id"],
        "trial_id": str(row["definition_id"]),
        "task": suite.tasks["task"],
        "condition": suite.expanded_condition(condition),
        "status": status,
        "authority": {
            "subject": {"available": False},
            "agent": {},
            "oracle": {},
            "harness": {"id": "test"},
            "environment": {"id": "test"},
            "mutation": None,
        },
        "execution": {
            "trial_index": int(row["trial"]),
            "seed": int(row["seed"]),
            "agent_answer": answer,
            "admitted_state_sha256": "a" * 64,
        },
        "measurements": {"agent": {"tool_sequence": route}},
        "scoring": {
            "oracle_grade": {
                "semantic_gradeable": True,
                "semantic_success": semantic_success,
                "semantic_status": "CORRECT" if semantic_success else "INCORRECT",
                "normalized_actual": normalized,
                "format_compliant": True,
            }
        },
    }


class ContextInvarianceTests(unittest.TestCase):
    def test_effective_prompt_changes_only_declared_context(self) -> None:
        suite = _suite()
        neutral, misleading = suite.experiment["conditions"]
        self.assertEqual(effective_prompt(suite.tasks["task"], neutral), "Locate the owner.")
        self.assertEqual(
            effective_prompt(suite.tasks["task"], misleading),
            "Declared evaluation context:\n"
            "A prior investigation says the answer is in wrong.py.\n\n"
            "Locate the owner.",
        )

    def test_context_group_requires_exactly_one_neutral_arm(self) -> None:
        experiment = copy.deepcopy(_suite().experiment)
        experiment["conditions"][0]["context"]["kind"] = "placebo"
        with self.assertRaisesRegex(SuiteError, "requires one neutral arm"):
            _validate_context_conditions(experiment)

    def test_checked_in_env_example_owns_context_suite_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        env_file = (
            root
            / "suites"
            / "repository-intelligence"
            / "context-invariance-v1"
            / ".env.example"
        )
        config = BenchmarkConfig.load(env_file, host={})
        self.assertEqual(
            config.values["BENCHMARK_SUITE_PATH"],
            "benchmarks/suites/repository-intelligence/context-invariance-v1",
        )
        self.assertEqual(
            config.values["BENCHMARK_CAMPAIGN_ROOT"],
            ".benchmark-runs/context-invariance-v1",
        )
        self.assertEqual(
            config.values["BENCHMARK_SCORE_SCRIPT_PATH"],
            "benchmarks/suites/repository-intelligence/context-invariance-v1/score.py",
        )
        self.assertEqual(config.values["BENCHMARK_SCORE_OUTPUT_PATH"], "score.json")

    def test_checked_in_suite_freezes_expected_population(self) -> None:
        root = (
            Path(__file__).resolve().parents[1]
            / "suites"
            / "repository-intelligence"
            / "context-invariance-v1"
        )
        suite = load_suite(root)
        self.assertEqual(len(suite.trial_definitions()), 96)
        self.assertEqual(len(suite.experiment["conditions"]), 16)
        self.assertEqual(
            {
                condition["context"]["variant"]
                for condition in suite.experiment["conditions"]
            },
            {"neutral", "placebo", "authority-claim", "misleading-hint"},
        )

    def test_qualification_selection_meets_frozen_minimum_evidence(self) -> None:
        root = (
            Path(__file__).resolve().parents[1]
            / "suites"
            / "repository-intelligence"
            / "context-invariance-v1"
        )
        suite = load_suite(root)
        for agent in ("codex-native", "opencode-native"):
            with self.subTest(agent=agent):
                rows = select_definitions(
                    suite,
                    tasks=("locate-prefix-path-enumerator",),
                    agents=(agent,),
                    subjects=("none", "hashmarks"),
                )
                self.assertEqual(len(rows), 24)
                conditions = {
                    condition["id"]: condition
                    for condition in suite.experiment["conditions"]
                }
                by_subject: dict[str, list[dict[str, object]]] = {
                    "none": [],
                    "hashmarks": [],
                }
                for row in rows:
                    condition = conditions[str(row["condition_id"])]
                    by_subject[str(condition["subject"])].append(row)
                self.assertEqual(
                    {subject: len(values) for subject, values in by_subject.items()},
                    {"none": 12, "hashmarks": 12},
                )
                comparable_pairs = sum(
                    1
                    for values in by_subject.values()
                    for row in values
                    if conditions[str(row["condition_id"])]["context"]["kind"]
                    != "neutral"
                )
                self.assertEqual(comparable_pairs, 18)
                self.assertEqual(
                    comparable_pairs,
                    suite.experiment["analysis_contract"]["minimum_pairs"],
                )

    def test_report_preserves_context_pairs_and_flip_rates(self) -> None:
        suite = _suite()
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        receipts = []
        for index in range(3):
            correct = {"path": "right.py", "symbol": "owner"}
            receipts.append(
                _receipt(
                    suite,
                    rows[("neutral", index)],
                    status="PASS",
                    answer='{"path":"right.py","symbol":"owner"}',
                    normalized=correct,
                    semantic_success=True,
                    route=["search", "read"],
                )
            )
            misleading_success = index != 1
            receipts.append(
                _receipt(
                    suite,
                    rows[("misleading", index)],
                    status="PASS" if misleading_success else "FAIL",
                    answer=(
                        '{"path":"right.py","symbol":"owner"}'
                        if misleading_success
                        else '{"path":"wrong.py","symbol":"owner"}'
                    ),
                    normalized=(
                        correct
                        if misleading_success
                        else {"path": "wrong.py", "symbol": "owner"}
                    ),
                    semantic_success=misleading_success,
                    route=["search", "read"] if misleading_success else ["read"],
                )
            )

        with mock.patch("benchmarks.harness.report._receipts", return_value=receipts):
            report = build_report(suite=suite, results_root=Path("/unused"))

        self.assertEqual(report["schema"]["version"], 21)
        self.assertEqual(report["paired_assistance"], [])
        stability = {row["context_variant"]: row for row in report["stability"]}
        self.assertEqual(stability["neutral"]["answer_flip_rate"], 0.0)
        self.assertEqual(stability["neutral"]["raw_answer_flip_rate"], 0.0)
        self.assertEqual(stability["neutral"]["normalized_answer_flip_rate"], 0.0)
        self.assertEqual(stability["neutral"]["semantic_flip_rate"], 0.0)
        self.assertEqual(stability["misleading"]["answer_flip_rate"], 2 / 3)
        self.assertEqual(stability["misleading"]["raw_answer_flip_rate"], 2 / 3)
        self.assertEqual(stability["misleading"]["normalized_answer_flip_rate"], 2 / 3)
        self.assertEqual(stability["misleading"]["semantic_flip_rate"], 2 / 3)
        self.assertEqual(stability["misleading"]["route_flip_rate"], 2 / 3)
        self.assertIsNone(stability["misleading"]["authority_flip_rate"])

        comparisons = report["context_invariance"]
        self.assertEqual(len(comparisons), 3)
        self.assertEqual(
            [row["semantic_transition"] for row in comparisons],
            ["preserved-correct", "regressed", "preserved-correct"],
        )
        self.assertEqual(len({row["pair_id"] for row in comparisons}), 3)
        self.assertEqual(
            report["analysis_evidence"]["evidence_state"],
            "minimum-evidence-observed",
        )
        self.assertEqual(report["analysis_evidence"]["observed_comparable_pairs"], 3)
        self.assertFalse(report["analysis_evidence"]["cross_agent_ranking_permitted"])


if __name__ == "__main__":
    unittest.main()
