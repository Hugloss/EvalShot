"""Byte-level host-capture audit regression tests. Run through ./benchmark's uv interpreter."""
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
from paired_host_capture_audit import audit


def build(root):
    pairs, entries = [], []
    for i in range(20):
        arms = {}
        for j, arm in enumerate(("baseline", "treatment")):
            payload = f"host bytes task={i} arm={arm}".encode()
            name = f"capture-{i}-{arm}.bin"
            (root / name).write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            trial, delivery = f"trial-{i}-{arm}", f"delivery-{i}-{arm}"
            arms[arm] = {
                "first_choice_owner_correct": i < (10 if j == 0 else 12),
                "exploratory_native_source_reads": 10 if j == 0 else 8,
                "patch_test_correct": True,
                "delivery": {"host_attested": True, "payload_sha256": digest,
                             "visible_payload_sha256": digest,
                             "host_trial_id": trial, "delivery_id": delivery},
            }
            entries.append({"task_id": f"task-{i}", "arm": arm,
                            "host_trial_id": trial, "delivery_id": delivery,
                            "payload_sha256": digest, "payload_path": name})
        pairs.append({"task_id": f"task-{i}", **arms})
    return {"schema": "agentscookbook.paired-outcome.v1", "pairs": pairs}, {
        "schema": "agentscookbook.host-capture.v1", "entries": entries}


class HostCaptureAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.campaign, self.manifest = build(self.root)

    def test_exact_bytes_and_bindings(self):
        result = audit(self.campaign, self.manifest, self.root)
        self.assertEqual(result["qualification"], "PASS")
        self.assertEqual(result["host_capture_audit"]["byte_verified_deliveries"], 40)
        self.assertEqual(result["host_capture_audit"]["capture_root_trust"], "EXTERNAL_VERIFICATION_REQUIRED")

    def test_modified_bytes_rejected(self):
        (self.root / "capture-0-baseline.bin").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "captured bytes"):
            audit(self.campaign, self.manifest, self.root)

    def test_cross_task_substitution_rejected(self):
        self.manifest["entries"][0]["host_trial_id"] = "different-trial"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            audit(self.campaign, self.manifest, self.root)

    def test_extra_capture_rejected(self):
        self.manifest["entries"].append(dict(self.manifest["entries"][0], task_id="extra", payload_path="different.bin"))
        (self.root / "different.bin").write_bytes((self.root / "capture-0-baseline.bin").read_bytes())
        with self.assertRaisesRegex(ValueError, "extra unmatched"):
            audit(self.campaign, self.manifest, self.root)

    def test_missing_capture_rejected(self):
        self.manifest["entries"].pop()
        with self.assertRaisesRegex(ValueError, "missing/duplicate"):
            audit(self.campaign, self.manifest, self.root)

    def test_path_escape_and_symlink_rejected(self):
        self.manifest["entries"][0]["payload_path"] = "../other.bin"
        with self.assertRaisesRegex(ValueError, "unsafe"):
            audit(self.campaign, self.manifest, self.root)
        self.manifest["entries"][0]["payload_path"] = "alias.bin"
        (self.root / "alias.bin").symlink_to(self.root / "capture-0-baseline.bin")
        with self.assertRaisesRegex(ValueError, "symlink"):
            audit(self.campaign, self.manifest, self.root)


if __name__ == "__main__":
    unittest.main()
