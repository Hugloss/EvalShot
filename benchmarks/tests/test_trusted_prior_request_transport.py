"""Model-free provider-boundary attacks for the deferred timing first request.

The opener is a deterministic fake. No real model, credential, external host,
randomization, or causal conclusion is asserted by these tests.
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
from benchmarks.harness.trusted_host_capture import TrustedModelRequestCapture
from benchmarks.harness.trusted_http_provider import (
    dispatch_verified_chat_request,
    dispatch_verified_pre_exposure_chat_request,
    dispatch_verified_pre_exposure_chat_response,
)
from benchmarks.harness.trusted_treatments import select_treatment
from benchmarks.tests.test_trusted_host_campaign import (
    HOST_KEY, _atif, _request, plan, source,
)
from benchmarks.tests.test_trusted_http_provider import FakeOpener, FakeResponse

H = lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest()
ENDPOINT = "https://api.openai.com/v1/chat/completions"
ORIGIN = "https://api.openai.com"


class VerifiedPriorProviderBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.key = self.root / "host.key"
        self.key.write_bytes(HOST_KEY)
        os.chmod(self.key, 0o600)
        self.manifest = plan("timing")
        self.variant = next(
            row for row in self.manifest["assignments"]
            if row["arm"] == "variant"
        )
        self.control = next(
            row for row in self.manifest["assignments"]
            if row["arm"] == "control"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def host(self, *, cell=None) -> TrustedModelRequestCapture:
        cell = cell or self.variant
        return TrustedModelRequestCapture(
            self.manifest, trial_id=cell["trial_id"],
            harness="codex", task="owner", replicate=1, arm=cell["arm"],
            host_identity="key-owning-external-provider-host",
            host_key_file=self.key,
        )

    def requests(self, *, cell=None) -> tuple[bytes, bytes, str]:
        cell = cell or self.variant
        selected = select_treatment(
            self.manifest, trial_id=cell["trial_id"], harness="codex",
            task="owner", replicate=1, arm=cell["arm"], current=source(),
        )
        final, prompt = _request(selected["selected_content"])
        before = json.loads(final)
        before["messages"] = before["messages"][:1]
        return canonical(before), final, prompt

    def first_args(self, host, request: bytes) -> dict:
        return {
            "capture": host, "serialized_model_request": request,
            "endpoint": ENDPOINT, "approved_origin": ORIGIN,
            "api_key": "fake-credential-only-for-unit-tests",
            "request_sequence": 1,
        }

    def second_args(self, host, request: bytes, prompt: str) -> dict:
        return {
            **self.first_args(host, request),
            "tool_call_id": "t1",
            "returned_packet": source()["content"],
            "request_sequence": 2,
            "current": source(), "catalog_sha256": H("catalog"),
            "prompt_sha256": prompt, "oracle_sha256": H("oracle"),
            "workspace_sha256": H("workspace"),
        }

    def attested(self, host, cell) -> dict:
        trajectory = self.root / (cell["trial_id"] + "-atif.json")
        trajectory.write_text(json.dumps(_atif(source()["content"])))
        receipt = self.root / (cell["trial_id"] + "-receipt.json")
        host.finalize(trajectory=trajectory, output=receipt)
        return verify_host_attestations(
            trajectory, receipt, HOST_KEY,
            campaign_id=self.manifest["design"]["campaign_id"],
            trial_id=cell["trial_id"],
        )

    def test_same_privileged_https_boundary_dispatches_both_stages_once(self) -> None:
        host = self.host()
        first, final, prompt = self.requests()
        earlier = FakeOpener()
        later = FakeOpener()
        with patch(
            "benchmarks.harness.trusted_http_provider.build_opener",
            side_effect=[earlier, later],
        ):
            a = dispatch_verified_pre_exposure_chat_request(
                **self.first_args(host, first),
            )
            b = dispatch_verified_chat_request(
                **self.second_args(host, final, prompt),
            )
        self.assertEqual(a["provider_submission_state"], "SUBMITTED")
        self.assertEqual(b["provider_submission_state"], "SUBMITTED")
        self.assertFalse(a["provider_consumption_proven"])
        self.assertFalse(b["provider_consumption_proven"])
        self.assertEqual(len(earlier.calls), 1)
        self.assertEqual(len(later.calls), 1)
        self.assertEqual(earlier.calls[0][0].data, first)
        self.assertEqual(later.calls[0][0].data, final)
        verified = self.attested(host, self.variant)
        self.assertEqual(verified["delivery_state"], "PROVEN")
        self.assertEqual(verified["provider_submission_state"], "SUBMITTED")
        self.assertEqual(verified["verified_provider_submission_packets"], 1)
        self.assertFalse(verified["model_attention_proven"])
        self.assertFalse(verified["provider_consumption_proven"])

    def test_relay_prior_response_bytes_without_files_or_secret_in_metadata(self) -> None:
        host = self.host()
        before, _, _ = self.requests()
        upstream = b'{"choices":[{"index":0,"message":{"role":"assistant","content":"prior"}}]}'
        fake = FakeOpener(FakeResponse(body=upstream))
        with patch(
            "benchmarks.harness.trusted_http_provider.build_opener",
            return_value=fake,
        ):
            returned = dispatch_verified_pre_exposure_chat_response(
                **self.first_args(host, before),
            )
        self.assertEqual(returned, upstream)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(list(self.root.iterdir()), [self.key])

    def test_direct_arm_and_non_tool_free_prior_refuse_before_provider_io(self) -> None:
        first, _, _ = self.requests()
        for payload in (
            first,
            canonical({
                **json.loads(first),
                "messages": [{"role": "tool", "tool_call_id": "t1", "content": {}}],
            }),
            canonical({
                **json.loads(first),
                "messages": [{"role": "assistant", "content": "model wrote this"}],
            }),
            canonical({
                **json.loads(first),
                "model": "other/provider-model",
            }),
        ):
            cell = self.control if payload == first else self.variant
            host = self.host(cell=cell)
            fake = FakeOpener()
            with patch(
                "benchmarks.harness.trusted_http_provider.build_opener",
                return_value=fake,
            ):
                with self.assertRaises(ValueError):
                    dispatch_verified_pre_exposure_chat_request(
                        **self.first_args(host, payload),
                    )
            self.assertEqual(fake.calls, [])

    def test_invalid_origin_credential_and_sequence_never_send(self) -> None:
        first, _, _ = self.requests()
        for changes in (
            {"request_sequence": 2},
            {"api_key": "a\rInjected: 1"},
            {"api_key": "a\nInjected: 1"},
            {"endpoint": "http://api.openai.com/v1/chat/completions"},
            {"approved_origin": "https://evil.example"},
        ):
            host = self.host()
            params = {**self.first_args(host, first), **changes}
            fake = FakeOpener()
            with patch(
                "benchmarks.harness.trusted_http_provider.build_opener",
                return_value=fake,
            ):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    dispatch_verified_pre_exposure_chat_request(**params)
            self.assertEqual(fake.calls, [])

    def test_prior_transport_failure_blocks_retry_second_stage_and_receipt(self) -> None:
        for response in (
            FakeResponse(status=429),
            FakeResponse(status=302),
            FakeResponse(body=b""),
            FakeResponse(body=b"{}"),
            FakeResponse(body=b"not-json"),
            FakeResponse(body=b"x" * 2_097_153),
            ValueError("potential-sensitive-network-message"),
        ):
            with self.subTest(response=type(response).__name__):
                host = self.host()
                first, final, prompt = self.requests()
                fake = FakeOpener(response)
                params = self.first_args(host, first)
                with patch(
                    "benchmarks.harness.trusted_http_provider.build_opener",
                    return_value=fake,
                ):
                    with self.assertRaisesRegex(
                        ValueError, "^provider-submission-unverified$",
                    ):
                        dispatch_verified_pre_exposure_chat_request(**params)
                    with self.assertRaises(ValueError):
                        dispatch_verified_pre_exposure_chat_request(**params)
                self.assertEqual(len(fake.calls), 1)
                with self.assertRaisesRegex(
                    ValueError, "deferred-delivery-without-prior-provider-submission",
                ):
                    dispatch_verified_chat_request(
                        **self.second_args(host, final, prompt),
                    )
                receipt = self.root / "denied-receipt.json"
                trajectory = self.root / "denied-trace.json"
                trajectory.write_text(json.dumps(_atif(source()["content"])))
                with self.assertRaises(ValueError):
                    host.finalize(trajectory=trajectory, output=receipt)
                self.assertFalse(receipt.exists())

    def test_changed_origin_or_prompt_fails_before_second_network_request(self) -> None:
        for drift in ("origin", "prompt"):
            host = self.host()
            first, final, prompt = self.requests()
            prior = FakeOpener()
            second = FakeOpener()
            with patch(
                "benchmarks.harness.trusted_http_provider.build_opener",
                side_effect=[prior, second],
            ):
                dispatch_verified_pre_exposure_chat_request(
                    **self.first_args(host, first),
                )
                params = self.second_args(host, final, prompt)
                if drift == "origin":
                    params["endpoint"] = "https://other.example/v1/chat/completions"
                    params["approved_origin"] = "https://other.example"
                else:
                    modified = json.loads(final)
                    modified["messages"][0]["content"] = "different prompt"
                    params["serialized_model_request"] = canonical(modified)
                    params["prompt_sha256"] = hashlib.sha256(canonical({
                        "request_settings": {
                            k: v for k, v in modified.items() if k != "messages"
                        },
                        "non_tool_messages": modified["messages"][:1],
                    })).hexdigest()
                with self.subTest(drift=drift), self.assertRaises(ValueError):
                    dispatch_verified_chat_request(**params)
            self.assertEqual(len(prior.calls), 1)
            self.assertEqual(len(second.calls), 0)

    def test_second_transport_failure_does_not_retroactively_create_receipt(self) -> None:
        host = self.host()
        first, final, prompt = self.requests()
        earlier = FakeOpener()
        later = FakeOpener(RuntimeError("provider-does-not-answer"))
        with patch(
            "benchmarks.harness.trusted_http_provider.build_opener",
            side_effect=[earlier, later],
        ):
            dispatch_verified_pre_exposure_chat_request(
                **self.first_args(host, first),
            )
            with self.assertRaisesRegex(ValueError, "provider-submission-unverified"):
                dispatch_verified_chat_request(
                    **self.second_args(host, final, prompt),
                )
        self.assertEqual(len(earlier.calls), 1)
        self.assertEqual(len(later.calls), 1)
        atif = self.root / "trace.json"
        atif.write_text(json.dumps(_atif(source()["content"])))
        with self.assertRaises(ValueError):
            host.finalize(trajectory=atif, output=self.root / "no.json")


if __name__ == "__main__":
    unittest.main()
