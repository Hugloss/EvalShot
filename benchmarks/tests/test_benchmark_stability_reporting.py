from __future__ import annotations

import copy
from collections import Counter
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness.campaign import campaign_status
from benchmarks.harness.report import build_report
from benchmarks.harness.suite import SuiteDefinition


def _suite() -> SuiteDefinition:
    experiment = {
        "id": "stability",
        "version": 1,
        "suite": "repository-intelligence",
        "tasks": ["task"],
        "conditions": [
            {
                "id": "bare",
                "subject": "none",
                "agent": "agent",
                "trials": 3,
                "seed": 10,
            },
            {
                "id": "hashmarks",
                "subject": "hashmarks",
                "agent": "agent",
                "trials": 3,
                "seed": 10,
            },
        ],
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
        "prompt": "find owner",
        "mutation": None,
        "oracle": {
            "adapter": "expected-json",
            "identity": {"id": "oracle", "version": "1"},
            "configuration": {"expected": {"ok": True}},
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
        },
        "hashmarks": {
            "id": "hashmarks",
            "kind": "repository_intelligence",
            "adapter": "hashmarks",
            "identity": {"id": "hashmarks", "version": "runtime-observed"},
            "capabilities": [],
            "configuration": {},
        },
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
    status: str,
    trial_id: str,
) -> dict[str, object]:
    condition = next(
        value
        for value in suite.experiment["conditions"]
        if value["id"] == row["condition_id"]
    )
    return {
        "definition_id": row["definition_id"],
        "trial_id": trial_id,
        "experiment": suite.experiment,
        "task": suite.tasks["task"],
        "condition": suite.expanded_condition(condition),
        "status": status,
        "authority": {"subject": {"available": False}},
        "execution": {
            "trial_index": int(row["trial"]),
            "seed": int(row["seed"]),
        },
        "measurements": {"agent": {}},
    }


class BenchmarkStabilityReportingTests(unittest.TestCase):
    def test_report_preserves_replicate_variance_and_paired_transitions(self) -> None:
        suite = _suite()
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        receipts = []
        bare_statuses = ("PASS", "FAIL", "PASS")
        hashmarks_statuses = ("PASS", "PASS", "PASS")
        for index in range(3):
            receipts.append(
                _receipt(
                    suite,
                    rows[("bare", index)],
                    bare_statuses[index],
                    ("a" if index == 0 else "b" if index == 1 else "c") * 64,
                )
            )
            receipts.append(
                _receipt(
                    suite,
                    rows[("hashmarks", index)],
                    hashmarks_statuses[index],
                    ("d" if index == 0 else "e" if index == 1 else "f") * 64,
                )
            )

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(suite=suite, results_root=Path("/unused"))

        self.assertEqual(report["schema"]["version"], 21)
        stability = {row["subject_id"]: row for row in report["stability"]}
        self.assertEqual(stability["none"]["state"], "unstable")
        self.assertEqual(stability["none"]["semantic_correct"], 2)
        self.assertEqual(stability["none"]["semantic_incorrect"], 1)
        self.assertEqual(stability["none"]["replicate_ids"], [10, 11, 12])
        self.assertEqual(stability["hashmarks"]["state"], "stable-correct")
        self.assertEqual(stability["hashmarks"]["semantic_correct"], 3)

        summary = report["paired_assistance_summary"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["subject_id"], "hashmarks")
        self.assertEqual(
            summary[0]["comparison_scope"],
            "subject-configured-condition-vs-bare",
        )
        self.assertTrue(summary[0]["attribution_requires_observed_subject_use"])
        self.assertEqual(
            summary[0]["contracted_attribution_requires"],
            "exact-required-operation-with-successful-nonempty-result",
        )
        self.assertEqual(
            summary[0]["transitions"],
            {
                "gain": 1,
                "preserved": 2,
                "unresolved": 0,
                "regression": 0,
            },
        )
        self.assertEqual(
            [row["assistance_transition"] for row in report["paired_assistance"]],
            ["preserved", "gain", "preserved"],
        )
        self.assertTrue(
            all(
                row["subject_use_state"] == "unknown"
                and row["attribution_interpretation"] == "invocation-unknown"
                for row in report["paired_assistance"]
            )
        )
        self.assertTrue(
            all(
                row["replicate_id"] == row["seed"]
                for row in report["paired_assistance"]
            )
        )
        self.assertFalse(
            report["authority"]["replicate_identity_is_provider_sampling_seed"]
        )

    def test_stability_distinguishes_raw_and_normalized_answer_variance(self) -> None:
        suite = _suite()
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        raw_answers = (
            '{"ok": true}',
            '```json\n{"ok": true}\n```',
            '{"ok":true}',
        )
        receipts = []
        selected = set()
        for index, answer in enumerate(raw_answers):
            row = rows[("bare", index)]
            selected.add(str(row["definition_id"]))
            receipt = _receipt(
                suite,
                row,
                "PASS",
                chr(ord("a") + index) * 64,
            )
            receipt["execution"]["agent_answer"] = answer
            receipt["scoring"] = {
                "oracle_grade": {
                    "semantic_success": True,
                    "semantic_gradeable": True,
                    "semantic_status": "CORRECT",
                    "format_compliant": index != 1,
                    "normalized_actual": {"ok": True},
                }
            }
            receipts.append(receipt)

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions=selected,
            )

        stability = report["stability"][0]
        self.assertEqual(stability["raw_answer_flip_rate"], 1.0)
        self.assertEqual(stability["raw_answer_flip_observations"], 3)
        self.assertEqual(stability["normalized_answer_flip_rate"], 0.0)
        self.assertEqual(stability["normalized_answer_flip_observations"], 3)
        self.assertEqual(stability["answer_flip_rate"], 1.0)
        self.assertEqual(
            stability["answer_flip_source"],
            "execution.agent_answer.raw-text",
        )
        self.assertEqual(
            stability["answer_distribution_source"],
            "scoring.oracle_grade.normalized_actual",
        )
        self.assertEqual(
            stability["answer_distribution"],
            {'{"ok": true}': 3},
        )

    def test_task_assistance_evidence_prevents_attributing_unused_subject_changes(self) -> None:
        suite = _suite()
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        bare_statuses = ("FAIL", "PASS", "PASS")
        assisted_statuses = ("PASS", "FAIL", "PASS")
        receipts = []
        for index in range(3):
            bare = _receipt(
                suite,
                rows[("bare", index)],
                bare_statuses[index],
                chr(ord("a") + index) * 64,
            )
            assisted = _receipt(
                suite,
                rows[("hashmarks", index)],
                assisted_statuses[index],
                chr(ord("d") + index) * 64,
            )
            assisted["authority"]["subject"]["available"] = True
            assisted["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": False,
                "subject_mcp_calls": 0,
                "subject_tool_names": [],
                "duration_ms": 120 + index,
                "tool_calls": 3,
                "mcp_calls": 0,
                "input_tokens": 1200,
                "output_tokens": 120,
            }
            bare["measurements"]["agent"] = {
                "duration_ms": 100 + index,
                "tool_calls": 2,
                "mcp_calls": 0,
                "input_tokens": 1000,
                "output_tokens": 100,
            }
            receipts.extend((bare, assisted))

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(suite=suite, results_root=Path("/unused"))

        self.assertEqual(len(report["task_assistance_evidence"]), 1)
        evidence = report["task_assistance_evidence"][0]
        self.assertEqual(evidence["task_id"], "task")
        self.assertEqual(evidence["condition_id"], "hashmarks")
        self.assertEqual(evidence["subject_id"], "hashmarks")
        self.assertEqual(evidence["expected_pairs"], 3)
        self.assertEqual(evidence["comparable_pairs"], 3)
        self.assertEqual(evidence["excluded_pairs"], 0)
        self.assertEqual(
            evidence["transitions"],
            {
                "gain": 1,
                "preserved": 1,
                "unresolved": 0,
                "regression": 1,
            },
        )
        self.assertEqual(
            evidence["transitions_by_invocation"]["not-invoked"],
            {
                "gain": 1,
                "preserved": 1,
                "unresolved": 0,
                "regression": 1,
            },
        )
        self.assertEqual(evidence["subject_use"]["invoked_pairs"], 0)
        self.assertEqual(evidence["subject_use"]["not_invoked_pairs"], 3)
        self.assertEqual(evidence["subject_use"]["subject_mcp_calls"], 0)
        self.assertEqual(evidence["subject_use"]["subject_tool_names"], [])
        self.assertTrue(
            all(
                row["subject_use_state"] == "not-invoked"
                and row["attribution_interpretation"]
                == "not-attributable-to-subject-tool"
                for row in report["paired_assistance"]
            )
        )
        self.assertIn("bare-headroom-observed", evidence["evidence_signals"])
        self.assertIn("subject-not-invoked", evidence["evidence_signals"])
        self.assertIn(
            "bare-headroom-without-subject-invocation",
            evidence["evidence_signals"],
        )
        self.assertIn(
            "regression-without-subject-invocation",
            evidence["evidence_signals"],
        )
        self.assertEqual(
            evidence["delta_metrics_by_invocation"]["not-invoked"]["input_tokens"],
            {
                "observations": 3,
                "mean": 200,
                "median": 200,
            },
        )
        exposure = report["campaign_qualification"]["subject_exposure"]
        self.assertEqual(exposure["status"], "FAIL")
        self.assertFalse(exposure["qualified"])
        self.assertEqual(exposure["failed_conditions"], 1)
        self.assertEqual(
            exposure["conditions"][0]["reason_codes"],
            ["subject-never-invoked"],
        )
        self.assertEqual(
            report["campaign_qualification"]["status"],
            "NOT_QUALIFIED",
        )

    def test_task_assistance_evidence_preserves_invoked_tool_names_and_effect(self) -> None:
        suite = _suite()
        suite.experiment["subject_exposure_contract"] = {
            "require_probe_contract": True
        }
        suite.subjects["hashmarks"]["exposure_probe"] = {
            "required_tool": "hashmarks_task_evidence"
        }
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        receipts = []
        for index in range(3):
            bare = _receipt(
                suite,
                rows[("bare", index)],
                "FAIL" if index == 0 else "PASS",
                chr(ord("a") + index) * 64,
            )
            assisted = _receipt(
                suite,
                rows[("hashmarks", index)],
                "PASS",
                chr(ord("d") + index) * 64,
            )
            invoked = index == 0
            assisted["authority"]["subject"]["available"] = True
            assisted["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": invoked,
                "subject_mcp_calls": 1 if invoked else 0,
                "subject_tool_names": ["task_evidence"] if invoked else [],
                "subject_tool_observability": "complete",
                "subject_tool_result_evidence": (
                    [
                        {
                            "operation": "task_evidence",
                            "status": "completed",
                            "result_bytes": 20,
                            "error_present": False,
                            "result_basis": "test",
                            "outcome": "successful-result-observed",
                        }
                    ]
                    if invoked
                    else []
                ),
                "duration_ms": 180 if invoked else 120,
                "tool_calls": 5 if invoked else 3,
                "mcp_calls": 1 if invoked else 0,
                "input_tokens": 1800 if invoked else 1200,
                "output_tokens": 140 if invoked else 110,
            }
            bare["measurements"]["agent"] = {
                "duration_ms": 100,
                "tool_calls": 2,
                "mcp_calls": 0,
                "input_tokens": 1000,
                "output_tokens": 100,
            }
            receipts.extend((bare, assisted))

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(suite=suite, results_root=Path("/unused"))

        evidence = report["task_assistance_evidence"][0]
        decision = report["decision_summary"]
        self.assertEqual(decision["headroom"]["selected_tasks"], 1)
        self.assertEqual(
            decision["headroom"]["tasks_with_bare_correctness_headroom"],
            1,
        )
        self.assertEqual(decision["headroom"]["headroom_task_ids"], ["task"])
        self.assertEqual(
            decision["headroom"]["generic_subject_use"]["gain_task_ids"],
            ["task"],
        )
        self.assertEqual(
            decision["headroom"]["contracted_treatment"]["gain_task_ids"],
            ["task"],
        )
        self.assertEqual(
            decision["headroom"]["contracted_treatment"]["gain_task_fraction"],
            1.0,
        )
        self.assertEqual(
            decision["claim_guardrails"]["subject_effect_attribution"],
            "exact-required-operation-with-successful-nonempty-result",
        )
        self.assertEqual(
            decision["claim_guardrails"]["generic_subject_invocation"],
            "routing-and-adoption-descriptive-only",
        )
        self.assertEqual(len(decision["subjects"]), 1)
        subject_decision = decision["subjects"][0]
        self.assertEqual(
            subject_decision["generic_subject_use"]["paired_when_invoked"][
                "transitions"
            ]["gain"],
            1,
        )
        self.assertEqual(
            subject_decision["generic_subject_use"]["gain_task_ids_when_invoked"],
            ["task"],
        )
        self.assertEqual(
            subject_decision["contracted_treatment"]["observed"]["transitions"][
                "gain"
            ],
            1,
        )
        self.assertEqual(
            subject_decision["contracted_treatment"]["gain_task_ids"],
            ["task"],
        )
        self.assertEqual(
            subject_decision["contracted_treatment"]["attribution_scope"],
            "exact-required-operation-with-successful-nonempty-result",
        )
        self.assertEqual(evidence["subject_use"]["invoked_pairs"], 1)
        self.assertEqual(evidence["subject_use"]["subject_mcp_calls"], 1)
        self.assertEqual(
            evidence["subject_use"]["subject_tool_names"],
            ["task_evidence"],
        )
        self.assertEqual(
            evidence["transitions_by_invocation"]["invoked"]["gain"],
            1,
        )
        self.assertIn("subject-invocation-observed", evidence["evidence_signals"])
        self.assertIn("invoked-gain-observed", evidence["evidence_signals"])
        self.assertNotIn("invoked-no-gain-observed", evidence["evidence_signals"])
        paired = report["paired_assistance"]
        self.assertEqual(paired[0]["subject_use_state"], "invoked")
        self.assertEqual(
            paired[0]["attribution_interpretation"],
            "contracted-treatment-observed",
        )
        self.assertEqual(
            paired[0]["subject_use_interpretation"],
            "subject-use-observed",
        )
        self.assertTrue(
            all(
                row["attribution_interpretation"]
                == "not-attributable-to-subject-tool"
                for row in paired[1:]
            )
        )
        exposure = report["campaign_qualification"]["subject_exposure"]
        self.assertEqual(exposure["status"], "PASS")
        self.assertTrue(exposure["qualified"])
        self.assertEqual(exposure["conditions"][0]["invoked_trials"], 1)
        self.assertEqual(report["campaign_qualification"]["status"], "QUALIFIED")

    def test_decision_summary_does_not_credit_wrong_operation_gain_as_treatment(
        self,
    ) -> None:
        suite = _suite()
        suite.experiment["subject_exposure_contract"] = {
            "require_probe_contract": True
        }
        suite.subjects["hashmarks"]["exposure_probe"] = {
            "required_tool": "hashmarks_task_evidence"
        }
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        receipts = []
        for index in range(3):
            bare_status = "FAIL" if index == 0 else "PASS"
            bare = _receipt(
                suite,
                rows[("bare", index)],
                bare_status,
                chr(ord("p") + index) * 64,
            )
            assisted = _receipt(
                suite,
                rows[("hashmarks", index)],
                "PASS",
                chr(ord("s") + index) * 64,
            )
            operation = "find" if index == 0 else "task_evidence"
            assisted["authority"]["subject"]["available"] = True
            assisted["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": True,
                "subject_mcp_calls": 1,
                "subject_tool_names": [operation],
                "subject_tool_observability": "complete",
                "subject_tool_result_evidence": [
                    {
                        "operation": operation,
                        "status": "completed",
                        "result_bytes": 20,
                        "error_present": False,
                        "result_basis": "test",
                        "outcome": "successful-result-observed",
                    }
                ],
            }
            receipts.extend((bare, assisted))

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
            )

        decision = report["decision_summary"]
        self.assertEqual(
            decision["headroom"]["generic_subject_use"]["gain_task_ids"],
            ["task"],
        )
        self.assertEqual(
            decision["headroom"]["contracted_treatment"]["gain_task_ids"],
            [],
        )
        self.assertEqual(
            decision["subjects"][0]["generic_subject_use"][
                "gain_task_ids_when_invoked"
            ],
            ["task"],
        )
        self.assertEqual(
            decision["subjects"][0]["contracted_treatment"]["gain_task_ids"],
            [],
        )
        treatment = {
            row["contracted_exposure_state"]: row
            for row in report["paired_assistance_treatment_summary"]
        }
        self.assertEqual(
            treatment["other-subject-operation"]["transitions"]["gain"],
            1,
        )
        self.assertEqual(
            treatment["contracted-successful-result"]["transitions"]["gain"],
            0,
        )

    def test_campaign_status_blocks_never_invoked_non_control_subject(self) -> None:
        suite = _suite()
        rows = suite.trial_definitions()
        receipts = []
        for index, row in enumerate(rows):
            receipt = _receipt(
                suite,
                row,
                "PASS",
                f"{index + 1:064x}",
            )
            if row["condition_id"] == "hashmarks":
                receipt["authority"]["subject"]["available"] = True
                receipt["measurements"]["agent"] = {
                    "subject_tool_configured": True,
                    "subject_tool_invoked": False,
                    "subject_mcp_calls": 0,
                    "subject_tool_names": [],
                }
            receipts.append(receipt)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for receipt in receipts:
                directory = root / str(receipt["trial_id"])
                directory.mkdir()
                (directory / "result.json").write_text(
                    json.dumps(receipt),
                    encoding="utf-8",
                )
            with mock.patch(
                "benchmarks.harness.campaign.verify_bundle",
                return_value=(True, None),
            ):
                status = campaign_status(
                    suite=suite,
                    results_root=root,
                    selected_definitions={
                        str(row["definition_id"])
                        for row in rows
                    },
                )

        self.assertFalse(status["qualified"])
        exposure = status["subject_exposure_qualification"]
        self.assertEqual(exposure["status"], "FAIL")
        self.assertEqual(exposure["failed_conditions"], 1)
        self.assertEqual(
            exposure["conditions"][0]["reason_codes"],
            ["subject-never-invoked"],
        )
        self.assertTrue(
            any(
                "subject-never-invoked" in issue
                for issue in status["health"]["issues"]
            )
        )

    def test_qualification_rejects_wrong_subject_tool_when_contract_requires_exact_operation(
        self,
    ) -> None:
        suite = _suite()
        suite.experiment["subject_exposure_contract"] = {
            "require_probe_contract": True
        }
        suite.subjects["hashmarks"]["exposure_probe"] = {
            "required_tool": "hashmarks_task_evidence"
        }
        rows = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == "hashmarks"
        ]
        receipts = []
        for index, row in enumerate(rows):
            receipt = _receipt(
                suite,
                row,
                "PASS",
                chr(ord("a") + index) * 64,
            )
            receipt["authority"]["subject"]["available"] = True
            receipt["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": True,
                "subject_mcp_calls": 1,
                "subject_tool_names": ["find"],
                "subject_tool_observability": "complete",
            }
            receipts.append(receipt)

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={
                    str(row["definition_id"])
                    for row in rows
                },
            )

        exposure = report["campaign_qualification"]["subject_exposure"]
        condition = exposure["conditions"][0]
        self.assertEqual(exposure["status"], "FAIL")
        self.assertFalse(exposure["qualified"])
        self.assertEqual(condition["invoked_trials"], 3)
        self.assertEqual(condition["required_tool"], "hashmarks_task_evidence")
        self.assertEqual(condition["required_operation"], "task_evidence")
        self.assertEqual(condition["required_tool_invoked_trials"], 0)
        self.assertEqual(
            condition["required_tool_name_observability_complete_trials"],
            3,
        )
        self.assertEqual(
            condition["reason_codes"],
            ["required-subject-tool-never-invoked"],
        )
        self.assertEqual(
            report["campaign_qualification"]["status"],
            "NOT_QUALIFIED",
        )

    def test_qualification_accepts_exact_contracted_operation_observed_once(
        self,
    ) -> None:
        suite = _suite()
        suite.experiment["subject_exposure_contract"] = {
            "require_probe_contract": True
        }
        suite.subjects["hashmarks"]["exposure_probe"] = {
            "required_tool": "hashmarks_task_evidence"
        }
        rows = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == "hashmarks"
        ]
        receipts = []
        for index, row in enumerate(rows):
            receipt = _receipt(
                suite,
                row,
                "PASS",
                chr(ord("d") + index) * 64,
            )
            names = ["task_evidence"] if index == 1 else ["find"]
            operation = names[0]
            receipt["authority"]["subject"]["available"] = True
            receipt["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": True,
                "subject_mcp_calls": 1,
                "subject_tool_names": names,
                "subject_tool_observability": "complete",
                "subject_tool_result_evidence": [
                    {
                        "operation": operation,
                        "status": "completed",
                        "result_bytes": 42,
                        "error_present": False,
                        "result_basis": "test-fixture",
                        "outcome": "successful-result-observed",
                    }
                ],
            }
            receipts.append(receipt)

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={
                    str(row["definition_id"])
                    for row in rows
                },
            )

        exposure = report["campaign_qualification"]["subject_exposure"]
        condition = exposure["conditions"][0]
        self.assertEqual(exposure["status"], "PASS")
        self.assertTrue(exposure["qualified"])
        self.assertEqual(condition["required_tool_invoked_trials"], 1)
        self.assertEqual(condition["required_tool_successful_result_trials"], 1)
        self.assertEqual(condition["required_tool_no_usable_result_trials"], 0)
        self.assertEqual(condition["required_tool_result_unproven_trials"], 0)
        self.assertEqual(condition["reason_codes"], [])
        self.assertEqual(
            report["campaign_qualification"]["status"],
            "QUALIFIED",
        )

    def test_qualification_rejects_exact_operation_with_no_usable_result(
        self,
    ) -> None:
        suite = _suite()
        suite.experiment["subject_exposure_contract"] = {
            "require_probe_contract": True
        }
        suite.subjects["hashmarks"]["exposure_probe"] = {
            "required_tool": "hashmarks_task_evidence"
        }
        rows = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == "hashmarks"
        ]
        receipts = []
        for index, row in enumerate(rows):
            receipt = _receipt(
                suite,
                row,
                "PASS",
                chr(ord("j") + index) * 64,
            )
            receipt["authority"]["subject"]["available"] = True
            receipt["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": True,
                "subject_mcp_calls": 1,
                "subject_tool_names": ["task_evidence"],
                "subject_tool_observability": "complete",
                "subject_tool_result_evidence": [
                    {
                        "operation": "task_evidence",
                        "status": "completed",
                        "result_bytes": 0,
                        "error_present": False,
                        "result_basis": "test-fixture",
                        "outcome": "empty-result",
                    }
                ],
            }
            receipts.append(receipt)

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={
                    str(row["definition_id"])
                    for row in rows
                },
            )

        condition = report["campaign_qualification"]["subject_exposure"][
            "conditions"
        ][0]
        self.assertEqual(condition["required_tool_invoked_trials"], 3)
        self.assertEqual(condition["required_tool_successful_result_trials"], 0)
        self.assertEqual(condition["required_tool_no_usable_result_trials"], 3)
        self.assertEqual(condition["required_tool_result_unproven_trials"], 0)
        self.assertEqual(
            condition["reason_codes"],
            ["required-subject-tool-no-usable-result"],
        )

    def test_qualification_fails_closed_when_exact_operation_result_is_unproven(
        self,
    ) -> None:
        suite = _suite()
        suite.experiment["subject_exposure_contract"] = {
            "require_probe_contract": True
        }
        suite.subjects["hashmarks"]["exposure_probe"] = {
            "required_tool": "hashmarks_task_evidence"
        }
        rows = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == "hashmarks"
        ]
        receipts = []
        for index, row in enumerate(rows):
            receipt = _receipt(
                suite,
                row,
                "PASS",
                chr(ord("m") + index) * 64,
            )
            receipt["authority"]["subject"]["available"] = True
            receipt["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": True,
                "subject_mcp_calls": 1,
                "subject_tool_names": ["task_evidence"],
                "subject_tool_observability": "complete",
                "subject_tool_result_evidence": [
                    {
                        "operation": "task_evidence",
                        "status": "completed",
                        "result_bytes": None,
                        "error_present": False,
                        "result_basis": "test-fixture",
                        "outcome": "result-unobserved",
                    }
                ],
            }
            receipts.append(receipt)

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={
                    str(row["definition_id"])
                    for row in rows
                },
            )

        condition = report["campaign_qualification"]["subject_exposure"][
            "conditions"
        ][0]
        self.assertEqual(condition["required_tool_invoked_trials"], 3)
        self.assertEqual(condition["required_tool_successful_result_trials"], 0)
        self.assertEqual(condition["required_tool_no_usable_result_trials"], 0)
        self.assertEqual(condition["required_tool_result_unproven_trials"], 3)
        self.assertEqual(
            condition["reason_codes"],
            ["required-subject-tool-result-unproven"],
        )

    def test_qualification_does_not_claim_wrong_tool_absence_when_name_observability_partial(
        self,
    ) -> None:
        suite = _suite()
        suite.experiment["subject_exposure_contract"] = {
            "require_probe_contract": True
        }
        suite.subjects["hashmarks"]["exposure_probe"] = {
            "required_tool": "hashmarks_task_evidence"
        }
        rows = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == "hashmarks"
        ]
        receipts = []
        for index, row in enumerate(rows):
            receipt = _receipt(
                suite,
                row,
                "PASS",
                chr(ord("g") + index) * 64,
            )
            receipt["authority"]["subject"]["available"] = True
            receipt["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": True,
                "subject_mcp_calls": 1,
                "subject_tool_names": ["find"],
                "subject_tool_observability": (
                    "partial" if index == 0 else "complete"
                ),
            }
            receipts.append(receipt)

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={
                    str(row["definition_id"])
                    for row in rows
                },
            )

        condition = report["campaign_qualification"]["subject_exposure"][
            "conditions"
        ][0]
        self.assertEqual(
            condition["reason_codes"],
            ["required-subject-tool-invocation-unproven"],
        )

    def test_paired_attribution_requires_successful_contracted_treatment(
        self,
    ) -> None:
        suite = _suite()
        suite.experiment["subject_exposure_contract"] = {
            "require_probe_contract": True
        }
        suite.subjects["hashmarks"]["exposure_probe"] = {
            "required_tool": "hashmarks_task_evidence"
        }
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        receipts = []
        assisted_specs = (
            (
                ["task_evidence"],
                [
                    {
                        "operation": "task_evidence",
                        "status": "completed",
                        "result_bytes": 20,
                        "error_present": False,
                        "result_basis": "test",
                        "outcome": "successful-result-observed",
                    }
                ],
            ),
            (
                ["find"],
                [
                    {
                        "operation": "find",
                        "status": "completed",
                        "result_bytes": 20,
                        "error_present": False,
                        "result_basis": "test",
                        "outcome": "successful-result-observed",
                    }
                ],
            ),
            (
                ["task_evidence"],
                [
                    {
                        "operation": "task_evidence",
                        "status": "completed",
                        "result_bytes": None,
                        "error_present": False,
                        "result_basis": "test",
                        "outcome": "result-unobserved",
                    }
                ],
            ),
        )
        for index, (names, result_evidence) in enumerate(assisted_specs):
            bare = _receipt(
                suite,
                rows[("bare", index)],
                "FAIL" if index == 0 else "PASS",
                chr(ord("a") + index) * 64,
            )
            assisted = _receipt(
                suite,
                rows[("hashmarks", index)],
                "PASS",
                chr(ord("d") + index) * 64,
            )
            bare["scoring"] = {
                "oracle_grade": {
                    "semantic_gradeable": True,
                    "semantic_success": index != 0,
                }
            }
            assisted["scoring"] = {
                "oracle_grade": {
                    "semantic_gradeable": True,
                    "semantic_success": True,
                }
            }
            assisted["authority"]["subject"]["available"] = True
            assisted["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": True,
                "subject_mcp_calls": 1,
                "subject_tool_names": names,
                "subject_tool_observability": "complete",
                "subject_tool_result_evidence": result_evidence,
                "duration_ms": 130 + index * 10,
                "input_tokens": 1200 + index * 100,
            }
            bare["measurements"]["agent"] = {
                "duration_ms": 100,
                "input_tokens": 1000,
            }
            receipts.extend((bare, assisted))

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
            )

        paired = report["paired_assistance"]
        self.assertEqual(
            [row["contracted_exposure_state"] for row in paired],
            [
                "contracted-successful-result",
                "other-subject-operation",
                "contracted-result-unproven",
            ],
        )
        self.assertEqual(
            [row["contracted_treatment_observed"] for row in paired],
            [True, False, None],
        )
        self.assertEqual(
            [row["attribution_interpretation"] for row in paired],
            [
                "contracted-treatment-observed",
                "subject-use-observed-without-contracted-operation",
                "contracted-operation-result-unproven",
            ],
        )
        self.assertTrue(
            all(
                row["subject_use_interpretation"] == "subject-use-observed"
                for row in paired
            )
        )

        treatment = {
            row["contracted_exposure_state"]: row
            for row in report["paired_assistance_treatment_summary"]
        }
        self.assertEqual(
            treatment["contracted-successful-result"]["transitions"]["gain"],
            1,
        )
        self.assertEqual(
            treatment["other-subject-operation"]["transitions"]["preserved"],
            1,
        )
        self.assertEqual(
            treatment["contracted-result-unproven"]["transitions"]["preserved"],
            1,
        )
        task = report["task_assistance_evidence"][0]
        self.assertEqual(
            task["contracted_treatment"],
            {
                "observed_pairs": 1,
                "definite_non_treatment_pairs": 1,
                "unproven_pairs": 1,
                "state_counts": {
                    "contracted-result-unproven": 1,
                    "contracted-successful-result": 1,
                    "other-subject-operation": 1,
                },
            },
        )
        self.assertIn(
            "contracted-treatment-observed",
            task["evidence_signals"],
        )
        self.assertIn(
            "subject-use-without-contracted-treatment",
            task["evidence_signals"],
        )
        self.assertIn(
            "contracted-treatment-unproven",
            task["evidence_signals"],
        )

    def test_report_summarizes_native_tool_strategy_without_raw_sequence(self) -> None:
        suite = _suite()
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        receipts = []
        for index in range(3):
            bare = _receipt(
                suite,
                rows[("bare", index)],
                "PASS",
                chr(ord("a") + index) * 64,
            )
            bare["measurements"]["agent"] = {
                "tool_strategy_observability": "codex-item-completed",
                "tool_name_counts": {"command_execution": 2},
                "tool_sequence": ["command_execution", "command_execution"],
                "subject_first_tool_call_ordinal": None,
            }
            assisted = _receipt(
                suite,
                rows[("hashmarks", index)],
                "PASS",
                chr(ord("d") + index) * 64,
            )
            assisted["measurements"]["agent"] = {
                "tool_strategy_observability": "codex-item-completed",
                "tool_name_counts": {
                    "command_execution": 2,
                    "mcp:hashmarks/task_evidence": 1,
                },
                "tool_sequence": [
                    "command_execution",
                    "mcp:hashmarks/task_evidence",
                    "command_execution",
                ],
                "subject_first_tool_call_ordinal": index + 2,
            }
            receipts.extend((bare, assisted))

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(suite=suite, results_root=Path("/unused"))

        strategy = report["conditions"]["hashmarks"]["tool_strategy"]
        self.assertEqual(
            strategy["observability_counts"],
            {"codex-item-completed": 3},
        )
        self.assertEqual(strategy["partial_observability_trials"], 0)
        self.assertEqual(
            strategy["tool_name_counts"],
            {
                "command_execution": 6,
                "mcp:hashmarks/task_evidence": 3,
            },
        )
        self.assertEqual(strategy["sequence_observations"], 3)
        self.assertEqual(
            strategy["sequence_length"],
            {
                "observations": 3,
                "mean": 3,
                "median": 3,
                "min": 3,
                "max": 3,
            },
        )
        self.assertEqual(
            strategy["subject_first_tool_call_ordinal"],
            {
                "observations": 3,
                "mean": 3,
                "median": 3,
                "min": 2,
                "max": 4,
            },
        )
        self.assertTrue(strategy["full_order_retained_in_receipts"])
        self.assertFalse(strategy["arguments_or_source_contents_included"])
        self.assertNotIn("tool_sequence", strategy)

    def test_report_separates_first_choice_late_rescue_and_never_invoked(
        self,
    ) -> None:
        suite = _suite()
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        receipts = []
        cases = (
            (
                ["mcp:hashmarks/task_evidence", "command_execution"],
                [1],
                True,
            ),
            (
                ["command_execution", "mcp:hashmarks/task_evidence"],
                [2],
                True,
            ),
            (
                ["command_execution"],
                [],
                False,
            ),
        )
        for index, (sequence, ordinals, invoked) in enumerate(cases):
            receipt = _receipt(
                suite,
                rows[("hashmarks", index)],
                "PASS",
                chr(ord("k") + index) * 64,
            )
            receipt["authority"]["subject"]["available"] = True
            receipt["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": invoked,
                "subject_mcp_calls": 1 if invoked else 0,
                "subject_tool_names": ["task_evidence"] if invoked else [],
                "tool_strategy_observability": "codex-item-completed",
                "tool_sequence": sequence,
                "tool_name_counts": dict(Counter(sequence)),
                "subject_tool_call_ordinals": ordinals,
                "subject_first_tool_call_ordinal": (
                    ordinals[0] if ordinals else None
                ),
            }
            receipts.append(receipt)

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={
                    str(rows[("hashmarks", index)]["definition_id"])
                    for index in range(3)
                },
            )

        strategy = report["conditions"]["hashmarks"]["tool_strategy"]
        self.assertEqual(
            strategy["subject_routing_timing"],
            {
                "observations": 3,
                "counts": {
                    "FIRST_CHOICE": 1,
                    "LATE_RESCUE": 1,
                    "NEVER_INVOKED": 1,
                },
                "invoked_ordered_observations": 2,
                "first_choice_rate": 0.5,
            },
        )

        adoption = report["subject_adoption"][0]
        self.assertEqual(
            adoption["routing_timing_counts"],
            {
                "FIRST_CHOICE": 1,
                "LATE_RESCUE": 1,
                "NEVER_INVOKED": 1,
            },
        )
        self.assertEqual(adoption["first_choice_trials"], 1)
        self.assertEqual(adoption["late_rescue_trials"], 1)
        self.assertEqual(adoption["never_invoked_timing_trials"], 1)
        self.assertEqual(adoption["routing_timing_unknown_trials"], 0)
        self.assertEqual(adoption["first_choice_rate"], 0.5)

    def test_format_contract_explains_saturated_noncompliance(self) -> None:
        suite = _suite()
        rows = suite.trial_definitions()[:3]
        receipts = []
        for index, row in enumerate(rows):
            receipt = _receipt(
                suite,
                row,
                "PASS",
                chr(ord("a") + index) * 64,
            )
            receipt["scoring"] = {
                "oracle_grade": {
                    "format_compliant": False,
                    "semantic_gradeable": True,
                    "semantic_status": "CORRECT",
                    "semantic_success": True,
                    "answer_shape": (
                        "JSON_FENCE"
                        if index < 2
                        else "PROSE_WITH_JSON_FENCE"
                    ),
                }
            }
            receipts.append(receipt)

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={str(row["definition_id"]) for row in rows},
            )

        contract = report["agent_profiles"]["agent"]["format_contract"]
        self.assertEqual(
            contract["state"],
            "strict-contract-saturated-noncompliant",
        )
        self.assertEqual(contract["observations"], 3)
        self.assertEqual(contract["compliant"], 0)
        self.assertEqual(contract["noncompliant"], 3)
        self.assertEqual(contract["semantic_gradeable"], 3)
        self.assertEqual(
            contract["answer_shapes"],
            {
                "JSON_FENCE": 2,
                "PROSE_WITH_JSON_FENCE": 1,
            },
        )
        self.assertIn("semantically gradeable", contract["interpretation"])

    def test_subject_adoption_distinguishes_available_but_unused_tools(self) -> None:
        suite = _suite()
        rows = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == "hashmarks"
        ]
        receipts = []
        for index, row in enumerate(rows):
            receipt = _receipt(
                suite,
                row,
                "FAIL" if index == 1 else "PASS",
                chr(ord("a") + index) * 64,
            )
            receipt["authority"]["subject"]["available"] = True
            receipt["measurements"]["agent"] = {
                "subject_tool_configured": True,
                "subject_tool_invoked": False,
                "subject_mcp_calls": 0,
                "subject_tool_names": [],
            }
            receipt["scoring"] = {
                "oracle_grade": {
                    "format_compliant": False if index == 1 else True,
                    "semantic_gradeable": False if index == 1 else True,
                    "semantic_status": "UNSCORABLE" if index == 1 else "CORRECT",
                    "semantic_success": False if index == 1 else True,
                }
            }
            receipts.append(receipt)

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={str(row["definition_id"]) for row in rows},
            )

        self.assertEqual(len(report["subject_adoption"]), 1)
        adoption = report["subject_adoption"][0]
        self.assertEqual(adoption["subject_id"], "hashmarks")
        self.assertEqual(adoption["trials"], 3)
        self.assertEqual(adoption["configured_trials"], 3)
        self.assertEqual(adoption["invoked_trials"], 0)
        self.assertEqual(adoption["not_invoked_trials"], 3)
        self.assertEqual(adoption["subject_mcp_calls"], 0)
        self.assertEqual(adoption["output_contract_failures"], 1)
        self.assertEqual(adoption["format_noncompliant_trials"], 1)
        self.assertEqual(adoption["state"], "configured-never-invoked")

    def test_paired_economics_separate_invoked_from_configured_unused(self) -> None:
        suite = _suite()
        rows = {
            (str(row["condition_id"]), int(row["trial"])): row
            for row in suite.trial_definitions()
        }
        receipts = []
        for index in range(3):
            bare = _receipt(
                suite,
                rows[("bare", index)],
                "PASS",
                chr(ord("a") + index) * 64,
            )
            bare["measurements"]["agent"] = {
                "duration_ms": 100,
                "command_calls": 1,
                "tool_calls": 2,
                "mcp_calls": 0,
                "subject_mcp_calls": 0,
                "input_tokens": 1000,
                "output_tokens": 100,
            }
            assisted = _receipt(
                suite,
                rows[("hashmarks", index)],
                "PASS",
                chr(ord("d") + index) * 64,
            )
            invoked = index == 2
            assisted["measurements"]["agent"] = {
                "duration_ms": 130 if not invoked else 180,
                "command_calls": 1,
                "tool_calls": 3 if not invoked else 5,
                "mcp_calls": 0 if not invoked else 1,
                "subject_mcp_calls": 0 if not invoked else 1,
                "subject_tool_invoked": invoked,
                "input_tokens": 1200 if not invoked else 1800,
                "output_tokens": 110 if not invoked else 140,
            }
            receipts.extend((bare, assisted))

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(suite=suite, results_root=Path("/unused"))

        summaries = {
            row["invocation_state"]: row
            for row in report["paired_assistance_usage_summary"]
            if row["subject_id"] == "hashmarks"
        }
        unused = summaries["not-invoked"]
        self.assertEqual(unused["total_pairs"], 2)
        self.assertEqual(unused["subject_mcp_calls"], 0)
        self.assertEqual(unused["transitions"]["preserved"], 2)
        self.assertEqual(unused["delta_metrics"]["input_tokens"]["mean"], 200)
        self.assertEqual(unused["delta_metrics"]["input_tokens"]["median"], 200)
        self.assertEqual(unused["delta_metrics"]["duration_ms"]["mean"], 30)

        used = summaries["invoked"]
        self.assertEqual(used["total_pairs"], 1)
        self.assertEqual(used["subject_mcp_calls"], 1)
        self.assertEqual(used["transitions"]["preserved"], 1)
        self.assertEqual(used["delta_metrics"]["mcp_calls"]["mean"], 1)
        self.assertEqual(used["delta_metrics"]["input_tokens"]["mean"], 800)
        self.assertEqual(used["delta_metrics"]["duration_ms"]["mean"], 80)

    def test_non_outcome_is_execution_instability_not_semantic_failure(self) -> None:
        suite = _suite()
        rows = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == "hashmarks"
        ]
        receipts = [
            _receipt(
                suite,
                row,
                "INCOMPLETE" if int(row["trial"]) == 1 else "PASS",
                chr(ord("a") + int(row["trial"])) * 64,
            )
            for row in rows
        ]
        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={str(row["definition_id"]) for row in rows},
            )
        self.assertEqual(report["stability"][0]["state"], "execution-unstable")
        self.assertEqual(report["stability"][0]["valid_outcomes"], 2)
        self.assertEqual(report["stability"][0]["semantic_correct"], 2)

    def test_unknown_outcome_cannot_qualify_report(self) -> None:
        suite = _suite()
        rows = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == "hashmarks"
        ]
        receipts = [
            _receipt(
                suite,
                row,
                "FUTURE_UNRESOLVED" if int(row["trial"]) == 1 else "PASS",
                chr(ord("a") + int(row["trial"])) * 64,
            )
            for row in rows
        ]
        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={str(row["definition_id"]) for row in rows},
            )

        self.assertEqual(
            report["campaign_qualification"]["status"],
            "NOT_QUALIFIED",
        )
        self.assertEqual(
            report["campaign_qualification"]["invalid_outcomes"],
            1,
        )
        self.assertEqual(
            report["conditions"]["hashmarks"]["valid_outcomes"],
            2,
        )

    def test_missing_replicate_remains_visible_in_stability_and_pair_exclusions(
        self,
    ) -> None:
        suite = _suite()
        rows = suite.trial_definitions()
        bare = next(row for row in rows if row["condition_id"] == "bare")
        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=[_receipt(suite, bare, "PASS", "a" * 64)],
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                require_complete=False,
            )
        by_subject = {row["subject_id"]: row for row in report["stability"]}
        self.assertEqual(by_subject["none"]["replicates"], 3)
        self.assertEqual(by_subject["none"]["observed_replicates"], 1)
        self.assertEqual(by_subject["none"]["state"], "execution-unstable")
        self.assertEqual(by_subject["hashmarks"]["replicates"], 3)
        self.assertEqual(by_subject["hashmarks"]["observed_replicates"], 0)
        self.assertEqual(len(report["paired_assistance_exclusions"]), 3)

    def test_ungradeable_fail_is_excluded_from_report_transition(self) -> None:
        suite = _suite()
        bare_row = next(row for row in suite.trial_definitions() if row["condition_id"] == "bare")
        assisted_row = next(
            row for row in suite.trial_definitions()
            if row["condition_id"] == "hashmarks"
        )
        bare = _receipt(suite, bare_row, "FAIL", "a" * 64)
        assisted = _receipt(suite, assisted_row, "PASS", "b" * 64)
        bare["scoring"] = {"oracle_grade": {"semantic_gradeable": False, "semantic_success": False}}
        assisted["scoring"] = {"oracle_grade": {"semantic_gradeable": True, "semantic_success": True}}
        with mock.patch("benchmarks.harness.report._receipts", return_value=[bare, assisted]):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={str(bare_row["definition_id"]), str(assisted_row["definition_id"])},
            )
        self.assertIsNone(report["paired_assistance"][0]["assistance_transition"])

    def test_receipt_diagnostic_converges_in_status_and_report(self) -> None:
        suite = _suite()
        row = suite.trial_definitions()[0]
        receipt = _receipt(suite, row, "INCOMPLETE", "a" * 64)
        receipt["diagnostic"] = {
            "stage": "campaign-recovery",
            "reason_code": "interrupted-launch",
            "detail": "prior launch did not publish a receipt",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / receipt["trial_id"]
            directory.mkdir()
            (directory / "result.json").write_text(json.dumps(receipt), encoding="utf-8")
            with mock.patch("benchmarks.harness.campaign.verify_bundle", return_value=(True, None)):
                status = campaign_status(
                    suite=suite,
                    results_root=root,
                    selected_definitions={str(row["definition_id"])},
                )
        self.assertEqual(status["rows"][0]["diagnostic"], {
            "stage": "campaign-recovery",
            "reason_code": "interrupted-launch",
        })
        with mock.patch("benchmarks.harness.report._receipts", return_value=[receipt]):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={str(row["definition_id"])},
            )
        diagnostic = report["diagnostics"][0]
        self.assertEqual(diagnostic["stage"], "campaign-recovery")
        self.assertEqual(diagnostic["reason_code"], "interrupted-launch")
        self.assertEqual(diagnostic["primary"], "runtime")
        self.assertEqual(diagnostic["diagnostic_source"], "receipt")

        del receipt["diagnostic"]
        with mock.patch("benchmarks.harness.report._receipts", return_value=[receipt]):
            legacy = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={str(row["definition_id"])},
            )
        self.assertIsNone(legacy["diagnostics"][0]["reason_code"])
        self.assertEqual(legacy["diagnostics"][0]["diagnostic_source"], "legacy-inferred")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / receipt["trial_id"]
            directory.mkdir()
            (directory / "result.json").write_text(json.dumps(receipt), encoding="utf-8")
            with mock.patch("benchmarks.harness.campaign.verify_bundle", return_value=(True, None)):
                legacy_status = campaign_status(
                    suite=suite,
                    results_root=root,
                    selected_definitions={str(row["definition_id"])},
                )
        self.assertIsNone(legacy_status["rows"][0]["diagnostic"])

    def test_report_diagnostic_preserves_bounded_receipt_reason_without_detail(self) -> None:
        suite = _suite()
        row = suite.trial_definitions()[0]
        receipt = _receipt(suite, row, "INCOMPLETE", "a" * 64)
        raw_reason = "agent terminal event was turn.failed: " + ("x" * 1_500)
        receipt["reason"] = raw_reason
        receipt["diagnostic"] = {
            "stage": "agent-execution",
            "reason_code": "agent-terminal-failed",
            "detail": "private traceback detail that must remain receipt-only",
        }

        with mock.patch("benchmarks.harness.report._receipts", return_value=[receipt]):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={str(row["definition_id"])},
            )

        diagnostic = report["diagnostics"][0]
        self.assertEqual(diagnostic["primary"], "agent-terminal")
        self.assertEqual(diagnostic["stage"], "agent-execution")
        self.assertEqual(diagnostic["reason_code"], "agent-terminal-failed")
        self.assertEqual(diagnostic["reason"], raw_reason[:1_000] + "…")
        self.assertNotIn("detail", diagnostic)

    def test_pair_exclusions_preserve_repeated_subject_conditions(self) -> None:
        original = _suite()
        experiment = copy.deepcopy(original.experiment)
        duplicate = copy.deepcopy(experiment["conditions"][1])
        duplicate["id"] = "hashmarks-extra"
        experiment["conditions"].append(duplicate)
        suite = SuiteDefinition(
            original.root, experiment, original.tasks, original.subjects, original.agents
        )
        bare = next(row for row in suite.trial_definitions() if row["condition_id"] == "bare")
        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=[_receipt(suite, bare, "PASS", "a" * 64)],
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                require_complete=False,
            )
        self.assertEqual(len(report["paired_assistance_exclusions"]), 6)
        self.assertEqual(report["expected_assistance_pairs"], 6)
        self.assertEqual(
            {item["condition_id"] for item in report["paired_assistance_exclusions"]},
            {"hashmarks", "hashmarks-extra"},
        )


if __name__ == "__main__":
    unittest.main()
