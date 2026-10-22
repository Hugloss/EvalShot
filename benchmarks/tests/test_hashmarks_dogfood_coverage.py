"""Model-free coverage triage: never invent a new oracle or qualified outcome."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from benchmarks.dogfood.hashmarks_coverage import CoverageError, build_coverage, main


def row(
    suite: str, task: str, family: str, membership: str, *,
    source: str = "VERIFIED_PATH", problems: list[str] | None = None,
    commit: str = "a" * 40,
    semantic: str | None = "c" * 64,
) -> dict[str, object]:
    return {
        "suite": suite,
        "task": task,
        "family": family,
        "mode": "read_only",
        "commit": commit,
        "task_sha256": "b" * 64,
        "task_semantic_digest": semantic,
        "experiment_membership": membership,
        "source": source,
        "review": "MATCH",
        "oracle_path": "src/owner.py",
        "problems": list(problems or []),
    }


def sample() -> dict[str, object]:
    tasks = [
        row("heldout", "locate-owner", "python-localization", "ASSIGNED"),
        row("pilot", "locate-owner", "python-localization", "ASSIGNED"),
        row("heldout", "known-symbol", "known-symbol-localization", "UNASSIGNED",
            source="ORACLE_PATH_MISSING", problems=["ORACLE_PATH_MISSING"]),
        row("legacy", "find-related", "known-symbol-localization", "UNASSIGNED",
            source="VERIFIED_PATH"),
        row("experimental", "unknown-source", "other", "UNKNOWN", source="NOT_CHECKED"),
    ]
    return {
        "schema": "evalshot.hashmarks-dogfood-audit.v1",
        "repository": "github.com/hugloss/hashmarks",
        "qualification": "MODEL_FREE_ONLY",
        "summary": {"tasks": len(tasks)},
        "requirements": {"audit_passed": True},
        "tasks": tasks,
    }


class HashmarksCoverageTests(unittest.TestCase):
    def test_counts_actual_frozen_entries_and_distinct_assigned_tasks(self):
        result = build_coverage(sample())
        self.assertEqual(result["qualification"], "DESCRIPTIVE_ONLY")
        self.assertEqual(result["summary"]["task_entries"], 5)
        self.assertEqual(result["summary"]["observed_families"], 3)
        family = next(x for x in result["family_matrix"] if x["family"] == "python-localization")
        self.assertEqual(family["task_entries"], 2)
        self.assertEqual(family["assigned_entries"], 2)
        self.assertEqual(family["distinct_assigned_task_identities"], 1)

    def test_nomination_does_not_create_an_oracle_or_benchmark(self):
        result = build_coverage(sample())
        kinds = [x["kind"] for x in result["candidate_review_queue"]]
        self.assertEqual(kinds.count("DORMANT_TASK_NEEDS_REVIEW"), 1)
        self.assertEqual(kinds.count("UNASSIGNED_CASE_FOR_TRIAGE"), 1)
        self.assertEqual(kinds.count("FAMILY_WITHOUT_ASSIGNED_TASK"), 1)
        self.assertEqual(kinds.count("SINGLE_ASSIGNED_TASK_FAMILY"), 1)
        for candidate in result["candidate_review_queue"]:
            self.assertEqual(candidate["oracle_status"], "NOT_ESTABLISHED")
            self.assertEqual(candidate["review_status"], "PROPOSAL_ONLY")
            self.assertNotIn("expected_owner", candidate)
            self.assertNotIn("score", candidate)

    def test_dormant_mismatch_is_never_reclassified_as_candidate_source_proof(self):
        result = build_coverage(sample())
        mismatch = next(c for c in result["candidate_review_queue"]
                        if c["kind"] == "DORMANT_TASK_NEEDS_REVIEW")
        self.assertEqual(mismatch["task"], "known-symbol")
        self.assertIn("ORACLE_PATH_MISSING", mismatch["basis"])
        self.assertIn("not rewrite historical", mismatch["next_action"])

    def test_never_treats_unknown_membership_as_unassigned(self):
        result = build_coverage(sample())
        unknown = next(x for x in result["family_matrix"] if x["family"] == "other")
        self.assertEqual(unknown["unknown_membership_entries"], 1)
        self.assertEqual(unknown["unassigned_entries"], 0)
        self.assertFalse(any(c["family"] == "other" for c in result["candidate_review_queue"]))

    def test_unknown_source_is_not_claimed_verified(self):
        document = sample()
        document["tasks"][3]["source"] = "NOT_CHECKED"
        result = build_coverage(document)
        triage = next(c for c in result["candidate_review_queue"]
                      if c["kind"] == "UNASSIGNED_CASE_FOR_TRIAGE")
        self.assertEqual(triage["basis"], "source identity has not been established")

    def test_deterministic_and_does_not_mutate_original_audit(self):
        data = sample()
        before = deepcopy(data)
        first = build_coverage(data)
        second = build_coverage(data)
        self.assertEqual(first, second)
        self.assertEqual(data, before)
        self.assertEqual(len({x["id"] for x in first["candidate_review_queue"]}),
                         first["summary"]["candidate_review_items"])

    def test_dormant_copy_of_assigned_case_is_not_new_trial_candidate(self):
        document = sample()
        document["tasks"].append(
            row("legacy", "locate-owner", "python-localization", "UNASSIGNED")
        )
        document["summary"]["tasks"] += 1
        result = build_coverage(document)
        self.assertEqual(result["summary"]["unassigned_entries"], 3)
        self.assertFalse(any(
            item["kind"] == "UNASSIGNED_CASE_FOR_TRIAGE"
            and item["task"] == "locate-owner"
            for item in result["candidate_review_queue"]
        ))
        self.assertEqual(result["summary"]["cross_suite_semantic_variants"], 0)

    def test_cross_suite_changed_task_meaning_requires_review_not_oracle(self):
        document = sample()
        document["tasks"].append(row(
            "legacy", "locate-owner", "python-localization",
            "UNASSIGNED", semantic="d" * 64,
        ))
        document["summary"]["tasks"] += 1
        result = build_coverage(document)
        variants = [
            item for item in result["candidate_review_queue"]
            if item["kind"] == "CROSS_SUITE_SEMANTIC_VARIANT"
        ]
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0]["task"], "locate-owner")
        self.assertEqual(variants[0]["oracle_status"], "NOT_ESTABLISHED")
        self.assertEqual(result["summary"]["cross_suite_semantic_variants"], 1)
        self.assertFalse(any(
            item["kind"] == "SINGLE_ASSIGNED_TASK_FAMILY"
            and item["family"] == "python-localization"
            for item in result["candidate_review_queue"]
        ))
        self.assertFalse(any(
            item["kind"] == "UNASSIGNED_CASE_FOR_TRIAGE"
            and item["task"] == "locate-owner"
            for item in result["candidate_review_queue"]
        ))

    def test_byte_only_drift_does_not_claim_changed_task_meaning(self):
        document = sample()
        extra = row("legacy", "locate-owner", "python-localization", "UNASSIGNED")
        extra["task_sha256"] = "f" * 64
        document["tasks"].append(extra)
        document["summary"]["tasks"] += 1
        result = build_coverage(document)
        self.assertEqual(result["summary"]["cross_suite_semantic_variants"], 0)
        self.assertFalse(any(
            item["kind"] == "CROSS_SUITE_SEMANTIC_VARIANT"
            for item in result["candidate_review_queue"]
        ))

    def test_old_audit_without_semantic_digest_remains_explicitly_unknown(self):
        document = sample()
        for item in document["tasks"]:
            item.pop("task_semantic_digest")
        result = build_coverage(document)
        self.assertEqual(result["summary"]["semantic_identity_unavailable_entries"], 5)
        self.assertEqual(result["summary"]["cross_suite_semantic_variants"], 0)

    def test_rejects_invalid_canonical_task_digest(self):
        document = sample()
        document["tasks"][0]["task_semantic_digest"] = "unknown"
        with self.assertRaisesRegex(CoverageError, "canonical task digest"):
            build_coverage(document)
        document["tasks"][0]["task_semantic_digest"] = "g" * 64
        with self.assertRaisesRegex(CoverageError, "canonical task digest"):
            build_coverage(document)

    def test_rejects_duplicate_inventory_identity(self):
        data = sample()
        data["tasks"].append(deepcopy(data["tasks"][0]))
        data["summary"]["tasks"] += 1
        with self.assertRaisesRegex(CoverageError, "duplicate suite/task"):
            build_coverage(data)

    def test_rejects_wrong_source_schema_and_repository(self):
        data = sample()
        data["schema"] = "unexpected"
        with self.assertRaisesRegex(CoverageError, "schema"):
            build_coverage(data)
        data = sample()
        data["repository"] = "github.com/attacker/repo"
        with self.assertRaisesRegex(CoverageError, "Hashmarks"):
            build_coverage(data)

    def test_rejects_count_mismatch_and_unknown_membership(self):
        data = sample()
        data["summary"]["tasks"] = 3
        with self.assertRaisesRegex(CoverageError, "task count"):
            build_coverage(data)
        data = sample()
        data["tasks"][0]["experiment_membership"] = "MAYBE_ASSIGNED"
        with self.assertRaisesRegex(CoverageError, "unknown experiment membership"):
            build_coverage(data)

    def test_rejects_invalid_problem_and_oracle_path_shape(self):
        data = sample()
        data["tasks"][0]["problems"] = [None]
        with self.assertRaisesRegex(CoverageError, "problem list"):
            build_coverage(data)
        data = sample()
        data["tasks"][0]["oracle_path"] = {"path": "secret"}
        with self.assertRaisesRegex(CoverageError, "oracle path"):
            build_coverage(data)

    def test_cli_preserves_source_audit_and_writes_derived_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            audit = Path(temporary) / "input.json"
            output = Path(temporary) / "coverage.json"
            original = json.dumps(sample(), sort_keys=True)
            audit.write_text(original, encoding="utf-8")
            with redirect_stdout(StringIO()) as stdout:
                code = main(["--audit", str(audit), "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(audit.read_text(encoding="utf-8"), original)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")),
                             json.loads(stdout.getvalue()))

    def test_cli_rejects_output_overwriting_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            audit = Path(temporary) / "input.json"
            audit.write_text(json.dumps(sample()), encoding="utf-8")
            with redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    main(["--audit", str(audit), "--output", str(audit)])
            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(json.loads(audit.read_text(encoding="utf-8"))["schema"],
                             "evalshot.hashmarks-dogfood-audit.v1")

    def test_invalid_source_audit_never_creates_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            audit = Path(temporary) / "input.json"
            output = Path(temporary) / "coverage.json"
            audit.write_text('{"schema": "invalid"}', encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["--audit", str(audit), "--output", str(output)])
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
