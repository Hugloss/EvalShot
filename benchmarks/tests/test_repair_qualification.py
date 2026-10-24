"""Model-free repair qualification regressions."""
import tempfile
import unittest
from pathlib import Path

from benchmarks.repair_qualification import RepairChecks, check_repair


class RepairQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.before = Path(self.temp.name) / "before"
        self.after = Path(self.temp.name) / "after"
        self.before.mkdir()
        self.after.mkdir()

    def write(self, before, after):
        for target, code in [(self.before, before), (self.after, after)]:
            (target / "logic.py").write_text(code, encoding="utf8")
            (target / "test_defect.py").write_text(
                "from logic import identity\nassert identity({'manifest': 'correct', 'artifact': 'wrong.zip'}) == 'correct'\n",
                encoding="utf8",
            )
            (target / "test_preservation.py").write_text(
                "from logic import identity\nassert identity({'manifest': 'same', 'artifact': 'same.zip'}) == 'same'\n",
                encoding="utf8",
            )

    def checks(self):
        return RepairChecks(
            ("python3", "-B", "test_defect.py"),
            (("python3", "-B", "test_preservation.py"),),
        )

    def test_behavioral_repair_passes_without_claiming_semantic_proof(self):
        self.write(
            "def identity(x): return x['artifact'].split('.')[0]\n",
            "def identity(x): return x['manifest']\n",
        )
        result = check_repair(before=self.before, after=self.after, checks=self.checks())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["semantic_root_cause"], "NOT_ADJUDICATED")

    def test_no_defect_baseline_cannot_qualify(self):
        self.write("def identity(x): return x['manifest']\n", "def identity(x): return x['manifest']\n")
        self.assertEqual(check_repair(before=self.before, after=self.after, checks=self.checks())["status"], "NOT_QUALIFIED")

    def test_preservation_regression_blocks_repair(self):
        self.write(
            "def identity(x): return x['artifact'].split('.')[0]\n",
            "def identity(x): return 'correct'\n",
        )
        self.assertEqual(check_repair(before=self.before, after=self.after, checks=self.checks())["status"], "NOT_QUALIFIED")

    def test_same_workspace_rejected(self):
        with self.assertRaises(ValueError):
            check_repair(before=self.before, after=self.before, checks=self.checks())

    def test_missing_controls_rejected(self):
        with self.assertRaises(ValueError):
            check_repair(before=self.before, after=self.after, checks=RepairChecks(("python3",), ()))


if __name__ == "__main__":
    unittest.main()
