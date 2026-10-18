"""E241–E244: provider transport and strict campaign submission attacks.

The opener is a deterministic fake; tests never dispatch to a real provider.
A fake 2xx demonstrates only contract verification, not empirical model use.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.host_input_attestation import canonical, verify_host_attestations
from benchmarks.harness.independent_campaign_qualification import qualify_campaign
from benchmarks.harness.trusted_host_capture import TrustedModelRequestCapture
from benchmarks.harness.trusted_http_provider import (
    _RejectRedirect, _endpoint, dispatch_verified_chat_request,
)
from benchmarks.harness.trusted_treatments import select_treatment
from benchmarks.tests.test_trusted_host_campaign import (
    HOST_KEY, REVIEW_KEY, _atif, _request, _seal, plan, source,
)

H = lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest()


class FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b'{"choices":[{"index":0}]}'):
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int):
        return self.body[:size]


class FakeOpener:
    def __init__(self, result: FakeResponse | BaseException | None = None):
        self.result = result or FakeResponse()
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class ProviderBoundaryAttackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = self.root / "host.key"
        self.key.write_bytes(HOST_KEY)
        os.chmod(self.key, 0o600)
        self.manifest = plan()
        self.cell = next(x for x in self.manifest["assignments"] if x["arm"] == "control")

    def tearDown(self):
        self.temp.cleanup()

    def host(self, *, cell=None):
        cell = cell or self.cell
        return TrustedModelRequestCapture(
            self.manifest, trial_id=cell["trial_id"],
            harness="codex", task="owner", replicate=1, arm=cell["arm"],
            host_identity="privileged-provider-host", host_key_file=self.key,
        )

    def args(self, host, *, extra_messages=None):
        chosen = select_treatment(
            self.manifest, trial_id=self.cell["trial_id"], harness="codex",
            task="owner", replicate=1, arm=self.cell["arm"], current=source(),
        )
        raw, prompt = _request(chosen["selected_content"])
        if extra_messages:
            value = json.loads(raw)
            value["messages"].extend(extra_messages)
            raw = canonical(value)
        return dict(
            capture=host,
            serialized_model_request=raw,
            endpoint="https://api.openai.com/v1/chat/completions",
            approved_origin="https://api.openai.com",
            api_key="test-secret-not-a-real-key",
            tool_call_id="t1", returned_packet=source()["content"],
            request_sequence=1, current=source(),
            catalog_sha256=H("catalog"), prompt_sha256=prompt,
            oracle_sha256=H("oracle"), workspace_sha256=H("workspace"),
        )

    def trajectory(self, cell=None):
        cell = cell or self.cell
        atif = self.root / (cell["trial_id"] + "-trace.json")
        atif.write_text(json.dumps(_atif(source()["content"])))
        return atif

    def test_validated_immutable_bytes_sent_once_and_mac_proves_submission_only(self):
        host = self.host()
        fake = FakeOpener()
        args = self.args(host)
        with patch("benchmarks.harness.trusted_http_provider.build_opener", return_value=fake):
            result = dispatch_verified_chat_request(**args)
        self.assertEqual(result["provider_submission_state"], "SUBMITTED")
        self.assertFalse(result["provider_consumption_proven"])
        self.assertEqual(len(fake.calls), 1)
        req, timeout = fake.calls[0]
        self.assertEqual(req.data, args["serialized_model_request"])
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(timeout, 60)
        self.assertEqual(req.get_header("Authorization"), "Bearer test-secret-not-a-real-key")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        atif = self.trajectory()
        external = self.root / "receipt.json"
        host.finalize(trajectory=atif, output=external)
        audit = verify_host_attestations(
            atif, external, HOST_KEY, campaign_id=self.manifest["design"]["campaign_id"],
            trial_id=self.cell["trial_id"],
        )
        self.assertEqual(audit["provider_submission_state"], "SUBMITTED")
        self.assertEqual(audit["verified_provider_submission_packets"], 1)
        self.assertFalse(audit["provider_consumption_proven"])
        self.assertNotIn("test-secret-not-a-real-key", external.read_text())
        attached = json.loads(external.read_text())
        attached["deliveries"][0]["transport"]["response_sha256"] = H("forged")
        external.write_text(json.dumps(attached))
        self.assertEqual(verify_host_attestations(
            atif, external, HOST_KEY, campaign_id=self.manifest["design"]["campaign_id"],
            trial_id=self.cell["trial_id"],
        )["attestation_state"], "REJECTED")

    def test_transport_denial_does_not_sign_and_cannot_retry(self):
        host = self.host()
        fake = FakeOpener(RuntimeError("private-provider-error-with-secret"))
        args = self.args(host)
        with patch("benchmarks.harness.trusted_http_provider.build_opener", return_value=fake):
            with self.assertRaisesRegex(ValueError, "^provider-submission-unverified$"):
                dispatch_verified_chat_request(**args)
        self.assertEqual(len(fake.calls), 1)
        with self.assertRaisesRegex(ValueError, "provider-transport-not-proven"):
            host.finalize(trajectory=self.trajectory(), output=self.root / "receipt.json")
        with patch("benchmarks.harness.trusted_http_provider.build_opener", return_value=fake):
            with self.assertRaises(ValueError):
                dispatch_verified_chat_request(**args)
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse((self.root / "receipt.json").exists())

    def test_non_2xx_malformed_oversized_or_empty_response_never_attested(self):
        variants = [
            FakeResponse(status=429), FakeResponse(status=302),
            FakeResponse(body=b"{}"), FakeResponse(body=b""),
            FakeResponse(body=b"not json"),
            FakeResponse(body=b"x" * 2_097_153),
        ]
        for response in variants:
            with self.subTest(status=response.status, length=len(response.body)):
                host = self.host()
                fake = FakeOpener(response)
                with patch("benchmarks.harness.trusted_http_provider.build_opener", return_value=fake):
                    with self.assertRaisesRegex(ValueError, "provider-submission-unverified"):
                        dispatch_verified_chat_request(**self.args(host))
                with self.assertRaises(ValueError):
                    host.finalize(trajectory=self.trajectory(), output=self.root / "no.json")

    def test_mismatched_input_or_extra_tool_message_fails_before_network(self):
        extra = [{"role": "tool", "tool_call_id": "other", "content": {"bad": True}}]
        for patch_args in ({"extra_messages": extra}, {}):
            host = self.host()
            fake = FakeOpener()
            args = self.args(host, **patch_args)
            if not patch_args:
                args["prompt_sha256"] = H("changed")
            with patch("benchmarks.harness.trusted_http_provider.build_opener", return_value=fake):
                with self.assertRaises(ValueError):
                    dispatch_verified_chat_request(**args)
            self.assertEqual(len(fake.calls), 0)

    def test_origin_credentials_paths_redirect_and_proxy_fallback_denied(self):
        for endpoint, origin in (
            ("http://api.openai.com/v1/chat/completions", "https://api.openai.com"),
            ("https://api.openai.com/other", "https://api.openai.com"),
            ("https://evil.example/v1/chat/completions", "https://api.openai.com"),
            ("https://user:pass@api.openai.com/v1/chat/completions", "https://api.openai.com"),
            ("https://api.openai.com/v1/chat/completions?x=1", "https://api.openai.com"),
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    _endpoint(endpoint, approved_origin=origin)
        with self.assertRaises(ValueError):
            _RejectRedirect().redirect_request(None, None, 307, "", {}, "https://evil.example")
        fake = FakeOpener()
        args = self.args(self.host())
        args["endpoint"] = "https://evil.example/v1/chat/completions"
        with patch("benchmarks.harness.trusted_http_provider.build_opener", return_value=fake):
            with self.assertRaises(ValueError):
                dispatch_verified_chat_request(**args)
        self.assertEqual(len(fake.calls), 0)


class StrictCampaignAdmissionTests(unittest.TestCase):
    def test_submission_required_does_not_upgrade_legacy_atif_host_receipt(self):
        m = plan()
        with patch(
            "benchmarks.harness.independent_campaign_qualification.audit_intervention_bundles",
            return_value={
                "coverage_state": "COMPLETE",
                "comparison_publication_state": "PUBLISHED_COMPLETE_DESCRIPTIVE",
            },
        ), patch(
            "benchmarks.harness.independent_campaign_qualification.validate_trial_alignment",
            return_value={"complete": True},
        ), patch(
            "benchmarks.harness.independent_campaign_qualification.verify_host_attestations",
            return_value={
                "attestation_state": "VERIFIED", "delivery_state": "PROVEN",
                "provider_submission_state": "UNKNOWN",
            },
        ):
            report = qualify_campaign(
                m, Path("/nonexistent"), Path("/nonexistent"), HOST_KEY,
                _seal(m), independent_key=REVIEW_KEY,
                require_provider_submission=True,
            )
        self.assertFalse(report["descriptive_campaign_admitted"])
        self.assertEqual(
            report["qualification_blockers"]["missing-or-unverified-provider-submission"],
            2,
        )
        self.assertEqual(report["verified_provider_submission_trials"], 0)
        self.assertFalse(report["provider_processing_proven"])

    def test_independent_verification_all_cells_and_no_causal_claim(self):
        m = plan()
        with patch(
            "benchmarks.harness.independent_campaign_qualification.audit_intervention_bundles",
            return_value={
                "coverage_state": "COMPLETE",
                "comparison_publication_state": "PUBLISHED_COMPLETE_DESCRIPTIVE",
            },
        ), patch(
            "benchmarks.harness.independent_campaign_qualification.validate_trial_alignment",
            return_value={"complete": True},
        ), patch(
            "benchmarks.harness.independent_campaign_qualification.verify_host_attestations",
            return_value={
                "attestation_state": "VERIFIED", "delivery_state": "PROVEN",
                "provider_submission_state": "SUBMITTED",
                "verified_provider_submission_packets": 1,
                "returned_packets": 1,
            },
        ) as verify:
            report = qualify_campaign(
                m, Path("/inaccessible"), Path("/inaccessible"), HOST_KEY,
                _seal(m), independent_key=REVIEW_KEY,
                require_provider_submission=True,
            )
        self.assertTrue(report["descriptive_campaign_admitted"])
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(report["verified_provider_submission_trials"], 2)
        self.assertFalse(report["provider_processing_proven"])
        self.assertFalse(report["causal_effect_proven"])
        self.assertFalse(report["trusted_pre_work_timestamp_proven"])


if __name__ == "__main__":
    unittest.main()
