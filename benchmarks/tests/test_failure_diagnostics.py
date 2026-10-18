from __future__ import annotations

import unittest
from pathlib import Path

from benchmarks.adapters.opencode_native import _run_failure_diagnostic
from benchmarks.harness.model import Observation
from benchmarks.harness.runner import (
    TrialRunResult,
    _agent_failure_diagnostic,
    bind_result_to_receipt,
)


class FailureDiagnosticTests(unittest.TestCase):
    def test_receipt_projector_clears_stale_optional_result_fields(self) -> None:
        transient = TrialRunResult(
            trial_id="a" * 64,
            definition_id="b" * 64,
            status="INCOMPLETE",
            result_dir=Path("/tmp/result"),
            reused=True,
            reason="stale reason",
            stage="stale-stage",
            reason_code="stale-code",
            diagnostic="stale diagnostic",
            recovered=True,
        )
        receipt = {
            "trial_id": transient.trial_id,
            "definition_id": transient.definition_id,
            "status": "PASS",
        }

        bound = bind_result_to_receipt(transient, receipt)

        self.assertEqual(bound.status, "PASS")
        self.assertIsNone(bound.reason)
        self.assertIsNone(bound.stage)
        self.assertIsNone(bound.reason_code)
        self.assertIsNone(bound.diagnostic)
        self.assertTrue(bound.reused)
        self.assertTrue(bound.recovered)
        self.assertEqual(bound.result_dir, transient.result_dir)

    def test_runner_prefers_preserved_adapter_diagnostic(self) -> None:
        observation = Observation(
            {
                "failure_diagnostic": (
                    "OpenCode export diagnostic:\n"
                    "Error: opencode export: terminal assistant message has no final text\n"
                    "    at extractFinalAnswer"
                )
            },
            "",
        )

        diagnostic = _agent_failure_diagnostic(
            observation,
            reason="agent terminal event was turn.failed",
        )

        self.assertIn("OpenCode export diagnostic", diagnostic)
        self.assertIn("at extractFinalAnswer", diagnostic)
        self.assertNotEqual(diagnostic, "agent terminal event was turn.failed")

    def test_runner_exception_traceback_remains_highest_priority(self) -> None:
        observation = Observation(
            {"failure_diagnostic": "adapter diagnostic"},
            "",
        )

        diagnostic = _agent_failure_diagnostic(
            observation,
            reason="terminal failure",
            exception_traceback="Traceback (most recent call last):\nValueError: boom",
        )

        self.assertEqual(
            diagnostic,
            "Traceback (most recent call last):\nValueError: boom",
        )

    def test_opencode_failure_diagnostic_preserves_export_stack_and_run_stderr(
        self,
    ) -> None:
        diagnostic = _run_failure_diagnostic(
            {
                "status": 0,
                "stdout": '{"type":"run"}',
                "stderr": "provider stack line",
                "error": None,
                "signal": None,
            },
            export_diagnostic=(
                "Error: opencode export: terminal assistant message has no final text\n"
                "    at extractFinalAnswer"
            ),
            runtime_stderr="",
        )

        assert diagnostic is not None
        self.assertIn("OpenCode export diagnostic", diagnostic)
        self.assertIn("at extractFinalAnswer", diagnostic)
        self.assertIn("OpenCode run stderr", diagnostic)
        self.assertIn("provider stack line", diagnostic)

    def test_opencode_nonzero_falls_back_to_bounded_stdout_tail(self) -> None:
        diagnostic = _run_failure_diagnostic(
            {
                "status": 17,
                "stdout": "x" * 3000 + "tail-marker",
                "stderr": "",
                "error": None,
                "signal": None,
            },
            export_diagnostic=None,
            runtime_stderr=None,
        )

        assert diagnostic is not None
        self.assertIn("OpenCode run stdout tail", diagnostic)
        self.assertIn("tail-marker", diagnostic)
        self.assertLess(len(diagnostic), 2200)


if __name__ == "__main__":
    unittest.main()
