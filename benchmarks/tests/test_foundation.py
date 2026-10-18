from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.identity import definition_id, digest, execution_id
from benchmarks.harness.model import TrialStatus
from benchmarks.harness.receipt import (
    ReceiptExistsError,
    is_complete_receipt,
    write_receipt,
)


class FoundationTests(unittest.TestCase):
    def test_identity_mapping_order(self) -> None:
        self.assertEqual(digest({"b": 2, "a": 1}), digest({"a": 1, "b": 2}))

    def test_definition_identity_binds_condition(self) -> None:
        common = {
            "experiment": {"id": "e", "version": 1},
            "task": {"id": "t", "version": 1},
            "trial": 0,
            "seed": 7,
        }
        self.assertNotEqual(
            definition_id(condition={"id": "bare"}, **common),
            definition_id(condition={"id": "hashmarks"}, **common),
        )

    def test_execution_identity_binds_observed_subject(self) -> None:
        common = {
            "definition": "d",
            "agent_identity": {"id": "codex", "version": "1"},
            "oracle_identity": {"id": "oracle", "version": "1"},
            "harness_identity": {"commit": "abc"},
            "environment_identity": {"id": "env"},
            "mutation_identity": None,
        }
        self.assertNotEqual(
            execution_id(
                subject_identity={"id": "hashmarks", "version": "1"}, **common
            ),
            execution_id(
                subject_identity={"id": "hashmarks", "version": "2"}, **common
            ),
        )

    def test_receipt_is_verified_before_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, checksum = write_receipt(root, {"status": TrialStatus.PASS.value})
            self.assertTrue(path.is_file())
            self.assertTrue(checksum)
            self.assertTrue(is_complete_receipt(root))
            with self.assertRaises(ReceiptExistsError):
                write_receipt(root, {"status": TrialStatus.FAIL.value})

    def test_partial_or_corrupt_receipt_is_not_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "result.json").write_text("{}\n", encoding="utf-8")
            self.assertFalse(is_complete_receipt(root))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_receipt(root, {"status": TrialStatus.PASS.value})
            (root / "result.sha256").write_text("0" * 64 + "  result.json\n")
            self.assertFalse(is_complete_receipt(root))

    def test_matching_partial_receipt_can_resume_finalization(self) -> None:
        from benchmarks.harness.identity import canonical_json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = {"status": TrialStatus.PASS.value}
            (root / "result.json").write_bytes(canonical_json(receipt))
            write_receipt(root, receipt)
            self.assertTrue(is_complete_receipt(root))

    def test_conflicting_partial_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "result.json").write_text('{"status":"FAIL"}\n', encoding="utf-8")
            with self.assertRaises(ReceiptExistsError):
                write_receipt(root, {"status": TrialStatus.PASS.value})

    def test_non_product_failure_states_exist(self) -> None:
        self.assertEqual(TrialStatus.INCOMPLETE.value, "INCOMPLETE")
        self.assertEqual(TrialStatus.INVALID.value, "INVALID")
        self.assertEqual(TrialStatus.CONTAMINATED.value, "CONTAMINATED")


if __name__ == "__main__":
    unittest.main()
