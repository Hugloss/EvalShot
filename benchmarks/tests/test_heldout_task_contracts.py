from __future__ import annotations

import unittest
from pathlib import Path

from benchmarks.harness.suite import load_suite


HELDOUT = (
    Path(__file__).resolve().parents[1]
    / "suites/repository-intelligence/heldout-v1"
)


class HeldoutTaskContractTests(unittest.TestCase):
    def test_every_non_control_subject_declares_exposure_probe(self) -> None:
        suite = load_suite(HELDOUT)
        self.assertEqual(
            suite.experiment["subject_exposure_contract"],
            {"require_probe_contract": True},
        )
        observed = {}
        for subject_id, subject in sorted(suite.subjects.items()):
            if subject["kind"] == "control":
                continue
            probe = subject.get("exposure_probe")
            self.assertIsInstance(probe, dict, subject_id)
            required_tool = probe.get("required_tool")
            self.assertIsInstance(required_tool, str, subject_id)
            self.assertTrue(required_tool.startswith(subject_id + "_"), subject_id)
            observed[subject_id] = required_tool
        self.assertEqual(
            observed,
            {
                "enola": "enola_explore",
                "hashmarks": "hashmarks_task_evidence",
            },
        )

    def test_repair_task_requires_temporary_repro_artifacts_to_leave_repository_clean(
        self,
    ) -> None:
        suite = load_suite(HELDOUT)
        task = suite.tasks["repair-partial-receipt-regression"]

        self.assertIn(
            "Temporary reproduction artifacts must be created outside the repository "
            "or removed before finishing",
            task["prompt"],
        )
        self.assertEqual(
            task["contamination"]["allowed_change_globs"],
            ["benchmarks/harness/receipt.py"],
        )
        self.assertNotIn("*.py", task["contamination"]["allowed_generated_globs"])
        self.assertNotIn("repro*", task["contamination"]["allowed_generated_globs"])


if __name__ == "__main__":
    unittest.main()
