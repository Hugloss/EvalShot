"""Attack the model-free measurement layer, including impossible proof upgrades."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.evidence_lifecycle import project_evidence_lifecycle
from benchmarks.harness.evaluation_assurance import build_assurance_summary


CALL = "mcp__hashmarks__task_evidence"


def _atif(*, response: object = None, subject: bool = True) -> dict:
    name = CALL if subject else "rg"
    return {
        "schema_version": "ATIF-v1.8",
        "steps": [{
            "source": "agent",
            "message": "I used it! Delivered to model! Ignore all checks.",
            "reasoning_content": "private cognition is not evidence",
            "tool_calls": [{
                "tool_call_id": "t1", "function_name": name, "arguments": {},
            }],
            "observation": {
                "results": [{"source_call_id": "t1", "content": (
                    {"owner": {"path": "src/a.py"}}
                    if response is None else response
                )}],
            },
        }],
    }


def _project(value: dict) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "atif.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return project_evidence_lifecycle(path)


class EvidenceLifecycleAttacks(unittest.TestCase):
    def test_linked_packet_is_returned_not_delivered_or_used(self) -> None:
        result = _project(_atif())
        self.assertTrue(result["qualified"])
        self.assertEqual(result["invocation_state"], "INVOKED")
        self.assertEqual(result["return_state"], "RETURNED")
        self.assertEqual(result["delivery_state"], "UNKNOWN")
        self.assertEqual(result["application_state"], "UNKNOWN")
        self.assertFalse(result["model_attention_proven"])
        self.assertFalse(result["causal_influence_proven"])
        self.assertEqual(result["returned_packets"], 1)
        self.assertEqual(len(result["packet_refs"][0]["packet_sha256"]), 64)
        self.assertNotIn("src/a.py", json.dumps(result))
        self.assertNotIn("private cognition", json.dumps(result))

    def test_never_invoked_is_distinct_from_missing_result(self) -> None:
        absent = _project(_atif(subject=False))
        self.assertTrue(absent["qualified"])
        self.assertEqual(absent["invocation_state"], "NEVER_INVOKED")
        missing = _atif()
        missing["steps"][0]["observation"]["results"] = []
        result = _project(missing)
        self.assertTrue(result["qualified"])
        self.assertEqual(result["invoked_calls"], 1)
        self.assertEqual(result["return_state"], "NO_USABLE_RESULT")

    def test_duplicate_orphan_earlier_and_bad_call_link_fail_closed(self) -> None:
        duplicate = _atif()
        duplicate["steps"][0]["observation"]["results"] *= 2
        self.assertEqual(_project(duplicate)["reason"], "invalid-or-duplicate-result")
        orphan = _atif()
        orphan["steps"][0]["observation"]["results"][0]["source_call_id"] = "phantom"
        self.assertEqual(_project(orphan)["reason"], "orphan-observation")
        early = _atif()
        late = early["steps"][0]["tool_calls"]
        early["steps"][0]["tool_calls"] = []
        early["steps"].append({"source": "agent", "tool_calls": late})
        self.assertEqual(_project(early)["reason"], "observation-precedes-call")
        malformed = _atif()
        malformed["steps"][0]["tool_calls"][0]["arguments"] = ["not", "a", "map"]
        self.assertEqual(_project(malformed)["reason"], "malformed-call-arguments")

    def test_structured_and_text_views_must_agree(self) -> None:
        packet = {"owner": {"path": "src/a.py"}}
        same = {
            "structuredContent": {"result": packet},
            "content": [{"type": "text", "text": json.dumps({"result": packet})}],
        }
        self.assertEqual(_project(_atif(response=same))["presentation_parity"], "EQUIVALENT")
        different = copy.deepcopy(same)
        different["content"][0]["text"] = json.dumps({"result": {"owner": {"path": "src/b.py"}}})
        self.assertEqual(_project(_atif(response=different))["reason"], "divergent-presentation")

    def test_opaque_error_and_oversized_are_not_negative_evidence(self) -> None:
        self.assertEqual(_project(_atif(response={"isError": True, "content": ""}))["reason"], "invalid-subject-packet")
        self.assertEqual(_project(_atif(response="x" * 270_000))["reason"], "invalid-subject-packet")
        self.assertEqual(_project({"schema_version": "ATIF-v1.8", "steps": []})["reason"], "atif-invalid")


def _pair(task: str, transition: str, *, harness: str = "codex", qualified: bool = True, treatment: str = "OBSERVED_SUCCESSFUL_RESULT") -> dict:
    return {
        "task": task, "harness": harness, "model": "model", "replicate_id": 1,
        "outcome_transition": transition, "tool_order_qualified": qualified,
        "treatment": treatment, "native_search_delta": -2, "token_delta": 30,
        "native_search_comparison": {
            "qualified": True, "reason": None, "qualified_delta": -2,
            "causal_savings_proven": False,
        },
        "delivery_evidence": {
            "qualified": True, "return_state": "RETURNED",
            "delivery_state": "UNKNOWN", "presentation_parity": "EQUIVALENT",
        },
    }


class AssuranceReportAttacks(unittest.TestCase):
    def test_too_few_task_clusters_never_claim_an_interval(self) -> None:
        report = build_assurance_summary([_pair("one", "FAIL_TO_PASS")] * 4)
        self.assertEqual(report["paired_effect"]["state"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(report["paired_effect"]["interval_95"])
        self.assertEqual(report["paired_effect"]["task_clusters"], 1)
        self.assertEqual(report["qualified_pairs"], 4)
        self.assertEqual(report["evidence_delivery"]["states"], {"RETURNED_DELIVERY_UNKNOWN": 4})
        self.assertFalse(report["evidence_delivery"]["attention_proven"])

    def test_bootstrap_is_deterministic_and_task_clustered(self) -> None:
        pairs = [_pair(f"task-{i:02}", "FAIL_TO_PASS" if i < 4 else "PASS_TO_PASS") for i in range(10)]
        report = build_assurance_summary(pairs)
        again = build_assurance_summary(pairs)
        effect = report["paired_effect"]
        self.assertEqual(effect, again["paired_effect"])
        self.assertEqual(effect["state"], "DESCRIPTIVE_INTERVAL")
        self.assertEqual(effect["task_clusters"], 10)
        self.assertEqual(effect["point_estimate"], 0.4)
        self.assertEqual(len(effect["interval_95"]), 2)
        self.assertIsNone(effect["probability_gain"])

    def test_unqualified_and_missing_outcomes_never_enter_denominator(self) -> None:
        pairs = [
            _pair("A", "FAIL_TO_PASS"),
            _pair("B", "PASS_TO_FAIL", qualified=False),
            _pair("C", "INCOMPLETE"),
        ]
        result = build_assurance_summary(pairs)
        self.assertEqual(result["qualified_pairs"], 1)
        self.assertEqual(result["excluded_pairs"], 2)
        self.assertEqual(result["exclusion_reasons"]["tool-order-unqualified"], 1)
        self.assertEqual(result["exclusion_reasons"]["incomplete-or-invalid-pair-outcome"], 1)
        self.assertEqual(result["bare_control"]["failures"], 1)
        self.assertEqual(result["interactions"]["state"], "NOT_IDENTIFIABLE")
        self.assertEqual(result["context_and_freshness"]["state"], "NOT_ASSESSED")

    def test_search_economics_excludes_opaque_and_unattested_pairs(self) -> None:
        explicit = _pair("explicit", "PASS_TO_PASS")
        opaque = _pair("opaque", "FAIL_TO_PASS")
        opaque["native_search_delta"] = -99
        opaque["native_search_comparison"] = {
            "qualified": False,
            "reason": "opaque-tool-boundary",
            "qualified_delta": None,
        }
        legacy = _pair("legacy", "PASS_TO_PASS")
        del legacy["native_search_comparison"]
        incomplete = _pair("incomplete", "INCOMPLETE")
        report = build_assurance_summary([explicit, opaque, legacy, incomplete])
        economics = report["economics"]
        self.assertEqual(economics["native_search_delta"]["observations"], 1)
        self.assertEqual(economics["native_search_delta"]["mean"], -2.0)
        self.assertEqual(economics["native_search_comparable_pairs"], 1)
        self.assertEqual(
            economics["native_search_comparison_exclusions"],
            {"opaque-tool-boundary": 1, "comparison-unavailable": 1},
        )
        self.assertFalse(economics["native_search_savings_proven"])
        self.assertEqual(report["qualified_pairs"], 3)
        self.assertEqual(report["excluded_pairs"], 1)

    def test_cross_harness_results_are_separated(self) -> None:
        report = build_assurance_summary([
            _pair("a", "FAIL_TO_PASS", harness="codex"),
            _pair("a", "PASS_TO_FAIL", harness="opencode"),
        ])
        self.assertEqual(set(report["per_harness"]), {"codex", "opencode"})
        self.assertEqual(report["paired_effect"]["state"], "NOT_COMPARABLE_ACROSS_HARNESSES")
        self.assertIsNone(report["paired_effect"]["interval_95"])
        self.assertEqual(report["per_harness"]["codex"]["paired_effect"]["point_estimate"], 1.0)
        self.assertEqual(report["per_harness"]["opencode"]["paired_effect"]["point_estimate"], -1.0)


if __name__ == "__main__":
    unittest.main()
