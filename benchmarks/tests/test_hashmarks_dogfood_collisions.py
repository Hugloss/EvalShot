"""Cross-file source collision dogfood must preserve ambiguity and uncertainty."""

from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_collisions import main, observe_collisions


class CollisionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        (self.repo / "hashmarks").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "hashmarks/owner.py").write_text(
            "class MapStore:\n    def paths_under(self): return []\n"
            "    def _sync_remove_stale_paths(self): return None\n",
            encoding="utf-8",
        )
        (self.repo / "hashmarks/alternate.py").write_text(
            "class OtherStore:\n    async def paths_under(self): return []\n"
            "# def _sync_remove_stale_paths(): NOT A DECLARATION\n"
            "text = 'def _sync_remove_stale_paths(): also NOT A DECLARATION'\n",
            encoding="utf-8",
        )
        (self.repo / "tests/test_owner.py").write_text("def paths_under(): pass\n")
        self._commit("pinned")
        self.audit = {
            "schema": "evalshot.hashmarks-dogfood-audit.v1",
            "repository": "github.com/hugloss/hashmarks",
            "qualification": "MODEL_FREE_ONLY",
            "summary": {"tasks": 3},
            "requirements": {"audit_passed": True},
            "tasks": [
                self._row("paths-under", "paths_under", "hashmarks/owner.py"),
                self._row("remove-stale", "_sync_remove_stale_paths", "hashmarks/owner.py"),
                self._row("wrong-source", "paths_under", "missing.py",
                          status="ORACLE_PATH_MISSING", errors=["ORACLE_PATH_MISSING"]),
            ],
        }

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def _commit(self, message):
        self._git("add", "-A")
        self._git("-c", "user.email=review@example.invalid",
                  "-c", "user.name=Reviewer", "commit", "-qm", message)
        self.commit = self._git("rev-parse", "HEAD")
        self.tree = self._git("rev-parse", "HEAD^{tree}")

    def _row(self, name, symbol, path, *, status="VERIFIED_PATH", errors=None):
        return {
            "suite": "heldout-v1", "task": name, "family": "known-symbol-localization",
            "mode": "read_only",
            "repository": "github.com/hugloss/hashmarks",
            "commit": self.commit, "tree": self.tree,
            "task_sha256": "a" * 64, "task_semantic_digest": "b" * 64,
            "experiment_membership": "UNASSIGNED", "review": "NO_TASK_REVIEW",
            "source": status, "problems": list(errors or []),
            "oracle_path": path, "oracle_symbol": symbol,
        }

    def _by_task(self, report, name):
        return next(row for row in report["observations"] if row["task"] == name)

    def test_entire_pinned_tree_finds_other_file_declarations_not_only_nomination(self):
        result = observe_collisions(self.audit, checkout=self.repo)
        observed = self._by_task(result, "paths-under")
        self.assertEqual(observed["status"], "SAME_NAME_ALTERNATIVES_OBSERVED")
        self.assertTrue(observed["scan_complete"])
        self.assertEqual(observed["same_file_declarations"], 1)
        self.assertEqual(observed["total_same_name_declarations"], 3)
        self.assertEqual(
            {r["path"] for r in observed["other_file_same_name_declarations"]},
            {"hashmarks/alternate.py", "tests/test_owner.py"},
        )
        self.assertEqual(observed["nominated_declarations"][0]["qualname"],
                         "MapStore.paths_under")
        self.assertFalse(observed["unique_semantic_owner_proven"])

    def test_no_same_name_candidate_is_not_promoted_to_unique_owner(self):
        result = observe_collisions(self.audit, checkout=self.repo)
        row = self._by_task(result, "remove-stale")
        self.assertEqual(row["status"], "NO_OTHER_STATIC_DECLARATIONS_OBSERVED")
        self.assertEqual(row["total_same_name_declarations"], 1)
        self.assertEqual(row["other_file_same_name_declarations"], [])
        self.assertFalse(row["unique_semantic_owner_proven"])
        self.assertEqual(result["summary"]["semantic_owner_claims"], 0)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertEqual(result["summary"]["baseline_headroom_established"], 0)

    def test_source_precondition_is_not_bypassed_by_matches_elsewhere(self):
        row = self._by_task(observe_collisions(self.audit, checkout=self.repo),
                            "wrong-source")
        self.assertEqual(row["status"], "SOURCE_PRECONDITION_FAILED")
        self.assertFalse(row["scan_complete"])
        self.assertEqual(row["total_same_name_declarations"], 0)

    def test_worktree_mutation_cannot_change_frozen_tree_observation(self):
        baseline = observe_collisions(self.audit, checkout=self.repo)
        (self.repo / "hashmarks/alternate.py").write_text(
            "def unrelated(): return False\n")
        (self.repo / "hashmarks/extra.py").write_text(
            "def paths_under(): return 'not in pinned commit'\n")
        self.assertEqual(baseline, observe_collisions(self.audit, checkout=self.repo))

    def test_tree_mismatch_fails_closed(self):
        self.audit["tasks"][0]["tree"] = "f" * 40
        report = observe_collisions(self.audit, checkout=self.repo)
        row = self._by_task(report, "paths-under")
        self.assertEqual(row["status"], "SOURCE_IDENTITY_MISMATCH")
        self.assertFalse(row["scan_complete"])

    def test_conflicting_frozen_trees_for_same_commit_fail_early(self):
        self.audit["tasks"][1]["tree"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "disagree on source tree"):
            observe_collisions(self.audit, checkout=self.repo)

    def test_unparseable_tracked_python_makes_scan_incomplete(self):
        (self.repo / "hashmarks/broken.py").write_text("def invalid( :\n")
        self._commit("broken")
        for row in self.audit["tasks"]:
            row["commit"] = self.commit
            row["tree"] = self.tree
        report = observe_collisions(self.audit, checkout=self.repo)
        row = self._by_task(report, "paths-under")
        self.assertEqual(row["status"], "SCAN_INCOMPLETE")
        self.assertFalse(row["scan_complete"])
        self.assertEqual(len(row["other_file_same_name_declarations"]), 2)
        scan = report["commit_scans"][self.commit]
        self.assertIn(
            {"path": "hashmarks/broken.py", "reason": "UNPARSEABLE_PYTHON"},
            scan["skipped"],
        )

    def test_non_regular_python_symlink_cannot_hide_scan_gap(self):
        (self.repo / "hashmarks/link.py").symlink_to("owner.py")
        self._commit("symlink")
        for row in self.audit["tasks"]:
            row["commit"] = self.commit
            row["tree"] = self.tree
        report = observe_collisions(self.audit, checkout=self.repo)
        self.assertEqual(
            self._by_task(report, "remove-stale")["status"],
            "SCAN_INCOMPLETE",
        )
        self.assertIn(
            {"path": "hashmarks/link.py", "reason": "NON_REGULAR_OR_UNSAFE"},
            report["commit_scans"][self.commit]["skipped"],
        )

    def test_only_exact_static_definitions_count_not_comments_strings_or_prefixes(self):
        report = observe_collisions(self.audit, checkout=self.repo)
        row = self._by_task(report, "remove-stale")
        self.assertEqual(row["total_same_name_declarations"], 1)

    def test_invalid_symbol_and_non_python_candidate_are_explicit(self):
        self.audit["tasks"][0]["oracle_symbol"] = "paths_under; unsafe"
        self.audit["tasks"][1]["oracle_path"] = "example.ts"
        report = observe_collisions(self.audit, checkout=self.repo)
        self.assertEqual(self._by_task(report, "paths-under")["status"], "INVALID_SYMBOL")
        self.assertEqual(self._by_task(report, "remove-stale")["status"],
                         "NOT_PYTHON_SOURCE_CANDIDATE")

    def test_assigned_tasks_are_not_scanned_as_new_candidate(self):
        self.audit["tasks"][0]["experiment_membership"] = "ASSIGNED"
        report = observe_collisions(self.audit, checkout=self.repo)
        self.assertEqual(report["summary"]["unassigned_candidates"], 2)
        self.assertFalse(any(x["task"] == "paths-under" for x in report["observations"]))

    def test_unchanged_source_evidence_produces_deterministic_reports(self):
        original = deepcopy(self.audit)
        first = observe_collisions(self.audit, checkout=self.repo)
        self.assertEqual(first, observe_collisions(self.audit, checkout=self.repo))
        self.assertEqual(self.audit, original)

    def test_bad_checkout_and_untrusted_audit_fail_before_scan(self):
        with self.assertRaisesRegex(ValueError, "Git working tree"):
            observe_collisions(self.audit, checkout=self.root)
        self.audit["repository"] = "github.com/attacker/repo"
        with self.assertRaisesRegex(ValueError, "Hashmarks"):
            observe_collisions(self.audit, checkout=self.repo)

    def test_cli_writes_only_separate_report_without_mutating_audit(self):
        path = self.root / "audit.json"
        output = self.root / "collisions.json"
        path.write_text(json.dumps(self.audit), encoding="utf-8")
        original = path.read_bytes()
        with redirect_stdout(StringIO()) as printed:
            status = main(["--audit", str(path), "--checkout", str(self.repo),
                           "--output", str(output)])
        self.assertEqual(status, 0)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(json.loads(printed.getvalue()), json.loads(output.read_text()))

    def test_cli_refuses_overwriting_source_evidence(self):
        path = self.root / "audit.json"
        path.write_text(json.dumps(self.audit), encoding="utf-8")
        with self.assertRaises(SystemExit) as error:
            main(["--audit", str(path), "--checkout", str(self.repo),
                  "--output", str(path)])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
