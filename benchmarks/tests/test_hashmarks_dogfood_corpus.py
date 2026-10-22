"""Dogfood audit must not requalify or silently rewrite frozen experiment tasks."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_corpus import audit


class HashmarksDogfoodAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.suites = self.root / "suites"
        self.suite = self.suites / "frozen-v1"
        (self.suite / "tasks").mkdir(parents=True)
        (self.suite / "qualification").mkdir()
        self.checkout = self.root / "source"
        self.checkout.mkdir()
        self._git("init", "-q")
        (self.checkout / "src").mkdir()
        (self.checkout / "src" / "owner.py").write_text("def decide(): return True\n")
        self._git("add", ".")
        self._git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "source")
        self.commit = self._git("rev-parse", "HEAD")
        self.tree = self._git("rev-parse", "HEAD^{tree}")
        self.task = {
            "id": "locate-decision", "family": "python-localization", "mode": "read_only",
            "repository": {"url": "https://github.com/Hugloss/Hashmarks.git", "commit": self.commit, "tree": self.tree},
            "oracle": {"adapter": "repository-location-json", "configuration": {"expected": {"path": "src/owner.py", "symbol": "decide"}}},
        }
        self._write()

    def _git(self, *args):
        proc = subprocess.run(["git", "-C", str(self.checkout), *args], check=True, capture_output=True, text=True)
        return proc.stdout.strip()

    def _write(self):
        (self.suite / "tasks" / "locate-decision.json").write_text(json.dumps(self.task))
        (self.suite / "qualification" / "oracle-reviews.json").write_text(json.dumps({
            "tasks": {"locate-decision": {"repository": self.task["repository"],
                                          "owner": {"path": "src/owner.py", "symbol": "decide"}}}
        }))

    def test_pinned_commit_and_review_agree_without_checkout_mutation(self):
        before = self._git("status", "--porcelain")
        report = audit(self.suites, checkout=self.checkout, require_source=True)
        self.assertEqual(report["summary"]["tasks_with_problems"], 0)
        self.assertEqual(report["tasks"][0]["source"], "VERIFIED_PATH")
        self.assertEqual(report["tasks"][0]["review"], "MATCH")
        self.assertEqual(self._git("status", "--porcelain"), before)

    def test_no_checkout_does_not_claim_reproducibility(self):
        row = audit(self.suites)["tasks"][0]
        self.assertEqual(row["source"], "NOT_CHECKED")
        self.assertEqual(row["problems"], [])
        with self.assertRaisesRegex(ValueError, "requires --checkout"):
            audit(self.suites, require_source=True)

    def test_review_owner_drift_fails(self):
        review_file = self.suite / "qualification" / "oracle-reviews.json"
        contents = json.loads(review_file.read_text())
        contents["tasks"]["locate-decision"]["owner"]["symbol"] = "other"
        review_file.write_text(json.dumps(contents))
        self.assertIn("REVIEW_OWNER_MISMATCH", audit(self.suites)["tasks"][0]["problems"])

    def test_review_source_drift_fails(self):
        review_file = self.suite / "qualification" / "oracle-reviews.json"
        contents = json.loads(review_file.read_text())
        contents["tasks"]["locate-decision"]["repository"]["commit"] = "0" * 40
        review_file.write_text(json.dumps(contents))
        self.assertIn("REVIEW_SOURCE_MISMATCH", audit(self.suites)["tasks"][0]["problems"])

    def test_tree_mismatch_and_missing_oracle_path_fail(self):
        self.task["repository"]["tree"] = "0" * 40
        self._write()
        self.assertIn("TREE_MISMATCH", audit(self.suites, checkout=self.checkout)["tasks"][0]["problems"])
        self.task["repository"]["tree"] = self.tree
        self.task["oracle"]["configuration"]["expected"]["path"] = "src/missing.py"
        self._write()
        self.assertIn("ORACLE_PATH_MISSING", audit(self.suites, checkout=self.checkout)["tasks"][0]["problems"])

    def test_unavailable_commit_is_unknown_unless_strict(self):
        self.task["repository"]["commit"] = "0" * 40
        self._write()
        self.assertEqual(audit(self.suites, checkout=self.checkout)["tasks"][0]["source"], "SOURCE_UNAVAILABLE")
        self.assertIn("SOURCE_UNAVAILABLE", audit(self.suites, checkout=self.checkout, require_source=True)["tasks"][0]["problems"])

    def test_frozen_variants_reported_not_declared_invalid(self):
        second = self.suites / "frozen-v2" / "tasks"
        second.mkdir(parents=True)
        modified = dict(self.task, prompt="same ID; different historical prompt")
        (second / "locate-decision.json").write_text(json.dumps(modified))
        report = audit(self.suites)
        self.assertEqual(len(report["variants"]), 1)
        self.assertEqual(report["summary"]["tasks_with_problems"], 0)

    def test_non_hashmarks_task_not_in_scope(self):
        self.task["repository"]["url"] = "https://github.com/example/other.git"
        self._write()
        with self.assertRaisesRegex(ValueError, "no Hashmarks-source tasks"):
            audit(self.suites)


if __name__ == "__main__":
    unittest.main()
