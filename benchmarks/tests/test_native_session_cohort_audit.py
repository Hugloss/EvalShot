"""E255/E256 model-free native cohort replay and adversarial admission tests."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.native_session_cohort_audit import audit_native_session_cohort
from benchmarks.harness.opencode_native_session import finalize_native_export
from benchmarks.harness.trusted_host_capture import TrustedModelRequestCapture
from benchmarks.harness.trusted_treatments import select_treatment
from benchmarks.tests.test_opencode_native_gateway import manifest, source
from benchmarks.tests.test_opencode_native_session import native_export, KEY

H = lambda x: hashlib.sha256(str(x).encode("utf-8")).hexdigest()


class NativeCohortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.results = self.root / "results"
        self.results.mkdir()
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.frozen = manifest()
        self.host = "privileged-native-test-host"
        self.endpoint = H("host-provider-endpoint")
        self._publish_both()

    def tearDown(self):
        self.temp.cleanup()

    def _publish_both(self):
        for cell in self.frozen["assignments"]:
            trial = cell["trial_id"]
            output = self.results / trial
            output.mkdir()
            current = source()
            (self.sources / (trial + ".json")).write_bytes(canonical({
                "current": current, "replaced": None,
            }))
            selected = select_treatment(
                self.frozen, trial_id=trial,
                harness=cell["harness"], task=cell["task"],
                replicate=cell["replicate"], arm=cell["arm"],
                current=current,
            )
            capture = TrustedModelRequestCapture(
                self.frozen, trial_id=trial, harness=cell["harness"],
                task=cell["task"], replicate=cell["replicate"],
                arm=cell["arm"], host_identity=self.host,
                host_key_file=self._host_key(),
            )
            body = canonical({
                "model": "gpt-fixture",
                "messages": [
                    {"role": "user", "content": "Find source owner"},
                    {"role": "tool", "tool_call_id": "call-" + trial,
                     "content": selected["selected_content"]},
                ],
            })
            prompt_sha = hashlib.sha256(canonical({
                "request_settings": {"model": "gpt-fixture"},
                "non_tool_messages": [{"role": "user", "content": "Find source owner"}],
            })).hexdigest()
            capture.observe_outbound_request(
                serialized_model_request=body,
                tool_call_id="call-" + trial,
                returned_packet=current["content"], request_sequence=1,
                current=current, catalog_sha256=H("catalog"),
                prompt_sha256=prompt_sha,
                oracle_sha256=H("oracle"), workspace_sha256=H("workspace"),
            )
            capture.begin_provider_transport()
            capture.record_provider_submission(
                tool_call_id="call-" + trial,
                serialized_model_request=body, endpoint_sha256=self.endpoint,
                status=200, response_sha256=H("answer-" + trial),
                response_bytes=30,
            )
            export = native_export(
                title="native-title-" + trial,
                session_id="ses_" + trial,
                workspace=self.workspace,
                call_id="call-" + trial,
            )
            finalize_native_export(
                export, capture=capture, session_id="ses_" + trial,
                title="native-title-" + trial, workspace=self.workspace,
                run_root=output, expected_provider="agentscookbook-captured",
                expected_model="gpt-fixture",
                original_packet=current["content"],
            )

    def _host_key(self):
        key = self.root / "host.key"
        if not key.exists():
            key.write_bytes(KEY)
            key.chmod(0o600)
        return key

    def audit(self):
        return audit_native_session_cohort(
            self.frozen, results_root=self.results,
            sources_root=self.sources, key=KEY,
            expected_workspace=self.workspace,
            host_identity=self.host,
            approved_endpoint_sha256=self.endpoint,
        )

    def test_matching_exported_calls_and_host_receipts_qualify_observation_only(self):
        report = self.audit()
        self.assertTrue(report["native_session_cohort_observationally_consistent"])
        self.assertEqual(report["verified_native_trial_exports"], 2)
        self.assertEqual(report["verified_native_pairs"], 1)
        self.assertEqual(report["issues"], {})
        self.assertFalse(report["native_process_origin_proven"])
        self.assertFalse(report["exclusive_network_egress_proven"])
        self.assertFalse(report["randomized_causal_uplift_proven"])

    def test_read_back_counts_native_calls_without_claiming_host_capture(self):
        result = self.audit()
        evidence = result["native_tool_order_evidence"]
        self.assertEqual(evidence["observed_tool_calls"], 2)
        self.assertEqual(evidence["observed_native_search_calls"], 0)
        self.assertEqual(evidence["observed_native_read_calls"], 0)
        self.assertEqual(evidence["routing_by_arm"], {
            "control": {"FIRST_CHOICE": 1},
            "variant": {"FIRST_CHOICE": 1},
        })
        self.assertTrue(evidence["export_tool_part_projection_qualified"])
        self.assertFalse(evidence["host_wide_capture_exhaustiveness_proven"])

    def test_unreported_native_search_in_raw_export_invalidates_existing_receipt(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        path = self.results / trial / "native-session.json"
        doc = json.loads(path.read_bytes())
        doc["messages"][0]["parts"].insert(0, {
            "type": "tool", "tool": "grep", "callID": "native-before-hashmarks",
            "state": {"status": "completed", "input": {"pattern": "owner"},
                      "output": "source search result"},
        })
        path.write_bytes(canonical(doc))
        result = self.audit()
        self.assertFalse(result["native_session_cohort_observationally_consistent"])
        self.assertEqual(result["verified_native_trial_exports"], 1)
        self.assertIn("native-trial-session-or-provider-mismatch", result["issues"])

    def test_tampered_native_export_or_derived_atif_fails(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        directory = self.results / trial
        raw = directory / "native-session.json"
        previous = raw.read_bytes()
        mutated = json.loads(previous)
        mutated["messages"][0]["parts"][0]["state"]["output"] = '{"owner":"forged.py"}'
        raw.write_bytes(canonical(mutated))
        self.assertFalse(self.audit()["native_session_cohort_observationally_consistent"])
        raw.write_bytes(previous)
        atif = directory / "trajectory.json"
        data = json.loads(atif.read_bytes())
        data["steps"][0]["observation"]["results"][0]["content"]["owner"] = "changed.py"
        atif.write_bytes(canonical(data))
        self.assertFalse(self.audit()["native_session_cohort_observationally_consistent"])

    def test_missing_or_replayed_attestation_and_source_drift_fails(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        receipt = self.results / trial / "host-attestation.json"
        receipt.unlink()
        self.assertFalse(self.audit()["native_session_cohort_observationally_consistent"])
        self.assertEqual(self.audit()["verified_native_trial_exports"], 1)

    def test_source_selection_is_independently_rechecked(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        expected = self.sources / (trial + ".json")
        previous = expected.read_bytes()
        tampered = json.loads(previous)
        tampered["current"]["content"] = {"owner": "forged.py"}
        expected.write_bytes(canonical(tampered))
        self.assertIn("native-trial-session-or-provider-mismatch",
                      self.audit()["issues"])

    def test_foreign_trial_and_symlink_are_never_ignored(self):
        (self.results / "unplanned").mkdir()
        self.assertIn("foreign-or-unsafe-trial-directory", self.audit()["issues"])
        (self.results / "unplanned").rmdir()
        trial = self.frozen["assignments"][0]["trial_id"]
        native = self.results / trial / "native-session.json"
        native.rename(self.root / "export-elsewhere.json")
        native.symlink_to(self.root / "export-elsewhere.json")
        self.assertIn("native-trial-session-or-provider-mismatch",
                      self.audit()["issues"])

    def test_cross_arm_presentation_surface_and_source_are_isolated(self):
        variant = next(cell for cell in self.frozen["assignments"]
                       if cell["arm"] == "variant")
        at = self.results / variant["trial_id"] / "host-attestation.json"
        receipt = json.loads(at.read_bytes())
        self.assertEqual(receipt["deliveries"][0]["intervention"]["arm"], "variant")
        # The signed surface really differs; semantic identity is unchanged.
        control = next(cell for cell in self.frozen["assignments"]
                       if cell["arm"] == "control")
        other = json.loads(
            (self.results / control["trial_id"] / "host-attestation.json").read_bytes()
        )
        self.assertNotEqual(
            receipt["deliveries"][0]["intervention"]["surface_sha256"],
            other["deliveries"][0]["intervention"]["surface_sha256"],
        )
        self.assertEqual(
            receipt["deliveries"][0]["intervention"]["semantic_sha256"],
            other["deliveries"][0]["intervention"]["semantic_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
