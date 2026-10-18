from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from benchmarks.__main__ import main as benchmark_main
from benchmarks.adapters.opencode_native import OpenCodeNativeAgent
from benchmarks.adapters.registry import build_agent
from benchmarks.harness.identity import digest
from benchmarks.harness.model import TrialContext
from benchmarks.harness.oracle_reviews import validate_oracle_reviews
from benchmarks.harness.suite import load_suite
from benchmarks.harness.trace_diagnostics import (
    _hashmarks_evidence,
    _last_assistant_text_present,
    _opencode_calls,
    _repository_intelligence_evidence_to_action,
    _repository_intelligence_quality,
    _repository_intelligence_search_efficiency,
    _trial_evidence_to_action,
    _trial_search_efficiency,
    _terminal_failure_cause,
    build_trace_diagnostics,
)
from benchmarks.harness.subject_exposure import exposure_probe_required_tool
from benchmarks.tool_probe import prepare_tool_probe_suite
from benchmarks.tool_probe_score import (
    smoke_gate,
    main as tool_probe_score_main,
)
from benchmarks.tool_routing import (
    NATIVE_READ,
    NATIVE_SEARCH,
    OTHER,
    ROUTING_FIRST_CHOICE,
    ROUTING_LATE_RESCUE,
    ROUTING_NEVER_INVOKED,
    ROUTING_NOT_CONFIGURED,
    ROUTING_UNKNOWN,
    SHELL,
    SUBJECT_REPOSITORY_INTELLIGENCE,
    TOOL_ROUTER,
    catalog_admission,
    classify_call,
    classify_tool,
    matches_subject_operation,
    required_before_native_discovery,
    required_call_result,
    subject_routing_timing,
)


SUITE = (
    Path(__file__).resolve().parents[1]
    / "suites/repository-intelligence/heldout-v1"
)


class TraceAndToolProbeTests(unittest.TestCase):
    def test_subject_routing_timing_distinguishes_choice_rescue_and_absence(
        self,
    ) -> None:
        self.assertEqual(
            subject_routing_timing(
                ["mcp:hashmarks/task_evidence", "command_execution"],
                [1],
                configured=True,
                invocation_observed=True,
                order_complete=True,
            ),
            ROUTING_FIRST_CHOICE,
        )
        self.assertEqual(
            subject_routing_timing(
                ["command_execution", "mcp:hashmarks/task_evidence"],
                [2],
                configured=True,
                invocation_observed=True,
                order_complete=True,
            ),
            ROUTING_LATE_RESCUE,
        )
        self.assertEqual(
            subject_routing_timing(
                ["command_execution"],
                [],
                configured=True,
                invocation_observed=False,
                order_complete=True,
            ),
            ROUTING_NEVER_INVOKED,
        )
        self.assertEqual(
            subject_routing_timing(
                ["other_tool", "mcp:hashmarks/task_evidence"],
                [2],
                configured=True,
                invocation_observed=True,
                order_complete=False,
            ),
            ROUTING_UNKNOWN,
        )
        self.assertEqual(
            subject_routing_timing(
                ["nested:grep", "mcp:hashmarks/task_evidence"],
                [2],
                configured=True,
                invocation_observed=True,
                order_complete=False,
            ),
            ROUTING_LATE_RESCUE,
        )
        self.assertEqual(
            subject_routing_timing(
                [],
                [],
                configured=False,
                invocation_observed=False,
                order_complete=True,
            ),
            ROUTING_NOT_CONFIGURED,
        )
        self.assertEqual(
            classify_tool("command_execution", subject="hashmarks"),
            SHELL,
        )

    def test_hashmarks_trace_projection_reads_compact_v4_symbol_locator(self) -> None:
        evidence = _hashmarks_evidence(
            {
                "schema": "hashmarks.task-evidence.v4",
                "retrieval": {
                    "presentation": "compact-locators-v1",
                    "bounds": {"limit": 20, "per_role": 3},
                    "canonical_omitted_results": 2,
                    "supplemental_results": 2,
                    "results": [
                        {
                            "path": "owner.py",
                            "rank": 19,
                            "symbol": "pkg.owner",
                            "evidence_visibility": "source",
                        }
                    ],
                },
                "ownership": {
                    "status": "ambiguous",
                    "next_read": {
                        "path": "owner.py",
                        "reason": "ownership-ambiguity-discrimination",
                        "authority": "non-authoritative-discrimination",
                    },
                },
            },
            {"path": "owner.py", "symbol": "owner"},
        )

        self.assertEqual(evidence["schema"], "hashmarks.task-evidence.v4")
        self.assertEqual(evidence["expected_target_rank"], 1)
        self.assertEqual(evidence["canonical_omitted_results"], 2)
        self.assertEqual(evidence["supplemental_results"], 2)
        self.assertEqual(evidence["retrieval_truncation_state"], "not-reported")
        self.assertEqual(evidence["expected_target_observability"], "observed")
        self.assertEqual(evidence["ownership_status"], "ambiguous")
        self.assertEqual(evidence["next_read_path"], "owner.py")
        self.assertEqual(
            evidence["next_read_reason"],
            "ownership-ambiguity-discrimination",
        )
        self.assertEqual(
            evidence["next_read_authority"],
            "non-authoritative-discrimination",
        )

    def test_hashmarks_truncation_requires_reported_metadata(self) -> None:
        cases = (
            ({"bounds": {"canonical_truncation": "complete"}}, "complete"),
            ({"bounds": {"canonical_truncation": "truncated"}}, "truncated"),
            ({"bounds": {"canonical_truncation": "unknown"}}, "unknown"),
            ({"truncated": False}, "complete"),
            ({"truncated": True}, "truncated"),
            ({"bounds": {"canonical_truncation": []}}, "unknown"),
            ({"truncated": "false"}, "unknown"),
            (
                {"bounds": {"canonical_truncation": "complete"}, "truncated": True},
                "unknown",
            ),
            ({"bounds": {"limit": 20}, "results": [{}] * 20}, "not-reported"),
        )
        for retrieval, state in cases:
            with self.subTest(retrieval=retrieval):
                evidence = _hashmarks_evidence(
                    {"schema": "hashmarks.task-evidence.v4", "retrieval": retrieval},
                    None,
                )
                self.assertEqual(evidence["retrieval_truncation_state"], state)

    def test_evidence_to_action_uses_subject_candidates_not_oracle(self) -> None:
        packet = {
            "schema": "hashmarks.task-evidence.v3",
            "retrieval": {
                "results": [
                    {
                        "path": "pkg/candidate.py",
                        "symbol": "pkg.owner",
                    }
                ]
            },
            "ownership": {"status": "ambiguous"},
        }
        receipt = {
            "condition": {"subject": "hashmarks"},
            "execution": {"workspace_root": "/trial/workspace"},
            "task": {
                "oracle": {
                    "configuration": {
                        "expected": {
                            "path": "different/oracle.py",
                            "symbol": "different",
                        }
                    }
                }
            },
        }
        trace = {
            "messages": [
                {
                    "parts": [
                        {
                            "type": "tool",
                            "tool": "hashmarks_task_evidence",
                            "state": {
                                "status": "completed",
                                "input": {"task": "find owner"},
                                "output": json.dumps(packet),
                            },
                        },
                        {
                            "type": "tool",
                            "tool": "read",
                            "state": {
                                "status": "completed",
                                "input": {
                                    "filePath": "/trial/workspace/pkg/candidate.py"
                                },
                                "output": "source",
                            },
                        },
                        {
                            "type": "tool",
                            "tool": "grep",
                            "state": {
                                "status": "completed",
                                "input": {"pattern": "owner"},
                                "output": "match",
                            },
                        },
                        {
                            "type": "tool",
                            "tool": "read",
                            "state": {
                                "status": "completed",
                                "input": {
                                    "filePath": "/trial/workspace/pkg/other.py"
                                },
                                "output": "other",
                            },
                        },
                    ]
                }
            ]
        }

        calls = _opencode_calls(trace, receipt)
        self.assertEqual(
            calls[0]["repository_evidence"]["state"],
            "candidate-locators-observed",
        )
        self.assertEqual(
            calls[0]["hashmarks_evidence"]["expected_target_observability"],
            "absent-from-returned-candidates",
        )
        self.assertEqual(
            calls[1]["evidence_followup"]["match_basis"],
            ["candidate-path-read"],
        )
        self.assertTrue(calls[1]["evidence_followup"]["candidate_match"])
        self.assertEqual(
            calls[2]["evidence_followup"]["match_basis"],
            ["candidate-symbol-query"],
        )
        self.assertTrue(calls[2]["evidence_followup"]["candidate_match"])
        self.assertFalse(calls[3]["evidence_followup"]["candidate_match"])

        action = _trial_evidence_to_action(calls)
        self.assertIsNotNone(action)
        assert action is not None
        self.assertFalse(action["oracle_relative"])
        self.assertFalse(action["correctness_joined"])
        self.assertEqual(action["candidate_evidence_segments"], 1)
        segment = action["segments"][0]
        self.assertEqual(segment["state"], "candidate-followed")
        self.assertEqual(segment["candidate_followup_calls"], 2)
        self.assertEqual(
            segment["candidate_followup_basis_counts"],
            {
                "candidate-path-read": 1,
                "candidate-symbol-query": 1,
            },
        )
        self.assertEqual(segment["other_native_navigation_calls"], 1)

    def test_evidence_to_action_fails_closed_when_native_inputs_are_unobservable(self) -> None:
        calls = [
            {
                "ordinal": 1,
                "tool": "hashmarks_task_evidence",
                "tool_class": SUBJECT_REPOSITORY_INTELLIGENCE,
                "subject_call": True,
                "status": "completed",
                "result_bytes": 100,
                "repository_evidence": {
                    "state": "candidate-locators-observed",
                    "candidate_results": 1,
                    "candidate_paths_observed": 1,
                    "candidate_symbols_observed": 1,
                },
            },
            {
                "ordinal": 2,
                "tool": "read",
                "tool_class": NATIVE_READ,
                "subject_call": False,
                "input_sha256": None,
                "observability": "execute-metadata",
                "evidence_followup": {
                    "source_subject_ordinal": 1,
                    "candidate_match": None,
                    "match_basis": [],
                    "observability": "inputs-unavailable",
                },
            },
        ]

        action = _trial_evidence_to_action(calls)
        self.assertIsNotNone(action)
        assert action is not None
        segment = action["segments"][0]
        self.assertEqual(segment["state"], "followup-unresolved")
        self.assertEqual(segment["candidate_followup_calls"], 0)
        self.assertEqual(segment["unresolved_native_navigation_calls"], 1)

    def test_evidence_to_action_aggregate_stays_descriptive(self) -> None:
        rows = [
            {
                "subject_id": "hashmarks",
                "evidence_to_action": {
                    "candidate_evidence_segments": 2,
                    "segments": [
                        {
                            "state": "candidate-followed",
                            "candidate_followup_basis_counts": {
                                "candidate-path-read": 1
                            },
                            "first_candidate_followup_ordinal_delta": 1,
                            "other_native_navigation_calls": 0,
                            "unresolved_native_navigation_calls": 0,
                            "shell_followup_calls_unknown_semantics": 0,
                        },
                        {
                            "state": "other-native-navigation",
                            "candidate_followup_basis_counts": {},
                            "first_candidate_followup_ordinal_delta": None,
                            "other_native_navigation_calls": 2,
                            "unresolved_native_navigation_calls": 0,
                            "shell_followup_calls_unknown_semantics": 0,
                        },
                    ],
                },
            }
        ]

        summary = _repository_intelligence_evidence_to_action(rows)
        self.assertEqual(summary["state"], "observed")
        self.assertFalse(summary["oracle_relative"])
        self.assertFalse(summary["correctness_joined"])
        subject = summary["subjects"][0]
        self.assertEqual(subject["candidate_evidence_segments"], 2)
        self.assertEqual(
            subject["action_state_counts"],
            {
                "candidate-followed": 1,
                "other-native-navigation": 1,
            },
        )
        self.assertEqual(subject["candidate_followup_rate"], 0.5)
        self.assertEqual(
            subject["candidate_followup_basis_counts"],
            {"candidate-path-read": 1},
        )
        self.assertIn(
            "not equivalent to ignored evidence",
            subject["interpretation"]["no_observed_native_followup"],
        )
        self.assertIn(
            "does not consult the frozen oracle",
            subject["interpretation"]["oracle"],
        )

    def test_trial_search_efficiency_keeps_search_read_and_repeats_separate(self) -> None:
        calls = [
            {
                "ordinal": 1,
                "tool": "grep",
                "tool_class": NATIVE_SEARCH,
                "subject_call": False,
                "input_sha256": "search-a",
            },
            {
                "ordinal": 2,
                "tool": "hashmarks_task_evidence",
                "tool_class": SUBJECT_REPOSITORY_INTELLIGENCE,
                "subject_call": True,
                "status": "completed",
                "result_bytes": 1200,
                "input_sha256": "subject",
                "hashmarks_evidence": {
                    "next_read_path": "owner.py",
                },
            },
            {
                "ordinal": 3,
                "tool": "grep",
                "tool_class": NATIVE_SEARCH,
                "subject_call": False,
                "input_sha256": "search-a",
            },
            {
                "ordinal": 4,
                "tool": "read",
                "tool_class": NATIVE_READ,
                "subject_call": False,
                "input_sha256": "read-a",
                "path_attempted": "owner.py",
            },
            {
                "ordinal": 5,
                "tool": "bash",
                "tool_class": SHELL,
                "subject_call": False,
                "input_sha256": "shell-a",
            },
        ]

        metrics = _trial_search_efficiency(calls)
        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertEqual(metrics["pre_subject_native_search_calls"], 1)
        self.assertEqual(metrics["post_subject_native_search_calls"], 1)
        self.assertEqual(metrics["post_subject_native_read_calls"], 1)
        self.assertEqual(metrics["post_subject_native_navigation_calls"], 2)
        self.assertEqual(metrics["post_subject_shell_calls"], 1)
        self.assertFalse(metrics["no_native_search_after_subject"])
        self.assertFalse(metrics["no_native_navigation_after_subject"])
        self.assertEqual(metrics["exact_repeat_native_navigation_calls"], 1)
        self.assertEqual(
            metrics["post_subject_exact_repeat_native_navigation_calls"],
            1,
        )
        self.assertEqual(metrics["subject_result_observed_calls"], 1)
        self.assertEqual(
            metrics["subject_next_read_guidance"],
            {
                "emitted_calls": 1,
                "followup_counts": {"search-first": 1},
                "advised_read_first_rate": 0.0,
                "claim_scope": "descriptive-followup-only",
            },
        )

    def test_trial_search_efficiency_classifies_next_read_followups(self) -> None:
        def measure(followup: dict[str, object] | None) -> dict[str, object]:
            calls: list[dict[str, object]] = [
                {
                    "ordinal": 1,
                    "tool": "hashmarks_task_evidence",
                    "tool_class": SUBJECT_REPOSITORY_INTELLIGENCE,
                    "subject_call": True,
                    "status": "completed",
                    "result_bytes": 100,
                    "hashmarks_evidence": {"next_read_path": "owner.py"},
                }
            ]
            if followup is not None:
                calls.append({"ordinal": 2, **followup})
            metrics = _trial_search_efficiency(calls)
            assert metrics is not None
            return metrics["subject_next_read_guidance"]

        self.assertEqual(
            measure(
                {
                    "tool": "read",
                    "tool_class": NATIVE_READ,
                    "subject_call": False,
                    "path_attempted": "owner.py",
                }
            )["followup_counts"],
            {"advised-read-first": 1},
        )
        self.assertEqual(
            measure(
                {
                    "tool": "read",
                    "tool_class": NATIVE_READ,
                    "subject_call": False,
                    "path_attempted": "other.py",
                }
            )["followup_counts"],
            {"different-read-first": 1},
        )
        self.assertEqual(
            measure(
                {
                    "tool": "grep",
                    "tool_class": NATIVE_SEARCH,
                    "subject_call": False,
                }
            )["followup_counts"],
            {"search-first": 1},
        )
        self.assertEqual(
            measure(None)["followup_counts"],
            {"no-native-navigation": 1},
        )

    def test_search_efficiency_compares_only_observed_subject_evidence_to_bare(self) -> None:
        def row(
            *,
            subject_id: str,
            invoked: bool | None,
            calls: list[dict[str, object]],
        ) -> dict[str, object]:
            return {
                "task_id": "task",
                "agent_id": "agent",
                "trial_index": 0,
                "replicate_id": 10,
                "context_group": None,
                "context_variant": None,
                "subject_id": subject_id,
                "subject_invocation_observed": invoked,
                "search_efficiency": _trial_search_efficiency(calls),
            }

        bare = row(
            subject_id="none",
            invoked=None,
            calls=[
                {
                    "ordinal": 1,
                    "tool": "grep",
                    "tool_class": NATIVE_SEARCH,
                    "subject_call": False,
                    "input_sha256": "a",
                },
                {
                    "ordinal": 2,
                    "tool": "grep",
                    "tool_class": NATIVE_SEARCH,
                    "subject_call": False,
                    "input_sha256": "b",
                },
                {
                    "ordinal": 3,
                    "tool": "grep",
                    "tool_class": NATIVE_SEARCH,
                    "subject_call": False,
                    "input_sha256": "a",
                },
                {
                    "ordinal": 4,
                    "tool": "read",
                    "tool_class": NATIVE_READ,
                    "subject_call": False,
                    "input_sha256": "r",
                    "path_attempted": "owner.py",
                },
            ],
        )
        assisted = row(
            subject_id="hashmarks",
            invoked=True,
            calls=[
                {
                    "ordinal": 1,
                    "tool": "hashmarks_task_evidence",
                    "tool_class": SUBJECT_REPOSITORY_INTELLIGENCE,
                    "subject_call": True,
                    "status": "completed",
                    "result_bytes": 900,
                    "input_sha256": "subject",
                    "hashmarks_evidence": {
                        "next_read_path": "owner.py",
                    },
                },
                {
                    "ordinal": 2,
                    "tool": "grep",
                    "tool_class": NATIVE_SEARCH,
                    "subject_call": False,
                    "input_sha256": "a",
                },
                {
                    "ordinal": 3,
                    "tool": "read",
                    "tool_class": NATIVE_READ,
                    "subject_call": False,
                    "input_sha256": "r",
                },
            ],
        )

        summary = _repository_intelligence_search_efficiency([bare, assisted])
        self.assertEqual(summary["state"], "observed")
        self.assertFalse(summary["correctness_joined"])
        subject = summary["subjects"][0]
        self.assertEqual(subject["subject_id"], "hashmarks")
        self.assertEqual(subject["evidence_observed_trials"], 1)
        self.assertEqual(subject["paired_vs_bare"]["comparable_pairs"], 1)
        self.assertEqual(
            subject["post_subject"]["next_read_guidance"],
            {
                "emitted_calls": 1,
                "followup_counts": {"search-first": 1},
                "advised_read_first_rate": 0.0,
                "claim_scope": "descriptive-followup-only",
            },
        )
        self.assertEqual(
            subject["paired_vs_bare"]["native_search_calls_delta"],
            {
                "observations": 1,
                "mean": -2,
                "median": -2,
                "min": -2,
                "max": -2,
                "reduced": 1,
                "same": 0,
                "increased": 0,
            },
        )
        self.assertEqual(
            subject["paired_vs_bare"][
                "exact_repeat_native_navigation_calls_delta"
            ]["mean"],
            -1,
        )
        self.assertIn(
            "fewer observed calls are not scored as better",
            subject["interpretation"]["correctness"],
        )
        self.assertIn(
            "shell command semantics are not inferred",
            subject["interpretation"]["native_search"],
        )

    def test_retrieval_quality_summary_is_aggregate_and_non_optimizing(self) -> None:
        rows = [
            {
                "calls": [
                    {
                        "result_bytes": 20831,
                        "hashmarks_evidence": {
                            "packet_status": "parsed",
                            "schema": "hashmarks.task-evidence.v3",
                            "retrieval_count": 20,
                            "retrieval_truncation_state": "complete",
                            "expected_target_rank": 19,
                            "expected_target_observability": "observed",
                            "ownership_status": "ambiguous",
                        },
                    }
                ]
            },
            {
                "calls": [
                    {
                        "result_bytes": 8000,
                        "hashmarks_evidence": {
                            "packet_status": "parsed",
                            "schema": "hashmarks.task-evidence.v3",
                            "retrieval_count": 10,
                            "retrieval_truncation_state": "complete",
                            "expected_target_rank": None,
                            "expected_target_observability": (
                                "absent-from-returned-candidates"
                            ),
                            "ownership_status": "unresolved",
                        },
                    }
                ]
            },
            {
                "calls": [
                    {
                        "result_bytes": 16071,
                        "hashmarks_evidence": {
                            "packet_status": "parsed",
                            "schema": "hashmarks.task-evidence.v3",
                            "retrieval_count": 17,
                            "retrieval_truncation_state": "truncated",
                            "expected_target_rank": 14,
                            "expected_target_observability": "observed",
                            "ownership_status": "ambiguous",
                        },
                    }
                ]
            },
            {
                "calls": [
                    {
                        "result_bytes": 500,
                        "hashmarks_evidence": {
                            "packet_status": "unparseable",
                        },
                    }
                ]
            },
        ]

        quality = _repository_intelligence_quality(rows)
        self.assertEqual(quality["state"], "observed")
        self.assertEqual(quality["claim_scope"], "descriptive-diagnostic-only")
        self.assertEqual(len(quality["subjects"]), 1)
        subject = quality["subjects"][0]
        self.assertEqual(subject["subject_id"], "hashmarks")
        self.assertEqual(subject["operation"], "task_evidence")
        self.assertEqual(subject["calls"], 4)
        self.assertEqual(
            subject["packet_status_counts"],
            {"parsed": 3, "unparseable": 1},
        )
        self.assertEqual(
            subject["candidate_set_size"],
            {
                "observations": 3,
                "mean": 47 / 3,
                "median": 17,
                "min": 10,
                "max": 20,
            },
        )
        self.assertEqual(
            subject["retrieval_truncation"],
            {"complete": 2, "truncated": 1, "unknown": 1, "not-reported": 0},
        )
        self.assertEqual(
            subject["canonical_omission"],
            {
                "calls_with_omission": 0,
                "observed_calls": 0,
                "unknown_calls": 4,
                "omitted_results": {
                    "observations": 0,
                    "mean": None,
                    "median": None,
                    "min": None,
                    "max": None,
                },
            },
        )
        self.assertEqual(
            subject["oracle_relative_target_observability"],
            {
                "counts": {
                    "absent-from-returned-candidates": 1,
                    "observed": 2,
                },
                "evaluable_calls": 3,
                "observed_calls": 2,
                "coverage_rate": 2 / 3,
            },
        )
        self.assertEqual(
            subject["oracle_relative_target_rank"]["buckets"],
            {"1": 0, "2-5": 0, "6-10": 0, "11+": 2},
        )
        self.assertEqual(
            subject["ownership"],
            {
                "observations": 3,
                "status_counts": {"ambiguous": 2, "unresolved": 1},
                "ambiguous_rate": 2 / 3,
                "unresolved_rate": 1 / 3,
            },
        )
        self.assertEqual(subject["evidence_sufficiency"]["state"], "partial")
        self.assertEqual(subject["evidence_sufficiency"]["complete_calls"], 3)
        self.assertEqual(subject["evidence_sufficiency"]["complete_rate"], 3 / 4)
        self.assertIn(
            "not a product acceptance threshold or optimization target",
            subject["interpretation"]["target_rank"],
        )

    def test_hashmarks_presentation_omission_is_not_retrieval_truncation(self) -> None:
        quality = _repository_intelligence_quality([{"calls": [{
            "result_bytes": 17838,
            "hashmarks_evidence": {
                "packet_status": "parsed",
                "retrieval_count": 20,
                "retrieval_truncation_state": "not-reported",
                "canonical_omitted_results": 2,
                "supplemental_results": 2,
                "expected_target_observability": "observed",
                "expected_target_rank": 19,
                "ownership_status": "ambiguous",
            },
        }]}])
        subject = quality["subjects"][0]
        self.assertEqual(subject["retrieval_truncation"]["not-reported"], 1)
        self.assertEqual(subject["canonical_omission"]["calls_with_omission"], 1)
        self.assertEqual(subject["canonical_omission"]["omitted_results"]["mean"], 2)
        self.assertEqual(subject["supplemental_results"]["mean"], 2)

    def test_terminal_failure_cause_retains_incomplete_outcome(self) -> None:
        receipt = {
            "status": "INCOMPLETE",
            "execution": {"agent_terminal": {
                "type": "turn.failed",
                "reason": "opencode export: terminal assistant message has no final text",
            }},
        }
        self.assertEqual(
            _terminal_failure_cause(receipt, "permission-denied"),
            "permission-denied",
        )
        self.assertEqual(
            _terminal_failure_cause(receipt, None),
            "no-final-text-unexplained",
        )
        receipt["execution"]["agent_terminal"]["reason"] = (
            "OpenCode run exited: ContextOverflowError"
        )
        self.assertEqual(
            _terminal_failure_cause(receipt, None),
            "context-overflow",
        )
        receipt["execution"]["agent_terminal"]["reason"] = (
            'OpenCode run exited: APIError {"message":"Cannot connect to API",'
            '"isRetryable":true}'
        )
        self.assertEqual(
            _terminal_failure_cause(receipt, None),
            "provider-connection-failed",
        )
        receipt["status"] = "PASS"
        self.assertIsNone(_terminal_failure_cause(receipt, None))

    def test_trace_projection_separates_denial_from_retrieval_absence(self) -> None:
        packet = {
            "schema": "hashmarks.task-evidence.v2",
            "retrieval": {"results": [{"path": "other.py", "name": "other"}]},
            "ownership": {"status": "ambiguous"},
        }
        receipt = {
            "condition": {"subject": "hashmarks"},
            "execution": {"workspace_root": "/trial/workspace"},
            "task": {"oracle": {"configuration": {"expected": {
                "path": "owner.py", "symbol": "owner"
            }}}},
        }
        trace = {"messages": [{"parts": [
            {"type": "tool", "tool": "hashmarks_task_evidence", "state": {
                "status": "completed", "input": {"task": "find owner"},
                "output": json.dumps(packet),
            }},
            {"type": "tool", "tool": "read", "state": {
                "status": "error", "input": {"filePath": "/trial/workspace/owner.py"},
                "output": "The user rejected permission to use this specific tool call.",
            }},
        ]}]}
        calls = _opencode_calls(trace, receipt)
        self.assertEqual(calls[0]["hashmarks_evidence"]["expected_target_rank"], None)
        self.assertEqual(
            calls[0]["hashmarks_evidence"]["expected_target_observability"],
            "absent-from-returned-candidates",
        )
        self.assertEqual(calls[1]["failure"], "permission-denied")
        self.assertEqual(calls[1]["path_attempted"], "owner.py")
        self.assertNotIn("output", calls[0])
        self.assertEqual(
            _hashmarks_evidence("not json", {"path": "owner.py", "symbol": "owner"}),
            {"packet_status": "unparseable"},
        )
        self.assertFalse(_last_assistant_text_present({"messages": [
            {"info": {"role": "assistant"}, "parts": [{"type": "tool"}]},
        ]}))
        self.assertTrue(_last_assistant_text_present({"messages": [
            {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "answer"}]},
        ]}))

    def test_verified_bundle_projection_does_not_guess_unknown_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial = root / ("a" * 64)
            trial.mkdir()
            (trial / "result.json").write_text(json.dumps({
                "trial_id": trial.name, "definition_id": "definition",
                "condition": {"id": "enola", "subject": "enola"},
                "task": {"id": "task"},
                "execution": {"artifacts": {}},
                "measurements": {"agent": {}},
            }), encoding="utf-8")
            with mock.patch(
                "benchmarks.harness.trace_diagnostics.verify_bundle",
                return_value=(True, None),
            ):
                result = build_trace_diagnostics(root)
            self.assertEqual(result["summary"]["trace_states"], {"missing": 1})
            self.assertIsNone(result["trials"][0]["calls"])
            self.assertEqual(result["summary"]["subject_calls"], {})

    def test_tool_probe_reuses_exact_reviewed_tasks_and_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "hashmarks-probe"
            evidence = prepare_tool_probe_suite(
                source_suite=SUITE, destination=destination, subject="hashmarks"
            )
            suite = load_suite(destination)
            self.assertEqual(len(suite.trial_definitions()), 9)
            self.assertEqual(evidence["required_tool"], "hashmarks_task_evidence")
            self.assertTrue(validate_oracle_reviews(suite, require_complete=True)["complete"])
            self.assertEqual(suite.tasks["locate-prefix-path-enumerator"],
                             load_suite(SUITE).tasks["locate-prefix-path-enumerator"])
            self.assertEqual(
                (destination / "tasks/locate-prefix-path-enumerator.json").read_bytes(),
                (SUITE / "tasks/locate-prefix-path-enumerator.json").read_bytes(),
            )
            self.assertEqual(suite.agents["opencode-native"]["configuration"]["diagnostic_required_tool"],
                             "hashmarks_task_evidence")
            self.assertTrue((destination / "score.py").is_file())
            self.assertIn(
                f"BENCHMARK_SUITE_PATH={destination}",
                (destination / ".env.example").read_text(encoding="utf-8"),
            )
            self.assertEqual(prepare_tool_probe_suite(
                source_suite=SUITE, destination=destination, subject="hashmarks", reuse=True
            ), evidence)

    def test_tool_probe_generator_accepts_future_subject_contract_without_code_map(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            shutil.copytree(SUITE, source)
            (source / "subjects/futuremcp.json").write_text(
                json.dumps(
                    {
                        "id": "futuremcp",
                        "kind": "repository_intelligence",
                        "adapter": "future-adapter",
                        "identity": {"id": "futuremcp", "version": "1"},
                        "capabilities": ["search"],
                        "configuration": {},
                        "exposure_probe": {
                            "required_tool": "futuremcp_context",
                        },
                    }
                ),
                encoding="utf-8",
            )
            destination = root / "futuremcp-probe"

            evidence = prepare_tool_probe_suite(
                source_suite=source,
                destination=destination,
                subject="futuremcp",
                task_ids=("locate-prefix-path-enumerator",),
            )

            generated = load_suite(destination)
            self.assertEqual(evidence["subject"], "futuremcp")
            self.assertEqual(evidence["required_tool"], "futuremcp_context")
            self.assertEqual(
                generated.agents["opencode-native"]["configuration"][
                    "diagnostic_required_tool"
                ],
                "futuremcp_context",
            )
            self.assertEqual(
                generated.subjects["futuremcp"]["exposure_probe"],
                {"required_tool": "futuremcp_context"},
            )

    def test_diagnostic_agent_adds_required_instruction_only_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            control = root / "control"
            workspace.mkdir()
            control.mkdir()
            (control / "opencode-native-evidence.json").write_text(json.dumps({
                "selected_server": "hashmarks",
                "opencode_executable_path": str(Path("/bin/opencode").resolve()),
                "opencode_executable_sha256": "c" * 64,
                "native_config_sha256": "a" * 64,
                "subject_exposure_sha256": "b" * 64,
                "native_subject_identity": {"executable_sha256": "d" * 64},
                "native_subject_identity_sha256": digest(
                    {"executable_sha256": "d" * 64}
                ),
                "native_mcp_servers": [],
            }), encoding="utf-8")
            context = TrialContext(workspace, control, {"BENCHMARK_OPENCODE_AGENT": "build"})
            process = mock.Mock(elapsed_ms=1, stdout=b"", stderr=b"",
                                executable_missing=False, stdout_truncated=False)
            process.metrics.return_value = {}
            agent = OpenCodeNativeAgent(diagnostic_required_tool="hashmarks_task_evidence")
            with mock.patch("benchmarks.adapters.opencode_native._native_environment",
                            return_value={"OPENCODE_BIN": "/bin/opencode"}), mock.patch(
                "benchmarks.adapters.opencode_native._sha256_file",
                return_value="c" * 64,
            ), mock.patch(
                "benchmarks.adapters.opencode_native._runtime_call", return_value=(None, process)
            ):
                agent.run(context, "Reviewed task", None)
            prompt = (control / "opencode-prompt.txt").read_text(encoding="utf-8")
            self.assertIn("call hashmarks_task_evidence", prompt)
            self.assertTrue(prompt.endswith("Reviewed task"))

    def test_agent_definition_accepts_generic_diagnostic_tool(self) -> None:
        agent = dict(load_suite(SUITE).agents["opencode-native"])
        agent["configuration"] = {"diagnostic_required_tool": "futuremcp_context"}
        built = build_agent(agent, budgets={"timeout_seconds": 600})
        self.assertEqual(built.diagnostic_required_tool, "futuremcp_context")

    def test_agent_definition_rejects_invalid_diagnostic_tool(self) -> None:
        agent = dict(load_suite(SUITE).agents["opencode-native"])
        agent["configuration"] = {"diagnostic_required_tool": " futuremcp_context "}
        with self.assertRaisesRegex(ValueError, "canonical tool name"):
            build_agent(agent, budgets={"timeout_seconds": 600})

    def test_exposure_probe_contract_is_subject_data_not_product_code(self) -> None:
        suite = load_suite(SUITE)
        suite.subjects["futuremcp"] = {
            "id": "futuremcp",
            "kind": "repository_intelligence",
            "adapter": "future-adapter",
            "identity": {"id": "futuremcp", "version": "1"},
            "capabilities": ["search"],
            "configuration": {},
            "exposure_probe": {"required_tool": "futuremcp_context"},
        }
        self.assertEqual(
            exposure_probe_required_tool(suite, "futuremcp"),
            "futuremcp_context",
        )

    def test_host_neutral_tool_classes_cover_opencode_and_chatgpt_names(self) -> None:
        self.assertEqual(
            classify_tool("hashmarks_task_evidence", subject="hashmarks"),
            SUBJECT_REPOSITORY_INTELLIGENCE,
        )
        self.assertEqual(
            classify_tool("mcp__hashmarks__task_evidence", subject="hashmarks"),
            SUBJECT_REPOSITORY_INTELLIGENCE,
        )
        self.assertEqual(
            classify_tool("grep", subject="hashmarks"),
            NATIVE_SEARCH,
        )
        self.assertEqual(
            classify_tool("mcp__GitHub__search", subject="hashmarks"),
            NATIVE_SEARCH,
        )
        self.assertEqual(
            classify_tool("mcp__GitHub__fetch_file", subject="hashmarks"),
            NATIVE_READ,
        )
        self.assertEqual(
            classify_tool("functions.exec", subject="hashmarks"),
            TOOL_ROUTER,
        )
        self.assertEqual(
            classify_tool("container.exec", subject="hashmarks"),
            SHELL,
        )
        self.assertEqual(
            classify_tool("image_gen", subject="hashmarks"),
            OTHER,
        )

    def test_chatgpt_style_hashmarks_call_must_precede_native_repository_discovery(
        self,
    ) -> None:
        required = "hashmarks_task_evidence"
        self.assertTrue(
            required_before_native_discovery(
                [
                    {
                        "tool": "mcp__hashmarks__task_evidence",
                        "status": "completed",
                        "result_bytes": 42,
                    },
                    {"tool": "mcp__GitHub__search", "status": "completed"},
                    {"tool": "mcp__GitHub__fetch_file", "status": "completed"},
                ],
                subject="hashmarks",
                required_tool=required,
            )
        )
        self.assertFalse(
            required_before_native_discovery(
                [
                    {"tool": "mcp__GitHub__search", "status": "completed"},
                    {
                        "tool": "mcp__hashmarks__task_evidence",
                        "status": "completed",
                        "result_bytes": 42,
                    },
                ],
                subject="hashmarks",
                required_tool=required,
            )
        )

    def test_generic_repository_api_fetch_uses_call_inputs_for_routing_class(
        self,
    ) -> None:
        self.assertEqual(
            classify_call(
                "mcp__GitHub__fetch",
                {
                    "url": (
                        "https://api.github.com/repos/acme/repo/"
                        "git/trees/abc123?recursive=1"
                    )
                },
                subject="hashmarks",
            ),
            NATIVE_SEARCH,
        )
        self.assertEqual(
            classify_call(
                "mcp__GitHub__fetch",
                {
                    "url": (
                        "https://api.github.com/repos/acme/repo/"
                        "contents/src/owner.py"
                    )
                },
                subject="hashmarks",
            ),
            NATIVE_READ,
        )

    def test_opaque_router_before_hashmarks_makes_order_unknown(self) -> None:
        required = "hashmarks_task_evidence"
        self.assertIsNone(
            required_before_native_discovery(
                [
                    {
                        "tool": "functions.exec",
                        "tool_class": TOOL_ROUTER,
                        "routing_observability": "opaque",
                    },
                    {
                        "tool": "mcp__hashmarks__task_evidence",
                        "status": "completed",
                        "result_bytes": 42,
                    },
                    {
                        "tool": "mcp__GitHub__search",
                        "status": "completed",
                    },
                ],
                subject="hashmarks",
                required_tool=required,
            )
        )
        self.assertTrue(
            required_before_native_discovery(
                [
                    {
                        "tool": "functions.exec",
                        "tool_class": TOOL_ROUTER,
                        "routing_observability": "expanded",
                    },
                    {
                        "tool": "mcp__hashmarks__task_evidence",
                        "status": "completed",
                        "result_bytes": 42,
                    },
                    {
                        "tool": "mcp__GitHub__search",
                        "status": "completed",
                    },
                ],
                subject="hashmarks",
                required_tool=required,
            )
        )

    def test_catalog_admission_distinguishes_missing_hashmarks_from_routing_failure(
        self,
    ) -> None:
        blocked = catalog_admission(
            ["mcp__GitHub__search", "mcp__GitHub__fetch_file"],
            subject="hashmarks",
            required_tool="hashmarks_task_evidence",
        )
        self.assertEqual(blocked["status"], "ENVIRONMENT_BLOCKED")
        self.assertEqual(
            blocked["reason_codes"],
            ["required-subject-tool-missing"],
        )
        self.assertEqual(
            blocked["native_discovery_classes"],
            [NATIVE_READ, NATIVE_SEARCH],
        )

        ready = catalog_admission(
            [
                "mcp__hashmarks__task_evidence",
                "mcp__GitHub__search",
                "mcp__GitHub__fetch_file",
            ],
            subject="hashmarks",
            required_tool="hashmarks_task_evidence",
        )
        self.assertEqual(ready["status"], "READY")
        self.assertTrue(ready["required_tool_visible"])
        self.assertEqual(ready["reason_codes"], [])

        generic_fetch = catalog_admission(
            [
                "mcp__hashmarks__task_evidence",
                "mcp__GitHub__fetch",
            ],
            subject="hashmarks",
            required_tool="hashmarks_task_evidence",
        )
        self.assertEqual(generic_fetch["status"], "READY")
        self.assertEqual(
            generic_fetch["native_discovery_classes"],
            [NATIVE_READ, NATIVE_SEARCH],
        )

    def test_tool_routing_catalog_cli_is_model_free_admission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocked_catalog = root / "blocked.json"
            blocked_catalog.write_text(
                json.dumps(
                    {
                        "tools": [
                            {"name": "mcp__GitHub__search"},
                            {"name": "mcp__GitHub__fetch_file"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = benchmark_main(
                    [
                        "tool-routing-catalog",
                        "--catalog",
                        str(blocked_catalog),
                        "--subject",
                        "hashmarks",
                        "--required-tool",
                        "hashmarks_task_evidence",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(
                json.loads(stdout.getvalue())["reason_codes"],
                ["required-subject-tool-missing"],
            )

            ready_catalog = root / "ready.json"
            ready_catalog.write_text(
                json.dumps(
                    [
                        "mcp__hashmarks__task_evidence",
                        "mcp__GitHub__search",
                    ]
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = benchmark_main(
                    [
                        "tool-routing-catalog",
                        "--catalog",
                        str(ready_catalog),
                        "--subject",
                        "hashmarks",
                        "--required-tool",
                        "hashmarks_task_evidence",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "READY")

    def test_required_tool_name_forms(self) -> None:
        self.assertTrue(
            matches_subject_operation(
                "tools.enola.explore",
                subject="enola",
                operation="explore",
            )
        )
        self.assertFalse(
            matches_subject_operation(
                "enola.query_facts",
                subject="enola",
                operation="explore",
            )
        )

    def test_required_call_outcomes_preserve_unknown_attempt_and_success(self) -> None:
        required = "hashmarks_task_evidence"
        self.assertEqual(
            required_call_result(
                None,
                subject="hashmarks",
                required_tool=required,
            ),
            (None, None),
        )
        self.assertEqual(
            required_call_result(
                [],
                subject="hashmarks",
                required_tool=required,
            ),
            (False, False),
        )
        self.assertEqual(
            required_call_result(
                [{"tool": required, "status": "error", "result_bytes": 50}],
                subject="hashmarks",
                required_tool=required,
            ),
            (True, False),
        )
        self.assertEqual(
            required_call_result(
                [{"tool": required, "status": "completed", "result_bytes": 42}],
                subject="hashmarks",
                required_tool=required,
            ),
            (True, True),
        )
        self.assertEqual(
            required_call_result(
                [{"tool": required, "status": "completed", "result_bytes": None}],
                subject="hashmarks",
                required_tool=required,
            ),
            (True, None),
        )
        self.assertFalse(
            required_before_native_discovery(
                [
                    {"tool": "grep"},
                    {
                        "tool": required,
                        "status": "completed",
                        "result_bytes": 42,
                    },
                ],
                subject="hashmarks",
                required_tool=required,
            )
        )
        self.assertTrue(
            required_before_native_discovery(
                [
                    {
                        "tool": required,
                        "status": "completed",
                        "result_bytes": 42,
                    },
                    {"tool": "grep"},
                ],
                subject="hashmarks",
                required_tool=required,
            )
        )

    def test_smoke_gate_requires_three_proven_early_calls(self) -> None:
        row = {"status": "PASS", "required_call_succeeded": True,
               "required_before_native_discovery": True}
        score = {"schema": "agents-cookbook-tool-probe-score.v3",
                 "subject": "hashmarks", "required_tool": "hashmarks_task_evidence",
                 "expected_trials": 3, "observed_trials": 3,
                 "required_tool_results": [dict(row, trial_id=f"trial-{n}", task_id="task")
                                           for n in range(3)]}
        smoke_gate(score, subject="hashmarks")
        score["required_tool_results"][1]["required_before_native_discovery"] = None
        with self.assertRaises(ValueError):
            smoke_gate(score, subject="hashmarks")

    def test_tool_probe_score_reports_attempt_failure_and_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "hashmarks-probe"
            prepare_tool_probe_suite(
                source_suite=SUITE, destination=destination, subject="hashmarks"
            )
            definition = load_suite(destination).trial_definitions()[0]["definition_id"]
            output = Path(tmp) / "score.json"
            trials = [
                {"trial_id": "a", "definition_id": definition, "task_id": "one",
                 "status": "PASS", "calls": [{"tool": "hashmarks_task_evidence",
                 "status": "error", "result_bytes": 12}]},
                {"trial_id": "b", "definition_id": definition, "task_id": "one",
                 "status": "INCOMPLETE", "calls": None},
            ]
            argv = ["score.py", "--results", str(Path(tmp) / "results"),
                    "--output", str(output), "--agent", "opencode-native",
                    "--definition-id", definition]
            with mock.patch("sys.argv", argv), mock.patch(
                "benchmarks.tool_probe_score.build_report",
                return_value={"expected_trials": 2, "observed_trials": 2,
                              "status_counts": {"PASS": 1, "INCOMPLETE": 1}},
            ), mock.patch(
                "benchmarks.tool_probe_score.build_trace_diagnostics",
                return_value={"trials": trials},
            ):
                self.assertEqual(tool_probe_score_main(destination), 0)
            score = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(score["schema"], "agents-cookbook-tool-probe-score.v3")
            self.assertEqual(score["required_call_successes"], 0)
            self.assertEqual(score["required_call_failures"], 1)
            self.assertEqual(score["required_call_unknown"], 1)
            self.assertTrue(score["required_tool_results"][0]["required_call_attempted"])
            self.assertEqual(score["required_before_native_discovery_failures"], 1)
            self.assertEqual(score["required_before_native_discovery_unknown"], 1)


if __name__ == "__main__":
    unittest.main()
