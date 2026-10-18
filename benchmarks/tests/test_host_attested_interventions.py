"""Adversarial E233-E236 tests: no false host delivery or intervention effect."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.evidence_lifecycle import project_evidence_lifecycle
from benchmarks.harness.host_input_attestation import (
    SCHEMA as HOST_SCHEMA, _bound_mac, canonical,
    load_host_key, verify_host_attestations,
)
from benchmarks.harness.intervention_audit import (
    SCHEMA as DESIGN_SCHEMA, audit_intervention_bundles,
    audit_intervention_observations, validate_design,
)

KEY = b"external-host-key-not-exposed-to-agent-42!!"
D = lambda n: hashlib.sha256(str(n).encode()).hexdigest()


def _atif() -> dict:
    return {
        "schema_version": "ATIF-v1.8",
        "steps": [{
            "source": "agent",
            "message": "I used the tool; I received its message.",
            "tool_calls": [{
                "tool_call_id": "t1",
                "function_name": "mcp__hashmarks__task_evidence",
                "arguments": {},
            }],
            "observation": {"results": [{
                "source_call_id": "t1",
                "content": {"owner": "src/owner.py"},
            }]},
        }],
    }


def _design(study: str = "presentation", tasks: list[str] | None = None) -> dict:
    return {
        "schema": DESIGN_SCHEMA, "study": study, "campaign_id": "C",
        "model": "provider/model", "harnesses": ["codex"],
        "tasks": tasks or ["owner"], "replicates": 1,
        "arms": (
            {"control": "structured", "variant": "text"}
            if study == "presentation" else {
                "control": "current-generation",
                "variant": "replaced-generation",
            } if study == "freshness" else {
                "control": "direct-first-request",
                "variant": "deferred-second-request",
            }
        ),
    }


def _facets(study: str, arm: str, design: dict) -> dict:
    common = {
        "study": study, "arm": arm,
        "design_sha256": hashlib.sha256(canonical(design)).hexdigest(),
        "current_generation_sha256": D("current"), "catalog_sha256": D("catalog"),
        "prompt_sha256": D("prompt"), "oracle_sha256": D("oracle"),
        "workspace_sha256": D("workspace"), "surface_sha256": D(arm),
    }
    if study == "presentation":
        common.update({
            "presentation": "structured" if arm == "control" else "text",
            "generation_sha256": D("current"), "semantic_sha256": D("same"),
        })
    elif study == "freshness":
        common.update({
            "presentation": "structured",
            "generation_sha256": D("current" if arm == "control" else "stale"),
            "semantic_sha256": D("current-answer" if arm == "control" else "old-answer"),
        })
    else:
        common.update({
            "presentation": "structured",
            "generation_sha256": D("current"),
            "semantic_sha256": D("same-timing-claims"),
            "surface_sha256": D("identical-timing-payload"),
            "delivery_request_sequence": 1 if arm == "control" else 2,
            "prior_request_sha256": "0" * 64 if arm == "control" else D("tool-free-prior"),
            "prior_transport_sha256": "0" * 64 if arm == "control" else D("prior-http-2xx"),
        })
    return common


def _observations(design: dict) -> list[dict]:
    return [{
        "harness": h, "task": t, "replicate": r, "arm": arm,
        "status": "PASS" if arm == "variant" else "FAIL",
        "model": design["model"], "host_attested": True,
        "source_contract_identity": "sha256:frozen-hashmarks-source",
        "intervention": _facets(design["study"], arm, design),
    } for h in design["harnesses"] for t in design["tasks"]
      for r in range(1, design["replicates"] + 1)
      for arm in ("control", "variant")]


class HostInputBoundaryAttacks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.trajectory = root / "trajectory.json"
        self.attestation = root / "host-receipt.json"
        self.trajectory.write_text(json.dumps(_atif()), encoding="utf-8")
        self.packet = project_evidence_lifecycle(self.trajectory)["packet_refs"][0]
        self.context = {
            "schema": HOST_SCHEMA,
            "campaign_id": "C", "trial_id": "trial-1",
            "trajectory_sha256": hashlib.sha256(self.trajectory.read_bytes()).hexdigest(),
        }
        self.delivery = {
            "host_identity": "codex-external-host",
            "host_build_sha256": D("host"),
            "model_request_sha256": D("request"),
            "model_input_sha256": D("input"),
            "model_message_sha256": D("message"),
            "packet_sha256": self.packet["packet_sha256"],
            "call_id_sha256": self.packet["call_id_sha256"],
            "model_request_sequence": 2,
            "boundary": "host-model-request-input",
        }
        self.write()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, delivery: dict | None = None, *, context: dict | None = None) -> None:
        context = context or self.context
        delivery = delivery or self.delivery
        body = dict(delivery)
        body["mac_sha256"] = _bound_mac(KEY, context, delivery)
        self.attestation.write_text(json.dumps({
            **context, "deliveries": [body],
        }), encoding="utf-8")

    def check(self, **kwargs: object) -> dict:
        return verify_host_attestations(
            self.trajectory, self.attestation, KEY,
            campaign_id=kwargs.get("campaign_id", "C"),
            trial_id=kwargs.get("trial_id", "trial-1"),
        )

    def test_host_key_holder_attestation_can_bound_delivery_not_attention(self) -> None:
        report = self.check()
        self.assertEqual(report["attestation_state"], "VERIFIED")
        self.assertEqual(report["delivery_state"], "PROVEN")
        self.assertEqual(report["verified_model_input_packets"], 1)
        self.assertFalse(report["model_attention_proven"])
        self.assertFalse(report["provider_consumption_proven"])
        self.assertFalse(report["causal_influence_proven"])
        self.assertNotIn("src/owner.py", json.dumps(report))

    def test_atif_prose_and_untrusted_embedded_receipt_never_upgrade_delivery(self) -> None:
        base = project_evidence_lifecycle(self.trajectory)
        self.assertEqual(base["delivery_state"], "UNKNOWN")
        payload = _atif()
        payload["host_model_delivery_attested"] = True
        self.trajectory.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(project_evidence_lifecycle(self.trajectory)["delivery_state"], "UNKNOWN")
        self.assertEqual(self.check()["reason"], "stale-or-foreign-attestation")

    def test_tamper_mac_packet_request_or_trial_cannot_upgrade(self) -> None:
        doc = json.loads(self.attestation.read_text())
        for field, value in (
            ("packet_sha256", D("wrong")),
            ("model_input_sha256", D("tampered-input")),
            ("mac_sha256", D("forged")),
            ("boundary", "tool-return"),
        ):
            forged = copy.deepcopy(doc)
            forged["deliveries"][0][field] = value
            self.attestation.write_text(json.dumps(forged), encoding="utf-8")
            with self.subTest(field=field):
                self.assertEqual(self.check()["attestation_state"], "REJECTED")
        self.write()
        self.assertEqual(self.check(trial_id="another")["delivery_state"], "UNKNOWN")

    def test_other_signed_packet_and_duplicate_request_rejected(self) -> None:
        forged = dict(self.delivery, packet_sha256=D("not-a-real-packet"))
        self.write(forged)
        self.assertEqual(self.check()["reason"], "packet-not-in-immutable-trace")
        self.write()
        doc = json.loads(self.attestation.read_text())
        doc["deliveries"].append(copy.deepcopy(doc["deliveries"][0]))
        self.attestation.write_text(json.dumps(doc))
        self.assertEqual(self.check()["reason"], "duplicate-model-input-binding")

    def test_wrong_host_key_and_untrusted_key_file_rejected(self) -> None:
        self.assertEqual(verify_host_attestations(
            self.trajectory, self.attestation, b"another-super-secret-host-key-holder!",
            campaign_id="C", trial_id="trial-1",
        )["attestation_state"], "REJECTED")
        key_path = Path(self.tmp.name) / "key"
        key_path.write_bytes(KEY)
        os.chmod(key_path, 0o600)
        self.assertEqual(load_host_key(key_path), KEY)
        os.chmod(key_path, 0o644)
        with self.assertRaises(ValueError):
            load_host_key(key_path)
        os.chmod(key_path, 0o600)
        alias = key_path.with_suffix(".link")
        alias.symlink_to(key_path)
        with self.assertRaises(ValueError):
            load_host_key(alias)

    def test_exact_host_signed_intervention_facets(self) -> None:
        design = _design()
        bound = dict(self.delivery, intervention=_facets("presentation", "control", design))
        self.write(bound)
        self.assertEqual(self.check()["delivery_state"], "PROVEN")
        doc = json.loads(self.attestation.read_text())
        doc["deliveries"][0]["intervention"]["presentation"] = "text"
        self.attestation.write_text(json.dumps(doc))
        self.assertEqual(self.check()["attestation_state"], "REJECTED")


class PresentationAndFreshnessInterventionAttacks(unittest.TestCase):
    def test_presentation_isolation_and_descriptive_only(self) -> None:
        design = _design()
        report = audit_intervention_observations(design, _observations(design))
        self.assertEqual(report["coverage_state"], "COMPLETE")
        self.assertEqual(report["qualified_matched_pairs"], 1)
        self.assertEqual(report["matched_pairs"][0]["observed_variant_minus_control"], 1)
        self.assertFalse(report["causal_effect_qualified"])
        self.assertFalse(report["randomization_or_pre_registration_attested"])
        self.assertEqual(report["per_harness_descriptive_effect"]["codex"]["state"],
                         "INSUFFICIENT_EVIDENCE")

    def test_freshness_checks_generation_change_and_target_identity(self) -> None:
        design = _design("freshness")
        rows = _observations(design)
        self.assertEqual(audit_intervention_observations(
            design, rows)["coverage_state"], "COMPLETE")
        changed = copy.deepcopy(rows)
        changed[1]["intervention"]["generation_sha256"] = D("current")
        invalid = audit_intervention_observations(design, changed)
        self.assertIn("freshness-intervention-not-isolated", invalid["issues"])

    def test_cannot_conflate_parity_with_controlled_presentation(self) -> None:
        design = _design()
        rows = _observations(design)
        rows[1]["intervention"]["surface_sha256"] = rows[0]["intervention"]["surface_sha256"]
        invalid = audit_intervention_observations(design, rows)
        self.assertIn("identical-model-input-surface", invalid["issues"])
        rows = _observations(design)
        rows[1]["intervention"]["semantic_sha256"] = D("different")
        self.assertIn("presentation-intervention-not-isolated",
                      audit_intervention_observations(design, rows)["issues"])

    def test_prompt_catalog_oracle_workspace_are_invariants(self) -> None:
        design = _design()
        for facet in ("prompt_sha256", "catalog_sha256", "oracle_sha256",
                      "workspace_sha256", "current_generation_sha256"):
            rows = _observations(design)
            rows[1]["intervention"][facet] = D("different")
            with self.subTest(facet=facet):
                self.assertEqual(audit_intervention_observations(
                    design, rows)["coverage_state"], "INCOMPLETE")

    def test_timing_study_reports_real_extra_request_confound_not_pure_delay(self) -> None:
        design = _design("timing")
        report = audit_intervention_observations(design, _observations(design))
        self.assertEqual(report["coverage_state"], "COMPLETE")
        self.assertEqual(report["qualified_matched_pairs"], 1)
        self.assertTrue(report["timing_delivery_extra_request_confound"])
        self.assertFalse(report["causal_effect_qualified"])
        self.assertFalse(report["randomization_or_pre_registration_attested"])
        self.assertFalse(report["model_attention_proven"])
        for facet, value in (
            ("delivery_request_sequence", 1),
            ("prior_request_sha256", "0" * 64),
            ("prior_transport_sha256", "0" * 64),
            ("semantic_sha256", D("another-semantic-content")),
            ("surface_sha256", D("another-visible-payload")),
            ("prompt_sha256", D("another-prompt")),
        ):
            altered = copy.deepcopy(_observations(design))
            altered[1]["intervention"][facet] = value
            report = audit_intervention_observations(design, altered)
            with self.subTest(field=facet):
                self.assertEqual(report["coverage_state"], "INCOMPLETE")
                self.assertFalse(report["causal_effect_qualified"])
        with self.assertRaisesRegex(ValueError, "timing-arms"):
            validate_design({
                **design, "arms": {
                    "control": "deferred-second-request", "variant": "direct-first-request",
                },
            })

    def test_source_identity_must_remain_constant_across_matched_arms(self) -> None:
        design = _design()
        rows = _observations(design)
        rows[1]["source_contract_identity"] = "sha256:changed"
        report = audit_intervention_observations(design, rows)
        self.assertEqual(report["qualified_matched_pairs"], 0)
        self.assertIn("cross-arm-source-contract-drift", report["issues"])

    def test_malformed_facet_does_not_crash_or_qualify(self) -> None:
        design = _design()
        rows = _observations(design)
        rows[0]["intervention"]["surface_sha256"] = []
        report = audit_intervention_observations(design, rows)
        self.assertEqual(report["coverage_state"], "INCOMPLETE")
        self.assertIn("host-intervention-design-unbound", report["issues"])
        rows = _observations(design)
        del rows[1]["intervention"]["surface_sha256"]
        report = audit_intervention_observations(design, rows)
        self.assertEqual(report["qualified_matched_pairs"], 0)

    def test_missing_duplicate_foreign_and_unattested_are_excluded(self) -> None:
        design = _design()
        rows = _observations(design)
        self.assertIn("missing-cell", audit_intervention_observations(
            design, rows[:1])["issues"])
        self.assertIn("duplicate-cell", audit_intervention_observations(
            design, rows + [copy.deepcopy(rows[0])])["issues"])
        foreign = copy.deepcopy(rows)
        foreign[0]["task"] = "other-task"
        self.assertIn("foreign-or-unexpected-observation",
                      audit_intervention_observations(design, foreign)["issues"])
        unattested = copy.deepcopy(rows)
        unattested[1]["host_attested"] = False
        self.assertEqual(audit_intervention_observations(
            design, unattested)["qualified_matched_pairs"], 0)
        self.assertIn("host-boundary-not-attested", audit_intervention_observations(
            design, unattested)["issues"])

    def test_design_freeze_and_many_task_clusters_remain_descriptive(self) -> None:
        plan = _design(tasks=[f"task-{i}" for i in range(9)])
        report = audit_intervention_observations(plan, _observations(plan))
        self.assertEqual(report["coverage_state"], "COMPLETE")
        self.assertEqual(report["per_harness_descriptive_effect"]["codex"]["state"],
                         "DESCRIPTIVE_INTERVAL")
        self.assertFalse(report["causal_effect_qualified"])
        other = _observations(plan)
        other[0]["intervention"]["design_sha256"] = D("mutable-design")
        self.assertIn("host-intervention-design-unbound",
                      audit_intervention_observations(plan, other)["issues"])
        with self.assertRaises(ValueError):
            validate_design(dict(plan, arms={"control": "text", "variant": "structured"}))
        with self.assertRaises(ValueError):
            validate_design(dict(plan, replicates=True))

    def test_live_bundle_admission_rejects_missing_host_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "run").mkdir()
            (base / "host").mkdir()
            # A checked-in synthetic Harbor receipt cannot substitute for a
            # MAC-verified host input receipt, even when the bundle is valid.
            (base / "run" / "trial-a").mkdir()
            with patch("benchmarks.harness.intervention_audit.load_harbor_bundle_projection") as mocked:
                mocked.return_value = {
                    "receipt": {
                        "execution": {
                            "campaign_id": "C",
                            "mcp_treatment": {
                                "full_contract": True,
                                "source_contract_identity": "sha256:frozen-hashmarks-source",
                            },
                        }, "subject": "hashmarks",
                        "harness": "codex", "task_id": "owner",
                        "replicate_id": 1, "model": "provider/model",
                        "status": "PASS",
                    },
                }
                report = audit_intervention_bundles(
                    _design(), base / "run", base / "host", KEY,
                )
            self.assertEqual(report["coverage_state"], "INCOMPLETE")
            self.assertIn("corrupt-or-unverifiable-bundle", report["issues"])
            self.assertFalse(report["causal_effect_qualified"])


if __name__ == "__main__":
    unittest.main()
