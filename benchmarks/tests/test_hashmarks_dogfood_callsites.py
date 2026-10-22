"""Independent static call evidence must not become an inferred call graph."""

from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_callsites import main, observe_callsites


class CallsiteDogfoodTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        (self.repo / "src").mkdir()
        (self.repo / "src/owner.py").write_text(
            "class Owner:\n"
            "    def paths_under(self): return []\n"
            "    def caller(self):\n"
            "        return self.paths_under()\n"
            "    def another(self):\n"
            "        paths_under()  # name could resolve differently\n",
            encoding="utf-8",
        )
        (self.repo / "src/other.py").write_text(
            "def invoke(other):\n"
            "    other.paths_under()\n"
            "    getattr(other, 'paths_under')()  # not an exact AST callee\n"
            "    text = 'other.paths_under()'\n"
            "    # other.paths_under()\n",
            encoding="utf-8",
        )
        self._commit("frozen")
        self.audit = {
            "schema": "evalshot.hashmarks-dogfood-audit.v1",
            "repository": "github.com/hugloss/hashmarks",
            "qualification": "MODEL_FREE_ONLY",
            "summary": {"tasks": 3},
            "requirements": {"audit_passed": True},
            "tasks": [
                self._row("candidate", "paths_under", "src/owner.py"),
                self._row("blocked", "paths_under", "src/missing.py",
                          source="ORACLE_PATH_MISSING", errors=["ORACLE_PATH_MISSING"]),
                self._row("assigned", "paths_under", "src/owner.py",
                          membership="ASSIGNED"),
            ],
        }

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def _commit(self, note):
        self._git("add", "-A")
        self._git("-c", "user.email=test@example.invalid",
                  "-c", "user.name=Test", "commit", "-qm", note)
        self.commit = self._git("rev-parse", "HEAD")
        self.tree = self._git("rev-parse", "HEAD^{tree}")

    def _row(self, name, symbol, path, *,
             source="VERIFIED_PATH", errors=None, membership="UNASSIGNED"):
        return {
            "suite": "heldout-v1", "task": name, "family": "known-symbol",
            "mode": "read_only", "commit": self.commit, "tree": self.tree,
            "task_sha256": "a" * 64, "task_semantic_digest": "b" * 64,
            "experiment_membership": membership,
            "source": source, "problems": list(errors or []),
            "review": "NO_TASK_REVIEW",
            "oracle_path": path, "oracle_symbol": symbol,
        }

    @staticmethod
    def _candidate(result):
        return next(x for x in result["observations"] if x["task"] == "candidate")

    def test_records_exact_syntactic_calls_but_no_target_authority(self):
        report = observe_callsites(self.audit, checkout=self.repo)
        candidate = self._candidate(report)
        self.assertEqual(candidate["status"], "STATIC_CALL_EXPRESSIONS_OBSERVED")
        self.assertEqual(candidate["call_site_count"], 3)
        self.assertEqual(
            {(x["form"], x["receiver_syntax"]) for x in candidate["same_spelling_call_sites"]},
            {("ATTRIBUTE_CALL", "self"), ("ATTRIBUTE_CALL", "OTHER_NAMED_RECEIVER"),
             ("NAME_CALL", None)},
        )
        self.assertFalse(candidate["runtime_target_resolved"])
        self.assertFalse(candidate["semantic_owner_proven"])
        self.assertFalse(candidate["oracle_approved"])
        self.assertFalse(candidate["baseline_headroom_established"])
        self.assertEqual(report["summary"]["semantic_owners_proven"], 0)
        self.assertEqual(report["summary"]["approved_oracles"], 0)

    def test_has_correct_lexical_scope_but_not_inferred_call_graph(self):
        candidate = self._candidate(observe_callsites(self.audit, checkout=self.repo))
        scopes = {x["lexical_scope"] for x in candidate["same_spelling_call_sites"]}
        self.assertEqual(scopes, {"Owner.caller", "Owner.another", "invoke"})
        self.assertTrue(all(x["target_resolved"] is False
                            for x in candidate["same_spelling_call_sites"]))

    def test_comments_strings_getattr_are_not_resolved_calls(self):
        candidate = self._candidate(observe_callsites(self.audit, checkout=self.repo))
        self.assertEqual(candidate["call_site_count"], 3)
        self.assertFalse(any(x["lexical_scope"] == "<module>"
                             for x in candidate["same_spelling_call_sites"]))

    def test_failed_precondition_stays_failed_even_with_matching_calls(self):
        report = observe_callsites(self.audit, checkout=self.repo)
        row = next(x for x in report["observations"] if x["task"] == "blocked")
        self.assertEqual(row["status"], "SOURCE_PRECONDITION_FAILED")
        self.assertEqual(row["call_site_count"], 0)
        self.assertFalse(row["scan_complete"])
        self.assertEqual(report["summary"]["complete_candidate_scans"], 1)
        self.assertEqual(report["summary"]["unassigned_candidates"], 2)

    def test_no_static_calls_does_not_prove_dead_code(self):
        self.audit["tasks"][0]["oracle_symbol"] = "unused"
        row = self._candidate(observe_callsites(self.audit, checkout=self.repo))
        self.assertEqual(row["status"], "NO_STATIC_CALL_EXPRESSIONS_OBSERVED")
        self.assertTrue(row["scan_complete"])
        self.assertEqual(row["call_site_count"], 0)
        self.assertFalse(row["semantic_owner_proven"])

    def test_unparseable_source_does_not_pretend_scan_complete(self):
        (self.repo / "src/broken.py").write_text("def invalid( :\n")
        self._commit("bad parse")
        for row in self.audit["tasks"]:
            row["commit"], row["tree"] = self.commit, self.tree
        result = observe_callsites(self.audit, checkout=self.repo)
        row = self._candidate(result)
        self.assertEqual(row["status"], "SCAN_INCOMPLETE")
        self.assertFalse(row["scan_complete"])
        self.assertEqual(row["call_site_count"], 3)
        self.assertIn({"path": "src/broken.py", "reason": "UNPARSEABLE_PYTHON"},
                      result["commit_scans"][self.commit]["skipped"])

    def test_tracked_symlink_makes_negative_evidence_incomplete(self):
        (self.repo / "src/alias.py").symlink_to("other.py")
        self._commit("symlink")
        for row in self.audit["tasks"]:
            row["commit"], row["tree"] = self.commit, self.tree
        report = observe_callsites(self.audit, checkout=self.repo)
        self.assertEqual(self._candidate(report)["status"], "SCAN_INCOMPLETE")

    def test_conflicting_tree_identities_rejected_early(self):
        second = self._row("another-eligible", "paths_under", "src/owner.py")
        second["tree"] = "f" * 40
        self.audit["tasks"].append(second)
        self.audit["summary"]["tasks"] += 1
        with self.assertRaisesRegex(ValueError, "conflicting trees"):
            observe_callsites(self.audit, checkout=self.repo)

    def test_incorrect_consistent_tree_fails_closed(self):
        for row in self.audit["tasks"]:
            row["tree"] = "f" * 40
        report = observe_callsites(self.audit, checkout=self.repo)
        self.assertEqual(self._candidate(report)["status"], "SOURCE_IDENTITY_MISMATCH")

    def test_uncommitted_mutation_cannot_change_pinned_callsites(self):
        original = observe_callsites(self.audit, checkout=self.repo)
        (self.repo / "src/other.py").write_text("def changed(): paths_under()\n")
        self.assertEqual(original, observe_callsites(self.audit, checkout=self.repo))

    def test_assigned_candidates_are_not_automatically_scanned(self):
        report = observe_callsites(self.audit, checkout=self.repo)
        self.assertFalse(any(x["task"] == "assigned" for x in report["observations"]))

    def test_other_language_and_invalid_name_stay_explicit(self):
        self.audit["tasks"][0]["oracle_path"] = "src/index.ts"
        self.assertEqual(self._candidate(observe_callsites(self.audit, checkout=self.repo))["status"],
                         "NOT_PYTHON_SOURCE_CANDIDATE")
        self.audit["tasks"][0]["oracle_path"] = "src/owner.py"
        self.audit["tasks"][0]["oracle_symbol"] = "paths_under;unsafe"
        self.assertEqual(self._candidate(observe_callsites(self.audit, checkout=self.repo))["status"],
                         "INVALID_SYMBOL")

    def test_deterministic_nonmutating_no_host_exposure(self):
        before = deepcopy(self.audit)
        one = observe_callsites(self.audit, checkout=self.repo)
        self.assertEqual(one, observe_callsites(self.audit, checkout=self.repo))
        self.assertEqual(before, self.audit)
        self.assertEqual(one["qualification"], "STATIC_SYNTAX_ONLY")

    def test_invalid_repo_rejected_before_work(self):
        with self.assertRaisesRegex(ValueError, "Git working tree"):
            observe_callsites(self.audit, checkout=self.root)
        self.audit["repository"] = "github.com/unrelated/repo"
        with self.assertRaisesRegex(ValueError, "Hashmarks"):
            observe_callsites(self.audit, checkout=self.repo)

    def test_cli_writes_separate_report_and_rejects_input_overwrite(self):
        audit = self.root / "audit.json"
        out = self.root / "result.json"
        audit.write_text(json.dumps(self.audit))
        frozen = audit.read_bytes()
        with redirect_stdout(StringIO()) as printed:
            code = main(["--audit", str(audit), "--checkout", str(self.repo),
                         "--output", str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(audit.read_bytes(), frozen)
        self.assertEqual(json.loads(printed.getvalue()), json.loads(out.read_text()))
        with self.assertRaises(SystemExit) as err:
            main(["--audit", str(audit), "--checkout", str(self.repo),
                  "--output", str(audit)])
        self.assertEqual(err.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
