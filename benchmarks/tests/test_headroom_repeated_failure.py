"""Regression for headroom failure reproducibility and exclusion."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE = (
    Path(__file__).resolve().parents[1]
    / "suites/repository-intelligence/headroom-v2/score.py"
)
spec = importlib.util.spec_from_file_location("headroom_v2_score_test", MODULE)
assert spec is not None and spec.loader is not None
score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score)


class HeadroomQualificationTests(unittest.TestCase):
    def test_one_failure_does_not_claim_reproducibility(self) -> None:
        contract = {
            "conditions": {"bare": {
                "trials": 3, "semantic_gradeable": 3,
                "semantic_passes": 2, "semantic_failures": 1,
            }},
            "tasks": {"task-a": {"bare": {
                "trials": 3, "semantic_gradeable": 3, "semantic_failures": 1,
            }}},
        }
        result = score._bare_headroom({}, bare_condition_ids={"bare"}, answer_contract=contract)
        self.assertEqual(result["state"], "observed")
        self.assertEqual(result["reproducibility_state"], "not-demonstrated")
        self.assertEqual(result["repeated_failure_task_conditions"], 0)
        self.assertNotIn("reproducible", result["interpretation"])

    def test_two_failures_across_three_replicates_is_observed(self) -> None:
        contract = {
            "conditions": {"bare": {
                "trials": 3, "semantic_gradeable": 3,
                "semantic_passes": 1, "semantic_failures": 2,
            }},
            "tasks": {"task-b": {"bare": {
                "trials": 3, "semantic_gradeable": 3, "semantic_failures": 2,
            }}},
        }
        result = score._bare_headroom({}, bare_condition_ids={"bare"}, answer_contract=contract)
        self.assertEqual(result["reproducibility_state"], "observed")
        self.assertEqual(result["per_task_bare_headroom"][0]["task_id"], "task-b")

    def test_incomplete_has_no_invented_headroom(self) -> None:
        contract = {
            "conditions": {"bare": {
                "trials": 3, "semantic_gradeable": 0,
                "semantic_passes": 0, "semantic_failures": 0,
            }},
            "tasks": {"task-c": {"bare": {
                "trials": 3, "semantic_gradeable": 0, "semantic_failures": 0,
            }}},
        }
        result = score._bare_headroom({}, bare_condition_ids={"bare"}, answer_contract=contract)
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["reproducibility_state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
