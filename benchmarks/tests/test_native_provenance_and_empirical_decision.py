"""E245–E248: attack native attribution and exact external host provenance.

Synthetic signed receipts here exercise verifier contracts only, never
establish independent key custody or genuine model execution.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.empirical_campaign_decision import assess_empirical_campaign
from benchmarks.harness.evidence_lifecycle import project_evidence_lifecycle
from benchmarks.harness.host_input_attestation import (
    SCHEMA as HOST_SCHEMA, _bound_mac, canonical,
)
from benchmarks.harness.host_transport_provenance import audit_transport_provenance
from benchmarks.harness.native_host_readiness import audit_native_readiness, describe_boundary
from benchmarks.harness.trusted_treatments import select_treatment
from benchmarks.tests.test_trusted_host_campaign import (
    HOST_KEY, REVIEW_KEY, _atif, _seal, design, plan, source,
)

H = lambda name: hashlib.sha256(str(name).encode()).hexdigest()
ENDPOINT = H("approved-provider-endpoint")
HOST = "verified-first-party-provider-transport"


class NativeBoundaryReadinessTests(unittest.TestCase):
    def test_native_harness_and_provider_managed_mcp_cannot_upgrade(self):
        for name in ("codex", "codex-native", "opencode",
                     "opencode-native", "claude-code", "openai-responses"):
            with self.subTest(harness=name):
                result = describe_boundary(name)
                self.assertFalse(result["native_model_input_attested"])
                self.assertFalse(result["may_claim_native_hashmarks_uplift"])
                self.assertFalse(result["native_provider_execution_qualified"])
        self.assertEqual(describe_boundary("openai-responses")["integration_state"],
                         "PROVIDER_MANAGED_MCP")
        self.assertEqual(describe_boundary("trusted-http-chat")["integration_state"],
                         "FIRST_PARTY_TRANSPORT_ONLY")
        self.assertEqual(describe_boundary("unknown-harness")["integration_state"],
                         "UNSUPPORTED_HOST")

    def test_even_signed_artifact_does_not_make_native_harness_attested(self):
        proposal = design()
        proposal["harnesses"] = ["codex", "opencode", "claude-code"]
        report = audit_native_readiness(proposal)
        self.assertFalse(report["native_model_input_population_qualified"])
        self.assertEqual(len(report["harnesses"]), 3)
        self.assertFalse(report["trusted_http_host_is_native_harness"])


class HostTransportFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundles = self.root / "bundles"
        self.attestations = self.root / "receipts"
        self.bundles.mkdir()
        self.attestations.mkdir()
        self.manifest = plan()
        self._write_receipts()

    def tearDown(self):
        self.temp.cleanup()

    def _write_receipts(self):
        for cell in self.manifest["assignments"]:
            trial = cell["trial_id"]
            folder = self.bundles / trial
            folder.mkdir()
            trace = folder / "trajectory.json"
            trace.write_text(json.dumps(_atif(source()["content"])))
            packet = project_evidence_lifecycle(trace)["packet_refs"][0]
            selected = select_treatment(
                self.manifest, trial_id=trial, harness=cell["harness"],
                task=cell["task"], replicate=cell["replicate"],
                arm=cell["arm"], current=source(),
            )
            meta = {
                "study": selected["study"], "arm": selected["arm"],
                "design_sha256": selected["design_sha256"],
                "presentation": selected["presentation"],
                "generation_sha256": selected["generation_sha256"],
                "current_generation_sha256": selected["current_generation_sha256"],
                "semantic_sha256": selected["semantic_sha256"],
                "surface_sha256": selected["surface_sha256"],
                "catalog_sha256": H("catalog"),
                "prompt_sha256": H("prompt"),
                "oracle_sha256": H("oracle"),
                "workspace_sha256": H("workspace"),
            }
            context = {
                "schema": HOST_SCHEMA,
                "campaign_id": self.manifest["design"]["campaign_id"],
                "trial_id": trial,
                "trajectory_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
            }
            record = {
                "host_identity": HOST,
                "host_build_sha256": self.manifest["host_build_sha256"],
                "model_request_sha256": H("request-" + trial),
                "model_input_sha256": H("input-" + trial),
                "model_message_sha256": H("message-" + trial),
                "packet_sha256": packet["packet_sha256"],
                "call_id_sha256": packet["call_id_sha256"],
                "model_request_sequence": 1,
                "boundary": "host-model-request-input",
                "intervention": meta,
                "transport": {
                    "boundary": "https-response",
                    "endpoint_sha256": ENDPOINT,
                    "http_status": 200,
                    "response_sha256": H("response-" + trial),
                    "response_bytes": 200,
                },
            }
            envelope = {
                **context, "deliveries": [{
                    **record, "mac_sha256": _bound_mac(HOST_KEY, context, record),
                }],
            }
            (self.attestations / (trial + ".json")).write_text(json.dumps(envelope))

    def edit_signed(self, trial_id: str, transform):
        path = self.attestations / (trial_id + ".json")
        envelope = json.loads(path.read_text())
        row = envelope["deliveries"][0]
        transform(row)
        body = {k: v for k, v in row.items() if k != "mac_sha256"}
        row["mac_sha256"] = _bound_mac(
            HOST_KEY, {k: envelope[k] for k in (
                "schema", "campaign_id", "trial_id", "trajectory_sha256"
            )}, body,
        )
        path.write_text(json.dumps(envelope))

    def check(self):
        return audit_transport_provenance(
            self.manifest, self.bundles, self.attestations, HOST_KEY,
            approved_endpoint_sha256=ENDPOINT, expected_host_identity=HOST,
        )


class SignedTransportProvenanceTests(HostTransportFixture):
    def test_complete_signed_pairs_have_stable_endpoint_host_and_prompt(self):
        result = self.check()
        self.assertTrue(result["cross_trial_transport_qualified"])
        self.assertEqual(result["verified_cells"], 2)
        self.assertEqual(result["expected_pairs"], 1)
        self.assertEqual(result["issues"], {})
        self.assertFalse(result["native_harness_request_capture_proven"])
        self.assertFalse(result["provider_consumption_proven"])

    def test_attack_host_build_identity_endpoint_and_arm_replacement(self):
        manipulations = (
            lambda row: row["transport"].update(endpoint_sha256=H("other")),
            lambda row: row.update(host_build_sha256=H("replacement")),
            lambda row: row.update(host_identity="other-host"),
            lambda row: row["intervention"].update(arm="other"),
            lambda row: row.update(model_request_sequence=2),
        )
        for tweak in manipulations:
            self._write_reset()
            trial = self.manifest["assignments"][0]["trial_id"]
            self.edit_signed(trial, tweak)
            with self.subTest(tweak=tweak):
                report = self.check()
                self.assertFalse(report["cross_trial_transport_qualified"])
                self.assertTrue(report["issues"])

    def _write_reset(self):
        for path in self.attestations.glob("*.json"):
            path.unlink()
        for folder in self.bundles.iterdir():
            for p in folder.iterdir():
                p.unlink()
            folder.rmdir()
        self._write_receipts()

    def test_signed_cross_arm_prompt_change_does_not_become_valid(self):
        trial = next(x["trial_id"] for x in self.manifest["assignments"]
                     if x["arm"] == "variant")
        self.edit_signed(trial, lambda row:
                         row["intervention"].update(prompt_sha256=H("different")))
        result = self.check()
        self.assertFalse(result["cross_trial_transport_qualified"])
        self.assertIn("cross-arm-host-or-context-transport-drift", result["issues"])

    def test_invalid_mac_and_missing_or_symlinked_trials(self):
        target = self.attestations / (self.manifest["assignments"][0]["trial_id"] + ".json")
        original = json.loads(target.read_text())
        original["deliveries"][0]["transport"]["response_sha256"] = H("unsanctioned")
        target.write_text(json.dumps(original))
        self.assertIn("trial-missing-independent-signed-provider-submission",
                      self.check()["issues"])
        target.unlink()
        self.assertFalse(self.check()["cross_trial_transport_qualified"])
        target.symlink_to(self.root / "missing")
        self.assertIn("symlinked-trial-source", self.check()["issues"])

    def test_replayed_receipt_from_other_trial_rejected(self):
        cells = self.manifest["assignments"]
        a = self.attestations / (cells[0]["trial_id"] + ".json")
        b = self.attestations / (cells[1]["trial_id"] + ".json")
        b.write_bytes(a.read_bytes())
        self.assertFalse(self.check()["cross_trial_transport_qualified"])

    def test_bad_endpoint_authority_rejected(self):
        with self.assertRaises(ValueError):
            audit_transport_provenance(
                self.manifest, self.bundles, self.attestations, HOST_KEY,
                approved_endpoint_sha256="not-a-digest", expected_host_identity=HOST,
            )


class EmpiricalEvidenceAttacks(HostTransportFixture):
    @staticmethod
    def campaign(pairs):
        return {
            "descriptive_campaign_admitted": True,
            "qualification_blockers": {},
            "independent_review_custodian_claim_authenticated": True,
            "host_intervention_coverage": {
                "coverage_state": "COMPLETE",
                "matched_pairs": pairs,
                "per_harness_descriptive_effect": {"codex": {"state": "INSUFFICIENT_EVIDENCE"}},
            },
        }

    def test_complete_submission_provenance_cannot_qualify_one_task_effect(self):
        rows = [{
            "harness": "codex", "task": "owner",
            "outcome_transition": "FAIL_TO_PASS",
        }]
        with patch(
            "benchmarks.harness.empirical_campaign_decision.qualify_campaign",
            return_value=self.campaign(rows),
        ):
            report = assess_empirical_campaign(
                self.manifest, self.bundles, self.attestations,
                HOST_KEY, _seal(self.manifest), REVIEW_KEY,
                approved_endpoint_sha256=ENDPOINT, expected_host_identity=HOST,
            )
        self.assertEqual(report["observed_control_failures"], 1)
        self.assertFalse(report["submission_bounded_descriptive_population_qualified"])
        self.assertIn("fewer-than-eight-task-clusters-in-harness",
                      report["descriptive_decision_blockers"])
        self.assertFalse(report["causal_hashmarks_improvement_proven"])
        self.assertFalse(report["native_harness_population_qualified"])

    def test_no_headroom_excludes_effect_even_when_all_control_tasks_pass(self):
        rows = [{
            "harness": "codex", "task": "owner",
            "outcome_transition": "PASS_TO_PASS",
        }]
        with patch(
            "benchmarks.harness.empirical_campaign_decision.qualify_campaign",
            return_value=self.campaign(rows),
        ):
            report = assess_empirical_campaign(
                self.manifest, self.bundles, self.attestations,
                HOST_KEY, _seal(self.manifest), REVIEW_KEY,
                approved_endpoint_sha256=ENDPOINT, expected_host_identity=HOST,
            )
        self.assertIn("no-observed-control-failure-headroom",
                      report["descriptive_decision_blockers"])

    def test_incomplete_or_unknown_outcomes_never_become_measurements(self):
        for pairs in ([], [{"harness": "codex", "task": "owner",
                            "outcome_transition": "UNKNOWN"}]):
            with patch(
                "benchmarks.harness.empirical_campaign_decision.qualify_campaign",
                return_value=self.campaign(pairs),
            ):
                report = assess_empirical_campaign(
                    self.manifest, self.bundles, self.attestations,
                    HOST_KEY, _seal(self.manifest), REVIEW_KEY,
                    approved_endpoint_sha256=ENDPOINT, expected_host_identity=HOST,
                )
            self.assertFalse(report["submission_bounded_descriptive_population_qualified"])
            self.assertEqual(report["graded_pairs"], 0)


if __name__ == "__main__":
    unittest.main()
