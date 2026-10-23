"""Blinded reviewer handoff must be source-bound, exclusive and non-authoritative."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_blind_review import (
    STAGED_RELATIVE, SOURCE_TASK_ROOT, PARTICIPANT_SOURCE,
    PARTICIPANTS, TASKS,
)
from benchmarks.dogfood.hashmarks_reviewer_kit import (
    ReviewerKitError, prepare_reviewer_kit,
)

ROOT = Path(__file__).resolve().parents[2]


class PinnedReviewerKitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.evalshot = self.workspace / "EvalShot"
        self.stage = self.evalshot / STAGED_RELATIVE
        shutil.copytree(ROOT / STAGED_RELATIVE, self.stage)
        for task_id in TASKS:
            self._copy(SOURCE_TASK_ROOT / f"{task_id}.json")
        for relative in PARTICIPANTS:
            self._copy(PARTICIPANT_SOURCE / relative)
        self.hashmarks = self.workspace / "Hashmarks"
        self.hashmarks.mkdir()
        self.git("init", "-q")
        (self.hashmarks / "hashmarks").mkdir()
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n    def lookup(self):\n        return 7\n", encoding="utf-8"
        )
        (self.hashmarks / "README.md").write_text(
            "Pinned source only, not an oracle review.\n", encoding="utf-8"
        )
        (self.hashmarks / "nested").mkdir()
        (self.hashmarks / "nested/data.json").write_text(
            '{"sample":true}\n', encoding="utf-8"
        )
        self.freeze("initial")
        self.update_source_pin()
        self.out = self.workspace / "handoff"

    def _copy(self, relative):
        path = self.evalshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.hashmarks), *args],
            check=True, capture_output=True, text=True
        ).stdout.strip()

    def freeze(self, message):
        self.git("add", "-A")
        self.git("-c", "user.email=case@example.invalid", "-c",
                 "user.name=Case", "commit", "-qm", message)
        self.commit = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")

    def update_source_pin(self):
        # Synthetic pin mutation is confined to the temporary test repository.
        # Do not rewrite the production frozen task definitions or the PR's
        # staged baseline.
        protocol_file = self.stage / "protocol.json"
        protocol = json.loads(protocol_file.read_text())
        protocol["source"]["commit"] = self.commit
        protocol["source"]["tree"] = self.tree
        for task_id in TASKS:
            for relative in (
                SOURCE_TASK_ROOT / f"{task_id}.json",
                STAGED_RELATIVE / "tasks" / f"{task_id}.json",
            ):
                path = self.evalshot / relative
                task = json.loads(path.read_text())
                task["repository"]["commit"] = self.commit
                task["repository"]["tree"] = self.tree
                raw = (json.dumps(task, indent=2) + "\n").encode()
                path.write_bytes(raw)
            protocol["task_transfers"][task_id]["git_blob_sha1"] = subprocess.run(
                ["git", "hash-object", "--stdin"], input=raw,
                check=True, capture_output=True,
            ).stdout.decode("ascii").strip()
        protocol_file.write_text(json.dumps(protocol, indent=2) + "\n")

    def build(self, destination=None):
        return prepare_reviewer_kit(
            repository_root=self.evalshot,
            checkout=self.hashmarks,
            output=destination or self.out,
        )

    def test_publishes_only_questions_source_and_content_manifest(self):
        result = self.build()
        self.assertEqual(result["source_files"], 3)
        self.assertEqual(result["questions"], 2)
        self.assertFalse(result["owner_oracle_approved"])
        self.assertFalse(result["reviewer_independence_attested"])
        self.assertEqual(result["native_trials_executed"], 0)
        self.assertEqual(
            {p.name for p in self.out.iterdir()},
            {"questions.json", "source", "manifest.json"},
        )
        self.assertFalse((self.out / ".git").exists())
        self.assertFalse((self.out / "source/.git").exists())
        self.assertFalse((self.out / "source/benchmarks").exists())
        packet = json.loads((self.out / "questions.json").read_text())
        self.assertEqual(packet["qualification"], "SOURCE_BLINDED_QUESTION_ONLY")
        self.assertEqual(len(packet["packets"]), 2)
        self.assertFalse(packet["experiment_admitted"])
        self.assertFalse(packet["independent_oracles_approved"])
        for task_id in TASKS:
            task = json.loads((self.stage / "tasks" / f"{task_id}.json").read_text())
            expected = task["oracle"]["configuration"]["expected"]["path"]
            self.assertNotIn(expected, (self.out / "questions.json").read_text())

    def test_tracked_dotfile_is_preserved_and_accounted_for(self):
        (self.hashmarks / ".gitattributes").write_text(
            "# pinned Git attribute source must survive artifact packaging\\n",
            encoding="utf-8",
        )
        self.freeze("tracked dotfile")
        self.update_source_pin()
        report = self.build()
        self.assertEqual(report["source_files"], 4)
        self.assertTrue((self.out / "source/.gitattributes").is_file())
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertIn(".gitattributes", manifest["source_sha256_by_path"])
        self.assertNotIn(".git", manifest["source_sha256_by_path"])

    def test_manifest_file_digests_bind_exported_bytes(self):
        self.build()
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(manifest["pinned_commit"], self.commit)
        self.assertEqual(manifest["pinned_tree"], self.tree)
        self.assertEqual(manifest["source_files"], 3)
        self.assertFalse(manifest["reviewer_independence_attested"])
        self.assertFalse(manifest["canonical_oracle_approved"])
        self.assertFalse(manifest["native_trial_executed"])
        for path, digest in manifest["source_sha256_by_path"].items():
            data = (self.out / "source" / path).read_bytes()
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertEqual(data, (self.hashmarks / path).read_bytes())
        self.assertEqual(
            manifest["questions_sha256"],
            hashlib.sha256((self.out / "questions.json").read_bytes()).hexdigest(),
        )

    def test_worktree_modified_and_untracked_files_do_not_enter_kit(self):
        (self.hashmarks / "hashmarks/store.py").write_text("def changed(): pass\n")
        (self.hashmarks / "secrets.txt").write_text("UNTRACKED-SECRET")
        self.build()
        self.assertIn(
            "def lookup", (self.out / "source/hashmarks/store.py").read_text()
        )
        self.assertFalse((self.out / "source/secrets.txt").exists())
        self.assertNotIn(
            "UNTRACKED-SECRET", (self.out / "manifest.json").read_text()
        )

    def test_unapproved_review_gate_remains_blocked_after_export(self):
        from benchmarks.harness.suite import SuiteError, load_suite
        self.build()
        with self.assertRaisesRegex(SuiteError, "oracle review evidence is missing"):
            load_suite(self.stage)
        self.assertFalse((self.stage / "qualification/oracle-reviews.json").exists())

    def test_refuses_missing_or_conflicting_historical_source_commit(self):
        original = self.commit
        self.commit = "f" * 40
        self.update_source_pin()
        with self.assertRaisesRegex(ReviewerKitError, "pinned historical"):
            self.build()
        self.assertFalse(self.out.exists())
        self.assertEqual(original, self.git("rev-parse", "HEAD"))

    def test_refuses_correct_commit_with_wrong_tree(self):
        protocol_file = self.stage / "protocol.json"
        proto = json.loads(protocol_file.read_text())
        proto["source"]["tree"] = "f" * 40
        protocol_file.write_text(json.dumps(proto))
        for task_id in TASKS:
            for relative in (
                SOURCE_TASK_ROOT / f"{task_id}.json",
                STAGED_RELATIVE / "tasks" / f"{task_id}.json",
            ):
                path = self.evalshot / relative
                data = json.loads(path.read_text())
                data["repository"]["tree"] = "f" * 40
                raw = (json.dumps(data, indent=2) + "\n").encode()
                path.write_bytes(raw)
            proto["task_transfers"][task_id]["git_blob_sha1"] = subprocess.run(
                ["git", "hash-object", "--stdin"], input=raw,
                capture_output=True, check=True,
            ).stdout.decode("ascii").strip()
        protocol_file.write_text(json.dumps(proto))
        with self.assertRaisesRegex(ReviewerKitError, "commit/tree"):
            self.build()
        self.assertFalse(self.out.exists())

    def test_refuses_git_symlink_in_historical_tree(self):
        (self.hashmarks / "hashmarks/shortcut.py").symlink_to("store.py")
        self.freeze("symlink")
        self.update_source_pin()
        with self.assertRaisesRegex(ReviewerKitError, "unsupported or unsafe"):
            self.build()
        self.assertFalse(self.out.exists())

    def test_refuses_git_export_ignore_incomplete_snapshot(self):
        (self.hashmarks / ".gitattributes").write_text(
            "nested/data.json export-ignore\n", encoding="utf-8"
        )
        self.freeze("export-ignored")
        self.update_source_pin()
        with self.assertRaisesRegex(ReviewerKitError, "omitted tracked"):
            self.build()
        self.assertFalse(self.out.exists())

    def test_refuses_git_export_subst_modified_source_bytes(self):
        (self.hashmarks / "tracked.txt").write_text(
            "$Format:%H$\n", encoding="utf-8"
        )
        (self.hashmarks / ".gitattributes").write_text(
            "tracked.txt export-subst\n", encoding="utf-8"
        )
        self.freeze("export substitution")
        self.update_source_pin()
        with self.assertRaisesRegex(ReviewerKitError, "differs from frozen Git blob"):
            self.build()
        self.assertFalse(self.out.exists())

    def test_refuses_destination_inside_evalshot(self):
        with self.assertRaisesRegex(ReviewerKitError, "external destination"):
            self.build(self.evalshot / "dangerous-review")
        self.assertFalse((self.evalshot / "dangerous-review").exists())

    def test_refuses_destination_inside_hashmarks(self):
        with self.assertRaisesRegex(ReviewerKitError, "external destination"):
            self.build(self.hashmarks / "review")
        self.assertFalse((self.hashmarks / "review").exists())

    def test_refuses_parent_destination_covering_existing_repository(self):
        with self.assertRaisesRegex(ReviewerKitError, "external destination"):
            self.build(self.workspace)
        self.assertTrue(self.evalshot.exists())

    def test_refuses_existing_destination_without_overwrite(self):
        self.out.mkdir()
        (self.out / "private-note.txt").write_text("preserve")
        with self.assertRaisesRegex(ReviewerKitError, "external destination"):
            self.build()
        self.assertEqual((self.out / "private-note.txt").read_text(), "preserve")

    def test_refuses_changed_staged_oracle_or_prompt(self):
        task_file = self.stage / "tasks" / f"{TASKS[0]}.json"
        task_file.write_text(task_file.read_text() + "\n")
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.out.exists())

    def test_rejects_pending_canonical_oracle_review_file(self):
        file = self.stage / "qualification/oracle-reviews.json"
        file.parent.mkdir()
        file.write_text('{"schema":"agents-cookbook-oracle-reviews.v1","tasks":{}}')
        with self.assertRaises(ValueError):
            self.build()

    def test_rejects_non_git_source_checkout(self):
        empty = self.workspace / "not-git"
        empty.mkdir()
        with self.assertRaises(ReviewerKitError):
            prepare_reviewer_kit(
                repository_root=self.evalshot,
                checkout=empty,
                output=self.out,
            )

    def test_does_not_mutate_staged_or_historical_inputs(self):
        before = {
            p.relative_to(self.evalshot): p.read_bytes()
            for p in self.evalshot.rglob("*.json")
        }
        self.build()
        self.assertEqual(before, {
            p.relative_to(self.evalshot): p.read_bytes()
            for p in self.evalshot.rglob("*.json")
        })


if __name__ == "__main__":
    unittest.main()
