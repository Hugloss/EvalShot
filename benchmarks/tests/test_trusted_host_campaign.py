"""Attack E237-E240 frozen assignments, host captures and campaign admission.

All runs are synthetic and model-free: no provider dispatch, independent
reviewer, real benchmark, or trustworthy timestamp is claimed by these tests.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.host_input_attestation import (
    canonical, verify_host_attestations,
)
from benchmarks.harness.independent_campaign_qualification import (
    DOMAIN, SCHEMA as SEAL_SCHEMA, qualify_campaign,
    validate_trial_alignment, verify_seal,
)
from benchmarks.harness.intervention_audit import (
    audit_intervention_bundles,
)
from benchmarks.harness.trusted_host_capture import TrustedModelRequestCapture
from benchmarks.harness.trusted_treatments import (
    assigned_cell, build_manifest, freeze_manifest,
    load_manifest, select_treatment, sha, verify_manifest,
)

HOST_KEY = b"simulated-external-host-key-ABC-123-456-789"
REVIEW_KEY = b"separate-reviewer-custodian-key-XYZ-123-456"
H = lambda v: hashlib.sha256(str(v).encode("utf-8")).hexdigest()


def design(study: str = "presentation") -> dict:
    return {
        "schema": "agentscookbook.host-attested-intervention-design.v1",
        "study": study, "campaign_id": "trusted-host-test-campaign",
        "model": "provider/model", "harnesses": ["codex"],
        "tasks": ["owner"], "replicates": 1,
        "arms": (
            {"control": "structured", "variant": "text"}
            if study == "presentation"
            else {"control": "current-generation", "variant": "replaced-generation"}
            if study == "freshness"
            else {"control": "direct-first-request", "variant": "deferred-second-request"}
        ),
    }


def plan(study: str = "presentation") -> dict:
    return build_manifest(
        design(study),
        source_contract_identity="sha256:frozen-hashmarks-runtime",
        host_build_sha256=H("frozen-host-build"),
        assignment_seed_sha256=H("declared-not-random-seed"),
    )


def source(label: str = "current") -> dict:
    return {
        "generation_sha256": H(label),
        "semantic_sha256": sha({"owner": "src/owner.py" if label == "current" else "src/old.py"}),
        "content": {"owner": "src/owner.py" if label == "current" else "src/old.py"},
    }


def _atif(packet: object) -> dict:
    return {
        "schema_version": "ATIF-v1.8",
        "steps": [{
            "source": "agent",
            "tool_calls": [{
                "tool_call_id": "t1", "function_name": "mcp__hashmarks__task_evidence",
                "arguments": {},
            }],
            "observation": {"results": [{
                "source_call_id": "t1", "content": packet,
            }]},
        }],
    }


def _request(content: object) -> tuple[bytes, str]:
    request = {
        "model": "provider/model", "temperature": 0,
        "messages": [
            {"role": "system", "content": "Find source ownership"},
            {"role": "tool", "tool_call_id": "t1", "content": content},
        ],
    }
    prompt = {
        "request_settings": {k: v for k, v in request.items() if k != "messages"},
        "non_tool_messages": request["messages"][:1],
    }
    return canonical(request), hashlib.sha256(canonical(prompt)).hexdigest()


def _seal(manifest: dict, *, reviewer: bool = True, prework: bool = True,
          key: bytes = REVIEW_KEY) -> dict:
    body = {
        "schema": SEAL_SCHEMA,
        "campaign_id": manifest["design"]["campaign_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "design_sha256": manifest["design_sha256"],
        "authority_id": "independent-reviewer-owned-not-agent",
        "review_corpus_sha256": H("independent-case-review-corpus"),
        "independent_oracle_review_completed": reviewer,
        "pre_work_frozen": prework,
        "external_anchor_sha256": H("opaque-external-anchor"),
    }
    return {**body, "mac_sha256": hmac.new(
        key, DOMAIN + canonical(body), hashlib.sha256,
    ).hexdigest()}


class FrozenTreatmentsTest(unittest.TestCase):
    def test_complete_deterministic_two_arm_assignments_and_no_redecision(self) -> None:
        m = plan()
        self.assertEqual(m, verify_manifest(m))
        self.assertEqual(m, plan())
        self.assertEqual(len(m["assignments"]), 2)
        self.assertEqual({x["arm"] for x in m["assignments"]}, {"control", "variant"})
        self.assertEqual({x["execution_ordinal"] for x in m["assignments"]}, {1, 2})
        for cell in m["assignments"]:
            self.assertEqual(assigned_cell(
                m, trial_id=cell["trial_id"], harness=cell["harness"],
                task=cell["task"], replicate=cell["replicate"], arm=cell["arm"],
            ), cell)
        changed = copy.deepcopy(m)
        changed["assignments"][0]["arm"] = (
            "variant" if changed["assignments"][0]["arm"] == "control" else "control"
        )
        with self.assertRaises(ValueError):
            verify_manifest(changed)
        with self.assertRaises(ValueError):
            assigned_cell(m, trial_id=m["assignments"][0]["trial_id"],
                          harness="other", task="owner", replicate=1,
                          arm=m["assignments"][0]["arm"])

    def test_presentation_is_semantically_equal_but_not_same_surface(self) -> None:
        m = plan()
        rows = {
            arm: select_treatment(
                m, trial_id=next(x["trial_id"] for x in m["assignments"]
                                 if x["arm"] == arm),
                harness="codex", task="owner", replicate=1, arm=arm,
                current=source(),
            ) for arm in ("control", "variant")
        }
        self.assertEqual(rows["control"]["selected_content"],
                         json.loads(rows["variant"]["selected_content"]))
        self.assertNotEqual(rows["control"]["surface_sha256"],
                            rows["variant"]["surface_sha256"])
        self.assertEqual(rows["control"]["semantic_sha256"], rows["variant"]["semantic_sha256"])
        self.assertFalse(rows["control"]["selection_is_host_delivery_proof"])

    def test_freshness_requires_explicit_real_source_distinction(self) -> None:
        m = plan("freshness")
        results = {}
        for arm in ("control", "variant"):
            results[arm] = select_treatment(
                m, trial_id=next(x["trial_id"] for x in m["assignments"]
                                 if x["arm"] == arm),
                harness="codex", task="owner", replicate=1, arm=arm,
                current=source(), replaced=source("old"),
            )
        self.assertNotEqual(results["control"]["generation_sha256"],
                            results["variant"]["generation_sha256"])
        self.assertEqual(results["control"]["current_generation_sha256"],
                         results["variant"]["current_generation_sha256"])
        with self.assertRaises(ValueError):
            select_treatment(
                m, trial_id=next(x["trial_id"] for x in m["assignments"]
                                 if x["arm"] == "variant"),
                harness="codex", task="owner", replicate=1, arm="variant",
                current=source(), replaced=source(),
            )

    def test_semantic_source_content_digest_cannot_be_forged(self) -> None:
        manifest = plan()
        cell = next(x for x in manifest["assignments"] if x["arm"] == "control")
        forged = source()
        forged["semantic_sha256"] = H("unrelated")
        with self.assertRaisesRegex(ValueError, "semantic-source-digest"):
            select_treatment(
                manifest, trial_id=cell["trial_id"], harness="codex",
                task="owner", replicate=1, arm="control", current=forged,
            )

    def test_create_only_authority_and_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "plan.json"
            freeze_manifest(path, plan())
            self.assertEqual(load_manifest(path), plan())
            with self.assertRaises(FileExistsError):
                freeze_manifest(path, plan("freshness"))
            modified = json.loads(path.read_text())
            modified["manifest_sha256"] = H("forgery")
            path.write_text(json.dumps(modified))
            with self.assertRaises(ValueError):
                load_manifest(path)


class TrustedHostInputIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.key_path = self.root / "host.key"
        self.key_path.write_bytes(HOST_KEY)
        os.chmod(self.key_path, 0o600)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _capture(self, cell: dict, *, study: str = "presentation",
                 altered: str | None = None) -> tuple[Path, Path]:
        m = plan(study)
        host = TrustedModelRequestCapture(
            m, trial_id=cell["trial_id"], harness="codex", task="owner",
            replicate=1, arm=cell["arm"], host_identity="trusted-provider-host",
            host_key_file=self.key_path,
        )
        current = source()
        replaced = source("old") if study == "freshness" else None
        chosen = select_treatment(
            m, trial_id=cell["trial_id"], harness="codex", task="owner",
            replicate=1, arm=cell["arm"], current=current, replaced=replaced,
        )
        req, prompt = _request(chosen["selected_content"])
        if altered == "wrong-model":
            document = json.loads(req)
            document["model"] = "other/model"
            req = canonical(document)
        if altered == "no-tool-message":
            document = json.loads(req)
            document["messages"].pop()
            req = canonical(document)
        if altered == "wrong-prompt":
            prompt = H("wrong-prompt")
        if altered == "wrong-source":
            current = {**current, "content": {"owner": "src/forged.py"}}
        if altered is None:
            result = host.observe_outbound_request(
                serialized_model_request=req, tool_call_id="t1",
                returned_packet=source()["content"], request_sequence=1,
                current=current, replaced=replaced, catalog_sha256=H("catalog"),
                prompt_sha256=prompt, oracle_sha256=H("oracle"),
                workspace_sha256=H("workspace"),
            )
            self.assertEqual(result["host_request_observed"], "true")
        else:
            with self.assertRaises(ValueError):
                host.observe_outbound_request(
                    serialized_model_request=req, tool_call_id="t1",
                    returned_packet=source()["content"], request_sequence=1,
                    current=current, replaced=replaced, catalog_sha256=H("catalog"),
                    prompt_sha256=prompt, oracle_sha256=H("oracle"),
                    workspace_sha256=H("workspace"),
                )
            return self.root / "nonexistent", self.root / "nonexistent"
        trajectory = self.root / (cell["trial_id"] + "-atif.json")
        trajectory.write_text(json.dumps(_atif(source()["content"])), encoding="utf-8")
        output = self.root / (cell["trial_id"] + ".json")
        result = host.finalize(trajectory=trajectory, output=output)
        self.assertFalse(result["provider_consumption_proven"])
        return trajectory, output

    def test_capture_integrates_frozen_assignment_real_request_shape_and_verifier(self) -> None:
        cell = next(x for x in plan()["assignments"] if x["arm"] == "variant")
        trajectory, receipt = self._capture(cell)
        proof = verify_host_attestations(
            trajectory, receipt, HOST_KEY,
            campaign_id=plan()["design"]["campaign_id"],
            trial_id=cell["trial_id"],
        )
        self.assertEqual(proof["delivery_state"], "PROVEN")
        self.assertFalse(proof["model_attention_proven"])
        changed = json.loads(trajectory.read_text())
        changed["steps"][0]["observation"]["results"][0]["content"] = {
            "owner": "src/changed.py",
        }
        trajectory.write_text(json.dumps(changed))
        self.assertEqual(verify_host_attestations(
            trajectory, receipt, HOST_KEY,
            campaign_id=plan()["design"]["campaign_id"],
            trial_id=cell["trial_id"],
        )["delivery_state"], "UNKNOWN")

    def test_foreign_provider_request_and_wrong_source_fail_before_signing(self) -> None:
        cell = next(x for x in plan()["assignments"] if x["arm"] == "control")
        for reason in ("wrong-model", "no-tool-message", "wrong-prompt", "wrong-source"):
            with self.subTest(reason=reason):
                self._capture(cell, altered=reason)

    def test_freshness_variant_selects_replaced_source_but_binds_current_return(self) -> None:
        cell = next(x for x in plan("freshness")["assignments"] if x["arm"] == "variant")
        trajectory, receipt = self._capture(cell, study="freshness")
        report = verify_host_attestations(
            trajectory, receipt, HOST_KEY,
            campaign_id=plan("freshness")["design"]["campaign_id"],
            trial_id=cell["trial_id"],
        )
        self.assertEqual(report["delivery_state"], "PROVEN")
        envelope = json.loads(receipt.read_text())
        self.assertNotEqual(
            envelope["deliveries"][0]["intervention"]["generation_sha256"],
            envelope["deliveries"][0]["intervention"]["current_generation_sha256"],
        )

    def test_finalizing_without_observed_request_or_matching_trace_refuses(self) -> None:
        m = plan()
        cell = m["assignments"][0]
        host = TrustedModelRequestCapture(
            m, trial_id=cell["trial_id"], harness="codex", task="owner",
            replicate=1, arm=cell["arm"], host_identity="trusted-provider-host",
            host_key_file=self.key_path,
        )
        trajectory = self.root / "empty.json"
        trajectory.write_text(json.dumps(_atif(source()["content"])))
        with self.assertRaises(ValueError):
            host.finalize(trajectory=trajectory, output=self.root / "receipt.json")



class TrustedTimingTreatmentTest(TrustedHostInputIntegrationTest):
    def _timing_run(
        self, *, arm: str, altered: str | None = None,
    ) -> tuple[Path, Path]:
        manifest = plan("timing")
        cell = next(x for x in manifest["assignments"] if x["arm"] == arm)
        host = TrustedModelRequestCapture(
            manifest, trial_id=cell["trial_id"], harness="codex", task="owner",
            replicate=1, arm=arm, host_identity="trusted-timing-provider-host",
            host_key_file=self.key_path,
        )
        chosen = select_treatment(
            manifest, trial_id=cell["trial_id"], harness="codex",
            task="owner", replicate=1, arm=arm, current=source(),
        )
        self.assertEqual(chosen["required_request_sequence"], 1 if arm == "control" else 2)
        if arm == "variant":
            pre_request, _ = _request(chosen["selected_content"])
            doc = json.loads(pre_request)
            doc["messages"] = doc["messages"][:1]
            if altered == "prior-with-tool":
                doc["messages"].append({"role": "tool", "tool_call_id": "t1", "content": source()["content"]})
            if altered == "prior-different-prompt":
                doc["messages"][0]["content"] = "Unrelated question"
            prior = canonical(doc)
            if altered != "missing-prior":
                if altered == "prior-with-tool":
                    with self.assertRaisesRegex(ValueError, "tool-free"):
                        host.observe_pre_exposure_request(
                            serialized_model_request=prior, request_sequence=1,
                        )
                    return self.root / "missing", self.root / "missing"
                host.observe_pre_exposure_request(
                    serialized_model_request=prior, request_sequence=1,
                )
                if altered != "unsubmitted-prior":
                    host.begin_pre_exposure_transport()
                    host.record_pre_exposure_submission(
                        serialized_model_request=prior, endpoint_sha256=H("endpoint"),
                        status=200, response_sha256=H("response-prior"),
                        response_bytes=101,
                    )
                    if altered == "retry-prior":
                        with self.assertRaisesRegex(ValueError, "retried"):
                            host.record_pre_exposure_submission(
                                serialized_model_request=prior,
                                endpoint_sha256=H("endpoint"),
                                status=200, response_sha256=H("response-prior"),
                                response_bytes=101,
                            )
                        return self.root / "missing", self.root / "missing"
        req, prompt = _request(chosen["selected_content"])
        if altered == "wrong-delivery-sequence":
            sequence = 1 if arm == "variant" else 2
        else:
            sequence = chosen["required_request_sequence"]
        kwargs = {
            "serialized_model_request": req, "tool_call_id": "t1",
            "returned_packet": source()["content"], "request_sequence": sequence,
            "current": source(), "catalog_sha256": H("catalog"),
            "prompt_sha256": prompt, "oracle_sha256": H("oracle"),
            "workspace_sha256": H("workspace"),
        }
        if altered in {
            "missing-prior", "unsubmitted-prior", "wrong-delivery-sequence",
            "prior-different-prompt",
        }:
            with self.assertRaises(ValueError):
                host.observe_outbound_request(**kwargs)
            return self.root / "missing", self.root / "missing"
        host.observe_outbound_request(**kwargs)
        trajectory = self.root / (cell["trial_id"] + "-timing-atif.json")
        trajectory.write_text(json.dumps(_atif(source()["content"])))
        output = self.root / (cell["trial_id"] + "-timing.json")
        if altered == "no-final-submission":
            with self.assertRaisesRegex(ValueError, "timing-study-provider-submission-incomplete"):
                host.finalize(trajectory=trajectory, output=output)
            return trajectory, output
        host.begin_provider_transport()
        host.record_provider_submission(
            tool_call_id="t1", serialized_model_request=req,
            endpoint_sha256=H("endpoint"), status=200,
            response_sha256=H("response-final"), response_bytes=100,
        )
        finished = host.finalize(trajectory=trajectory, output=output)
        self.assertTrue(finished["provider_submission_attested"])
        return trajectory, output

    def test_direct_and_deferred_are_frozen_same_semantic_source(self) -> None:
        manifest = plan("timing")
        self.assertEqual(manifest, verify_manifest(manifest))
        selections = {}
        for cell in manifest["assignments"]:
            selections[cell["arm"]] = select_treatment(
                manifest, trial_id=cell["trial_id"], harness="codex",
                task="owner", replicate=1, arm=cell["arm"], current=source(),
            )
        self.assertEqual(
            selections["control"]["surface_sha256"],
            selections["variant"]["surface_sha256"],
        )
        self.assertTrue(selections["variant"]["timing_extra_model_request_confound"])
        for arm in ("control", "variant"):
            trajectory, output = self._timing_run(arm=arm)
            cell = next(x for x in manifest["assignments"] if x["arm"] == arm)
            verified = verify_host_attestations(
                trajectory, output, HOST_KEY,
                campaign_id=manifest["design"]["campaign_id"],
                trial_id=cell["trial_id"],
            )
            self.assertEqual(verified["delivery_state"], "PROVEN")
            self.assertEqual(verified["provider_submission_state"], "SUBMITTED")
            self.assertFalse(verified["causal_influence_proven"])
            delivery = json.loads(output.read_text())["deliveries"][0]
            self.assertEqual(
                delivery["intervention"]["delivery_request_sequence"],
                1 if arm == "control" else 2,
            )
            prior = delivery["intervention"]["prior_transport_sha256"]
            self.assertEqual(prior == "0" * 64, arm == "control")

    def test_deferred_requires_verified_tool_free_prior_and_matching_prompt(self) -> None:
        for altered in (
            "missing-prior", "unsubmitted-prior", "prior-with-tool",
            "prior-different-prompt", "wrong-delivery-sequence", "retry-prior",
        ):
            with self.subTest(altered=altered):
                self._timing_run(arm="variant", altered=altered)

    def test_direct_denies_second_sequence_and_finalize_requires_submission(self) -> None:
        self._timing_run(arm="control", altered="wrong-delivery-sequence")
        self._timing_run(arm="control", altered="no-final-submission")

    def test_signed_timing_receipt_rejects_modified_stage_and_prior_proof(self) -> None:
        manifest = plan("timing")
        cell = next(x for x in manifest["assignments"] if x["arm"] == "variant")
        trajectory, output = self._timing_run(arm="variant")
        original = json.loads(output.read_text())
        for field, value in (
            ("delivery_request_sequence", 1),
            ("prior_request_sha256", "0" * 64),
            ("prior_transport_sha256", H("forged-prior-transport")),
        ):
            with self.subTest(field=field):
                receipt = copy.deepcopy(original)
                receipt["deliveries"][0]["intervention"][field] = value
                output.write_text(json.dumps(receipt))
                result = verify_host_attestations(
                    trajectory, output, HOST_KEY,
                    campaign_id=manifest["design"]["campaign_id"],
                    trial_id=cell["trial_id"],
                )
                self.assertEqual(result["attestation_state"], "REJECTED")
        output.write_text(json.dumps(original))

class IndependentCampaignQualificationTest(TrustedHostInputIntegrationTest):
    def test_all_bound_cells_and_separate_custodian_claim_enable_descriptive_only(self) -> None:
        m = plan()
        bundles = self.root / "bundles"
        attestations = self.root / "attestations"
        bundles.mkdir()
        attestations.mkdir()
        projections = {}
        host_fixture = TrustedHostInputIntegrationTest._capture
        for cell in m["assignments"]:
            (bundles / cell["trial_id"]).mkdir()
            trajectory, output = host_fixture(self, cell)
            (bundles / cell["trial_id"] / "trajectory.json").write_bytes(trajectory.read_bytes())
            (attestations / (cell["trial_id"] + ".json")).write_bytes(output.read_bytes())
            projections[cell["trial_id"]] = {
                "receipt": {
                    "task_id": cell["task"], "harness": cell["harness"],
                    "replicate_id": cell["replicate"], "subject": "hashmarks",
                    "model": m["design"]["model"],
                    "status": "PASS" if cell["arm"] == "variant" else "FAIL",
                    "execution": {
                        "campaign_id": m["design"]["campaign_id"],
                        "mcp_treatment": {
                            "full_contract": True,
                            "source_contract_identity": m["source_contract_identity"],
                        },
                    },
                },
            }
        projector = lambda path: projections[path.name]
        with patch(
            "benchmarks.harness.intervention_audit.load_harbor_bundle_projection",
            side_effect=projector,
        ), patch(
            "benchmarks.harness.independent_campaign_qualification.load_harbor_bundle_projection",
            side_effect=projector,
        ):
            audit = audit_intervention_bundles(
                m["design"], bundles, attestations, HOST_KEY,
            )
            self.assertEqual(audit["coverage_state"], "COMPLETE")
            self.assertEqual(audit["qualified_matched_pairs"], 1)
            alignment = validate_trial_alignment(m, bundles, attestations)
            self.assertTrue(alignment["complete"])
            pending = qualify_campaign(
                m, bundles, attestations, HOST_KEY, None, independent_key=None,
            )
            self.assertFalse(pending["descriptive_campaign_admitted"])
            self.assertIn("independent-authority-seal-unavailable",
                          pending["qualification_blockers"])
            qualified = qualify_campaign(
                m, bundles, attestations, HOST_KEY, _seal(m),
                independent_key=REVIEW_KEY,
            )
            self.assertTrue(qualified["descriptive_campaign_admitted"])
            self.assertFalse(qualified["trusted_pre_work_timestamp_proven"])
            self.assertFalse(qualified["real_randomization_proven"])
            self.assertFalse(qualified["causal_effect_proven"])
            self.assertTrue(qualified["independent_seal_authenticated"])
            unreviewed = qualify_campaign(
                m, bundles, attestations, HOST_KEY, _seal(m, reviewer=False),
                independent_key=REVIEW_KEY,
            )
            self.assertIn("independent-review-not-approved",
                          unreviewed["qualification_blockers"])
            conflated = qualify_campaign(
                m, bundles, attestations, HOST_KEY, _seal(m, key=HOST_KEY),
                independent_key=HOST_KEY,
            )
            self.assertFalse(conflated["descriptive_campaign_admitted"])
            self.assertIn("host-and-independent-authority-keys-must-differ",
                          conflated["qualification_blockers"])
            forged = _seal(m)
            forged["pre_work_frozen"] = False
            self.assertFalse(qualify_campaign(
                m, bundles, attestations, HOST_KEY, forged,
                independent_key=REVIEW_KEY,
            )["descriptive_campaign_admitted"])
            (attestations / "foreign.json").write_text("{}")
            self.assertFalse(validate_trial_alignment(m, bundles, attestations)["complete"])

    def test_manifest_cannot_be_reused_for_different_source_or_campaign(self) -> None:
        m = plan()
        invalid = copy.deepcopy(m)
        invalid["source_contract_identity"] = "sha256:swapped-runtime"
        with self.assertRaises(ValueError):
            verify_manifest(invalid)
        forged = _seal(m)
        forged["campaign_id"] = "another"
        with self.assertRaises(ValueError):
            verify_seal(m, forged, key=REVIEW_KEY)


if __name__ == "__main__":
    unittest.main()
