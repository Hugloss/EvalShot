"""E271–E272 independent native text readback attack ring.

Authentic *test-owned* HMAC receipts are produced from synthetic native
session exports. The prior Linux isolation gate is mocked only for unit
testing the second, exact model-message-digest check; no real OpenCode
or upstream provider is used or claimed.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.native_text_presentation_readback import (
    assess_native_text_presentation,
)
from benchmarks.harness.opencode_native_session import finalize_native_export
from benchmarks.harness.trusted_host_capture import TrustedModelRequestCapture
from benchmarks.harness.trusted_treatments import select_treatment
from benchmarks.tests.test_native_text_presentation import text_manifest
from benchmarks.tests.test_opencode_native_gateway import H, KEY, source, request
from benchmarks.tests.test_opencode_native_session import native_export


class TextMessageReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.results = self.root / "results"
        self.results.mkdir()
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.key = self.root / "host.key"
        self.key.write_bytes(KEY)
        self.key.chmod(0o600)
        self.plan = text_manifest()
        self.host = "independent-source-host"
        for cell in self.plan["assignments"]:
            self._make_cell(cell)

    def tearDown(self):
        self.temp.cleanup()

    def _make_cell(self, cell):
        trial = cell["trial_id"]
        folder = self.results / trial
        folder.mkdir()
        (self.sources / (trial + ".json")).write_bytes(canonical({
            "current": source(), "replaced": None,
        }))
        selected = select_treatment(
            self.plan, trial_id=trial, harness=cell["harness"],
            task=cell["task"], replicate=cell["replicate"],
            arm=cell["arm"], current=source(),
        )
        capture = TrustedModelRequestCapture(
            self.plan, trial_id=trial, harness=cell["harness"],
            task=cell["task"], replicate=cell["replicate"],
            arm=cell["arm"], host_identity=self.host,
            host_key_file=self.key,
        )
        body = request(selected=selected["selected_content"], tool=True)
        doc = json.loads(body)
        prompt = {
            "request_settings": {
                key: val for key, val in doc.items() if key != "messages"
            },
            "non_tool_messages": [
                msg for msg in doc["messages"] if msg["role"] != "tool"
            ],
        }
        capture.observe_outbound_request(
            serialized_model_request=body,
            tool_call_id="native-tool-1",
            returned_packet=source()["content"],
            request_sequence=1, current=source(),
            catalog_sha256=H("catalog"),
            prompt_sha256=hashlib.sha256(canonical(prompt)).hexdigest(),
            oracle_sha256=H("oracle"), workspace_sha256=H("workspace"),
        )
        capture.begin_provider_transport()
        capture.record_provider_submission(
            tool_call_id="native-tool-1",
            serialized_model_request=body,
            endpoint_sha256=H("approved-url"),
            status=200, response_sha256=H("provider-response"), response_bytes=64,
        )
        raw = native_export(
            title="native-paired-" + trial,
            session_id="ses_" + trial,
            workspace=self.workspace,
            call_id="native-tool-1",
            packet=source()["content"],
        )
        result = finalize_native_export(
            raw, capture=capture, session_id="ses_" + trial,
            title="native-paired-" + trial, workspace=self.workspace,
            run_root=folder, expected_provider="agentscookbook-captured",
            expected_model="gpt-fixture",
            original_packet=source()["content"],
        )
        self.assertTrue(result["signed_host_input_and_transport_verified"])

    def audit(self):
        with patch(
            "benchmarks.harness.native_text_presentation_readback.evaluate_native_netns_pilot",
            return_value={
                "paired_pilot_observations_ready_for_independent_grading": True,
            },
        ) as prior:
            report = assess_native_text_presentation(
                self.plan, results_root=self.results,
                sources_root=self.sources,
                workspace=self.workspace, host_key=KEY,
                host_identity=self.host,
                approved_endpoint_sha256=H("approved-url"),
                expected_bwrap_sha256=H("pinned-bwrap"),
                expected_hashmarks_sha256=H("pinned-hashmarks"),
            )
        prior.assert_called_once()
        return report

    def test_both_exact_host_observed_text_surfaces_pass_readback_only(self):
        report = self.audit()
        self.assertTrue(report["text_presentation_pairs_host_transport_verified"])
        self.assertEqual(report["host_attested_exact_text_messages"], 2)
        self.assertEqual(report["blockers"], {})
        for field in (
            "native_process_origin_independently_attested",
            "native_sdk_behavior_independently_attested",
            "model_attention_proven", "independent_grading_completed",
            "randomized_causal_uplift_proven",
        ):
            self.assertFalse(report[field])

    def test_swapped_model_message_digest_and_native_call_identity_refuse(self):
        cell = next(c for c in self.plan["assignments"] if c["arm"] == "variant")
        trial = cell["trial_id"]
        folder = self.results / trial
        receipt_path = folder / "host-attestation.json"
        original = receipt_path.read_bytes()
        doc = json.loads(original)
        doc["deliveries"][0]["model_message_sha256"] = H("forged-message")
        receipt_path.write_bytes(canonical(doc) + b"\n")
        self.assertIn("native-text-wire-parity-unverified", self.audit()["blockers"])
        receipt_path.write_bytes(original)

        native_path = folder / "native-session.json"
        raw = native_path.read_bytes()
        native = json.loads(raw)
        native["messages"][0]["parts"][0]["callID"] = "wrong-call"
        native_path.write_bytes(canonical(native))
        self.assertIn("native-text-wire-parity-unverified", self.audit()["blockers"])

    def test_missing_treatment_arm_denies_pair_even_with_mock_prior_gate(self):
        cell = next(c for c in self.plan["assignments"] if c["arm"] == "control")
        (self.sources / (cell["trial_id"] + ".json")).unlink()
        with self.assertRaisesRegex(ValueError, "incomplete-native-chat-frozen"):
            self.audit()

    def test_legacy_structured_study_never_reinterpreted_as_text_presentation(self):
        from benchmarks.tests.test_opencode_native_gateway import manifest
        with self.assertRaisesRegex(ValueError, "not-a-new-text-presentation"):
            with patch(
                "benchmarks.harness.native_text_presentation_readback.evaluate_native_netns_pilot"
            ) as not_called:
                assess_native_text_presentation(
                    manifest(), results_root=self.results,
                    sources_root=self.sources, workspace=self.workspace,
                    host_key=KEY, host_identity=self.host,
                    approved_endpoint_sha256=H("approved-url"),
                    expected_bwrap_sha256=H("pinned-bwrap"),
                    expected_hashmarks_sha256=H("pinned-hashmarks"),
                )
                not_called.assert_not_called()

    def test_forged_text_format_byte_digest_does_not_match_signed_host_receipt(self):
        cell = next(c for c in self.plan["assignments"] if c["arm"] == "variant")
        trial = cell["trial_id"]
        path = self.sources / (trial + ".json")
        payload = json.loads(path.read_bytes())
        # Alter source data but retain its old externally pinned digest.
        payload["current"]["content"]["owner"] = "src/other.py"
        path.write_bytes(canonical(payload))
        with self.assertRaises(ValueError):
            self.audit()


if __name__ == "__main__":
    unittest.main()
