"""Pinned-source declaration observations never approve benchmark oracle truth."""

from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_declarations import main, observe


class PinnedDeclarationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "owner.py").write_text(
            "class Owner:\n"
            "    def paths_under(self):\n"
            "        return 1\n"
            "    async def other(self):\n"
            "        return 2\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("-c", "user.email=case@example.invalid",
                  "-c", "user.name=Case", "commit", "-qm", "frozen")
        self.commit = self._git("rev-parse", "HEAD")
        self.audit = self._audit()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            text=True, capture_output=True,
        ).stdout.strip()

    def _row(self, task: str, symbol: str, path: str, *,
             state: str = "VERIFIED_PATH", errors=None, member="UNASSIGNED"):
        return {
            "suite": "historical", "task": task, "family": "known-symbol",
            "mode": "read_only", "commit": self.commit,
            "task_sha256": "a" * 64, "task_semantic_digest": "b" * 64,
            "experiment_membership": member, "source": state,
            "review": "NO_TASK_REVIEW", "oracle_path": path,
            "oracle_symbol": symbol, "problems": errors or [],
        }

    def _audit(self):
        rows = [
            self._row("known", "paths_under", "src/owner.py"),
            self._row("absent", "nonexistent", "src/owner.py"),
            self._row("blocked", "paths_under", "src/missing.py",
                      state="ORACLE_PATH_MISSING", errors=["ORACLE_PATH_MISSING"]),
            self._row("assigned", "paths_under", "src/owner.py", member="ASSIGNED"),
        ]
        return {
            "schema": "evalshot.hashmarks-dogfood-audit.v1",
            "repository": "github.com/hugloss/hashmarks",
            "qualification": "MODEL_FREE_ONLY", "summary": {"tasks": len(rows)},
            "requirements": {"audit_passed": True},
            "tasks": rows,
        }

    def test_known_declaration_is_static_observation_not_owner_proof(self):
        report = observe(self.audit, checkout=self.repo)
        known = next(x for x in report["observations"] if x["task"] == "known")
        self.assertEqual(known["status"], "DECLARATION_OBSERVED")
        self.assertEqual(known["declarations_in_candidate_file"][0]["qualname"],
                         "Owner.paths_under")
        self.assertEqual(known["declarations_in_candidate_file"][0]["line"], 2)
        self.assertFalse(known["unique_owner_proven"])
        self.assertFalse(known["oracle_approved"])
        self.assertFalse(known["headroom_established"])
        self.assertEqual(report["summary"]["unique_owners_proven"], 0)
        self.assertEqual(report["summary"]["oracles_approved"], 0)
        self.assertEqual(report["summary"]["headroom_established"], 0)

    def test_absence_and_precondition_failure_remain_distinct(self):
        report = observe(self.audit, checkout=self.repo)
        states = {x["task"]: x["status"] for x in report["observations"]}
        self.assertEqual(states["absent"], "DECLARATION_ABSENT")
        self.assertEqual(states["blocked"], "SOURCE_PRECONDITION_FAILED")
        self.assertNotIn("assigned", states)

    def test_comments_and_strings_do_not_count_as_declarations(self):
        (self.repo / "src" / "owner.py").write_text(
            "# def paths_under():\ntext = 'def paths_under():'\n",
            encoding="utf-8",
        )
        self._git("add", "src/owner.py")
        self._git("-c", "user.email=case@example.invalid",
                  "-c", "user.name=Case", "commit", "-qm", "comment-only")
        self.audit["tasks"][0]["commit"] = self._git("rev-parse", "HEAD")
        report = observe(self.audit, checkout=self.repo)
        self.assertEqual(report["observations"][0]["status"], "DECLARATION_ABSENT")

    def test_multiple_declarations_never_claim_unique_owner(self):
        (self.repo / "src" / "owner.py").write_text(
            "def paths_under(): pass\nclass A:\n    def paths_under(self): pass\n",
            encoding="utf-8",
        )
        self._git("add", "src/owner.py")
        self._git("-c", "user.email=case@example.invalid",
                  "-c", "user.name=Case", "commit", "-qm", "two-definitions")
        self.audit["tasks"][0]["commit"] = self._git("rev-parse", "HEAD")
        result = next(x for x in observe(self.audit, checkout=self.repo)["observations"]
                      if x["task"] == "known")
        self.assertEqual(result["status"], "MULTIPLE_DECLARATIONS")
        self.assertEqual({r["qualname"] for r in result["declarations_in_candidate_file"]},
                         {"paths_under", "A.paths_under"})
        self.assertFalse(result["unique_owner_proven"])

    def test_source_changes_after_frozen_commit_do_not_affect_observation(self):
        baseline = observe(self.audit, checkout=self.repo)
        (self.repo / "src" / "owner.py").write_text("def unrelated(): pass\n")
        changed = observe(self.audit, checkout=self.repo)
        self.assertEqual(baseline, changed)

    def test_unsupported_extension_and_invalid_symbol_are_explicit(self):
        report = deepcopy(self.audit)
        report["tasks"][0]["oracle_path"] = "src/example.ts"
        result = next(x for x in observe(report, checkout=self.repo)["observations"]
                      if x["task"] == "known")
        self.assertEqual(result["status"], "NOT_PYTHON_CANDIDATE")
        report["tasks"][0]["oracle_path"] = "src/owner.py"
        report["tasks"][0]["oracle_symbol"] = "paths_under; unsafe"
        result = next(x for x in observe(report, checkout=self.repo)["observations"]
                      if x["task"] == "known")
        self.assertEqual(result["status"], "INVALID_SYMBOL")

    def test_deterministic_does_not_mutate_audit(self):
        before = deepcopy(self.audit)
        one = observe(self.audit, checkout=self.repo)
        self.assertEqual(one, observe(self.audit, checkout=self.repo))
        self.assertEqual(self.audit, before)
        self.assertEqual(len(one["observations"]), 3)

    def test_legacy_missing_semantic_identity_stays_unknown(self):
        self.audit["tasks"][0].pop("task_semantic_digest")
        obs = next(x for x in observe(self.audit, checkout=self.repo)["observations"]
                   if x["task"] == "known")
        self.assertIsNone(obs["semantic_task_digest"])
        self.assertFalse(obs["oracle_approved"])

    def test_rejects_invalid_source_audit_and_checkout(self):
        tampered = deepcopy(self.audit)
        tampered["repository"] = "github.com/incorrect/repo"
        with self.assertRaisesRegex(ValueError, "Hashmarks"):
            observe(tampered, checkout=self.repo)
        with self.assertRaisesRegex(ValueError, "Git working tree"):
            observe(self.audit, checkout=self.root)

    def test_cli_keeps_audit_unchanged_and_emits_report(self):
        audit = self.root / "audit.json"
        output = self.root / "observation.json"
        audit.write_text(json.dumps(self.audit, sort_keys=True), encoding="utf-8")
        before = audit.read_bytes()
        with redirect_stdout(StringIO()) as visible:
            rc = main(["--audit", str(audit), "--checkout", str(self.repo),
                       "--output", str(output)])
        self.assertEqual(rc, 0)
        self.assertEqual(audit.read_bytes(), before)
        self.assertEqual(json.loads(output.read_text()), json.loads(visible.getvalue()))

    def test_cli_refuses_overwriting_input(self):
        audit = self.root / "audit.json"
        audit.write_text(json.dumps(self.audit))
        with self.assertRaises(SystemExit) as err:
            main(["--audit", str(audit), "--checkout", str(self.repo),
                  "--output", str(audit)])
        self.assertEqual(err.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
