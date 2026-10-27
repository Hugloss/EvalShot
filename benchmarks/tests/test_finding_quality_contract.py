"""Model-free checks for opt-in finding-quality review evidence."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from benchmarks.harness.identity import digest
from benchmarks.harness.oracle_reviews import OracleReviewError, validate_oracle_reviews


def _record() -> dict:
    return {
        "schema": "evalshot-finding-quality.v1",
        "verdict": "FINDING",
        "requirement": "The imported target owns the header call",
        "trigger": "definition-time default evaluation",
        "causal_owner": "consumer.py",
        "supported_path": "consumer.py::build",
        "consequence": "false call ownership",
        "source_references": [{"path": "consumer.py", "line": 2, "quote": "def build(value=build()):"}],
        "evidence_qualifier": "source-observed, semantics-not-adjudicated",
        "unresolved_scope": "none in declared fixture",
    }


class FindingQualityContractTests(unittest.TestCase):
    def test_optional_record_is_checked_without_changing_existing_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = {"repository": {"url": "https://example.invalid/source", "commit": "a" * 40},
                    "oracle": {"adapter": "repository-location-json",
                               "configuration": {"expected": {"path": "consumer.py", "symbol": "build"}}}}
            suite = SimpleNamespace(root=root, tasks={"case": task},
                                    experiment={"tasks": ["case"], "oracle_reviews": "oracle.json"})
            row = {
                "task_digest": digest(task), "repository": task["repository"],
                "owner": task["oracle"]["configuration"]["expected"],
                "alternatives": [], "evidence": "independent source inspection",
                "review_requirement": {"minimum_independent_reviews": 1, "escalation_reason": None},
                "reviews": [{"reviewer": "independent-a", "task_digest": digest(task),
                             "decision": "unique", "reason": "source-qualified owner"}],
            }
            path = root / "oracle.json"
            def check(value, message=None):
                path.write_text(json.dumps({"schema": "agents-cookbook-oracle-reviews.v1",
                                             "tasks": {"case": value}}), encoding="utf-8")
                if message:
                    with self.assertRaisesRegex(OracleReviewError, message):
                        validate_oracle_reviews(suite, require_complete=True)
                else:
                    self.assertEqual(validate_oracle_reviews(suite, require_complete=True)["approved_tasks"], 1)

            check(row)  # Existing evidence remains valid without optional metadata.
            row["finding_quality"] = _record()
            check(row)  # Shape validated, not semantic approval.
            for change, message in (
                ({"schema": "future.v2"}, "unknown finding-quality schema"),
                ({"verdict": "CERTAIN"}, "invalid finding-quality verdict"),
                ({"requirement": ""}, "invalid finding-quality requirement"),
                ({"source_references": [{"path": "consumer.py", "line": True, "quote": "x"}]}, "invalid finding-quality source reference"),
                ({"source_references": [{"path": "consumer.py", "line": 0, "quote": "x"}]}, "invalid finding-quality source reference"),
                ({"source_references": []}, "lacks source references"),
            ):
                with self.subTest(change=change):
                    altered = copy.deepcopy(row)
                    altered["finding_quality"].update(change)
                    check(altered, message)
            altered = copy.deepcopy(row)
            altered["finding_quality"]["surprise"] = "unversioned extra authority"
            check(altered, "invalid finding-quality record")
            altered = copy.deepcopy(row)
            altered["finding_quality"]["verdict"] = "INSUFFICIENT EVIDENCE"
            altered["finding_quality"]["source_references"] = []
            check(altered)


if __name__ == "__main__":
    unittest.main()
