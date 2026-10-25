"""Canonical oracle review JSON must not silently choose a duplicate key."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from benchmarks.harness.identity import digest
from benchmarks.harness.oracle_reviews import (
    OracleReviewError,
    oracle_review_guide,
    validate_oracle_reviews,
)


class OracleReviewJsonIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "qualification" / "oracle-reviews.json"
        self.path.parent.mkdir()
        self.task_id = "test-owner"
        self.task = {
            "repository": {
                "url": "https://example.invalid/repo",
                "commit": "a" * 40,
            },
            "oracle": {
                "adapter": "repository-location-json",
                "configuration": {
                    "expected": {"path": "src/owner.py", "symbol": "owner"},
                },
            },
        }
        self.suite = SimpleNamespace(
            root=self.root,
            experiment={
                "tasks": [self.task_id],
                "oracle_reviews": "qualification/oracle-reviews.json",
            },
            tasks={self.task_id: self.task},
        )

    def _valid_document(self) -> dict:
        row = {
            "task_digest": digest(self.task),
            "repository": self.task["repository"],
            "owner": self.task["oracle"]["configuration"]["expected"],
            "alternatives": [],
            "evidence": "Independent source inspection found the owner.",
            "review_requirement": {
                "minimum_independent_reviews": 1,
                "escalation_reason": None,
            },
            "reviews": [{
                "reviewer": "reviewer-one",
                "task_digest": digest(self.task),
                "decision": "unique",
                "reason": "Inspected and challenged the owner.",
            }],
        }
        return {
            "schema": "agents-cookbook-oracle-reviews.v1",
            "tasks": {self.task_id: row},
        }

    def _canonical(self) -> str:
        return json.dumps(self._valid_document(), separators=(",", ":"))

    @staticmethod
    def _replace_once(source: str, needle: str, replacement: str) -> str:
        assert source.count(needle) == 1, needle
        return source.replace(needle, replacement, 1)

    def _reject(self, source: str, message: str) -> None:
        self.path.write_text(source, encoding="utf-8")
        for require_complete in (False, True):
            with self.subTest(require_complete=require_complete):
                with self.assertRaisesRegex(OracleReviewError, message):
                    validate_oracle_reviews(
                        self.suite, require_complete=require_complete
                    )
        with self.assertRaisesRegex(OracleReviewError, message):
            oracle_review_guide(self.suite)
        # Invalid input is never automatically repaired or rewritten.
        self.assertEqual(self.path.read_text(encoding="utf-8"), source)

    def test_single_unambiguous_json_document_remains_qualified(self) -> None:
        self.path.write_text(self._canonical(), encoding="utf-8")
        status = validate_oracle_reviews(self.suite, require_complete=True)
        self.assertEqual(status["approved_tasks"], 1)
        self.assertTrue(status["complete"])
        self.assertIn("Oracle review READY", oracle_review_guide(self.suite))

    def test_duplicate_root_schema_fails_closed(self) -> None:
        source = self._replace_once(
            self._canonical(),
            '"schema":"agents-cookbook-oracle-reviews.v1"',
            '"schema":"invalid","schema":"agents-cookbook-oracle-reviews.v1"',
        )
        self._reject(source, "duplicate oracle review evidence JSON key")

    def test_duplicate_root_tasks_map_fails_closed(self) -> None:
        source = self._replace_once(
            self._canonical(), '"tasks":{', '"tasks":{},"tasks":{'
        )
        self._reject(source, "duplicate oracle review evidence JSON key")

    def test_duplicate_task_identity_fails_closed(self) -> None:
        source = self._replace_once(
            self._canonical(),
            '"tasks":{"test-owner":',
            '"tasks":{"test-owner":{},"test-owner":',
        )
        self._reject(source, "duplicate oracle review evidence JSON key")

    def test_duplicate_owner_field_fails_closed(self) -> None:
        source = self._replace_once(
            self._canonical(), '"owner":{',
            '"owner":{"path":"bad.py","symbol":"bad"},"owner":{',
        )
        self._reject(source, "duplicate oracle review evidence JSON key")

    def test_duplicate_reviewer_decision_fails_closed(self) -> None:
        source = self._replace_once(
            self._canonical(), '"decision":"unique"',
            '"decision":"ambiguous","decision":"unique"',
        )
        self._reject(source, "duplicate oracle review evidence JSON key")

    def test_duplicate_review_requirement_fails_closed(self) -> None:
        source = self._replace_once(
            self._canonical(), '"minimum_independent_reviews":1',
            '"minimum_independent_reviews":2,"minimum_independent_reviews":1',
        )
        self._reject(source, "duplicate oracle review evidence JSON key")

    def test_duplicate_key_in_unused_metadata_fails_closed(self) -> None:
        source = self._replace_once(
            self._canonical(), '"tasks":{',
            '"metadata":{"x":1,"x":2},"tasks":{',
        )
        self._reject(source, "duplicate oracle review evidence JSON key")

    def test_nonfinite_numbers_in_unused_metadata_fail_closed(self) -> None:
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                source = self._replace_once(
                    self._canonical(), '"tasks":{',
                    f'"metadata":{value},"tasks":{{',
                )
                self._reject(source, "non-finite oracle review evidence JSON number")


if __name__ == "__main__":
    unittest.main()
