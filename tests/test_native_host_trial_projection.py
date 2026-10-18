"""Native host trial projection: strict task pairing and byte-audited output."""
import hashlib
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks"))
from native_host_trial_projection import project
from paired_host_capture_audit import audit


def fixture(root):
    rows = []
    for i in range(20):
        for arm in ("baseline", "treatment"):
            data = f"host-delivered:{i}:{arm}".encode()
            name = f"payload-{i}-{arm}.bin"
            (root / name).write_bytes(data)
            rows.append({
                "task_id": f"task-{i}", "arm": arm,
                "native_agent": "opencode-native",
                "matched_authority_sha256": "a" * 64,
                "first_choice_owner_correct": i < (10 if arm == "baseline" else 12),
                "exploratory_native_source_reads": 10 if arm == "baseline" else 8,
                "patch_test_correct": True, "host_attested": True,
                "host_trial_id": f"trial-{i}-{arm}",
                "delivery_id": f"delivery-{i}-{arm}",
                "payload_sha256": hashlib.sha256(data).hexdigest(),
                "payload_path": name,
            })
    return {"schema": "agentscookbook.native-host-trials.v1", "trials": rows}


class NativeProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.export = fixture(self.root)

    def test_projection_feeds_existing_audit(self):
        campaign, manifest = project(self.export)
        self.assertEqual(len(campaign["pairs"]), 20)
        self.assertEqual(audit(campaign, manifest, self.root)["qualification"], "PASS")

    def test_unmatched_pair_rejected(self):
        self.export["trials"].pop()
        with self.assertRaisesRegex(ValueError, "unmatched"):
            project(self.export)

    def test_cross_arm_agent_rejected(self):
        self.export["trials"][1]["native_agent"] = "codex-native"
        with self.assertRaisesRegex(ValueError, "native agent mismatch"):
            project(self.export)

    def test_cross_arm_authority_rejected(self):
        self.export["trials"][1]["matched_authority_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "authority mismatch"):
            project(self.export)

    def test_duplicate_host_trial_rejected(self):
        self.export["trials"][1]["host_trial_id"] = self.export["trials"][0]["host_trial_id"]
        with self.assertRaisesRegex(ValueError, "duplicate host trial"):
            project(self.export)

    def test_missing_host_attestation_rejected(self):
        self.export["trials"][0]["host_attested"] = False
        with self.assertRaisesRegex(ValueError, "attestation absent"):
            project(self.export)

    def test_wrong_metric_type_rejected(self):
        self.export["trials"][0]["exploratory_native_source_reads"] = True
        with self.assertRaisesRegex(ValueError, "native reads"):
            project(self.export)

    def test_tampered_payload_rejected_by_downstream(self):
        paired, manifest = project(self.export)
        (self.root / self.export["trials"][0]["payload_path"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "captured bytes"):
            audit(paired, manifest, self.root)


if __name__ == "__main__":
    unittest.main()
