"""Component/selector-isolated semantic evidence in Harbor ATIF observations."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.semantic_information import project_semantic_information


def _answer() -> dict:
    return {
        "expected": {"winner": "not-selected", "comparison": "differing"},
        "observed": {"winner": "not-selected", "comparison": "differing"},
        "error": None,
        "oracle": {
            "schema": "agents-cookbook-lexigram-oracle.v1",
            "rubric": {
                "correct_fields": ["comparison", "winner"],
                "incorrect_fields": [],
                "missing_fields": [],
            },
        },
    }


def _call(name: str, call_id: str, response: object, *, args=None) -> dict:
    return {
        "source": "agent",
        "tool_calls": [
            {
                "tool_call_id": call_id,
                "function_name": name,
                "arguments": args if args is not None else {},
            }
        ],
        "observation": {
            "results": [{"source_call_id": call_id, "content": response}],
        },
    }


def _project(steps: list[dict], *, component: str, selector=None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        trajectory = Path(tmp) / "trajectory.json"
        trajectory.write_text(
            json.dumps({"schema_version": "ATIF-v1.8", "steps": steps}),
            encoding="utf-8",
        )
        return project_semantic_information(
            trajectory,
            answer=_answer(),
            component=component,
            selector=selector,
        )


class HarborComponentSemanticTests(unittest.TestCase):
    def test_exact_component_ignores_other_hashmarks_observations(self) -> None:
        steps = [
            _call("mcp__hashmarks__find", "find", {"winner": "selected"}),
            _call("mcp__hashmarks__repository_declarations", "target", {
                "winner": "not-selected",
                "comparison": "differing",
            }),
            _call("rg", "native", "search"),
        ]
        result = _project(steps, component="repository_declarations")
        self.assertTrue(result["qualified"])
        self.assertEqual(result["claim_alignment"], "ALIGNED_ONLY")
        self.assertEqual(result["aligned_fields"], ["comparison", "winner"])
        self.assertEqual(result["divergent_fields"], [])
        self.assertEqual(result["first_subject_observation_step"], 2)
        self.assertEqual(result["arrival_timing"], "BEFORE_NATIVE_DISCOVERY")
        self.assertFalse(result["causal_influence_claimed"])

    def test_other_tool_does_not_substitute_for_missing_component(self) -> None:
        result = _project(
            [_call("mcp__hashmarks__find", "find", {"winner": "not-selected"})],
            component="repository_declarations",
        )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["claim_alignment"], "NO_SUBJECT_RESULT")
        self.assertEqual(result["arrival_timing"], "NO_SUBJECT_RESULT")
        self.assertEqual(result["aligned_fields"], [])

    def test_selector_is_exact_not_a_component_wide_result(self) -> None:
        name = "mcp__hashmarks__repository_intelligence_query"
        steps = [
            _call(name, "wrong", {"winner": "selected"}, args={"surface_name": "other"}),
            _call(name, "right", {"winner": "not-selected"}, args={"surface_name": "ownership"}),
        ]
        selected = _project(
            steps,
            component="repository_intelligence_query",
            selector={"argument": "surface_name", "value": "ownership"},
        )
        self.assertTrue(selected["qualified"])
        self.assertEqual(selected["claim_alignment"], "ALIGNED_ONLY")
        self.assertEqual(selected["first_subject_observation_step"], 2)

        none = _project(
            steps,
            component="repository_intelligence_query",
            selector={"argument": "surface_name", "value": "absent"},
        )
        self.assertEqual(none["claim_alignment"], "NO_SUBJECT_RESULT")

    def test_relevant_error_invalidates_projection_not_other_tool_error(self) -> None:
        result = _project([
            _call("mcp__hashmarks__find", "other", {"error": "failed"}),
            _call("mcp__hashmarks__repository_declarations", "target", {"winner": "not-selected"}),
        ], component="repository_declarations")
        self.assertTrue(result["qualified"])
        self.assertEqual(result["claim_alignment"], "ALIGNED_ONLY")
        bad = _project([
            _call("mcp__hashmarks__repository_declarations", "target", {"error": "failed"}),
        ], component="repository_declarations")
        self.assertFalse(bad["qualified"])
        self.assertEqual(bad["reason"], "unstructured-subject-observation")

    def test_invalid_selector_fails_closed(self) -> None:
        result = _project(
            [_call("mcp__hashmarks__repository_declarations", "target", {"winner": "not-selected"})],
            component="repository_declarations",
            selector={"argument": "arbitrary", "value": "ownership"},
        )
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "invalid-component-selector")

    def test_malformed_call_keeps_information_unknown(self) -> None:
        result = _project(
            [_call("mcp__hashmarks__repository_declarations", "target", {"winner": "not-selected"}),
             {"source": "agent", "tool_calls": [None]}],
            component="repository_declarations",
        )
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "malformed-tool-call")


if __name__ == "__main__":
    unittest.main()
