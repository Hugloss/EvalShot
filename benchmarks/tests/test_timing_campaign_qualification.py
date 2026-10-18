"""Fail-closed campaign qualification using real model-free host boundary receipts.

The deterministic fake HTTPS responder exercises the actual two-stage
transport, ATIF packet binding, host-key MAC verification, and campaign audit.
Only Harbor's immutable bundle reader is mocked; these tests do not call a model.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.independent_campaign_qualification import qualify_campaign
from benchmarks.harness.intervention_audit import audit_intervention_observations
from benchmarks.harness.trusted_host_capture import TrustedModelRequestCapture
from benchmarks.harness.trusted_http_provider import (
    dispatch_verified_chat_request, dispatch_verified_pre_exposure_chat_request,
)
from benchmarks.harness.trusted_treatments import select_treatment
from benchmarks.tests.test_host_attested_interventions import _design, _observations
from benchmarks.tests.test_trusted_host_campaign import (
    HOST_KEY, REVIEW_KEY, H, _atif, _request, _seal, plan, source,
)
from benchmarks.tests.test_trusted_http_provider import FakeOpener


class TimingCampaignPublicationTests(unittest.TestCase):
    def test_incomplete_campaign_cannot_publish_surviving_effect(self) -> None:
        design = _design("timing", tasks=["case-1", "case-2"])
        complete = _observations(design)
        good = audit_intervention_observations(design, complete)
        self.assertEqual(good["comparison_publication_state"], "PUBLISHED_COMPLETE_DESCRIPTIVE")
        self.assertEqual(good["qualified_matched_pairs"], 2)
        self.assertEqual(len(good["matched_pairs"]), 2)

        for scenario in ("missing", "duplicate", "foreign", "drift", "unattested", "corrupt"):
            rows = copy.deepcopy(complete)
            corrupt = 0
            if scenario == "missing":
                rows.pop()
            elif scenario == "duplicate":
                rows.append(copy.deepcopy(rows[0]))
            elif scenario == "foreign":
                rows.append({**rows[0], "task": "not-in-manifest"})
            elif scenario == "drift":
                rows[-1]["intervention"]["prompt_sha256"] = H("changed-prompt")
            elif scenario == "unattested":
                rows[-1]["host_attested"] = False
            else:
                corrupt = 1
            report = audit_intervention_observations(
                design, rows, corrupt_bundles=corrupt,
            )
            with self.subTest(scenario=scenario):
                self.assertEqual(report["coverage_state"], "INCOMPLETE")
                self.assertEqual(
                    report["comparison_publication_state"],
                    "WITHHELD_INCOMPLETE_CAMPAIGN",
                )
                self.assertEqual(report["matched_pairs"], [])
                self.assertEqual(report["per_harness_descriptive_effect"], {})
                self.assertGreater(report["partial_observed_matched_pairs"], 0)
                self.assertFalse(report["causal_effect_qualified"])

    def test_empty_campaign_cannot_invent_effect(self) -> None:
        report = audit_intervention_observations(_design("timing"), [])
        self.assertEqual(report["comparison_publication_state"], "WITHHELD_INCOMPLETE_CAMPAIGN")
        self.assertEqual(report["qualified_matched_pairs"], 0)
        self.assertEqual(report["matched_pairs"], [])
        self.assertEqual(report["per_harness_descriptive_effect"], {})


class SignedTwoStageCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundles = self.root / "bundles"
        self.attestations = self.root / "attestations"
        self.bundles.mkdir()
        self.attestations.mkdir()
        self.key = self.root / "host.key"
        self.key.write_bytes(HOST_KEY)
        os.chmod(self.key, 0o600)
        self.manifest = plan("timing")
        self.projections: dict[str, dict] = {}
        for cell in self.manifest["assignments"]:
            self._run_cell(cell)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_cell(self, cell: dict) -> None:
        host = TrustedModelRequestCapture(
            self.manifest, trial_id=cell["trial_id"],
            harness=cell["harness"], task=cell["task"],
            replicate=cell["replicate"], arm=cell["arm"],
            host_identity="trusted-two-stage-test-host", host_key_file=self.key,
        )
        selected = select_treatment(
            self.manifest, trial_id=cell["trial_id"],
            harness=cell["harness"], task=cell["task"],
            replicate=cell["replicate"], arm=cell["arm"], current=source(),
        )
        final, prompt = _request(selected["selected_content"])
        connection = {
            "capture": host,
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "approved_origin": "https://api.openai.com",
            "api_key": "fake-test-credential",
        }
        if cell["arm"] == "variant":
            before = json.loads(final)
            before["messages"] = before["messages"][:1]
            with patch(
                "benchmarks.harness.trusted_http_provider.build_opener",
                return_value=FakeOpener(),
            ):
                dispatch_verified_pre_exposure_chat_request(
                    **connection, serialized_model_request=canonical(before),
                    request_sequence=1,
                )
        with patch(
            "benchmarks.harness.trusted_http_provider.build_opener",
            return_value=FakeOpener(),
        ):
            dispatch_verified_chat_request(
                **connection, serialized_model_request=final,
                tool_call_id="t1", returned_packet=source()["content"],
                request_sequence=selected["required_request_sequence"],
                current=source(), catalog_sha256=H("catalog"),
                prompt_sha256=prompt, oracle_sha256=H("oracle"),
                workspace_sha256=H("workspace"),
            )
        directory = self.bundles / cell["trial_id"]
        directory.mkdir()
        trajectory = directory / "trajectory.json"
        trajectory.write_text(json.dumps(_atif(source()["content"])), encoding="utf-8")
        host.finalize(
            trajectory=trajectory,
            output=self.attestations / (cell["trial_id"] + ".json"),
        )
        self.projections[cell["trial_id"]] = {
            "receipt": {
                "task_id": cell["task"], "harness": cell["harness"],
                "replicate_id": cell["replicate"], "subject": "hashmarks",
                "model": self.manifest["design"]["model"],
                "status": "PASS" if cell["arm"] == "variant" else "FAIL",
                "execution": {
                    "campaign_id": self.manifest["design"]["campaign_id"],
                    "mcp_treatment": {
                        "full_contract": True,
                        "source_contract_identity": self.manifest["source_contract_identity"],
                    },
                },
            },
        }

    def _qualify(self, *, seal=None, key=REVIEW_KEY) -> dict:
        # Only the Harbor immutable bundle loader is replaced by frozen
        # receipts. The real host MAC/ATIF/manifest validators still execute.
        with patch(
            "benchmarks.harness.intervention_audit.load_harbor_bundle_projection",
            side_effect=lambda path: self.projections[path.name],
        ), patch(
            "benchmarks.harness.independent_campaign_qualification.load_harbor_bundle_projection",
            side_effect=lambda path: self.projections[path.name],
        ):
            return qualify_campaign(
                self.manifest, self.bundles, self.attestations, HOST_KEY,
                _seal(self.manifest) if seal is None else seal,
                independent_key=key,
                # Deliberately omit --require-provider-submission.
            )

    def test_all_signed_timing_cells_admitted_and_zero_causal_claim(self) -> None:
        result = self._qualify()
        self.assertTrue(result["require_provider_submission"])
        self.assertEqual(result["provider_submission_policy"], "MANDATORY_TIMING")
        self.assertEqual(result["verified_provider_submission_trials"], 2)
        self.assertEqual(result["expected_provider_submission_trials"], 2)
        self.assertEqual(
            result["host_intervention_coverage"]["comparison_publication_state"],
            "PUBLISHED_COMPLETE_DESCRIPTIVE",
        )
        self.assertTrue(result["frozen_trial_alignment"]["complete"])
        self.assertTrue(result["descriptive_campaign_admitted"])
        self.assertTrue(result["descriptive_comparison_publication_admitted"])
        self.assertFalse(result["real_randomization_proven"])
        self.assertFalse(result["trusted_pre_work_timestamp_proven"])
        self.assertFalse(result["causal_effect_proven"])

    def test_lost_or_modified_receipts_deny_campaign_publication(self) -> None:
        variant = next(
            cell for cell in self.manifest["assignments"]
            if cell["arm"] == "variant"
        )
        path = self.attestations / (variant["trial_id"] + ".json")
        original = path.read_bytes()
        try:
            for scenario in ("lost", "bad-mac", "unsigned-prior-transport"):
                if scenario == "lost":
                    path.unlink(missing_ok=True)
                else:
                    value = json.loads(original)
                    facet = value["deliveries"][0]["intervention"]
                    if scenario == "bad-mac":
                        facet["delivery_request_sequence"] = 99
                    else:
                        facet["prior_transport_sha256"] = "0" * 64
                    path.write_text(json.dumps(value), encoding="utf-8")
                report = self._qualify()
                with self.subTest(scenario=scenario):
                    self.assertFalse(report["descriptive_campaign_admitted"])
                    self.assertFalse(report["descriptive_comparison_publication_admitted"])
                    self.assertEqual(
                        report["host_intervention_coverage"]["comparison_publication_state"],
                        "WITHHELD_INCOMPLETE_CAMPAIGN",
                    )
                    self.assertIn(
                        "missing-or-unverified-provider-submission",
                        report["qualification_blockers"],
                    )
                path.write_bytes(original)
        finally:
            path.write_bytes(original)

    def test_foreign_extra_attestation_denies_campaign(self) -> None:
        extra = self.attestations / "foreign.json"
        extra.write_text("{}", encoding="utf-8")
        result = self._qualify()
        self.assertFalse(result["descriptive_campaign_admitted"])
        self.assertIn("incomplete-or-misaligned-frozen-plan", result["qualification_blockers"])
        self.assertFalse(result["descriptive_comparison_publication_admitted"])

    def test_seal_required_for_publication_even_with_complete_signed_cells(self) -> None:
        with patch(
            "benchmarks.harness.intervention_audit.load_harbor_bundle_projection",
            side_effect=lambda path: self.projections[path.name],
        ), patch(
            "benchmarks.harness.independent_campaign_qualification.load_harbor_bundle_projection",
            side_effect=lambda path: self.projections[path.name],
        ):
            result = qualify_campaign(
                self.manifest, self.bundles, self.attestations, HOST_KEY,
                None, independent_key=None,
            )
        self.assertEqual(
            result["host_intervention_coverage"]["comparison_publication_state"],
            "PUBLISHED_COMPLETE_DESCRIPTIVE",
        )
        self.assertFalse(result["descriptive_campaign_admitted"])
        self.assertFalse(result["descriptive_comparison_publication_admitted"])
        self.assertIn("independent-authority-seal-unavailable", result["qualification_blockers"])


if __name__ == "__main__":
    unittest.main()
