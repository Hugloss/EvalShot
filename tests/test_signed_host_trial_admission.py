"""Negative admission checks for independently signed native host receipts."""
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks"))
from signed_host_trial_admission import _path, admit


class SignedHostTrialAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        (self.root / "trajectory.json").write_text("{}")
        (self.root / "receipt.json").write_text("{}")
        self.export = {"trials": [{"host_trial_id": "t1"}]}
        self.map = {"schema": "agentscookbook.native-host-signed-receipts.v1",
                    "campaign_id": "campaign-1",
                    "records": [{"host_trial_id": "t1", "trajectory_path": "trajectory.json",
                                 "attestation_path": "receipt.json"}]}
        self.paired = {"schema": "agentscookbook.paired-outcome.v1", "pairs": []}
        self.manifest = {"schema": "agentscookbook.host-capture.v1", "entries": []}

    def test_path_traversal_and_symlinks_rejected(self):
        with self.assertRaisesRegex(ValueError, "traversal"):
            _path(self.root, "../outside", "file")
        (self.root / "link").symlink_to(self.root / "trajectory.json")
        with self.assertRaisesRegex(ValueError, "symlink"):
            _path(self.root, "link", "file")

    def test_missing_trial_receipt_rejected(self):
        with patch("signed_host_trial_admission.project", return_value=(self.paired, self.manifest)):
            self.map["records"] = []
            with self.assertRaises(ValueError):
                admit(self.export, self.map, self.root, b"k" * 32, self.root)

    def test_verified_without_provider_submission_rejected(self):
        result = {"attestation_state": "VERIFIED", "delivery_state": "PROVEN",
                  "provider_submission_state": "UNKNOWN", "returned_packets": 1,
                  "verified_provider_submission_packets": 0, "reason": None}
        with patch("signed_host_trial_admission.project", return_value=(self.paired, self.manifest)), \
             patch("signed_host_trial_admission.verify_host_attestations", return_value=result), \
             patch("signed_host_trial_admission.audit") as downstream:
            with self.assertRaisesRegex(ValueError, "no complete signed"):
                admit(self.export, self.map, self.root, b"k" * 32, self.root)
            downstream.assert_not_called()

    def test_full_verified_proven_submitted_passes_to_byte_audit(self):
        result = {"attestation_state": "VERIFIED", "delivery_state": "PROVEN",
                  "provider_submission_state": "SUBMITTED", "returned_packets": 1,
                  "verified_provider_submission_packets": 1, "reason": None}
        with patch("signed_host_trial_admission.project", return_value=(self.paired, self.manifest)), \
             patch("signed_host_trial_admission.verify_host_attestations", return_value=result), \
             patch("signed_host_trial_admission.audit", return_value={"qualification": "PASS"}) as downstream:
            decision = admit(self.export, self.map, self.root, b"k" * 32, self.root)
            downstream.assert_called_once()
        self.assertEqual(decision["signed_host_submission_admission"]["verified_trial_receipts"], 1)
        self.assertFalse(decision["signed_host_submission_admission"]["provider_consumption_proven"])

    def test_foreign_trial_rejected_before_verification(self):
        self.map["records"][0]["host_trial_id"] = "alien"
        with patch("signed_host_trial_admission.project", return_value=(self.paired, self.manifest)), \
             patch("signed_host_trial_admission.verify_host_attestations") as verifier:
            with self.assertRaisesRegex(ValueError, "foreign"):
                admit(self.export, self.map, self.root, b"k" * 32, self.root)
            verifier.assert_not_called()


if __name__ == "__main__":
    unittest.main()
