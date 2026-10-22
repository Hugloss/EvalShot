"""Dogfood audit must not requalify or silently rewrite frozen experiment tasks."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

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
        row = audit(self.suites)["tasks"][0]
        self.assertIn("REVIEW_OWNER_MISMATCH", row["problems"])
        self.assertEqual(row["review"], "MISMATCH")

    def test_review_source_object_key_order_is_not_semantic_drift(self):
        review_file = self.suite / "qualification" / "oracle-reviews.json"
        contents = json.loads(review_file.read_text())
        old = contents["tasks"]["locate-decision"]["repository"]
        contents["tasks"]["locate-decision"]["repository"] = {
            "tree": old["tree"], "commit": old["commit"], "url": old["url"]
        }
        review_file.write_text(json.dumps(contents))
        self.assertEqual(audit(self.suites)["tasks"][0]["review"], "MATCH")

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

    def test_oracle_directory_is_not_a_source_file(self):
        self.task["oracle"]["configuration"]["expected"]["path"] = "src"
        self._write()
        row = audit(self.suites, checkout=self.checkout)["tasks"][0]
        self.assertEqual(row["source"], "ORACLE_PATH_NOT_FILE")
        self.assertIn("ORACLE_PATH_NOT_FILE", row["problems"])

    def test_oracle_symlink_is_not_a_source_file(self):
        (self.checkout / "src" / "alias.py").symlink_to("owner.py")
        self._git("add", "src/alias.py")
        self._git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "symlink")
        self.task["repository"]["commit"] = self._git("rev-parse", "HEAD")
        self.task["repository"]["tree"] = self._git("rev-parse", "HEAD^{tree}")
        self.task["oracle"]["configuration"]["expected"]["path"] = "src/alias.py"
        self._write()
        row = audit(self.suites, checkout=self.checkout)["tasks"][0]
        self.assertEqual(row["source"], "ORACLE_PATH_NOT_FILE")

    def test_invalid_oracle_path_is_not_resolved_by_git(self):
        self.task["oracle"]["configuration"]["expected"]["path"] = "../src/owner.py"
        self._write()
        row = audit(self.suites, checkout=self.checkout)["tasks"][0]
        self.assertEqual(row["source"], "INVALID_PATH")
        self.assertIn("INVALID_ORACLE_PATH", row["problems"])

    def test_canonical_review_gate_is_not_inferred_from_transcript(self):
        with patch("benchmarks.dogfood.hashmarks_corpus._canonical_review_ready", return_value=False) as ready:
            initial = audit(self.suites)
            ready.assert_not_called()
            self.assertEqual(initial["canonical_review_qualification"], "NOT_CHECKED")
            checked = audit(self.suites, require_reviewed=True)
            ready.assert_called_once_with(self.suite)
            self.assertEqual(checked["canonical_review_qualification"], {"frozen-v1": "BLOCKED"})
            self.assertIn("CANONICAL_REVIEW_NOT_APPROVED", checked["tasks"][0]["problems"])
            self.assertEqual(checked["summary"]["tasks_with_problems"], 1)

    def test_approved_canonical_review_is_distinct_from_source_proof(self):
        with patch("benchmarks.dogfood.hashmarks_corpus._canonical_review_ready", return_value=True):
            checked = audit(self.suites, require_reviewed=True)
        self.assertEqual(checked["canonical_review_qualification"], {"frozen-v1": "APPROVED"})
        self.assertEqual(checked["tasks"][0]["source"], "NOT_CHECKED")
        self.assertEqual(checked["summary"]["tasks_with_problems"], 0)

    def test_unassigned_archived_task_is_not_a_missing_active_review(self):
        (self.suite / "experiment.json").write_text(json.dumps({"tasks": ["other-task"]}))
        report = audit(self.suites)
        row = report["tasks"][0]
        self.assertEqual(row["experiment_membership"], "UNASSIGNED")
        self.assertEqual(report["summary"]["unreviewed_assigned_tasks"], 0)
        with patch("benchmarks.dogfood.hashmarks_corpus._canonical_review_ready", return_value=False):
            checked = audit(self.suites, require_reviewed=True)
        self.assertEqual(checked["canonical_review_qualification"], {"frozen-v1": "BLOCKED"})
        self.assertNotIn("CANONICAL_REVIEW_NOT_APPROVED", checked["tasks"][0]["problems"])

    def test_assigned_unreviewed_task_is_visible_without_approving(self):
        (self.suite / "experiment.json").write_text(json.dumps({"tasks": ["locate-decision"]}))
        (self.suite / "qualification" / "oracle-reviews.json").unlink()
        report = audit(self.suites)
        self.assertEqual(report["tasks"][0]["experiment_membership"], "ASSIGNED")
        self.assertEqual(report["summary"]["unreviewed_assigned_tasks"], 1)
        with patch("benchmarks.dogfood.hashmarks_corpus._canonical_review_ready", return_value=False):
            checked = audit(self.suites, require_reviewed=True)
        self.assertIn("CANONICAL_REVIEW_NOT_APPROVED", checked["tasks"][0]["problems"])

    def test_non_hashmarks_task_not_in_scope(self):
        self.task["repository"]["url"] = "https://github.com/example/other.git"
        self._write()
        with self.assertRaisesRegex(ValueError, "no Hashmarks-source tasks"):
            audit(self.suites)


if __name__ == "__main__":
    unittest.main()
