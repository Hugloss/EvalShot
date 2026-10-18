from __future__ import annotations

import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness.regrade import RegradeError
from benchmarks.harness.suite import load_suite


HELDOUT = (
    Path(__file__).resolve().parents[1] / "suites/repository-intelligence/heldout-v1"
)
SCORE = HELDOUT / "score.py"


class HeldoutScoreSelectionTests(unittest.TestCase):
    def test_score_covers_exactly_the_selected_agents(self) -> None:
        suite = load_suite(HELDOUT)
        conditions = {str(row["id"]): row for row in suite.experiment["conditions"]}
        score_main = runpy.run_path(str(SCORE))["main"]

        for value, agents in (
            ("opencode-native", {"opencode-native"}),
            ("codex-native,opencode-native", {"codex-native", "opencode-native"}),
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "score.json"
                selected_by_language: list[set[str]] = []

                def report(*, selected_definitions, require_complete, **kwargs):
                    self.assertTrue(require_complete)
                    selected_by_language.append(selected_definitions)
                    return {
                        "expected_trials": len(selected_definitions),
                        "observed_trials": len(selected_definitions),
                        "status_counts": {"PASS": len(selected_definitions)},
                        "campaign_qualification": {
                            "status": "QUALIFIED",
                            "complete": True,
                            "invalid_outcomes": 0,
                            "mixed_execution_authority": False,
                            "mixed_localization_scoring_policy": False,
                        },
                        "conditions": {},
                        "paired_assistance": [],
                        "paired_assistance_summary": [],
                        "paired_assistance_exclusions": [],
                        "expected_assistance_pairs": 0,
                        "stability": [],
                        "task_agent_authority": [],
                        "subject_adoption": [],
                        "diagnostics": [
                            {
                                "reason_code": "agent-terminal-failed",
                                "reason": "agent terminal event was turn.failed: fixture",
                            }
                        ],
                        "agent_profiles": {},
                        "cross_agent_observations": [],
                    }

                with (
                    mock.patch.dict(score_main.__globals__, {"build_report": report}),
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            str(SCORE),
                            "--results",
                            str(Path(tmp) / "results"),
                            "--output",
                            str(output),
                            "--agent",
                            value,
                        ],
                    ),
                ):
                    self.assertEqual(score_main(), 0)

                expected_ids = {
                    str(row["definition_id"])
                    for row in suite.trial_definitions()
                    if conditions[str(row["condition_id"])]["agent"] in agents
                }
                self.assertEqual(len(selected_by_language), 2)
                self.assertFalse(selected_by_language[0] & selected_by_language[1])
                self.assertEqual(set.union(*selected_by_language), expected_ids)
                payload = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(
                    payload["schema"],
                    "agents-cookbook-heldout-observer-outcomes.v20",
                )
                self.assertEqual(payload["selection"]["agents"], sorted(agents))
                self.assertEqual(
                    payload["selection"]["definition_count"],
                    len(expected_ids),
                )
                self.assertEqual(
                    set(payload["selection"]["definition_ids"]),
                    expected_ids,
                )
                self.assertEqual(payload["projection_mode"], "live")
                for language in payload["languages"].values():
                    self.assertEqual(
                        language["diagnostics"][0]["reason"],
                        "agent terminal event was turn.failed: fixture",
                    )
                self.assertEqual(
                    payload["campaign_qualification"]["status"],
                    "QUALIFIED",
                )
                self.assertEqual(payload["expected_trials"], 108 * len(agents))
                self.assertEqual(payload["observed_trials"], 108 * len(agents))
                self.assertEqual(
                    payload["authority"]["cross_agent_comparison"],
                    "descriptive-only"
                    if len(agents) == 2
                    else "not-applicable-single-agent-selection",
                )
                self.assertEqual(
                    payload["decision_summary"]["claim_guardrails"][
                        "subject_effect_attribution"
                    ],
                    "exact-required-operation-with-successful-nonempty-result",
                )
                self.assertEqual(
                    payload["decision_summary"]["claim_guardrails"][
                        "generic_subject_invocation"
                    ],
                    "routing-and-adoption-descriptive-only",
                )

    def test_score_honors_exact_partial_campaign_definitions(self) -> None:
        suite = load_suite(HELDOUT)
        conditions = {str(row["id"]): row for row in suite.experiment["conditions"]}
        rows = [
            row
            for row in suite.trial_definitions()
            if row["task_id"] == "locate-prefix-path-enumerator"
            and row["condition_id"] == "hashmarks-opencode-native"
        ]
        selected_ids = {str(row["definition_id"]) for row in rows}
        self.assertEqual(len(selected_ids), 3)
        score_main = runpy.run_path(str(SCORE))["main"]

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "score.json"
            calls: list[set[str]] = []

            def report(*, selected_definitions, require_complete, **kwargs):
                self.assertTrue(require_complete)
                calls.append(set(selected_definitions))
                return {
                    "expected_trials": len(selected_definitions),
                    "observed_trials": len(selected_definitions),
                    "status_counts": {"PASS": len(selected_definitions)},
                    "campaign_qualification": {
                        "status": "QUALIFIED",
                        "complete": True,
                        "invalid_outcomes": 0,
                    },
                    "conditions": {},
                    "paired_assistance": [],
                    "paired_assistance_summary": [],
                    "paired_assistance_usage_summary": [],
                    "paired_assistance_treatment_summary": [
                        {
                            "agent_id": "opencode-native",
                            "subject_id": "hashmarks",
                            "contracted_exposure_state": (
                                "contracted-successful-result"
                            ),
                            "contracted_treatment_observed": True,
                            "total_pairs": 1,
                            "transitions": {
                                "gain": 1,
                                "preserved": 0,
                                "unresolved": 0,
                                "regression": 0,
                            },
                            "delta_metrics": {},
                        }
                    ],
                    "paired_assistance_exclusions": [],
                    "expected_assistance_pairs": 0,
                    "task_assistance_evidence": [],
                    "stability": [],
                    "task_agent_authority": [],
                    "subject_adoption": [],
                    "diagnostics": [],
                    "agent_profiles": {},
                    "cross_agent_observations": [],
                }

            argv = [
                str(SCORE),
                "--results",
                str(Path(tmp) / "results"),
                "--output",
                str(output),
                "--agent",
                "opencode-native",
            ]
            for definition_id in sorted(selected_ids):
                argv.extend(("--definition-id", definition_id))

            with (
                mock.patch.dict(score_main.__globals__, {"build_report": report}),
                mock.patch.object(sys, "argv", argv),
            ):
                self.assertEqual(score_main(), 0)

            self.assertEqual(calls, [selected_ids])
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["expected_trials"], 3)
            self.assertEqual(payload["observed_trials"], 3)
            self.assertEqual(set(payload["languages"]), {"python"})
            self.assertEqual(
                payload["languages"]["python"]["task_ids"],
                ["locate-prefix-path-enumerator"],
            )
            self.assertEqual(
                payload["selection"]["definition_count"],
                3,
            )
            self.assertEqual(
                payload["languages"]["python"][
                    "paired_assistance_treatment_summary"
                ][0]["contracted_exposure_state"],
                "contracted-successful-result",
            )
            self.assertEqual(
                set(payload["selection"]["definition_ids"]),
                selected_ids,
            )
            self.assertTrue(
                all(
                    conditions[str(row["condition_id"])]["agent"]
                    == "opencode-native"
                    for row in rows
                )
            )

    def test_offline_score_uses_projected_rows_and_preserves_output_on_failure(
        self,
    ) -> None:
        score_main = runpy.run_path(str(SCORE))["main"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            output = root / "score.json"
            calls = []

            def project(*, selected_definitions, **kwargs):
                calls.append(len(selected_definitions))
                return [{}], [
                    {
                        "current_definition_id": str(len(calls)),
                        "source_trial_id": "a" * 64,
                        "source_result_sha256": "b" * 64,
                        "projection_identity": "c" * 64,
                    }
                ]

            def report(*, projected_receipts, selected_definitions, **kwargs):
                self.assertEqual(projected_receipts, [{}])
                return {
                    "expected_trials": len(selected_definitions),
                    "observed_trials": len(selected_definitions),
                    "status_counts": {"PASS": len(selected_definitions)},
                    "campaign_qualification": {"status": "QUALIFIED"},
                    "conditions": {},
                    "paired_assistance": [],
                    "paired_assistance_summary": [],
                    "paired_assistance_exclusions": [],
                    "expected_assistance_pairs": 0,
                    "stability": [],
                    "task_agent_authority": [],
                    "subject_adoption": [],
                    "diagnostics": [],
                    "agent_profiles": {},
                    "cross_agent_observations": [],
                }

            argv = [
                str(SCORE),
                "--regrade-source-results",
                str(source),
                "--output",
                str(output),
                "--agent",
                "opencode-native",
            ]
            with (
                mock.patch.dict(
                    score_main.__globals__,
                    {
                        "project_campaign_receipts": project,
                        "build_report": report,
                    },
                ),
                mock.patch.object(sys, "argv", argv),
            ):
                self.assertEqual(score_main(), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["projection_mode"], "offline-regrade")
            self.assertEqual(len(payload["source_lineage"]), 2)
            self.assertEqual(calls, [54, 54])

            def fail(**kwargs):
                raise RegradeError("source changed")

            before = output.read_bytes()
            with (
                mock.patch.dict(
                    score_main.__globals__,
                    {
                        "project_campaign_receipts": fail,
                    },
                ),
                mock.patch.object(sys, "argv", argv),
                self.assertRaisesRegex(SystemExit, "source changed"),
            ):
                score_main()
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
