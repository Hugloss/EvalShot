from __future__ import annotations

import collections
import subprocess
import sys
import unittest
from pathlib import Path

from benchmarks.harness.suite import load_suite

ROOT = (
    Path(__file__).resolve().parents[1] / "suites/repository-intelligence/behavioral-v3"
)


class BehavioralV3BenchmarkTests(unittest.TestCase):
    def test_suite_is_frozen_balanced_and_independent(self) -> None:
        suite = load_suite(ROOT)
        self.assertEqual(len(suite.tasks), 8)
        self.assertEqual(len(suite.trial_definitions()), 48)
        self.assertEqual(
            collections.Counter(task["family"] for task in suite.tasks.values()),
            {
                "post_change": 2,
                "change_impact": 2,
                "verification": 2,
                "dependency_delta": 2,
            },
        )
        self.assertEqual(
            collections.Counter(task["mode"] for task in suite.tasks.values()),
            {"edit": 4, "read_only": 4},
        )
        for task in suite.tasks.values():
            self.assertEqual(
                task["repository"]["commit"],
                "844dfa06d50b2f4402306b16a217b29946a3dedf",
            )
            self.assertNotIn("Hashmarks.git", task["repository"]["url"])
            self.assertEqual(task["oracle"]["adapter"], "command")
            self.assertTrue(task["mutation"]["changed_paths"])

    def test_edit_tasks_allow_only_the_intended_agent_edit(self) -> None:
        suite = load_suite(ROOT)
        expected = {
            "post_change-00": ["benchmark_case/post_change_00/src/engine.py"],
            "post_change-01": ["benchmark_case/post_change_01/src/route.py"],
            "change_impact-00": ["benchmark_case/change_impact_00/src/core.py"],
            "change_impact-01": ["benchmark_case/change_impact_01/src/engine.ts"],
        }
        for task_id, paths in expected.items():
            self.assertEqual(
                suite.tasks[task_id]["contamination"]["allowed_change_globs"],
                paths,
            )
        for task_id, task in suite.tasks.items():
            if task["mode"] == "read_only":
                self.assertEqual(
                    task["contamination"]["allowed_change_globs"],
                    [],
                    task_id,
                )

    def test_every_independent_oracle_healthcheck_is_executable(self) -> None:
        suite = load_suite(ROOT)
        oracle = ROOT / "oracle.py"
        for task_id in suite.experiment["tasks"]:
            with self.subTest(task_id=task_id):
                result = subprocess.run(
                    (sys.executable, str(oracle), "health", task_id),
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
