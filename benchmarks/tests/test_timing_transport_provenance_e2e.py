"""End-to-end model-free signed timing transport provenance and empirical gates.

Exercises the REAL dual-stage fake HTTPS dispatch, host-owned MAC receipt,
immutable ATIF packet readback, independent campaign audit and provenance.
Only the Harbor bundle loader is mocked with immutable synthetic grades.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from benchmarks.harness.empirical_campaign_decision import assess_empirical_campaign
from benchmarks.harness.host_input_attestation import _bound_mac
from benchmarks.harness.host_transport_provenance import audit_transport_provenance
from benchmarks.tests.test_timing_campaign_qualification import SignedTwoStageCampaignTests
from benchmarks.tests.test_trusted_host_campaign import (
    HOST_KEY, REVIEW_KEY, _seal, H,
)


HOST = "trusted-two-stage-test-host"
ENDPOINT = H("https://api.openai.com/v1/chat/completions")


class TimingTransportProvenanceE2E(SignedTwoStageCampaignTests):
    """Reuses the already tested real provider-host and ATIF fixtures."""

    def check_provenance(self) -> dict:
        return audit_transport_provenance(
            self.manifest, self.bundles, self.attestations, HOST_KEY,
            approved_endpoint_sha256=ENDPOINT, expected_host_identity=HOST,
        )

    def _variant_receipt(self):
        cell = next(
            cell for cell in self.manifest["assignments"]
            if cell["arm"] == "variant"
        )
        return self.attestations / (cell["trial_id"] + ".json")

    def _tamper_as_host(self, modifier) -> None:
        """Resign changed material to test semantics, not simply a bad MAC."""
        path = self._variant_receipt()
        envelope = json.loads(path.read_text(encoding="utf-8"))
        observation = envelope["deliveries"][0]
        modifier(observation)
        context = {
            name: envelope[name]
            for name in ("schema", "campaign_id", "trial_id", "trajectory_sha256")
        }
        delivery = {
            name: value for name, value in observation.items()
            if name != "mac_sha256"
        }
        observation["mac_sha256"] = _bound_mac(HOST_KEY, context, delivery)
        path.write_text(json.dumps(envelope), encoding="utf-8")

    def test_two_transport_stages_qualify_signed_cross_trial_provenance(self) -> None:
        report = self.check_provenance()
        self.assertTrue(report["cross_trial_transport_qualified"])
        self.assertEqual(report["verified_cells"], 2)
        self.assertEqual(report["expected_cells"], 2)
        self.assertEqual(report["expected_pairs"], 1)
        self.assertEqual(report["issues"], {})
        self.assertEqual(
            report["timing_arm_transport_protocol"],
            "DIRECT_REQUEST_1_VS_DEFERRED_REQUEST_2",
        )
        self.assertTrue(report["timing_extra_model_request_confound"])
        self.assertFalse(report["native_harness_request_capture_proven"])
        self.assertFalse(report["provider_consumption_proven"])

    def test_full_empirical_evaluator_accepts_transport_not_causal_gain(self) -> None:
        with patch(
            "benchmarks.harness.intervention_audit.load_harbor_bundle_projection",
            side_effect=lambda path: self.projections[path.name],
        ), patch(
            "benchmarks.harness.independent_campaign_qualification.load_harbor_bundle_projection",
            side_effect=lambda path: self.projections[path.name],
        ):
            decision = assess_empirical_campaign(
                self.manifest, self.bundles, self.attestations,
                HOST_KEY, _seal(self.manifest), REVIEW_KEY,
                approved_endpoint_sha256=ENDPOINT, expected_host_identity=HOST,
            )
        self.assertTrue(decision["transport"]["cross_trial_transport_qualified"])
        self.assertTrue(decision["independent_campaign"]["admitted"])
        self.assertEqual(decision["graded_pairs"], 1)
        self.assertEqual(decision["expected_pairs"], 1)
        self.assertEqual(decision["outcome_transitions"], {"FAIL_TO_PASS": 1})
        self.assertEqual(decision["observed_control_failures"], 1)
        self.assertFalse(decision["submission_bounded_descriptive_population_qualified"])
        self.assertIn(
            "fewer-than-eight-task-clusters-in-harness",
            decision["descriptive_decision_blockers"],
        )
        self.assertFalse(decision["native_harness_population_qualified"])
        self.assertFalse(decision["causal_hashmarks_improvement_proven"])

    def test_host_resigned_wrong_stage_or_prior_digests_fail_closed(self) -> None:
        path = self._variant_receipt()
        original = path.read_bytes()
        try:
            changes = (
                lambda x: x.update(model_request_sequence=1),
                lambda x: x["intervention"].update(delivery_request_sequence=1),
                lambda x: x["intervention"].update(prior_request_sha256="0" * 64),
                lambda x: x["intervention"].update(prior_transport_sha256="0" * 64),
                lambda x: x["transport"].update(endpoint_sha256=H("foreign-endpoint")),
                lambda x: x["intervention"].update(prompt_sha256=H("other-prompt")),
                lambda x: x.update(host_identity="different-host"),
            )
            for change in changes:
                path.write_bytes(original)
                self._tamper_as_host(change)
                report = self.check_provenance()
                with self.subTest(change=changes.index(change)):
                    self.assertFalse(report["cross_trial_transport_qualified"])
                    self.assertTrue(report["issues"])
        finally:
            path.write_bytes(original)

    def test_missing_trial_or_replayed_cross_trial_receipt_is_not_qualified(self) -> None:
        path = self._variant_receipt()
        original = path.read_bytes()
        control = next(
            cell for cell in self.manifest["assignments"]
            if cell["arm"] == "control"
        )
        another = self.attestations / (control["trial_id"] + ".json")
        try:
            for modification in ("missing", "replayed"):
                if modification == "missing":
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(another.read_bytes())
                result = self.check_provenance()
                with self.subTest(modification=modification):
                    self.assertFalse(result["cross_trial_transport_qualified"])
                    self.assertIn("incomplete-paired-transport", result["issues"])
                path.write_bytes(original)
        finally:
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
