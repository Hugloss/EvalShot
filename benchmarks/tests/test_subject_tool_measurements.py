from __future__ import annotations

import unittest

from benchmarks.adapters.codex import _metrics
from benchmarks.adapters.opencode_native import _token_metrics
from benchmarks.harness.tool_results import tool_result_evidence


class SubjectToolMeasurementTests(unittest.TestCase):
    def test_codex_missing_or_invalid_usage_stays_unknown(self) -> None:
        for usage in ({}, {"input_tokens": True, "output_tokens": -1},
                      {"input_tokens": "10", "output_tokens": 1.5}):
            with self.subTest(usage=usage):
                metrics = _metrics(
                    [{"type": "turn.completed", "usage": usage}],
                    subject_server=None,
                )
                self.assertIsNone(metrics["input_tokens"])
                self.assertIsNone(metrics["output_tokens"])
                self.assertIsNone(metrics["cached_input_tokens"])
        self.assertIsNone(_metrics([], subject_server=None)["input_tokens"])

    def test_codex_explicit_zero_is_observed_usage(self) -> None:
        metrics = _metrics([{"type": "turn.completed", "usage": {
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
        }}], subject_server=None)
        for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
            self.assertEqual(metrics[key], 0)

    def test_opencode_partial_usage_does_not_become_a_total(self) -> None:
        def message(tokens):
            return {"info": {"role": "assistant", "tokens": tokens}, "parts": []}

        self.assertEqual(_token_metrics({"messages": []}), (None, None, None))
        self.assertEqual(_token_metrics({"messages": [message({
            "input": 0, "output": 0, "cache": {"read": 0},
        })]}), (0, 0, 0))
        self.assertEqual(_token_metrics({"messages": [
            message({"input": 10, "output": 2, "cache": {"read": 1}}),
            message({"output": 3, "cached": 0}),
        ]}), (None, 5, 1))
        self.assertEqual(_token_metrics({"messages": [
            message({"input": 10, "output": 2, "cached": 1}),
            message(None),
        ]}), (None, None, None))
        for invalid in (True, -1, "2", 1.5):
            self.assertEqual(_token_metrics({"messages": [message({
                "input": invalid, "output": invalid, "cached": invalid,
            })]}), (None, None, None))

    def test_explicit_failure_overrides_nonempty_tool_payload(self) -> None:
        evidence = tool_result_evidence(
            operation="context",
            status="completed",
            result_present=True,
            result="diagnostic error payload",
            error={"message": "boom"},
            basis="test",
        )
        self.assertEqual(evidence["outcome"], "failed")
        self.assertTrue(evidence["error_present"])
        self.assertGreater(evidence["result_bytes"], 0)

    def test_empty_completed_tool_payload_is_not_usable_result(self) -> None:
        evidence = tool_result_evidence(
            operation="context",
            status="completed",
            result_present=True,
            result="",
            error=None,
            basis="test",
        )
        self.assertEqual(evidence["outcome"], "empty-result")
        self.assertEqual(evidence["result_bytes"], 0)

    def test_codex_reports_exact_subject_operation_names_as_complete(self) -> None:
        events = [
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "futuremcp",
                    "tool": "context",
                    "result": {"ok": True},
                    "error": None,
                    "status": "completed",
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "cached_input_tokens": 0,
                },
            },
        ]

        metrics = _metrics(events, subject_server="futuremcp")

        self.assertTrue(metrics["subject_tool_invoked"])
        self.assertEqual(metrics["subject_mcp_calls"], 1)
        self.assertEqual(metrics["subject_tool_names"], ["context"])
        self.assertEqual(metrics["subject_tool_observability"], "complete")
        evidence = metrics["subject_tool_result_evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["operation"], "context")
        self.assertEqual(evidence[0]["status"], "completed")
        self.assertGreater(evidence[0]["result_bytes"], 0)
        self.assertFalse(evidence[0]["error_present"])
        self.assertEqual(
            evidence[0]["outcome"],
            "successful-result-observed",
        )


if __name__ == "__main__":
    unittest.main()
