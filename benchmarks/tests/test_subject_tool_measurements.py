from __future__ import annotations

import unittest

from benchmarks.adapters.codex import _metrics
from benchmarks.harness.tool_results import tool_result_evidence


class SubjectToolMeasurementTests(unittest.TestCase):
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
