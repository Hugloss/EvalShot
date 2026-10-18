"""Model-free regression suite for Harbor semantic evidence timing and quality."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.mechanism_attribution import pair_projection
from benchmarks.harness.semantic_information import project_semantic_information

HASHMARKS = "mcp__hashmarks__dependency_codemap"


def _answer(expected: dict, observed: dict, *, with_oracle: bool = True) -> dict:
    correct = sorted(
        key for key, value in expected.items()
        if key in observed and type(value) is type(observed[key]) and value == observed[key]
    )
    missing = sorted(set(expected) - set(observed))
    incorrect = sorted(set(expected) - set(correct) - set(missing))
    return {
        "schema": "agentscookbook.harbor-answer-evidence.v1",
        "observed": observed,
        "expected": expected,
        "match": not missing and not incorrect and set(observed) == set(expected),
        "error": None,
        "oracle": {
            "schema": "agents-cookbook-lexigram-oracle.v1",
            "rubric": {
                "correct_fields": correct,
                "missing_fields": missing,
                "incorrect_fields": incorrect,
            },
        } if with_oracle else None,
    }


def _step(tool: str, call_id: str, *, content=None) -> dict:
    row = {
        "source": "agent",
        "message": "private message must not influence claims",
        "reasoning_content": "do not inspect",
        "tool_calls": [{
            "function_name": tool,
            "tool_call_id": call_id,
            "arguments": {"task": "analyze"},
        }],
    }
    if content is not None:
        row["observation"] = {
            "results": [{"source_call_id": call_id, "content": content}],
        }
    return row


def _run(steps: list[dict], answer: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trajectory.json"
        path.write_text(json.dumps({"schema_version": "ATIF-v1.8", "steps": steps}), encoding="utf-8")
        return project_semantic_information(path, answer=answer)


class SemanticInformationTests(unittest.TestCase):
    def test_correct_atoms_exposed_early_and_repeated_in_final(self) -> None:
        expected = {
            "component": "dummy-dep",
            "transition": "version-selection",
            "version_change": True,
        }
        observed = dict(expected)
        result = _run(
            [
                _step(HASHMARKS, "h1", content={"result": expected}),
                _step("rg", "n1", content="search output"),
            ],
            _answer(expected, observed),
        )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["claim_alignment"], "ALIGNED_ONLY")
        self.assertEqual(result["arrival_timing"], "BEFORE_NATIVE_DISCOVERY")
        self.assertEqual(result["final_answer_overlap"], "ALIGNED_REPEATED")
        self.assertEqual(result["aligned_fields"], sorted(expected))
        self.assertEqual(result["first_subject_observation_step"], 1)
        self.assertEqual(result["first_aligned_observation_step"], 1)
        self.assertEqual(result["first_native_discovery_step"], 2)
        self.assertFalse(result["agent_attention_proven"])
        self.assertFalse(result["causal_influence_claimed"])
        self.assertFalse(result["message_content_consumed"])

    def test_correct_information_available_even_when_final_answer_failed(self) -> None:
        expected = {"causation": "not-inferred", "source_equivalence": "unknown"}
        observed = {"causation": "caused", "source_equivalence": "unknown"}
        info = _run(
            [_step(HASHMARKS, "h1", content={"causation": "not-inferred"})],
            _answer(expected, observed),
        )
        self.assertEqual(info["aligned_fields"], ["causation"])
        self.assertEqual(info["aligned_repeated_in_final"], [])
        self.assertEqual(info["final_answer_overlap"], "NO_REPEATED_STRUCTURED_ATOM")
        self.assertTrue(info["qualified"])

    def test_divergent_claim_repeated_in_failing_final_answer(self) -> None:
        expected = {"causation": "not-inferred", "provenance": "runtime-log"}
        observed = {"causation": "proven", "provenance": "runtime-log"}
        result = _run(
            [_step(HASHMARKS, "h1", content={"data": {"causation": "proven"}})],
            _answer(expected, observed),
        )
        self.assertEqual(result["claim_alignment"], "DIVERGENT_ONLY")
        self.assertEqual(result["divergent_fields"], ["causation"])
        self.assertEqual(result["divergent_repeated_in_final"], ["causation"])
        self.assertEqual(result["final_answer_overlap"], "DIVERGENT_REPEATED")
        self.assertFalse(result["causal_influence_claimed"])

    def test_late_conflicting_declarations_are_kept_separate(self) -> None:
        expected = {"winner": "not-selected", "comparison": "differing"}
        observed = {"winner": "not-selected", "comparison": "differing"}
        result = _run(
            [
                _step("rg", "n1", content="native"),
                _step(
                    HASHMARKS, "h1",
                    content=[{"type": "text", "text": json.dumps({
                        "winner": "not-selected", "comparison": "equivalent",
                    })}],
                ),
            ],
            _answer(expected, observed),
        )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["claim_alignment"], "MIXED_OR_CONFLICTING")
        self.assertEqual(result["aligned_fields"], ["winner"])
        self.assertEqual(result["divergent_fields"], ["comparison"])
        self.assertEqual(result["arrival_timing"], "AFTER_NATIVE_DISCOVERY")

    def test_conflicting_subject_values_cannot_be_cherry_picked(self) -> None:
        expected = {"winner": "not-selected"}
        info = _run(
            [
                _step(HASHMARKS, "h1", content={"winner": "selected"}),
                _step(HASHMARKS, "h2", content={"winner": "not-selected"}),
            ],
            _answer(expected, expected),
        )
        self.assertEqual(info["claim_alignment"], "MIXED_OR_CONFLICTING")
        self.assertEqual(info["conflicted_fields"], ["winner"])
        self.assertEqual(info["aligned_fields"], [])
        self.assertEqual(info["divergent_fields"], [])

    def test_null_false_and_zero_must_not_alias(self) -> None:
        expected = {"owner": None, "namespaces_merged": False, "version_change": 0}
        observed = dict(expected)
        info = _run(
            [_step(HASHMARKS, "h1", content={
                "owner": None, "namespaces_merged": 0, "version_change": False,
            })],
            _answer(expected, observed),
        )
        self.assertTrue(info["qualified"])
        self.assertEqual(info["aligned_fields"], ["owner"])
        self.assertEqual(info["divergent_fields"], ["namespaces_merged", "version_change"])

    def test_nested_unrelated_keys_are_not_false_semantic_claims(self) -> None:
        expected = {"comparison": "equivalent"}
        info = _run(
            [_step(HASHMARKS, "h1", content={"metadata": {"comparison": "equivalent"}})],
            _answer(expected, expected),
        )
        self.assertEqual(info["claim_alignment"], "NO_COMPARABLE_CLAIMS")

    def test_opaque_text_does_not_prove_no_matching_claims(self) -> None:
        expected = {"comparison": "equivalent"}
        info = _run(
            [_step(HASHMARKS, "h1", content=[{"type": "text", "text": "equivalent"}])],
            _answer(expected, expected),
        )
        self.assertFalse(info["qualified"])
        self.assertEqual(info["reason"], "unstructured-subject-observation")

    def test_explicit_tool_error_is_not_classified_as_no_comparable_claim(self) -> None:
        expected = {"winner": "not-selected"}
        for payload in (
            {"error": "tool denied"},
            {"isError": True, "content": []},
            {"status": "failed"},
            {"status": {"malformed": True}},
        ):
            with self.subTest(payload=payload):
                result = _run(
                    [_step(HASHMARKS, "h1", content=payload)],
                    _answer(expected, expected),
                )
                self.assertFalse(result["qualified"])
                self.assertEqual(result["reason"], "unstructured-subject-observation")

    def test_missing_oracle_and_stale_legacy_artifact_are_unqualified(self) -> None:
        expected = {"comparison": "equivalent"}
        info = _run(
            [_step(HASHMARKS, "h1", content={"comparison": "equivalent"})],
            _answer(expected, expected, with_oracle=False),
        )
        self.assertFalse(info["qualified"])
        self.assertEqual(info["reason"], "frozen-semantic-oracle-unavailable")

    def test_oracle_rubric_disagreement_denies_claim_grading(self) -> None:
        expected = {"winner": "not-selected"}
        invalid = _answer(expected, {"winner": "selected"})
        invalid["oracle"]["rubric"]["correct_fields"] = ["winner"]
        invalid["oracle"]["rubric"]["incorrect_fields"] = []
        info = _run([_step(HASHMARKS, "h1", content=expected)], invalid)
        self.assertFalse(info["qualified"])
        self.assertEqual(info["reason"], "frozen-semantic-oracle-unavailable")

    def test_partial_tool_call_records_deny_negative_evidence(self) -> None:
        expected = {"winner": "not-selected"}
        result = _run(
            [_step(HASHMARKS, "h1", content=expected), {"source": "agent", "tool_calls": [None]}],
            _answer(expected, expected),
        )
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "malformed-tool-call")

    def test_subject_without_link_does_not_prove_no_claim(self) -> None:
        expected = {"winner": "not-selected"}
        result = _run([_step(HASHMARKS, "h1")], _answer(expected, expected))
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "missing-subject-observation-link")

    def test_observation_arriving_after_native_search_is_late(self) -> None:
        expected = {"winner": "not-selected"}
        steps = [
            _step(HASHMARKS, "h1"),
            _step("rg", "n1", content="native exploration"),
            {
                "source": "environment",
                "observation": {"results": [
                    {"source_call_id": "h1", "content": expected},
                ]},
            },
        ]
        result = _run(steps, _answer(expected, expected))
        self.assertTrue(result["qualified"])
        self.assertEqual(result["first_subject_observation_step"], 3)
        self.assertEqual(result["arrival_timing"], "AFTER_NATIVE_DISCOVERY")

    def test_same_step_is_unknown_not_first_choice(self) -> None:
        expected = {"winner": "not-selected"}
        steps = [{
            "source": "agent",
            "tool_calls": [
                {"function_name": HASHMARKS, "tool_call_id": "h1"},
                {"function_name": "rg", "tool_call_id": "n1"},
            ],
            "observation": {"results": [
                {"source_call_id": "h1", "content": expected},
            ]},
        }]
        result = _run(steps, _answer(expected, expected))
        self.assertTrue(result["qualified"])
        self.assertEqual(result["arrival_timing"], "UNKNOWN_SAME_STEP")

    def test_duplicate_observation_link_is_unqualified(self) -> None:
        expected = {"winner": "not-selected"}
        steps = [
            _step(HASHMARKS, "h1", content=expected),
            {"source": "environment", "observation": {"results": [
                {"source_call_id": "h1", "content": expected},
            ]}},
        ]
        result = _run(steps, _answer(expected, expected))
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "duplicate-observation-link")

    def test_completed_no_subject_call_is_explicit_and_non_causal(self) -> None:
        expected = {"winner": "not-selected"}
        result = _run([_step("rg", "n1", content="native")], _answer(expected, expected))
        self.assertTrue(result["qualified"])
        self.assertEqual(result["claim_alignment"], "NO_SUBJECT_RESULT")
        self.assertEqual(result["final_answer_overlap"], "NO_SUBJECT_RESULT")
        self.assertFalse(result["agent_attention_proven"])


if __name__ == "__main__":
    unittest.main()
