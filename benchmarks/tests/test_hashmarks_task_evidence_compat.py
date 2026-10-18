from __future__ import annotations

import json
import unittest

from benchmarks.evidence import grade_direct_claims
from benchmarks.hashmarks_task_evidence import (
    TASK_EVIDENCE_SCHEMAS,
    is_task_evidence_packet,
    retrieval_candidate_matches,
    retrieval_candidate_symbol,
)


class HashmarksTaskEvidenceCompatibilityTests(unittest.TestCase):
    def test_supported_schemas_keep_historical_v2_and_current_v3(self) -> None:
        self.assertEqual(
            TASK_EVIDENCE_SCHEMAS,
            frozenset(
                {
                    "hashmarks.task-evidence.v2",
                    "hashmarks.task-evidence.v3",
                }
            ),
        )
        self.assertTrue(
            is_task_evidence_packet({"schema": "hashmarks.task-evidence.v2"})
        )
        self.assertTrue(
            is_task_evidence_packet({"schema": "hashmarks.task-evidence.v3"})
        )
        self.assertFalse(
            is_task_evidence_packet({"schema": "hashmarks.task-evidence.v4"})
        )

    def test_candidate_matching_supports_v2_and_compact_v3_shapes(self) -> None:
        expected = {"path": "src/owner.py", "symbol": "target"}
        cases = (
            {"path": "src/owner.py", "name": "target"},
            {"path": "src/owner.py", "qualname": "Owner.target"},
            {"path": "src/owner.py", "symbol": "Owner.target"},
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    retrieval_candidate_symbol(candidate),
                    "target",
                )
                self.assertTrue(
                    retrieval_candidate_matches(candidate, expected)
                )

    def test_direct_evidence_grading_accepts_task_evidence_v3(self) -> None:
        result = grade_direct_claims(
            {"family": "semantics"},
            "hashmarks",
            json.dumps(
                {
                    "schema": "hashmarks.task-evidence.v3",
                    "evidence_receipt": {
                        "repository_identity": "sha256:repo",
                        "evidence_identity": "sha256:evidence",
                    },
                    "evidence_packet_identity": "sha256:packet",
                }
            ),
        )

        self.assertTrue(result["parse_valid"])
        self.assertEqual(result["provenance_structure"], "PRESENT")


if __name__ == "__main__":
    unittest.main()
