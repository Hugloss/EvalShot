"""Regress claim-specific timing and post-treatment conditioned outcome tables."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.semantic_information import project_semantic_information
from benchmarks.harness.semantic_outcome_analysis import (
    exposure_profile,
    quartet_information_outcomes,
    summarize_information_outcomes,
)


TOOL = "mcp__hashmarks__repository_declarations"
EXPECTED = {"winner": "not-selected", "comparison": "differing"}


def _answer(observed: dict | None = None) -> dict:
    final = EXPECTED if observed is None else observed
    correct = sorted(key for key in EXPECTED if key in final and final[key] == EXPECTED[key])
    missing = sorted(set(EXPECTED) - set(final))
    wrong = sorted(set(EXPECTED) - set(correct) - set(missing))
    return {
        "expected": EXPECTED,
        "observed": final,
        "error": None,
        "oracle": {
            "schema": "agents-cookbook-lexigram-oracle.v1",
            "rubric": {
                "correct_fields": correct,
                "incorrect_fields": wrong,
                "missing_fields": missing,
            },
        },
    }


def _call(name: str, call_id: str, content: object) -> dict:
    return {
        "source": "agent",
        "tool_calls": [{
            "function_name": name,
            "tool_call_id": call_id,
            "arguments": {"query": "declarations"},
        }],
        "observation": {
            "results": [{"source_call_id": call_id, "content": content}],
        },
        "reasoning_content": "not evidence",
        "message": "also not evidence",
    }


def _project(steps: list[dict], observed: dict | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        trajectory = Path(tmp) / "trajectory.json"
        trajectory.write_text(json.dumps({
            "schema_version": "ATIF-v1.8",
            "steps": steps,
        }), encoding="utf-8")
        return project_semantic_information(
            trajectory, answer=_answer(observed), component="repository_declarations",
        )


def _semantic(
    alignment: str = "ALIGNED_ONLY",
    aligned_arrival: str = "BEFORE_NATIVE_DISCOVERY",
    divergent_arrival: str = "NOT_OBSERVED",
    final_echo: str = "aligned",
) -> dict:
    aligned = ["winner"] if alignment in ("ALIGNED_ONLY", "MIXED_OR_CONFLICTING") else []
    divergent = ["comparison"] if alignment in ("DIVERGENT_ONLY", "MIXED_OR_CONFLICTING") else []
    if not aligned:
        aligned_arrival = "NOT_OBSERVED"
    if not divergent:
        divergent_arrival = "NOT_OBSERVED"
    if aligned and divergent:
        order = "ALIGNED_FIRST"
    elif aligned:
        order = "ALIGNED_ONLY"
    elif divergent:
        order = "DIVERGENT_ONLY"
    else:
        order = "NO_UNCONFLICTED_CLAIMS"
    return {
        "qualified": True,
        "claim_alignment": alignment,
        "aligned_claim_arrival": aligned_arrival,
        "divergent_claim_arrival": divergent_arrival,
        "claim_arrival_order": order,
        "aligned_fields": aligned,
        "divergent_fields": divergent,
        "conflicted_fields": [],
        "aligned_repeated_in_final": aligned if final_echo in ("aligned", "both") else [],
        "divergent_repeated_in_final": divergent if final_echo in ("divergent", "both") else [],
    }


def _quartet(*, full=None, only=None, statuses=None, task="case-00",
             replicate=1, harness="codex", model="model") -> dict:
    return {
        "task": task,
        "replicate_id": replicate,
        "harness": harness,
        "model": model,
        "statuses": statuses or {
            "bare": "FAIL", "full": "PASS", "remove": "FAIL", "only": "PASS"
        },
        "semantic_component_evidence": {
            "qualified": True,
            "reason": None,
            "full": full or _semantic(),
            "only": only or _semantic(),
        },
    }


class SemanticOutcomeBundleTests(unittest.TestCase):
    def test_early_unrelated_result_does_not_make_late_correct_claim_early(self) -> None:
        result = _project([
            _call(TOOL, "first", {"metadata": {"free_text": "present"}}),
            _call("rg", "native", "native search"),
            _call(TOOL, "later", {"winner": "not-selected"}),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["arrival_timing"], "BEFORE_NATIVE_DISCOVERY")
        self.assertEqual(result["aligned_claim_arrival"], "AFTER_NATIVE_DISCOVERY")
        self.assertEqual(result["first_aligned_observation_step"], 3)
        self.assertEqual(result["first_native_discovery_step"], 2)
        self.assertEqual(result["claim_arrival_order"], "ALIGNED_ONLY")
        profile = exposure_profile(result)
        self.assertTrue(profile["qualified"])
        self.assertEqual(profile["aligned_claim_arrival"], "AFTER_NATIVE_DISCOVERY")
        self.assertFalse(profile["causal_influence_claimed"])

    def test_misleading_claim_early_correct_claim_late_are_distinct(self) -> None:
        result = _project([
            _call(TOOL, "wrong", {"comparison": "equivalent"}),
            _call("rg", "native", "search"),
            _call(TOOL, "right", {"winner": "not-selected"}),
        ], observed={"comparison": "equivalent", "winner": "not-selected"})
        self.assertEqual(result["claim_alignment"], "MIXED_OR_CONFLICTING")
        self.assertEqual(result["divergent_claim_arrival"], "BEFORE_NATIVE_DISCOVERY")
        self.assertEqual(result["aligned_claim_arrival"], "AFTER_NATIVE_DISCOVERY")
        self.assertEqual(result["claim_arrival_order"], "DIVERGENT_FIRST")
        profile = exposure_profile(result)
        self.assertTrue(profile["qualified"])
        self.assertEqual(profile["final_answer_echo"], "BOTH_REPEATED")
        self.assertEqual(profile["descriptive_signal"], "DIVERGENT_CLAIM_REPEATED")
        self.assertEqual(profile["divergent_echo_field_count"], 1)
        self.assertEqual(profile["aligned_echo_field_count"], 1)

    def test_conflicting_claims_preserve_uncertainty_not_cherry_pick(self) -> None:
        result = _project([
            _call(TOOL, "wrong", {"winner": "selected"}),
            _call(TOOL, "right", {"winner": "not-selected"}),
        ])
        self.assertEqual(result["conflicted_fields"], ["winner"])
        self.assertIsNone(result["first_aligned_observation_step"])
        self.assertIsNone(result["first_divergent_observation_step"])
        self.assertEqual(result["first_conflicted_observation_step"], 1)
        self.assertEqual(result["aligned_claim_arrival"], "NOT_OBSERVED")
        self.assertEqual(result["divergent_claim_arrival"], "NOT_OBSERVED")
        self.assertEqual(result["claim_arrival_order"], "NO_UNCONFLICTED_CLAIMS")
        profile = exposure_profile(result)
        self.assertTrue(profile["qualified"])
        self.assertEqual(profile["descriptive_signal"], "CONFLICTED_FIELDS")
        self.assertEqual(profile["conflicted_field_count"], 1)

    def test_same_step_and_no_native_discovery_are_not_early(self) -> None:
        single = _project([_call(TOOL, "right", {"winner": "not-selected"})])
        self.assertEqual(single["aligned_claim_arrival"], "NO_NATIVE_DISCOVERY")
        same = _project([{
            "source": "agent",
            "tool_calls": [
                {"function_name": TOOL, "tool_call_id": "a", "arguments": {}},
                {"function_name": "rg", "tool_call_id": "b", "arguments": {}},
            ],
            "observation": {"results": [{
                "source_call_id": "a", "content": {"winner": "not-selected"},
            }]},
        }])
        self.assertEqual(same["aligned_claim_arrival"], "UNKNOWN_SAME_STEP")

    def test_malformed_or_missing_fields_cannot_claim_outcome_signal(self) -> None:
        missing = _semantic()
        missing.pop("aligned_claim_arrival")
        bad = exposure_profile(missing)
        self.assertFalse(bad["qualified"])
        self.assertEqual(bad["reason"], "semantic-profile-fields-incomplete")
        inconsistent = _semantic()
        inconsistent["aligned_repeated_in_final"] = ["comparison"]
        self.assertEqual(
            exposure_profile(inconsistent)["reason"], "semantic-profile-inconsistent",
        )
        no_golden = {"qualified": False, "reason": "frozen-semantic-oracle-unavailable"}
        self.assertFalse(exposure_profile(no_golden)["qualified"])

    def test_adverse_but_not_causal_echo_and_complete_controls(self) -> None:
        row = _quartet(
            full=_semantic(
                "DIVERGENT_ONLY",
                divergent_arrival="BEFORE_NATIVE_DISCOVERY",
                final_echo="divergent",
            ),
            only=_semantic("ALIGNED_ONLY", final_echo="aligned"),
            statuses={"bare": "FAIL", "full": "FAIL", "remove": "PASS", "only": "PASS"},
        )
        diagnostic = quartet_information_outcomes(row)
        self.assertTrue(diagnostic["qualified"])
        self.assertEqual(diagnostic["comparisons"][0]["contrast"], "full_vs_remove")
        self.assertEqual(diagnostic["comparisons"][0]["outcome_transition"], "PASS_TO_FAIL")
        self.assertEqual(
            diagnostic["comparisons"][0]["exposure"]["descriptive_signal"],
            "DIVERGENT_CLAIM_REPEATED",
        )
        self.assertEqual(diagnostic["comparisons"][1]["contrast"], "only_vs_bare")
        self.assertEqual(diagnostic["comparisons"][1]["outcome_transition"], "FAIL_TO_PASS")
        self.assertFalse(diagnostic["causal_influence_claimed"])

    def test_grouped_counts_include_independent_cluster_denominators(self) -> None:
        rows = [
            _quartet(replicate=1),
            _quartet(replicate=2),
            _quartet(task="case-01", replicate=1,
                     statuses={"bare": "PASS", "full": "PASS", "remove": "FAIL", "only": "PASS"}),
        ]
        for row in rows:
            row["semantic_information_outcomes"] = quartet_information_outcomes(row)
        summary = summarize_information_outcomes(rows)
        self.assertEqual(summary["qualified_quartets"], 3)
        self.assertEqual(summary["excluded_quartets"], 0)
        self.assertEqual(sum(c["replicate_count"] for c in summary["conditional_outcome_cells"]), 6)
        fail_to_pass_full = [
            c for c in summary["conditional_outcome_cells"]
            if c["contrast"] == "full_vs_remove"
            and c["outcome_transition"] == "FAIL_TO_PASS"
        ]
        self.assertEqual(len(fail_to_pass_full), 1)
        self.assertEqual(fail_to_pass_full[0]["replicate_count"], 3)
        self.assertEqual(
            fail_to_pass_full[0]["independent_task_harness_model_clusters"], 2,
        )
        self.assertFalse(summary["causal_influence_claimed"])
        self.assertIn("not randomized", summary["conditioning_warning"])

    def test_unqualified_and_incomplete_quartets_excluded_not_as_failures(self) -> None:
        good = _quartet()
        bad_profile = _quartet(full=_semantic())
        bad_profile["semantic_component_evidence"]["full"].pop("claim_arrival_order")
        missing_trace = _quartet()
        missing_trace["semantic_component_evidence"]["qualified"] = False
        missing_trace["semantic_component_evidence"]["reason"] = "only:missing-result"
        incomplete = _quartet(statuses={
            "bare": "FAIL", "full": "PASS", "remove": "FAIL", "only": "INCOMPLETE",
        })
        rows = [good, bad_profile, missing_trace, incomplete]
        for row in rows:
            row["semantic_information_outcomes"] = quartet_information_outcomes(row)
        result = summarize_information_outcomes(rows)
        self.assertEqual(result["qualified_quartets"], 1)
        self.assertEqual(result["excluded_quartets"], 3)
        self.assertEqual(result["exclusion_reasons"], {
            "full:semantic-profile-fields-incomplete": 1,
            "only:missing-result": 1,
            "incomplete-matched-outcomes": 1,
        })

    def test_no_data_does_not_invent_success_rate_or_significance(self) -> None:
        result = summarize_information_outcomes([])
        self.assertEqual(result["qualified_quartets"], 0)
        self.assertEqual(result["conditional_outcome_cells"], [])
        self.assertNotIn("uplift", result)
        self.assertNotIn("p_value", result)


if __name__ == "__main__":
    unittest.main()
