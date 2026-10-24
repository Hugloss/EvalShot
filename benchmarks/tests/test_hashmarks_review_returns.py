"""External first-pass returns are claims, not verified independent oracle reviews."""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.dogfood.hashmarks_review_returns import (
    RETURNS_SCHEMA, ReturnError, _read_json, main, project_returns,
)
from benchmarks.dogfood.hashmarks_blind_review import (
    STAGED_RELATIVE, SOURCE_TASK_ROOT, PARTICIPANT_SOURCE, PARTICIPANTS,
    TASKS,
)
from benchmarks.harness.identity import digest
from benchmarks.harness.suite import SuiteError, load_suite

ROOT = Path(__file__).resolve().parents[2]


def questions():
    return {
        "schema": "evalshot.hashmarks-blind-semantic-review-packet.v1",
        "qualification": "SOURCE_BLINDED_QUESTION_ONLY",
        "experiment_admitted": False,
        "independent_oracles_approved": 0,
        "trial_executions": 0,
        "packets": [
            {
                "task_id": "lookup-known-symbol-paths-under",
                "packet_id": "sha256:" + "a" * 64,
                "task_digest": "b" * 64,
                "repository": {"commit": "c" * 40, "tree": "d" * 40},
            },
            {
                "task_id": "lookup-known-symbol-sync-remove-stale-paths",
                "packet_id": "sha256:" + "e" * 64,
                "task_digest": "f" * 64,
                "repository": {"commit": "c" * 40, "tree": "d" * 40},
            },
        ],
    }


def review(packet, *, index=0, decision="unique", prior=False, inspected=True):
    source = packet["packets"][index]
    locations = [
        {"path": "hashmarks/a.py", "symbol": "a"},
    ]
    if decision == "ambiguous":
        locations.append({"path": "hashmarks/b.py", "symbol": "b"})
    if decision == "unable_to_review":
        locations = []
        inspected = False
    return {
        "packet_id": source["packet_id"],
        "task_digest": source["task_digest"],
        "reviewer_id": "separate-reviewer",
        "source_commit": source["repository"]["commit"],
        "source_tree": source["repository"]["tree"],
        "decision": decision,
        "prior_expected_answer_access": prior,
        "source_inspected": inspected,
        "candidate_locations": locations,
        "reason": "I examined the pinned tree independently",
        "source_evidence": (
            [{"path": "hashmarks/a.py", "line": 9,
              "observation": "Found a syntactic declaration"}]
            if inspected else []
        ),
    }


def responses(packet, entries):
    return {
        "schema": RETURNS_SCHEMA,
        "blinded_packet_identity": "sha256:" + digest(packet),
        "reviews": entries,
    }


class ReviewReturnTests(unittest.TestCase):
    def test_empty_returns_never_approve_or_claim_independence(self):
        result = project_returns(questions(), None)
        self.assertEqual(result["qualification"], "UNTRUSTED_FIRST_PASS_CLAIM_STATUS_ONLY")
        self.assertEqual(result["summary"]["expected_questions"], 2)
        self.assertEqual(result["summary"]["responses_received"], 0)
        self.assertEqual(result["summary"]["awaiting_responses"], 2)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertFalse(result["source_review_returns_present"])
        self.assertEqual(result["next_owner"], "benchmarks.harness.oracle_reviews")

    def test_unique_claim_is_never_oracle_approval(self):
        packet = questions()
        item = review(packet)
        report = project_returns(packet, responses(packet, [item]))
        state = report["tasks"][0]
        self.assertEqual(state["status"], "UNVERIFIED_UNIQUE_CLAIM_RECORDED")
        self.assertEqual(state["exposure_status"], "CLAIMED_NO_PRIOR_ACCESS_NOT_VERIFIED")
        self.assertFalse(state["source_claim_authenticated"])
        self.assertFalse(state["reviewer_identity_verified"])
        self.assertFalse(state["semantic_truth_verified"])
        self.assertFalse(state["canonical_oracle_approved"])
        self.assertFalse(state["native_experiment_admitted"])
        self.assertEqual(report["summary"]["unverified_unique_claims"], 1)

    def test_return_does_not_echo_private_answers_rationale_or_reviewer(self):
        packet = questions()
        item = review(packet)
        item["candidate_locations"][0]["path"] = "secret/unique/path.py"
        item["reason"] = "secret rationale with expected owner"
        item["reviewer_id"] = "sensitive-reviewer-identity"
        item["source_evidence"][0]["observation"] = "sensitive evidence"
        result = project_returns(packet, responses(packet, [item]))
        encoded = json.dumps(result)
        for value in ("secret/unique/path.py", "secret rationale",
                      "sensitive-reviewer-identity", "sensitive evidence"):
            self.assertNotIn(value, encoded)

    def test_ambiguity_is_preserved_not_resolved_as_first_location(self):
        packet = questions()
        result = project_returns(packet, responses(
            packet, [review(packet, decision="ambiguous")]
        ))
        self.assertEqual(result["tasks"][0]["status"], "AMBIGUITY_REPORTED")
        self.assertEqual(result["summary"]["ambiguous_or_invalid_claims"], 1)

    def test_invalidity_is_reported_not_silently_repaired(self):
        packet = questions()
        result = project_returns(packet, responses(
            packet, [review(packet, decision="invalid")]
        ))
        self.assertEqual(result["tasks"][0]["status"], "INVALIDITY_REPORTED")

    def test_prior_answer_exposure_stays_explicit_even_for_unique(self):
        packet = questions()
        result = project_returns(packet, responses(
            packet, [review(packet, prior=True)]
        ))
        self.assertEqual(
            result["tasks"][0]["status"], "PRIOR_ANSWER_EXPOSURE_DECLARED"
        )
        self.assertEqual(
            result["tasks"][0]["exposure_status"], "DECLARED_PRIOR_ACCESS"
        )
        self.assertEqual(result["summary"]["approved_oracles"], 0)

    def test_unable_to_review_does_not_imply_semantic_failure(self):
        packet = questions()
        result = project_returns(packet, responses(
            packet, [review(packet, decision="unable_to_review")]
        ))
        self.assertEqual(result["tasks"][0]["status"], "UNABLE_TO_REVIEW")
        self.assertEqual(result["summary"]["approved_oracles"], 0)

    def test_rejects_stale_packet_identity(self):
        packet = questions()
        data = responses(packet, [review(packet)])
        data["blinded_packet_identity"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ReturnError, "exact blinded packet"):
            project_returns(packet, data)

    def test_rejects_cross_task_packet_digest_mismatch(self):
        packet = questions()
        data = responses(packet, [review(packet)])
        data["reviews"][0]["task_digest"] = packet["packets"][1]["task_digest"]
        with self.assertRaisesRegex(ReturnError, "identity"):
            project_returns(packet, data)

    def test_rejects_changed_source_commit_and_tree(self):
        packet = questions()
        for field in ("source_commit", "source_tree"):
            with self.subTest(field=field):
                data = responses(packet, [review(packet)])
                data["reviews"][0][field] = "1" * 40
                with self.assertRaisesRegex(ReturnError, "provenance"):
                    project_returns(packet, data)

    def test_duplicate_packet_response_is_rejected(self):
        packet = questions()
        data = responses(packet, [review(packet), review(packet)])
        with self.assertRaisesRegex(ReturnError, "identity"):
            project_returns(packet, data)

    def test_unknown_task_identity_is_rejected(self):
        packet = questions()
        data = responses(packet, [review(packet)])
        data["reviews"][0]["packet_id"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ReturnError, "identity"):
            project_returns(packet, data)

    def test_bad_candidate_path_and_symbol_are_rejected(self):
        packet = questions()
        for field, value in (("path", "../private.py"), ("symbol", "run();")):
            with self.subTest(field=field):
                data = responses(packet, [review(packet)])
                data["reviews"][0]["candidate_locations"][0][field] = value
                with self.assertRaisesRegex(ReturnError, "candidate source"):
                    project_returns(packet, data)

    def test_unique_claim_requires_one_location(self):
        packet = questions()
        data = responses(packet, [review(packet)])
        data["reviews"][0]["candidate_locations"].append(
            {"path": "hashmarks/b.py", "symbol": "b"}
        )
        with self.assertRaisesRegex(ReturnError, "one declared candidate"):
            project_returns(packet, data)

    def test_ambiguous_claim_requires_two_candidates(self):
        packet = questions()
        data = responses(packet, [review(packet, decision="ambiguous")])
        data["reviews"][0]["candidate_locations"].pop()
        with self.assertRaisesRegex(ReturnError, "at least two"):
            project_returns(packet, data)

    def test_semantic_claim_without_source_evidence_rejected(self):
        packet = questions()
        data = responses(packet, [review(packet)])
        data["reviews"][0]["source_evidence"] = []
        with self.assertRaisesRegex(ReturnError, "pinned-source inspection"):
            project_returns(packet, data)

    def test_unable_to_review_cannot_assert_inspection(self):
        packet = questions()
        data = responses(packet, [review(packet, decision="unable_to_review")])
        data["reviews"][0]["source_inspected"] = True
        with self.assertRaisesRegex(ReturnError, "contradicts"):
            project_returns(packet, data)

    def test_evidence_line_must_be_positive_integer(self):
        packet = questions()
        data = responses(packet, [review(packet)])
        data["reviews"][0]["source_evidence"][0]["line"] = 0
        with self.assertRaisesRegex(ReturnError, "malformed source evidence"):
            project_returns(packet, data)

    def test_unknown_review_fields_are_rejected(self):
        packet = questions()
        data = responses(packet, [review(packet)])
        data["reviews"][0]["canonical_oracle_approved"] = True
        with self.assertRaisesRegex(ReturnError, "envelope"):
            project_returns(packet, data)

    def test_unexpected_decision_is_rejected(self):
        packet = questions()
        data = responses(packet, [review(packet)])
        data["reviews"][0]["decision"] = "APPROVED"
        with self.assertRaisesRegex(ReturnError, "decision"):
            project_returns(packet, data)

    def test_two_separate_responses_are_still_unverified(self):
        packet = questions()
        data = responses(packet, [review(packet, index=0), review(packet, index=1)])
        report = project_returns(packet, data)
        self.assertEqual(report["summary"]["responses_received"], 2)
        self.assertEqual(report["summary"]["unverified_unique_claims"], 2)
        self.assertEqual(report["summary"]["approved_oracles"], 0)

    def test_non_string_decision_rejected_with_typed_error(self):
        packet = questions()
        data = responses(packet, [review(packet)])
        data["reviews"][0]["decision"] = ["unique"]
        with self.assertRaisesRegex(ReturnError, "decision"):
            project_returns(packet, data)

    def test_cli_rejects_answer_bearing_responses_inside_repository(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            answers = repo / "private-reviewer-answers.json"
            answers.write_text('{"schema":"invalid"}')
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                main([
                    "--repository-root", str(repo),
                    "--responses", str(answers),
                ])
            self.assertEqual(error.exception.code, 2)
            self.assertTrue(answers.exists())

    def test_cli_refuses_existing_sanitized_status_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "private-findings.json"
            output.write_text("sensitive original", encoding="utf-8")
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                main(["--repository-root", str(root), "--output", str(output)])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), "sensitive original")

    def test_cli_refuses_symlink_status_target_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "private-findings.json"
            private.write_text("sensitive original", encoding="utf-8")
            output = root / "status.json"
            output.symlink_to(private)
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                main(["--repository-root", str(root), "--output", str(output)])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(private.read_text(encoding="utf-8"), "sensitive original")

    def test_review_return_reader_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "private.json"
            p.write_text('{"schema": "a", "schema": "b"}')
            with self.assertRaisesRegex(ReturnError, "unreadable"):
                _read_json(p)

    def test_cli_default_is_safe_status_without_private_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / STAGED_RELATIVE
            shutil.copytree(ROOT / STAGED_RELATIVE, stage)
            for task_id in TASKS:
                target = root / SOURCE_TASK_ROOT / f"{task_id}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / SOURCE_TASK_ROOT / f"{task_id}.json", target)
            for name in PARTICIPANTS:
                path = root / PARTICIPANT_SOURCE / name
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / PARTICIPANT_SOURCE / name, path)
            output = root / "review-status.json"
            with redirect_stdout(StringIO()) as stdout:
                code = main(["--repository-root", str(root), "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue()), json.loads(output.read_text()))
            self.assertEqual(json.loads(output.read_text())["summary"]["awaiting_responses"], 2)
            self.assertFalse((stage / "qualification/oracle-reviews.json").exists())
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(["--repository-root", str(root),
                      "--output", str(stage / "review-status.json")])
            self.assertFalse((stage / "review-status.json").exists())

            # The output path can be created by another process after the
            # preflight check. Report the race as a controlled CLI failure.
            raced_output = root / "race-status.json"
            original_open = Path.open

            def race_on_exclusive_open(path, *args, **kwargs):
                if path == raced_output and args and args[0] == "x":
                    raise FileExistsError("status output already created")
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", race_on_exclusive_open):
                with redirect_stderr(StringIO()) as stderr:
                    with self.assertRaises(SystemExit) as error:
                        main(["--repository-root", str(root),
                              "--output", str(raced_output)])
            self.assertEqual(error.exception.code, 2)
            self.assertIn("status output already created", stderr.getvalue())
            self.assertFalse(raced_output.exists())


if __name__ == "__main__":
    unittest.main()
