from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from benchmarks.__main__ import _emit_run_results, _parser


class BenchmarkRunOutputTests(unittest.TestCase):
    def test_run_json_results_can_be_suppressed_explicitly(self) -> None:
        default = _parser().parse_args(["run", "--new", "--env-file", ".env"])
        explicit = _parser().parse_args(
            ["run", "--new", "--env-file", ".env", "--no-json-results"]
        )

        self.assertFalse(default.no_json_results)
        self.assertTrue(explicit.no_json_results)

    def test_run_results_can_be_suppressed_for_human_terminal_output(self) -> None:
        results = [
            {
                "status": "INCOMPLETE",
                "reason_code": "agent-terminal-failed",
                "result_dir": "/tmp/evidence",
            }
        ]
        output = io.StringIO()

        with redirect_stdout(output):
            _emit_run_results(results, enabled=False)

        self.assertEqual(output.getvalue(), "")

    def test_run_results_remain_emitted_by_default(self) -> None:
        results = [{"status": "PASS", "trial_id": "abc"}]
        output = io.StringIO()

        with redirect_stdout(output):
            _emit_run_results(results, enabled=True)

        self.assertEqual(json.loads(output.getvalue()), results)

    def test_benchmark_new_make_target_suppresses_raw_json(self) -> None:
        makefile = Path("Makefile").read_text(encoding="utf-8")
        self.assertIn(
            './benchmark run --new --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)" --no-json-results',
            makefile,
        )


if __name__ == "__main__":
    unittest.main()
