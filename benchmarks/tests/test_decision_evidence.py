from __future__ import annotations

import unittest

from benchmarks.harness.decision_evidence import build_decision_evidence


class DecisionEvidenceTests(unittest.TestCase):
    def test_decision_evidence_separates_runtime_semantic_format_and_assistance(self) -> None:
        report = {
            "expected_trials": 6,
            "observed_trials": 6,
            "status_counts": {"PASS": 3, "FAIL": 2, "INCOMPLETE": 1},
            "campaign_qualification": {
                "status": "NOT_QUALIFIED",
                "complete": True,
                "invalid_outcomes": 1,
            },
            "diagnostics": [
                {
                    "task_id": "runtime-task",
                    "primary": "agent-terminal",
                    "reason_code": "agent-terminal-failed",
                    "stage": "execution",
                },
                {
                    "task_id": "semantic-task",
                    "primary": "semantic-incorrect",
                    "reason_code": None,
                    "stage": None,
                },
            ],
            "stability": [
                {
                    "task_id": "semantic-task",
                    "agent_id": "opencode-native",
                    "subject_id": "hashmarks",
                    "state": "unstable",
                    "valid_outcomes": 3,
                    "gradeable_outcomes": 3,
                    "semantic_correct": 2,
                    "semantic_incorrect": 1,
                }
            ],
            "agent_profiles": {
                "opencode-native": {
                    "format_contract": {
                        "state": "strict-contract-saturated-noncompliant",
                        "observations": 3,
                        "compliant": 0,
                        "noncompliant": 3,
                        "answer_shapes": {"JSON_FENCE": 3},
                        "semantic_gradeable_observations": 3,
                        "semantic_gradeable": 3,
                        "interpretation": "strict format only",
                    },
                    "source_read_observability": [
                        "not-authoritatively-exposed-by-opencode-export"
                    ],
                    "tool_strategy": {
                        "observability_counts": {
                            "opencode-export-direct-only-partial": 1
                        },
                        "partial_observability_trials": 1,
                        "tool_name_counts": {"bash": 2},
                        "sequence_observations": 1,
                        "sequence_length": {
                            "observations": 1,
                            "mean": 2,
                            "median": 2,
                            "min": 2,
                            "max": 2,
                        },
                        "subject_first_tool_call_ordinal": {
                            "observations": 0,
                            "mean": None,
                            "median": None,
                            "min": None,
                            "max": None,
                        },
                        "full_order_retained_in_receipts": True,
                        "arguments_or_source_contents_included": False,
                    },
                }
            },
            "conditions": {},
            "subject_adoption": [
                {
                    "agent_id": "opencode-native",
                    "subject_id": "hashmarks",
                    "state": "invoked-some-observed",
                    "trials": 3,
                    "available_trials": 3,
                    "configured_trials": 3,
                    "invocation_observed_trials": 3,
                    "invoked_trials": 1,
                    "not_invoked_trials": 2,
                    "invocation_unknown_trials": 0,
                    "first_choice_trials": 0,
                    "late_rescue_trials": 1,
                    "never_invoked_timing_trials": 2,
                    "routing_timing_unknown_trials": 0,
                    "first_choice_rate": 0.0,
                }
            ],
            "paired_assistance_summary": [],
            "paired_assistance_usage_summary": [
                {
                    "agent_id": "opencode-native",
                    "subject_id": "hashmarks",
                    "invocation_state": "invoked",
                    "total_pairs": 1,
                    "subject_mcp_calls": 1,
                    "transitions": {
                        "gain": 1,
                        "preserved": 0,
                        "unresolved": 0,
                        "regression": 0,
                    },
                },
                {
                    "agent_id": "opencode-native",
                    "subject_id": "hashmarks",
                    "invocation_state": "not-invoked",
                    "total_pairs": 2,
                    "subject_mcp_calls": 0,
                    "transitions": {
                        "gain": 0,
                        "preserved": 1,
                        "unresolved": 0,
                        "regression": 1,
                    },
                },
            ],
            "paired_assistance_treatment_summary": [
                {
                    "agent_id": "opencode-native",
                    "subject_id": "hashmarks",
                    "contracted_exposure_state": "contracted-successful-result",
                    "contracted_treatment_observed": True,
                    "total_pairs": 1,
                    "transitions": {
                        "gain": 1,
                        "preserved": 0,
                        "unresolved": 0,
                        "regression": 0,
                    },
                },
                {
                    "agent_id": "opencode-native",
                    "subject_id": "hashmarks",
                    "contracted_exposure_state": "subject-not-invoked",
                    "contracted_treatment_observed": False,
                    "total_pairs": 2,
                    "transitions": {
                        "gain": 0,
                        "preserved": 1,
                        "unresolved": 0,
                        "regression": 1,
                    },
                },
            ],
            "decision_summary": {
                "claim_guardrails": {"overall_winner": "not-permitted"}
            },
            "task_assistance_evidence": [
                {
                    "task_id": "semantic-task",
                    "evidence_signals": [
                        "bare-headroom-observed",
                        "subject-not-invoked",
                    ],
                }
            ],
        }

        trace_diagnostics = {
            "summary": {
                "repository_intelligence_quality": {
                    "state": "observed",
                    "claim_scope": "descriptive-diagnostic-only",
                    "subjects": [
                        {
                            "subject_id": "hashmarks",
                            "operation": "task_evidence",
                            "calls": 1,
                        }
                    ],
                },
                "repository_intelligence_search_efficiency": {
                    "state": "observed",
                    "claim_scope": "descriptive-behavioral-only",
                    "correctness_joined": False,
                    "subjects": [
                        {
                            "subject_id": "hashmarks",
                            "evidence_observed_trials": 1,
                        }
                    ],
                },
                "repository_intelligence_evidence_to_action": {
                    "state": "observed",
                    "claim_scope": "descriptive-behavioral-only",
                    "oracle_relative": False,
                    "correctness_joined": False,
                    "subjects": [
                        {
                            "subject_id": "hashmarks",
                            "candidate_evidence_segments": 1,
                            "candidate_followed_segments": 1,
                        }
                    ],
                },
            }
        }
        evidence = build_decision_evidence(
            report,
            trace_diagnostics=trace_diagnostics,
        )

        self.assertEqual(
            evidence["schema"],
            "agents-cookbook-benchmark-decision-evidence.v8",
        )
        self.assertTrue(evidence["authority"]["derived_only"])
        self.assertEqual(
            evidence["decision_summary"]["claim_guardrails"]["overall_winner"],
            "not-permitted",
        )
        self.assertFalse(evidence["authority"]["ranking_performed"])
        self.assertFalse(evidence["authority"]["recommendation_performed"])
        self.assertEqual(
            evidence["authority"]["sources"],
            ["report.json", "trace-diagnostics.json"],
        )
        self.assertEqual(
            evidence["repository_intelligence_quality"]["state"],
            "observed",
        )
        self.assertEqual(
            evidence["repository_intelligence_quality"]["subjects"][0]["calls"],
            1,
        )
        self.assertEqual(
            evidence["repository_intelligence_search_efficiency"]["state"],
            "observed",
        )
        self.assertFalse(
            evidence["repository_intelligence_search_efficiency"][
                "correctness_joined"
            ]
        )
        self.assertEqual(
            evidence["repository_intelligence_evidence_to_action"]["state"],
            "observed",
        )
        self.assertFalse(
            evidence["repository_intelligence_evidence_to_action"][
                "oracle_relative"
            ]
        )
        self.assertFalse(
            evidence["repository_intelligence_evidence_to_action"][
                "correctness_joined"
            ]
        )
        self.assertEqual(evidence["surfaces"]["runtime"]["trials"], 1)
        self.assertEqual(
            evidence["surfaces"]["runtime"]["tasks"],
            ["runtime-task"],
        )
        self.assertEqual(
            evidence["surfaces"]["runtime"]["reason_codes"],
            {"agent-terminal-failed": 1},
        )
        self.assertEqual(
            evidence["surfaces"]["semantic"]["rows"][0]["task_id"],
            "semantic-task",
        )
        self.assertEqual(
            evidence["surfaces"]["assistance"]["task_signal_counts"],
            {
                "bare-headroom-observed": 1,
                "subject-not-invoked": 1,
            },
        )
        funnel = evidence["surfaces"]["assistance"]["funnel"][0]
        self.assertEqual(funnel["availability"]["rate"], 1.0)
        self.assertEqual(funnel["adoption"]["rate"], 1 / 3)
        self.assertEqual(
            funnel["routing_timing"],
            {
                "first_choice_trials": 0,
                "late_rescue_trials": 1,
                "never_invoked_trials": 2,
                "unknown_trials": 0,
                "first_choice_rate": 0.0,
            },
        )
        self.assertEqual(
            funnel["usefulness_when_invoked"]["transitions"],
            {
                "gain": 1,
                "preserved": 0,
                "unresolved": 0,
                "regression": 0,
            },
        )
        self.assertEqual(
            funnel["condition_outcomes_when_not_invoked"]["transitions"]["regression"],
            1,
        )
        self.assertEqual(
            funnel["contracted_treatment"]["observed"]["transitions"],
            {
                "gain": 1,
                "preserved": 0,
                "unresolved": 0,
                "regression": 0,
            },
        )
        self.assertEqual(
            funnel["contracted_treatment"]["definite_non_treatment"][
                "transitions"
            ]["regression"],
            1,
        )
        self.assertEqual(
            funnel["contracted_treatment"]["unproven"]["pairs"],
            0,
        )
        self.assertIn(
            "cannot be attributed",
            funnel["interpretation"]["not_invoked"],
        )
        self.assertEqual(
            evidence["evidence_gaps"]["source_read_observability"],
            ["not-authoritatively-exposed-by-opencode-export"],
        )
        self.assertEqual(
            evidence["evidence_gaps"]["tool_strategy_partial_trials"],
            1,
        )
        self.assertEqual(
            evidence["surfaces"]["tool_strategy"]["agent_profiles"][0][
                "tool_name_counts"
            ],
            {"bash": 2},
        )
        self.assertEqual(
            evidence["evidence_signals"],
            [
                "bare-headroom-observed",
                "late-rescue-observed",
                "native-tool-strategy-partially-observed",
                "repository-intelligence-evidence-to-action-observed",
                "repository-intelligence-quality-observed",
                "repository-intelligence-search-efficiency-observed",
                "runtime-or-host-instability-observed",
                "semantic-misses-observed",
                "source-read-archaeology-unavailable",
                "strict-format-saturation-observed",
                "subject-not-invoked",
            ],
        )

    def test_decision_evidence_tolerates_older_report_without_task_projection(self) -> None:
        evidence = build_decision_evidence(
            {
                "expected_trials": 1,
                "observed_trials": 1,
                "status_counts": {"PASS": 1},
                "campaign_qualification": {"status": "QUALIFIED"},
                "diagnostics": [],
                "stability": [],
                "agent_profiles": {},
                "conditions": {},
                "subject_adoption": [],
                "paired_assistance_summary": [],
                "paired_assistance_usage_summary": [],
                "paired_assistance_treatment_summary": [],
            }
        )

        self.assertEqual(
            evidence["surfaces"]["assistance"]["task_evidence"],
            [],
        )
        self.assertEqual(evidence["evidence_signals"], [])
        self.assertEqual(evidence["decision_summary"], {})
        self.assertEqual(
            evidence["repository_intelligence_quality"]["state"],
            "not-projected",
        )
        self.assertEqual(
            evidence["evidence_gaps"]["repository_intelligence_quality_state"],
            "not-projected",
        )
        self.assertEqual(
            evidence["repository_intelligence_search_efficiency"]["state"],
            "not-projected",
        )
        self.assertEqual(
            evidence["evidence_gaps"][
                "repository_intelligence_search_efficiency_state"
            ],
            "not-projected",
        )
        self.assertEqual(
            evidence["repository_intelligence_evidence_to_action"]["state"],
            "not-projected",
        )
        self.assertEqual(
            evidence["evidence_gaps"][
                "repository_intelligence_evidence_to_action_state"
            ],
            "not-projected",
        )


if __name__ == "__main__":
    unittest.main()
