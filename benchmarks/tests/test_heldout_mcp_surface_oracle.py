from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.harness.identity import digest


ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
TASK = SUITE / "tasks/locate-mcp-task-evidence.json"
REVIEWS = SUITE / "qualification/oracle-reviews.json"


class HeldoutMcpSurfaceOracleTests(unittest.TestCase):
    def test_task_evidence_oracle_distinguishes_semantic_surface_from_transport(self) -> None:
        task = json.loads(TASK.read_text(encoding="utf-8"))
        reviews = json.loads(REVIEWS.read_text(encoding="utf-8"))
        review = reviews["tasks"]["locate-mcp-task-evidence"]

        self.assertIn("HashmarksMcpSurface", task["prompt"])
        self.assertIn("transport registration wrapper", task["prompt"])
        self.assertEqual(
            task["oracle"]["configuration"]["expected"],
            {
                "path": "hashmarks/mcp_surface.py",
                "symbol": "task_evidence",
            },
        )
        self.assertTrue(
            any("mcp_server.py::task_evidence" in row for row in review["alternatives"])
        )
        self.assertIn("delegates", review["evidence"])
        self.assertEqual(review["task_digest"], digest(task))
        self.assertTrue(
            all(row["task_digest"] == digest(task) for row in review["reviews"])
        )


if __name__ == "__main__":
    unittest.main()
