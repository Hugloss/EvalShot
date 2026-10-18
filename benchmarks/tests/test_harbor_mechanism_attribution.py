"""Focused regressions for paired Harbor mechanism attribution."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.bundle_writer import publish_bundle
from benchmarks.harness.identity import canonical_json, digest
from benchmarks.harness.mechanism_attribution import (
    ANSWER_EVIDENCE_SCHEMA,
    ATTRIBUTION_PROVEN,
    ATTRIBUTION_SUPPORTED,
    ATTRIBUTION_UNPROVEN,
    FOLLOWED,
    TREATMENT_NEVER_INVOKED,
    TREATMENT_SUCCESS,
    build_mechanism_report,
    pair_projection,
    project_atif,
)


def _trace(
    *,
    available: bool = True,
    treatment: str = TREATMENT_SUCCESS,
    routing: str = "FIRST_CHOICE",
    native_search: int = 0,
    native_read: int = 0,
    native_discovery: int = 0,
    tool_calls: int = 1,
    tokens: int = 100,
    followthrough: str = FOLLOWED,
    search_observability: str = "EXPLICIT_TOOL_CALLS_ONLY",
) -> dict[str, object]:
    return {
        "available": available,
        "treatment": treatment,
        "subject_routing_timing": routing,
        "native_search_calls": native_search,
        "native_search_observability": search_observability,
        "tool_order_complete": True,
        "native_read_calls": native_read,
        "native_discovery_calls": native_discovery,
        "tool_calls": tool_calls,
        "total_tokens": tokens,
        "subject_target_followthrough": followthrough,
    }


def _projection(
    *,
    status: str,
    subject: str,
    trace: dict[str, object],
    observed: dict[str, str] | None,
    match: bool | None,
) -> dict[str, object]:
    answer = (
        None
        if match is None
        else {
            "match": match,
            "observed": observed,
            "expected": {"path": "src/owner.py", "symbol": "resolve"},
            "tracked_clean": True,
        }
    )
    return {
        "receipt": {
            "task_id": "localize-owner",
            "harness": "codex",
            "subject": subject,
            "model": "provider/model",
            "replicate_id": 6201,
            "status": status,
            "execution": {"campaign_id": "campaign"},
        },
        "trace": trace,
        "answer": answer,
    }


class HarborMechanismAttributionTests(unittest.TestCase):
    def test_atif_projection_reuses_tool_vocabulary_without_reasoning(self) -> None:
        trajectory = {
            "schema_version": "ATIF-v1.8",
            "agent": {"name": "codex", "version": "test"},
            "steps": [
                {
                    "step_id": 1,
                    "source": "agent",
                    "message": "do not inspect this message",
                    "reasoning_content": "private reasoning must not be consumed",
                    "tool_calls": [
                        {
                            "tool_call_id": "hm-1",
                            "function_name": "mcp__hashmarks__task_evidence",
                            "arguments": {"task": "find owner"},
                        }
                    ],
                    "observation": {
                        "results": [
                            {
                                "source_call_id": "hm-1",
                                "content": json.dumps(
                                    {
                                        "owner": {
                                            "path": "src/owner.py",
                                            "symbol": "resolve",
                                        }
                                    }
                                ),
                            }
                        ]
                    },
                    "metrics": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "cached_tokens": 10,
                    },
                    "llm_call_count": 1,
                },
                {
                    "step_id": 2,
                    "source": "agent",
                    "message": "",
                    "tool_calls": [
                        {
                            "tool_call_id": "read-1",
                            "function_name": "read_file",
                            "arguments": {"path": "src/owner.py"},
                        }
                    ],
                    "observation": {
                        "results": [
                            {
                                "source_call_id": "read-1",
                                "content": "def resolve(): ...",
                            }
                        ]
                    },
                    "metrics": {
                        "prompt_tokens": 50,
                        "completion_tokens": 10,
                    },
                    "llm_call_count": 1,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trajectory.json"
            path.write_text(json.dumps(trajectory), encoding="utf-8")
            projection = project_atif(path)

        self.assertTrue(projection["available"])
        self.assertEqual(projection["treatment"], TREATMENT_SUCCESS)
        self.assertEqual(projection["subject_routing_timing"], "FIRST_CHOICE")
        self.assertEqual(projection["subject_target_followthrough"], FOLLOWED)
        self.assertEqual(projection["subject_target_count"], 1)
        self.assertEqual(projection["native_read_calls"], 1)
        self.assertEqual(projection["native_search_calls"], 0)
        self.assertEqual(projection["total_tokens"], 180)
        self.assertFalse(projection["reasoning_content_consumed"])
        self.assertFalse(projection["message_content_consumed"])

    def test_atif_projection_keeps_bounded_query_surface_selector(self) -> None:
        trajectory = {
            "schema_version": "ATIF-v1.8",
            "steps": [
                {
                    "source": "agent",
                    "tool_calls": [
                        {
                            "tool_call_id": "query-1",
                            "function_name": (
                                "mcp__hashmarks__repository_intelligence_query"
                            ),
                            "arguments": {
                                "surface_name": "verification-explanation",
                                "task": "large task text not needed by selector audit",
                            },
                        }
                    ],
                    "observation": {
                        "results": [
                            {
                                "source_call_id": "query-1",
                                "content": {"schema": "example"},
                            }
                        ]
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trajectory.json"
            path.write_text(json.dumps(trajectory), encoding="utf-8")
            projection = project_atif(path)

        self.assertEqual(
            projection["subject_call_selectors"],
            [
                {
                    "tool": (
                        "mcp__hashmarks__repository_intelligence_query"
                    ),
                    "surface_name": "verification-explanation",
                }
            ],
        )
        self.assertNotIn(
            "large task text",
            json.dumps(projection["subject_call_selectors"]),
        )

    def test_malformed_atif_calls_do_not_prove_never_invoked(self) -> None:
        scenarios = (
            ([], "empty-trajectory"),
            ([None], "malformed-step"),
            ([{"source": "agent", "tool_calls": None}], "malformed-tool-call-list"),
            ([{"source": "agent", "tool_calls": ["unknown"]}], "malformed-tool-call"),
            (
                [{"source": "agent", "tool_calls": [{"function_name": ""}]}],
                "missing-tool-name",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trajectory.json"
            for steps, reason in scenarios:
                with self.subTest(reason=reason):
                    path.write_text(
                        json.dumps({"schema_version": "ATIF-v1.8", "steps": steps}),
                        encoding="utf-8",
                    )
                    result = project_atif(path)
                    self.assertTrue(result["available"])
                    self.assertFalse(result["tool_order_complete"])
                    self.assertIn(reason, result["tool_order_issue_codes"])
                    self.assertGreater(result["tool_order_issue_count"], 0)
                    self.assertEqual(result["treatment"], "UNKNOWN")
                    self.assertEqual(result["subject_routing_timing"], "UNKNOWN")
                    self.assertEqual(result["subject_target_followthrough"], "FOLLOWTHROUGH_UNKNOWN")

    def test_malformed_atif_preserves_positive_calls_but_never_proves_complete_order(
        self,
    ) -> None:
        steps = [{
            "source": "agent",
            "tool_calls": [
                {
                    "tool_call_id": "known",
                    "function_name": "mcp__hashmarks__task_evidence",
                    "arguments": {"task": "find owner"},
                },
                {"tool_call_id": "lost", "function_name": None},
            ],
            "observation": {
                "results": [{"source_call_id": "known", "content": {"path": "src/x.py"}}]
            },
        }]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trajectory.json"
            path.write_text(
                json.dumps({"schema_version": "ATIF-v1.8", "steps": steps}),
                encoding="utf-8",
            )
            result = project_atif(path)
        self.assertEqual(result["subject_tool_calls"], 1)
        self.assertEqual(result["subject_tools"], ["mcp__hashmarks__task_evidence"])
        self.assertFalse(result["tool_order_complete"])
        self.assertEqual(result["tool_order_issue_codes"], ["missing-tool-name"])
        self.assertEqual(result["treatment"], "UNKNOWN")
        self.assertEqual(result["subject_routing_timing"], "UNKNOWN")
        self.assertEqual(result["subject_target_followthrough"], "FOLLOWTHROUGH_UNKNOWN")

    def test_optional_atif_tool_call_field_does_not_imply_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trajectory.json"
            path.write_text(
                json.dumps({
                    "schema_version": "ATIF-v1.8",
                    "steps": [
                        {"source": "agent", "message": "no tools"},
                        {"source": "agent", "tool_calls": []},
                    ],
                }),
                encoding="utf-8",
            )
            result = project_atif(path)
        self.assertTrue(result["tool_order_complete"])
        self.assertEqual(result["tool_order_issue_count"], 0)
        self.assertEqual(result["tool_order_issue_codes"], [])
        self.assertEqual(result["treatment"], TREATMENT_NEVER_INVOKED)

    def test_partial_atif_does_not_support_paired_tool_displacement(self) -> None:
        bare = _projection(
            status="FAIL",
            subject="none",
            trace={**_trace(
                treatment=TREATMENT_NEVER_INVOKED,
                tool_calls=8,
                native_read=5,
                native_discovery=5,
                tokens=1200,
            ), "tool_order_complete": False},
            observed={"path": "wrong.py", "symbol": "wrong"},
            match=False,
        )
        treated = _projection(
            status="PASS",
            subject="hashmarks",
            trace={**_trace(
                tool_calls=2,
                native_read=1,
                native_discovery=1,
                tokens=600,
            ), "tool_order_complete": True},
            observed={"path": "src/owner.py", "symbol": "resolve"},
            match=True,
        )
        pair = pair_projection(bare, treated)
        self.assertFalse(pair["tool_order_qualified"])
        self.assertEqual(pair["outcome_transition"], "FAIL_TO_PASS")
        self.assertEqual(pair["attribution_strength"], ATTRIBUTION_UNPROVEN)
        self.assertEqual(pair["attribution_interpretation"], "tool-order-incomplete")
        for key in (
            "tool_call_delta",
            "native_read_delta",
            "native_search_delta",
            "native_discovery_delta",
            "token_delta",
        ):
            self.assertIsNone(pair[key])
        self.assertEqual(pair["native_discovery_change"], "UNKNOWN")
        self.assertNotIn("FEWER_TOOL_CALLS", pair["mechanism_tags"])

    def test_opaque_router_suppresses_search_reduction_and_efficiency_tags(self) -> None:
        bare = _projection(
            status="PASS", subject="none",
            trace=_trace(
                native_search=4, native_read=3, native_discovery=7,
                tool_calls=8, treatment=TREATMENT_NEVER_INVOKED,
            ),
            observed={"path": "src/owner.py", "symbol": "resolve"}, match=True,
        )
        treated = _projection(
            status="PASS", subject="hashmarks",
            trace=_trace(
                native_search=0, native_read=0, native_discovery=1,
                tool_calls=2, search_observability="PARTIAL_OPAQUE_TOOL",
            ),
            observed={"path": "src/owner.py", "symbol": "resolve"}, match=True,
        )
        pair = pair_projection(bare, treated)
        self.assertEqual(pair["native_search_delta"], -4)
        self.assertFalse(pair["native_search_comparison"]["qualified"])
        self.assertEqual(
            pair["native_search_comparison"]["reason"], "opaque-tool-boundary"
        )
        self.assertEqual(pair["native_search_comparison"]["observed_delta"], -4)
        self.assertIsNone(pair["native_search_comparison"]["qualified_delta"])
        self.assertEqual(pair["native_discovery_change"], "UNKNOWN")
        self.assertNotIn("NATIVE_DISCOVERY_REDUCED", pair["mechanism_tags"])
        self.assertNotIn("NATIVE_DISCOVERY_AVOIDED", pair["mechanism_tags"])
        self.assertNotIn("FIRST_CHOICE_LOCALIZATION", pair["mechanism_tags"])
        self.assertNotIn("FEWER_TOOL_CALLS", pair["mechanism_tags"])
        self.assertNotIn("SAME_ANSWER_FEWER_READS", pair["mechanism_tags"])
        self.assertFalse(pair["positive_causal_proof_claimed"])

        bare["trace"]["native_search_observability"] = "PARTIAL_OPAQUE_TOOL"
        treated["trace"]["native_search_observability"] = "EXPLICIT_TOOL_CALLS_ONLY"
        switched = pair_projection(bare, treated)
        self.assertFalse(switched["native_search_comparison"]["qualified"])

    def test_explicit_calls_comparable_but_never_causally_proven(self) -> None:
        bare = _projection(
            status="PASS", subject="none",
            trace=_trace(native_search=3, native_discovery=3),
            observed={"path": "src/owner.py", "symbol": "resolve"}, match=True,
        )
        treated = _projection(
            status="PASS", subject="hashmarks",
            trace=_trace(native_search=1, native_discovery=1),
            observed={"path": "src/owner.py", "symbol": "resolve"}, match=True,
        )
        result = pair_projection(bare, treated)
        self.assertTrue(result["native_search_comparison"]["qualified"])
        self.assertEqual(result["native_search_comparison"]["qualified_delta"], -2)
        self.assertFalse(result["native_search_comparison"]["causal_savings_proven"])

        del treated["trace"]["native_search_observability"]
        legacy = pair_projection(bare, treated)
        self.assertEqual(
            legacy["native_search_comparison"]["reason"], "observability-unestablished"
        )
        self.assertIsNone(legacy["native_search_comparison"]["qualified_delta"])

        treated["trace"]["native_search_observability"] = "EXPLICIT_TOOL_CALLS_ONLY"
        del treated["trace"]["tool_order_complete"]
        unknown_order = pair_projection(bare, treated)
        self.assertFalse(unknown_order["native_search_comparison"]["qualified"])
        self.assertEqual(
            unknown_order["native_search_comparison"]["reason"],
            "tool-order-unestablished",
        )

    def test_project_atif_exposes_opaque_tool_search_observability(self) -> None:
        def project(names: list[str]) -> dict:
            trajectory = {
                "schema_version": "ATIF-v1.8",
                "steps": [
                    {"source": "agent", "tool_calls": [
                        {"function_name": name, "tool_call_id": f"c{i}", "arguments": {}}
                    ]}
                    for i, name in enumerate(names)
                ],
            }
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "trajectory.json"
                path.write_text(json.dumps(trajectory), encoding="utf-8")
                return project_atif(path)
        for name in ("functions.exec", "shell", "container.exec"):
            with self.subTest(name=name):
                projected = project(["rg", name])
                self.assertEqual(
                    projected["native_search_observability"], "PARTIAL_OPAQUE_TOOL"
                )
                self.assertEqual(projected["native_search_calls"], 1)
        self.assertEqual(
            project(["rg", "read_file"])["native_search_observability"],
            "EXPLICIT_TOOL_CALLS_ONLY",
        )

    def test_never_invoked_fail_to_pass_is_explicitly_not_attributable(self) -> None:
        bare = _projection(
            status="FAIL",
            subject="none",
            trace=_trace(
                treatment=TREATMENT_NEVER_INVOKED,
                routing="NEVER_INVOKED",
            ),
            observed={"path": "wrong.py", "symbol": "wrong"},
            match=False,
        )
        treated = _projection(
            status="PASS",
            subject="hashmarks",
            trace=_trace(
                treatment=TREATMENT_NEVER_INVOKED,
                routing="NEVER_INVOKED",
            ),
            observed={"path": "src/owner.py", "symbol": "resolve"},
            match=True,
        )

        pair = pair_projection(bare, treated)

        self.assertEqual(pair["outcome_transition"], "FAIL_TO_PASS")
        self.assertEqual(pair["attribution_result"], "NOT_ATTRIBUTABLE")
        self.assertEqual(pair["attribution_strength"], ATTRIBUTION_PROVEN)
        self.assertEqual(
            pair["attribution_interpretation"],
            "not-attributable-subject-never-invoked",
        )
        self.assertIn("SUBJECT_NEVER_INVOKED", pair["mechanism_tags"])
        self.assertFalse(pair["positive_causal_proof_claimed"])

    def test_fail_to_pass_first_choice_with_reduced_discovery_is_supported(self) -> None:
        bare = _projection(
            status="FAIL",
            subject="none",
            trace=_trace(
                treatment=TREATMENT_NEVER_INVOKED,
                routing="NOT_CONFIGURED",
                native_search=3,
                native_read=2,
                native_discovery=5,
                tool_calls=7,
                tokens=1200,
                followthrough="FOLLOWTHROUGH_UNKNOWN",
            ),
            observed={"path": "wrong.py", "symbol": "wrong"},
            match=False,
        )
        treated = _projection(
            status="PASS",
            subject="hashmarks",
            trace=_trace(
                native_search=0,
                native_read=1,
                native_discovery=1,
                tool_calls=3,
                tokens=700,
            ),
            observed={"path": "src/owner.py", "symbol": "resolve"},
            match=True,
        )

        pair = pair_projection(bare, treated)

        self.assertEqual(pair["answer_transition"], "WRONG_TO_CORRECT")
        self.assertEqual(pair["native_discovery_change"], "REDUCED")
        self.assertEqual(pair["native_discovery_delta"], -4)
        self.assertEqual(pair["tool_call_delta"], -4)
        self.assertEqual(pair["token_delta"], -500)
        self.assertEqual(pair["attribution_strength"], ATTRIBUTION_SUPPORTED)
        self.assertEqual(pair["attribution_result"], "SUPPORTED_ASSOCIATION")
        self.assertIn("FIRST_CHOICE_LOCALIZATION", pair["mechanism_tags"])
        self.assertIn("NATIVE_DISCOVERY_REDUCED", pair["mechanism_tags"])
        self.assertIn("WRONG_TO_CORRECT_ANSWER", pair["mechanism_tags"])
        self.assertIn(FOLLOWED, pair["mechanism_tags"])

    def test_same_answer_with_fewer_reads_is_classified(self) -> None:
        observed = {"path": "src/owner.py", "symbol": "resolve"}
        bare = _projection(
            status="PASS",
            subject="none",
            trace=_trace(
                treatment=TREATMENT_NEVER_INVOKED,
                routing="NOT_CONFIGURED",
                native_read=4,
                native_discovery=4,
                tool_calls=5,
            ),
            observed=observed,
            match=True,
        )
        treated = _projection(
            status="PASS",
            subject="hashmarks",
            trace=_trace(
                native_read=1,
                native_discovery=1,
                tool_calls=3,
            ),
            observed=observed,
            match=True,
        )

        pair = pair_projection(bare, treated)

        self.assertTrue(pair["same_observed_answer"])
        self.assertEqual(pair["native_read_delta"], -3)
        self.assertIn("SAME_ANSWER_FEWER_READS", pair["mechanism_tags"])

    def test_report_pairs_only_matching_campaign_task_harness_model_replicate(self) -> None:
        atif = {
            "schema_version": "ATIF-v1.8",
            "agent": {"name": "codex", "version": "test"},
            "steps": [
                {
                    "step_id": 1,
                    "source": "agent",
                    "message": "",
                    "tool_calls": [],
                    "metrics": {"prompt_tokens": 10, "completion_tokens": 2},
                }
            ],
        }
        answer = {
            "schema": ANSWER_EVIDENCE_SCHEMA,
            "observed": {"path": "src/owner.py", "symbol": "resolve"},
            "expected": {"path": "src/owner.py", "symbol": "resolve"},
            "match": True,
            "tracked_clean": True,
            "error": None,
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            campaign_id = "campaign"
            for subject in ("none", "hashmarks"):
                definition_id = f"definition-{subject}"
                trial_id = digest(
                    {
                        "campaign_id": campaign_id,
                        "definition_id": definition_id,
                    }
                )
                job_name = f"h{trial_id[:24]}-a000001"
                harbor_result = {
                    "schema": "agentscookbook.harbor-harness-trial.v1",
                    "trial_id": job_name,
                    "task": "localize-owner",
                    "harness": "codex",
                    "subject": subject,
                    "attempt": 1,
                    "model": "provider/model",
                    "status": "COMPLETE",
                    "success": True,
                    "reward": 1.0,
                    "duration_ms": 1,
                    "harbor_return_code": 0,
                    "harbor_job_root": f"/jobs/{job_name}",
                    "reward_path": f"/jobs/{job_name}/reward.txt",
                    "mcp_exposed": subject == "hashmarks",
                    "stderr_tail": "",
                }
                receipt = {
                    "backend": "harbor",
                    "definition_id": definition_id,
                    "trial_id": trial_id,
                    "task_id": "localize-owner",
                    "condition_id": f"{subject}-codex",
                    "trial": 0,
                    "replicate_id": 1,
                    "harness": "codex",
                    "subject": subject,
                    "model": "provider/model",
                    "status": "PASS",
                    "harbor": harbor_result,
                    "execution": {
                        "campaign_id": campaign_id,
                        "launch_attempt": 1,
                        "job_name": job_name,
                    },
                }
                publish_bundle(
                    results_root=root,
                    trial_id=trial_id,
                    artifacts={
                        "harbor_result": (
                            "harbor-result.json",
                            canonical_json(harbor_result),
                        ),
                        "reward": ("reward.txt", b"1\n"),
                        "trajectory": ("trajectory.json", canonical_json(atif)),
                        "answer": ("answer.json", canonical_json(answer)),
                    },
                    receipt=receipt,
                )

            report = build_mechanism_report(root)

        self.assertEqual(report["summary"]["paired_observations"], 1)
        self.assertEqual(report["summary"]["information_qualified_pairs"], 1)
        self.assertEqual(
            report["summary"]["information_outcome_cross_tab"],
            {
                "PASS_TO_PASS|NO_SUBJECT_RESULT|NO_SUBJECT_RESULT|NO_SUBJECT_RESULT": 1,
            },
        )
        self.assertEqual(report["summary"]["unpaired_groups"], 0)
        self.assertEqual(report["summary"]["never_invoked_pairs"], 1)
        self.assertEqual(report["summary"]["never_invoked_credited_pairs"], 0)
        self.assertFalse(report["summary"]["positive_causal_proof_claimed"])


if __name__ == "__main__":
    unittest.main()
