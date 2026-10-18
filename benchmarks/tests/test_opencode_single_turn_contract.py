from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.adapters.opencode_native import _run_failure_reason


ROOT = Path(__file__).resolve().parents[2]
AGENT = (
    ROOT
    / "benchmarks/suites/repository-intelligence/heldout-v1/agents/opencode-native.json"
)


class OpenCodeRuntimeContractTests(unittest.TestCase):
    def test_opencode_agent_identity_versions_execution_runtime(self) -> None:
        agent = json.loads(AGENT.read_text(encoding="utf-8"))
        self.assertEqual(agent["identity"]["version"], "native-config-v7")

    def test_recovered_context_overflow_requires_exported_final(self) -> None:
        run = {"status": 1, "stderr": "", "error": None, "signal": None}
        self.assertIsNone(_run_failure_reason(
            run, export_error=None, final_text="answer",
            recovered_context_overflow=True,
        ))
        self.assertIsNotNone(_run_failure_reason(
            run, export_error="invalid export", final_text="answer",
            recovered_context_overflow=True,
        ))
        self.assertIsNotNone(_run_failure_reason(
            run, export_error=None, final_text=None,
            recovered_context_overflow=True,
        ))


if __name__ == "__main__":
    unittest.main()
