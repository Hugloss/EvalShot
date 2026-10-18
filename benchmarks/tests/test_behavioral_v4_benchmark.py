from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from benchmarks.harness.suite import load_suite

ROOT = (
    Path(__file__).resolve().parents[1] / "suites/repository-intelligence/behavioral-v4"
)


class BehavioralV4BenchmarkTests(unittest.TestCase):
    def test_suite_has_32_tasks_and_192_trials(self) -> None:
        suite = load_suite(ROOT)
        self.assertEqual(len(suite.tasks), 32)
        self.assertEqual(len(suite.trial_definitions()), 192)
        self.assertEqual(
            {
                family: sum(
                    task.get("family") == family for task in suite.tasks.values()
                )
                for family in (
                    "post_change",
                    "change_impact",
                    "verification",
                    "dependency_delta",
                    "correlation",
                    "declarations",
                    "freshness",
                    "negative_bounds",
                )
            },
            {
                family: 4
                for family in (
                    "post_change",
                    "change_impact",
                    "verification",
                    "dependency_delta",
                    "correlation",
                    "declarations",
                    "freshness",
                    "negative_bounds",
                )
            },
        )

    def test_oracle_health_and_positive_lexigram_case(self) -> None:
        oracle = ROOT / "oracle.py"
        for task_id in load_suite(ROOT).experiment["tasks"]:
            result = subprocess.run(
                (sys.executable, str(oracle), "health", task_id),
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        observation = Path("/tmp/behavioral-v4-observation.json")
        observation.write_text(
            json.dumps(
                {
                    "payload": {
                        "final_message": json.dumps(
                            {
                                "change": "unchanged",
                                "invalidated": [],
                                "revision_reusable": True,
                            }
                        )
                    }
                }
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            (sys.executable, str(oracle), "grade", "post_change-02"),
            cwd=ROOT,
            env={
                **__import__("os").environ,
                "BENCHMARK_OBSERVATION_PATH": str(observation),
            },
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads(result.stdout)["rubric"]["resolution"], "COMPLETE")
        observation.unlink()

    def test_every_mutation_is_applicable_and_scored_as_lexigram(self) -> None:
        suite = load_suite(ROOT)
        for task in suite.tasks.values():
            self.assertEqual(
                task["oracle"]["configuration"]["result_format"], "lexigram-v1"
            )
            self.assertIsNotNone(task["mutation"])


if __name__ == "__main__":
    unittest.main()
