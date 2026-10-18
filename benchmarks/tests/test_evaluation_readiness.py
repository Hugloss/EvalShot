"""Readiness truth must not confuse committed code with empirical evidence."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.harness.evaluation_readiness import build_readiness


class EvaluationReadinessTests(unittest.TestCase):
    def test_unreviewed_cases_and_missing_confusion_are_visible(self) -> None:
        root = Path(__file__).resolve().parents[2]
        result = build_readiness(root)
        self.assertFalse(result["evaluation_claim_qualified"])
        self.assertEqual(result["multidomain"]["cases"], 60)
        self.assertEqual(result["qualification_blockers"]["unreviewed_multidomain_cases"], 60)
        skill_cases = json.loads(
            (root / "evals/sharp-skill-cases.json").read_text(encoding="utf-8")
        )["cases"]
        evaluated_skills = {case["skill"] for case in skill_cases}
        self.assertIn("canonical-consumer-contract-review", evaluated_skills)
        # This projection owns the frozen corpus. Live skill availability is
        # checked by the JS evaluator against its explicit --cookbook-root.
        for name in evaluated_skills:
            self.assertTrue({"positive", "control"}.issubset(
                {case["kind"] for case in skill_cases if case["skill"] == name}
            ))
        self.assertEqual(result["skill_corpus"]["skills"], len(evaluated_skills))
        self.assertEqual(result["qualification_blockers"]["skills_without_confusion_case"], 54)
        self.assertEqual(result["qualification_blockers"]["fixture_positive_without_pinned_anchor"], 0)
        self.assertFalse(result["qualification_blockers"]["host_model_input_delivery_attested"])
        self.assertFalse(result["qualification_blockers"]["factorial_model_run_qualified"])
        self.assertEqual(
            result["factorial"]["frozen_components"], ["task_evidence", "find"],
        )


if __name__ == "__main__":
    unittest.main()
