"""Execute the generated read-only Harbor verifier against real Git worktrees."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Callable

from benchmarks.harbor_matrix import _verifier


EXPECTED = {"path": "src/owner.py", "symbol": "resolve"}


class HarborReadonlyVerifierTests(unittest.TestCase):
    def _run(
        self,
        change: Callable[[Path], None] | None = None,
        *,
        ignore_answer: bool = False,
    ) -> tuple[dict, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            logs = root / "logs" / "verifier"
            workspace.mkdir()
            source = workspace / "owner.txt"
            source.write_text("original\n", encoding="utf-8")
            (workspace / ".gitignore").write_text(
                "*.cache\n" + ("*.json\n" if ignore_answer else ""),
                encoding="utf-8",
            )
            for argv in (
                ["git", "init", "-q", str(workspace)],
                ["git", "-C", str(workspace), "add", "."],
                [
                    "git", "-C", str(workspace),
                    "-c", "user.name=Harbor Test",
                    "-c", "user.email=harbor@example.invalid",
                    "commit", "-qm", "baseline",
                ],
            ):
                subprocess.run(argv, check=True, capture_output=True, timeout=10)
            (workspace / ".agentscookbook-answer.json").write_text(
                json.dumps(EXPECTED), encoding="utf-8"
            )
            if change is not None:
                change(workspace)
            script = _verifier(EXPECTED)
            script = script.replace("/logs/verifier", str(logs))
            script = script.replace("/workspace", str(workspace))
            verifier = root / "verify.sh"
            verifier.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["sh", str(verifier)],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return (
                json.loads((logs / "answer.json").read_text(encoding="utf-8")),
                (logs / "reward.txt").read_text(encoding="utf-8"),
            )

    def test_exact_untracked_answer_transport_is_allowed(self) -> None:
        evidence, reward = self._run()
        self.assertEqual(reward, "1\n")
        self.assertTrue(evidence["tracked_clean"])
        self.assertTrue(evidence["match"])
        self.assertEqual(evidence["unexpected_change_count"], 0)
        self.assertIsNone(evidence["workspace_status_error"])

    def test_ignored_answer_transport_is_still_allowed(self) -> None:
        evidence, reward = self._run(ignore_answer=True)
        self.assertEqual(reward, "1\n")
        self.assertTrue(evidence["tracked_clean"])

    def test_extra_untracked_file_denies_reward(self) -> None:
        def add_extra(root: Path) -> None:
            (root / "scratch.txt").write_text("agent wrote this", encoding="utf-8")

        evidence, reward = self._run(add_extra)
        self.assertEqual(reward, "0\n")
        self.assertTrue(evidence["match"])
        self.assertFalse(evidence["tracked_clean"])
        self.assertIn("scratch.txt", evidence["unexpected_change_paths"])

    def test_ignored_extra_file_denies_reward(self) -> None:
        def add_ignored(root: Path) -> None:
            (root / "private.cache").write_text("generated", encoding="utf-8")

        evidence, reward = self._run(add_ignored)
        self.assertEqual(reward, "0\n")
        self.assertFalse(evidence["tracked_clean"])
        self.assertIn("private.cache", evidence["unexpected_change_paths"])

    def test_unstaged_tracked_edit_denies_reward(self) -> None:
        def edit(root: Path) -> None:
            (root / "owner.txt").write_text("changed", encoding="utf-8")

        evidence, reward = self._run(edit)
        self.assertEqual(reward, "0\n")
        self.assertFalse(evidence["tracked_clean"])
        self.assertIn("owner.txt", evidence["unexpected_change_paths"])

    def test_staged_tracked_edit_denies_reward(self) -> None:
        def stage(root: Path) -> None:
            (root / "owner.txt").write_text("changed", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "owner.txt"],
                check=True, timeout=10, capture_output=True,
            )

        evidence, reward = self._run(stage)
        self.assertEqual(reward, "0\n")
        self.assertFalse(evidence["tracked_clean"])

    def test_deleted_tracked_file_denies_reward(self) -> None:
        def delete(root: Path) -> None:
            (root / "owner.txt").unlink()

        evidence, reward = self._run(delete)
        self.assertEqual(reward, "0\n")
        self.assertFalse(evidence["tracked_clean"])

    def test_status_failure_does_not_claim_cleanliness(self) -> None:
        def remove_repository_authority(root: Path) -> None:
            import shutil

            shutil.rmtree(root / ".git")

        evidence, reward = self._run(remove_repository_authority)
        self.assertEqual(reward, "0\n")
        self.assertFalse(evidence["tracked_clean"])
        self.assertEqual(evidence["workspace_status_error"], "git-status-failed")

    def test_symlink_answer_cannot_be_used_to_pass_oracle(self) -> None:
        def replace_answer(root: Path) -> None:
            answer = root / ".agentscookbook-answer.json"
            other = root / "outside.json"
            other.write_text(json.dumps(EXPECTED), encoding="utf-8")
            answer.unlink()
            answer.symlink_to(other.name)

        evidence, reward = self._run(replace_answer)
        self.assertEqual(reward, "0\n")
        self.assertFalse(evidence["match"])
        self.assertEqual(evidence["error"], "answer-symlink")

    def test_oversized_answer_cannot_exhaust_verifier(self) -> None:
        def oversize(root: Path) -> None:
            (root / ".agentscookbook-answer.json").write_text(
                json.dumps({**EXPECTED, "extra": "x" * 65536}),
                encoding="utf-8",
            )

        evidence, reward = self._run(oversize)
        self.assertEqual(reward, "0\n")
        self.assertFalse(evidence["match"])
        self.assertEqual(evidence["error"], "answer-size-limit")


if __name__ == "__main__":
    unittest.main()
