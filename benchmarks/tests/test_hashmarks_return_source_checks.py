"""Source-citation existence never qualifies untrusted blinded reviewer claims."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_return_source_checks import (
    main, verify_source_references,
)
from benchmarks.tests.test_hashmarks_review_returns import questions, review, responses


class FrozenReviewerCitationChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "Hashmarks"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "hashmarks").mkdir()
        (self.repo / "hashmarks/a.py").write_text(
            "def a():\n"
            "    return 3\n"
            "# static source content\n"
            "\n\n\n\n\n"
            "def private_helper(): pass\n", encoding="utf-8",
        )
        (self.repo / "hashmarks/b.py").write_text(
            "class Other:\n"
            "    def b(self):\n"
            "        return 2\n",
            encoding="utf-8",
        )
        self.freeze("source")
        self.packet = questions()
        for row in self.packet["packets"]:
            row["repository"]["commit"] = self.commit
            row["repository"]["tree"] = self.tree

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def freeze(self, message):
        self.git("add", "-A")
        self.git("-c", "user.email=case@example.invalid",
                 "-c", "user.name=Case", "commit", "-qm", message)
        self.commit = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")

    def assertion(self, *, decision="unique", index=0):
        data = review(self.packet, decision=decision, index=index)
        data["source_evidence"] = [{
            "path": "hashmarks/a.py", "line": 9,
            "observation": "checked this source line, meaning still unverified",
        }]
        return data

    def check(self, claims=None):
        return verify_source_references(
            self.packet,
            responses(self.packet, claims) if claims is not None else None,
            checkout=self.repo,
        )

    def status(self, report, index=0):
        return report["tasks"][index]["source_reference_status"]

    def test_real_git_commit_tree_and_lines_exist_without_oracle_approval(self):
        report = self.check([self.assertion()])
        self.assertEqual(
            self.status(report),
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertEqual(report["summary"]["pinned_references_exist"], 1)
        self.assertEqual(report["summary"]["approved_oracles"], 0)
        self.assertEqual(report["summary"]["verified_independent_reviewers"], 0)
        self.assertEqual(report["summary"]["measured_native_headroom"], 0)
        self.assertFalse(report["tasks"][0]["claim_content_verified"])
        self.assertFalse(report["tasks"][0]["semantic_owner_proven"])

    def test_no_private_returns_means_waiting_not_verified_review(self):
        result = self.check()
        self.assertEqual(result["summary"]["responses_received"], 0)
        self.assertEqual(result["summary"]["awaiting_first_pass"], 2)
        self.assertEqual(result["summary"]["pinned_references_exist"], 0)
        self.assertTrue(all(
            row["source_reference_status"] == "AWAITING_FIRST_PASS"
            for row in result["tasks"]
        ))

    def test_missing_git_candidate_file_remains_unavailable(self):
        claim = self.assertion()
        claim["candidate_locations"][0]["path"] = "hashmarks/missing.py"
        result = self.check([claim])
        self.assertEqual(self.status(result), "PINNED_CANDIDATE_FILE_UNAVAILABLE")

    def test_wrong_declared_symbol_cannot_impersonate_python_declaration(self):
        claim = self.assertion()
        claim["candidate_locations"][0]["symbol"] = "not_there"
        report = self.check([claim])
        self.assertEqual(self.status(report), "CANDIDATE_DECLARATION_NOT_OBSERVED")

    def test_missing_line_is_not_a_supported_source_observation(self):
        claim = self.assertion()
        claim["source_evidence"][0]["line"] = 10000
        report = self.check([claim])
        self.assertEqual(self.status(report), "PINNED_EVIDENCE_LINE_ABSENT")

    def test_missing_evidence_file_is_distinct_from_missing_line(self):
        claim = self.assertion()
        claim["source_evidence"][0]["path"] = "hashmarks/never.py"
        report = self.check([claim])
        self.assertEqual(self.status(report), "PINNED_EVIDENCE_FILE_UNAVAILABLE")

    def test_source_symlink_does_not_masquerade_as_regular_python(self):
        (self.repo / "hashmarks/link.py").symlink_to("a.py")
        self.freeze("symlink")
        for row in self.packet["packets"]:
            row["repository"]["commit"] = self.commit
            row["repository"]["tree"] = self.tree
        claim = self.assertion()
        claim["candidate_locations"][0]["path"] = "hashmarks/link.py"
        self.assertEqual(
            self.status(self.check([claim])),
            "PINNED_CANDIDATE_FILE_UNAVAILABLE",
        )

    def test_unparseable_python_cannot_supply_declaration(self):
        (self.repo / "hashmarks/bad.py").write_text("def a( :\n")
        self.freeze("unparseable")
        for row in self.packet["packets"]:
            row["repository"]["commit"] = self.commit
            row["repository"]["tree"] = self.tree
        claim = self.assertion()
        claim["candidate_locations"][0]["path"] = "hashmarks/bad.py"
        self.assertEqual(
            self.status(self.check([claim])),
            "CANDIDATE_PYTHON_NOT_PARSEABLE",
        )

    def test_ambiguity_with_two_pinned_declarations_remains_ambiguous(self):
        claim = self.assertion(decision="ambiguous")
        result = self.check([claim])
        self.assertEqual(result["summary"]["pinned_references_exist"], 1)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/b.py", json.dumps(result))

    def test_pinned_tree_mismatch_fails_without_private_source_claim(self):
        self.packet["packets"][0]["repository"]["tree"] = "0" * 40
        result = self.check([self.assertion()])
        self.assertEqual(
            self.status(result), "PINNED_SOURCE_IDENTITY_UNAVAILABLE",
        )
        # Both tasks claim one commit but inconsistent frozen trees; neither
        # may inherit a valid identity from the other.
        self.assertEqual(result["summary"]["unavailable_or_disputed_source"], 2)
        self.assertEqual(
            self.status(result, index=1), "PINNED_SOURCE_IDENTITY_UNAVAILABLE"
        )

    def test_conflicting_shared_tree_blocks_all_private_source_claims(self):
        self.packet["packets"][1]["repository"]["tree"] = "0" * 40
        first = self.assertion(index=0)
        second = self.assertion(index=1)
        result = self.check([first, second])
        self.assertEqual(
            [row["source_reference_status"] for row in result["tasks"]],
            ["PINNED_SOURCE_IDENTITY_UNAVAILABLE"] * 2,
        )
        self.assertEqual(result["summary"]["pinned_references_exist"], 0)
        self.assertEqual(result["summary"]["unavailable_or_disputed_source"], 2)
        self.assertEqual(result["summary"]["approved_oracles"], 0)

    def test_absent_frozen_commit_is_not_qualified(self):
        self.packet["packets"][0]["repository"]["commit"] = "f" * 40
        self.assertEqual(
            self.status(self.check([self.assertion()])),
            "PINNED_SOURCE_IDENTITY_UNAVAILABLE",
        )

    def test_worktree_edits_cannot_change_source_claim_status(self):
        before = self.check([self.assertion()])
        (self.repo / "hashmarks/a.py").write_text("def other(): pass\n")
        (self.repo / "hashmarks/new.py").write_text("def a(): pass\n")
        self.assertEqual(before, self.check([self.assertion()]))

    def test_source_answers_and_review_identity_never_appear_in_report(self):
        claim = self.assertion()
        claim["reviewer_id"] = "secret-private-reviewer"
        claim["reason"] = "secret rationale which must never be shown"
        claim["source_evidence"][0]["observation"] = "secret observation"
        result = self.check([claim])
        rendered = json.dumps(result)
        for secret in (
            "hashmarks/a.py", "secret-private-reviewer",
            "secret rationale", "secret observation",
        ):
            self.assertNotIn(secret, rendered)

    def test_unsupported_decision_or_stale_packet_is_rejected_before_git(self):
        wrong = self.assertion()
        wrong["source_commit"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.check([wrong])

    def test_non_git_checkout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Git working tree"):
            verify_source_references(self.packet, None, checkout=Path(self.temp.name))

    def test_cli_refuses_private_answers_stored_in_evalshot_checkout(self):
        # This is checked before attempting any reviewer or source material.
        root = Path(self.temp.name) / "EvalShot"
        root.mkdir()
        answers = root / "private-reviewer-answers.json"
        answers.write_text('{"schema":"invalid"}')
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            main([
                "--repository-root", str(root),
                "--checkout", str(self.repo),
                "--responses", str(answers),
            ])
        self.assertEqual(error.exception.code, 2)
        self.assertTrue(answers.exists())

    def test_cli_refuses_writing_source_status_under_evalshot_checkout(self):
        root = Path(self.temp.name) / "EvalShot"
        root.mkdir()
        target = root / "source-status.json"
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            main([
                "--repository-root", str(root),
                "--checkout", str(self.repo),
                "--output", str(target),
            ])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(target.exists())

    def test_determinism_and_source_input_non_mutation(self):
        incoming = responses(self.packet, [self.assertion()])
        saved = deepcopy((self.packet, incoming))
        first = verify_source_references(self.packet, incoming, checkout=self.repo)
        self.assertEqual(first, verify_source_references(
            self.packet, incoming, checkout=self.repo,
        ))
        self.assertEqual((self.packet, incoming), saved)


if __name__ == "__main__":
    unittest.main()
