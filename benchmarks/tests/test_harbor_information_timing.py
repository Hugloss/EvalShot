"""Regress information availability versus observed follow-through in Harbor."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.information_timing import (
    project_information_timing,
)
from benchmarks.harness.mechanism_attribution import pair_projection


ORACLE = "src/owner.py"
HASHMARKS = "mcp__hashmarks__task_evidence"


def _call(name: str, call_id: str, *, path: str | None = None) -> dict:
    return {
        "tool_call_id": call_id,
        "function_name": name,
        "arguments": {"path": path} if path else {"task": "locate owner"},
    }


def _step(name: str, call_id: str, *, path: str | None = None, result=None) -> dict:
    step = {
        "source": "agent",
        "message": "do not use this text as evidence",
        "reasoning_content": "must not be consumed",
        "tool_calls": [_call(name, call_id, path=path)],
    }
    if result is not None:
        step["observation"] = {
            "results": [{"source_call_id": call_id, "content": result}]
        }
    return step


def _run(steps: list[dict], oracle: object = ORACLE) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        trajectory = Path(tmp) / "trajectory.json"
        trajectory.write_text(
            json.dumps({"schema_version": "ATIF-v1.8", "steps": steps}),
            encoding="utf-8",
        )
        return project_information_timing(trajectory, expected_path=oracle)


class HarborInformationTimingTests(unittest.TestCase):
    def test_early_correct_target_return_then_native_read(self) -> None:
        result = _run([
            _step(HASHMARKS, "h1", result={"owner": {"path": ORACLE}}),
            _step("read_file", "r1", path=ORACLE, result="file bytes"),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["target_alignment"], "ORACLE_TARGET_ONLY")
        self.assertEqual(result["arrival_timing"], "BEFORE_NATIVE_DISCOVERY")
        self.assertEqual(result["native_read_followthrough"], "ORACLE_PATH_READ")
        self.assertEqual(result["first_subject_result_ordinal"], 1)
        self.assertEqual(result["first_oracle_read_after_result_ordinal"], 2)
        self.assertFalse(result["reasoning_content_consumed"])
        self.assertFalse(result["agent_message_consumed"])
        self.assertFalse(result["causal_influence_claimed"])

    def test_early_return_precedes_native_search(self) -> None:
        result = _run([
            _step(HASHMARKS, "h1", result={"path": ORACLE}),
            _step("rg", "r1", result="search results"),
            _step("read_file", "r2", path=ORACLE, result="file"),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["arrival_timing"], "BEFORE_NATIVE_DISCOVERY")
        self.assertEqual(result["first_native_discovery_ordinal"], 2)
        self.assertEqual(result["native_read_followthrough"], "ORACLE_PATH_READ")

    def test_late_rescue_is_separate_from_correctness(self) -> None:
        result = _run([
            _step("rg", "r1", result="first exploration"),
            _step("read_file", "r2", path="src/unrelated.py", result="code"),
            _step(HASHMARKS, "h1", result={"path": ORACLE}),
            _step("read_file", "r3", path=ORACLE, result="code"),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["arrival_timing"], "AFTER_NATIVE_DISCOVERY")
        self.assertEqual(result["first_oracle_target_ordinal"], 3)
        self.assertEqual(result["first_oracle_read_after_result_ordinal"], 4)

    def test_delayed_observation_is_not_backdated_to_subject_invocation(self) -> None:
        result = _run([
            _step(HASHMARKS, "h1"),
            _step("rg", "r1", result="native work happened"),
            {
                "source": "environment",
                "observation": {
                    "results": [{"source_call_id": "h1", "content": {"path": ORACLE}}]
                },
            },
            _step("read_file", "r2", path=ORACLE, result="code"),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["first_subject_result_ordinal"], 1)
        self.assertEqual(result["first_subject_result_step"], 3)
        self.assertEqual(result["first_native_discovery_step"], 2)
        self.assertEqual(result["arrival_timing"], "AFTER_NATIVE_DISCOVERY")
        self.assertEqual(result["first_oracle_read_after_result_ordinal"], 3)

    def test_observation_before_call_is_not_valid_information(self) -> None:
        result = _run([
            {
                "source": "environment",
                "observation": {
                    "results": [{"source_call_id": "h1", "content": {"path": ORACLE}}]
                },
            },
            _step(HASHMARKS, "h1"),
        ])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "observation-precedes-call")

    def test_same_step_observation_and_native_read_is_ambiguous(self) -> None:
        result = _run([{
            "source": "agent",
            "tool_calls": [
                _call(HASHMARKS, "h1"),
                _call("read_file", "r1", path=ORACLE),
            ],
            "observation": {
                "results": [
                    {"source_call_id": "h1", "content": {"path": ORACLE}},
                    {"source_call_id": "r1", "content": "code"},
                ]
            },
        }])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "native-discovery-observation-same-step")

    def test_alternative_path_is_not_called_harmful_without_outcome(self) -> None:
        result = _run([
            _step(HASHMARKS, "h1", result={"path": "src/alternative.py"}),
            _step("read_file", "r1", path="src/alternative.py", result="code"),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["target_alignment"], "ALTERNATE_TARGETS_ONLY")
        self.assertEqual(result["native_read_followthrough"], "ALTERNATE_PATH_READ")
        self.assertIsNone(result["first_oracle_target_ordinal"])
        self.assertFalse(result["causal_influence_claimed"])

    def test_mixed_result_does_not_silently_choose_winner(self) -> None:
        result = _run([
            _step(
                HASHMARKS,
                "h1",
                result={
                    "candidates": [
                        {"path": ORACLE},
                        {"path": "src/alternative.py"},
                    ]
                },
            ),
            _step("read_file", "r1", path="src/alternative.py", result="code"),
            _step("read_file", "r2", path=ORACLE, result="code"),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["target_alignment"], "MIXED_TARGETS")
        self.assertEqual(result["native_read_followthrough"], "BOTH_PATHS_READ")

    def test_json_in_mcp_text_block_is_supported(self) -> None:
        result = _run([
            _step(
                HASHMARKS,
                "h1",
                result=[{"type": "text", "text": json.dumps({"path": ORACLE})}],
            )
        ])
        self.assertEqual(result["target_alignment"], "ORACLE_TARGET_ONLY")
        self.assertEqual(result["native_read_followthrough"], "NO_MATCHING_NATIVE_READ")

    def test_missing_oracle_denies_negative_and_positive_claims(self) -> None:
        result = _run([_step(HASHMARKS, "h1", result={"path": ORACLE})], oracle=None)
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "no-verifier-owned-path-oracle")
        self.assertEqual(result["target_alignment"], "UNKNOWN")

    def test_plain_text_subject_return_is_unknown(self) -> None:
        result = _run([_step(HASHMARKS, "h1", result="see src/owner.py")])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "unstructured-subject-observation")

    def test_opaque_mcp_text_block_is_not_absence_of_evidence(self) -> None:
        result = _run([
            _step(
                HASHMARKS,
                "h1",
                result=[{"type": "text", "text": "Owner might be src/owner.py"}],
            ),
        ])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "unstructured-subject-observation")

    def test_unstructured_native_read_inputs_do_not_prove_no_followthrough(self) -> None:
        result = _run([
            _step(HASHMARKS, "h1", result={"path": ORACLE}),
            {
                "source": "agent",
                "tool_calls": [{
                    "tool_call_id": "r1",
                    "function_name": "read_file",
                    "arguments": {"opaque_command": "open target"},
                }],
            },
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(
            result["native_read_followthrough"],
            "UNKNOWN_NATIVE_READ_ARGUMENTS",
        )

    def test_missing_link_is_not_no_evidence(self) -> None:
        result = _run([_step(HASHMARKS, "h1")])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "missing-subject-observation-link")

    def test_duplicate_observation_link_is_unqualified(self) -> None:
        first = _step(HASHMARKS, "h1", result={"path": ORACLE})
        second = {
            "source": "environment",
            "observation": {
                "results": [{"source_call_id": "h1", "content": {"path": "src/other.py"}}]
            },
        }
        result = _run([first, second])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "duplicate-observation-link")

    def test_duplicate_tool_id_is_unqualified(self) -> None:
        result = _run([
            _step(HASHMARKS, "h1", result={"path": ORACLE}),
            _step("read_file", "h1", path=ORACLE),
        ])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "duplicate-tool-call-id")

    def test_malformed_trace_does_not_prove_never_invoked(self) -> None:
        result = _run([{"source": "agent", "tool_calls": ["bad"]}])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "malformed-tool-call")

    def test_never_invoked_is_observed_only_with_complete_order(self) -> None:
        result = _run([_step("rg", "r1", result="search")])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["reason"], "subject-never-invoked")
        self.assertEqual(result["arrival_timing"], "NO_SUBJECT_RESULT")

    def test_paths_do_not_match_by_basename_or_substring(self) -> None:
        result = _run([
            _step(HASHMARKS, "h1", result={"path": "other/owner.py"}),
            _step("read_file", "r1", path=ORACLE, result="read"),
        ])
        self.assertEqual(result["target_alignment"], "ALTERNATE_TARGETS_ONLY")
        self.assertEqual(result["native_read_followthrough"], "NO_MATCHING_NATIVE_READ")

    def test_pair_preserves_information_without_causal_claim(self) -> None:
        info = _run([
            _step(HASHMARKS, "h1", result={"path": ORACLE}),
            _step("read_file", "r1", path=ORACLE, result="code"),
        ])
        def arm(status: str, subject: str, information: dict | None = None) -> dict:
            trace = {
                "available": True,
                "tool_order_complete": True,
                "treatment": (
                    "NEVER_INVOKED" if subject == "none"
                    else "OBSERVED_SUCCESSFUL_RESULT"
                ),
                "subject_routing_timing": "FIRST_CHOICE",
                "subject_target_followthrough": "SUBJECT_TARGET_FOLLOWED",
                "native_discovery_calls": 1,
                "native_search_calls": 0,
                "native_read_calls": 1,
                "tool_calls": 2,
                "total_tokens": 200,
            }
            return {
                "receipt": {
                    "task_id": "task",
                    "harness": "codex",
                    "model": "model",
                    "replicate_id": 1,
                    "status": status,
                },
                "trace": trace,
                "answer": None,
                "information": information,
            }

        pair = pair_projection(
            arm("FAIL", "none"),
            arm("PASS", "hashmarks", info),
        )
        self.assertEqual(pair["outcome_transition"], "FAIL_TO_PASS")
        self.assertEqual(
            pair["information_evidence"]["native_read_followthrough"],
            "ORACLE_PATH_READ",
        )
        self.assertFalse(pair["positive_causal_proof_claimed"])


if __name__ == "__main__":
    unittest.main()
