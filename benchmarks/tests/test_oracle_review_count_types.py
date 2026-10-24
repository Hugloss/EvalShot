"""Regression: oracle-review count is a JSON integer, never bool/float."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from benchmarks.harness.identity import digest
from benchmarks.harness.oracle_reviews import OracleReviewError, validate_oracle_reviews


class OracleReviewCountTypeTests(unittest.TestCase):
    def test_reject_noninteger_counts_that_compare_equal_to_valid_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = {
                "repository": {"url": "https://example.invalid/repo", "commit": "a" * 40},
                "oracle": {
                    "adapter": "repository-location-json",
                    "configuration": {
                        "expected": {"path": "src/owner.py", "symbol": "owner"}
                    },
                },
            }
            task_id = "test-owner"
            suite = SimpleNamespace(
                root=root,
                experiment={"tasks": [task_id], "oracle_reviews": "qualification/oracle-reviews.json"},
                tasks={task_id: task},
            )
            path = root / "qualification" / "oracle-reviews.json"
            path.parent.mkdir()
            row = {
                "task_digest": digest(task),
                "repository": task["repository"],
                "owner": task["oracle"]["configuration"]["expected"],
                "alternatives": [],
                "evidence": "Independent source rationale",
                "review_requirement": {
                    "minimum_independent_reviews": 1,
                    "escalation_reason": None,
                },
                "reviews": [{
                    "reviewer": "reviewer-one",
                    "task_digest": digest(task),
                    "decision": "unique",
                    "reason": "Independent finding",
                }],
            }
            for value in (True, False, 1.0, 2.0, "1", None):
                with self.subTest(value=value):
                    row["review_requirement"]["minimum_independent_reviews"] = value
                    path.write_text(json.dumps({
                        "schema": "agents-cookbook-oracle-reviews.v1",
                        "tasks": {task_id: row},
                    }), encoding="utf-8")
                    with self.assertRaisesRegex(OracleReviewError, "invalid oracle review requirement"):
                        validate_oracle_reviews(suite, require_complete=True)
            row["review_requirement"]["minimum_independent_reviews"] = 1
            path.write_text(json.dumps({
                "schema": "agents-cookbook-oracle-reviews.v1",
                "tasks": {task_id: row},
            }), encoding="utf-8")
            result = validate_oracle_reviews(suite, require_complete=True)
            self.assertEqual(result["approved_tasks"], 1)

            for malformed_root in (None, [], "wrong", 42):
                with self.subTest(root=malformed_root):
                    path.write_text(json.dumps(malformed_root), encoding="utf-8")
                    with self.assertRaisesRegex(OracleReviewError, "unknown oracle review evidence schema"):
                        validate_oracle_reviews(suite, require_complete=True)

            # A JSON array or object is not a legal semantic review decision.
            # The validator must fail with its typed error, not TypeError.
            for malformed_decision in ([], {}, ["unique"], None, True, 1):
                with self.subTest(decision=malformed_decision):
                    row["reviews"][0]["decision"] = malformed_decision
                    path.write_text(json.dumps({
                        "schema": "agents-cookbook-oracle-reviews.v1",
                        "tasks": {task_id: row},
                    }), encoding="utf-8")
                    with self.assertRaisesRegex(OracleReviewError, "invalid review record"):
                        validate_oracle_reviews(suite, require_complete=True)


if __name__ == "__main__":
    unittest.main()
