"""E249-E252 adversarial OpenCode native gateway tests, entirely model-free.

The pinned native launch is a fake executable; HTTPS upstream is mocked.
This proves local routing and error contracts, not real native provenance,
provider inference, independent human review, or empirical uplift.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.trusted_http_provider import (
    dispatch_verified_chat_stream_response, validate_sse_response,
)
from benchmarks.harness.opencode_native_gateway import (
    NativeOpenCodeGateway, admit_opencode_binary,
    gateway_config, sha256_file, write_gateway_config,
)
from benchmarks.harness.opencode_native_trial import launch_native_trial
from benchmarks.harness.trusted_treatments import (
    build_manifest, select_treatment, sha,
)

KEY = b"externally-owned-provider-capture-hmac-key-12345"
H = lambda name: hashlib.sha256(name.encode("utf-8")).hexdigest()
REPLY = b'{"choices":[{"index":0,"message":{"role":"assistant","content":"owner"}}]}'
UPSTREAM = "https://api.example.invalid/v1/chat/completions"


def manifest() -> dict:
    return build_manifest({
        "schema": "agentscookbook.host-attested-intervention-design.v1",
        "study": "presentation",
        "campaign_id": "native-gateway-fixture",
        "model": "gpt-fixture",
        "harnesses": ["opencode-native"],
        "tasks": ["source-owner"],
        "replicates": 1,
        "arms": {"control": "structured", "variant": "text"},
    }, source_contract_identity="sha256:hashmarks-pinned",
       host_build_sha256=H("host"), assignment_seed_sha256=H("assignment"))


def source() -> dict:
    value = {"owner": "src/owner.py"}
    return {
        "generation_sha256": H("workspace-generation"),
        "semantic_sha256": sha(value),
        "content": value,
    }


def request(*, selected: object, tool: bool, stream: bool = False) -> bytes:
    messages = [{"role": "user", "content": "Find owner"}]
    if tool:
        messages.append({
            "role": "tool", "tool_call_id": "native-tool-1", "content": selected,
        })
    return canonical({"model": "gpt-fixture", "messages": messages, "stream": stream})


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.key = self.root / "host.key"
        self.key.write_bytes(KEY)
        self.key.chmod(0o600)
        self.manifest = manifest()
        self.cell = next(x for x in self.manifest["assignments"] if x["arm"] == "control")
        self.gateway = NativeOpenCodeGateway(
            self.manifest, trial_id=self.cell["trial_id"],
            host_key_file=self.key, host_identity="trial-provider-host",
            current=source(), replaced=None,
            gateway_token="some-privileged-local-secret-12345678",
            upstream=UPSTREAM, approved_origin="https://api.example.invalid",
            upstream_api_key="secret-upstream-key", catalog_sha256=H("catalog"),
            oracle_sha256=H("oracle"), workspace_sha256=H("workspace"),
        )

    def tearDown(self):
        self.gateway.close()
        self.tmp.cleanup()

    def body(self, *, tool=False):
        return request(
            selected=self.gateway.selected["selected_content"], tool=tool,
        )

    def test_selected_tool_message_is_forwarded_once_no_double_dispatch(self):
        call_count = []
        with patch("benchmarks.harness.opencode_native_gateway._forward_pre_evidence",
                   return_value=REPLY) as pre, patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
            side_effect=lambda **kw: (call_count.append(kw), REPLY)[1],
        ):
            response = self.gateway._handle_request(
                self.body(), authorization="Bearer " + self.gateway.token,
            )
            self.assertEqual(response, REPLY)
            response = self.gateway._handle_request(
                self.body(tool=True),
                authorization="Bearer " + self.gateway.token,
            )
            self.assertEqual(response, REPLY)
            self.assertEqual(pre.call_count, 1)
        self.assertEqual(len(call_count), 1)
        call = call_count[0]
        self.assertEqual(call["serialized_model_request"], self.body(tool=True))
        self.assertEqual(call["tool_call_id"], "native-tool-1")
        self.assertEqual(call["returned_packet"], source()["content"])
        self.assertEqual(self.gateway.result()["post_evidence_submissions"], 1)
        self.assertFalse(self.gateway.result()["model_request_attested"])
        self.assertFalse(self.gateway.result()["native_process_origin_proven"])
        with self.assertRaisesRegex(ValueError, "gateway-extra-request"):
            self.gateway._handle_request(
                self.body(tool=True),
                authorization="Bearer " + self.gateway.token,
            )

    def test_streaming_native_provider_request_uses_exact_sse_relay(self):
        selected = self.gateway.selected["selected_content"]
        original = request(selected=selected, tool=True, stream=True)
        stream = (
            b'data: {"choices":[{"delta":{"content":"owner"}}]}\n\n'
            b'data: [DONE]\n\n'
        )
        # Patch transport only; all gateway model/tool/link checks still run.
        with patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_stream_response",
            return_value=stream,
        ) as submit:
            returned = self.gateway._handle_request(
                original, authorization="Bearer " + self.gateway.token,
            )
        self.assertEqual(returned, stream)
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(submit.call_args.kwargs["serialized_model_request"], original)
        self.assertEqual(self.gateway.result()["post_evidence_submissions"], 1)

    def test_terminal_sse_validation_rejects_truncated_and_malformed_events(self):
        good = (
            b'data: {"choices":[{"delta":{"content":"yes"}}]}\n\n'
            b'data: [DONE]\n\n'
        )
        self.assertTrue(validate_sse_response(good))
        for invalid in (
            good.replace(b"data: [DONE]\n\n", b""),
            b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
            b'data: not-json\n\ndata: [DONE]\n\n',
            b'data: [DONE]\n\ndata: {"choices":[1]}\n\n',
            b'data: {"choices":[1]}\n\ndata: [DONE]',
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(validate_sse_response(invalid))

    def test_transport_owner_signs_only_complete_sse_response(self):
        body = request(
            selected=self.gateway.selected["selected_content"],
            tool=True, stream=True,
        )
        prompt = {
            "request_settings": {"model": "gpt-fixture", "stream": True},
            "non_tool_messages": [{"role": "user", "content": "Find owner"}],
        }
        expected_prompt = hashlib.sha256(canonical(prompt)).hexdigest()
        done = (
            b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n'
            b'data: [DONE]\n\n'
        )

        class Response:
            status = 200
            headers = {"Content-Type": "text/event-stream"}
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, size):
                return self.payload[:size]

        class Opener:
            def __init__(self, payload):
                self.payload = payload
                self.calls = 0
            def open(self, request, timeout):
                self.calls += 1
                return Response(self.payload)

        for payload, accepted in ((done, True), (done[:-7], False)):
            # Only a fresh capture can attempt provider submission.
            gateway = NativeOpenCodeGateway(
                self.manifest, trial_id=self.cell["trial_id"],
                host_key_file=self.key, host_identity="trial-provider-host",
                current=source(), replaced=None,
                gateway_token="some-privileged-local-secret-12345678",
                upstream=UPSTREAM, approved_origin="https://api.example.invalid",
                upstream_api_key="secret-upstream-key", catalog_sha256=H("catalog"),
                oracle_sha256=H("oracle"), workspace_sha256=H("workspace"),
            )
            opener = Opener(payload)
            with patch(
                "benchmarks.harness.trusted_http_provider.build_opener",
                return_value=opener,
            ):
                kwargs = dict(
                    capture=gateway.capture, serialized_model_request=body,
                    endpoint=UPSTREAM, approved_origin="https://api.example.invalid",
                    api_key="secret-upstream-key", tool_call_id="native-tool-1",
                    returned_packet=source()["content"], request_sequence=1,
                    current=source(), replaced=None,
                    catalog_sha256=H("catalog"), prompt_sha256=expected_prompt,
                    oracle_sha256=H("oracle"), workspace_sha256=H("workspace"),
                )
                if accepted:
                    self.assertEqual(dispatch_verified_chat_stream_response(**kwargs), done)
                    self.assertIn("transport", gateway.capture._recorded[0])
                else:
                    with self.assertRaisesRegex(ValueError, "provider-submission-unverified"):
                        dispatch_verified_chat_stream_response(**kwargs)
                    self.assertNotIn("transport", gateway.capture._recorded[0])
            self.assertEqual(opener.calls, 1)

    def test_wrong_tool_packet_and_foreign_call_do_not_dispatch(self):
        fake = []
        with patch("benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
                   side_effect=lambda **kw: fake.append(kw)):
            bad = request(selected={"other": "not-the-packet"}, tool=True)
            with self.assertRaisesRegex(ValueError, "selected-packet"):
                self.gateway._handle_request(
                    bad, authorization="Bearer " + self.gateway.token,
                )
            self.assertFalse(fake)
            self.assertEqual(self.gateway.result()["post_evidence_submissions"], 0)

    def test_authentication_model_and_budget_are_fail_before_work(self):
        with self.assertRaisesRegex(ValueError, "authorization-rejected"):
            self.gateway._handle_request(self.body(), authorization="Bearer wrong")
        self.assertEqual(self.gateway.unauthenticated_attempts, 1)
        wrong = json.loads(self.body())
        wrong["model"] = "other-model"
        with self.assertRaisesRegex(ValueError, "model-or-request"):
            self.gateway._handle_request(
                canonical(wrong), authorization="Bearer " + self.gateway.token,
            )
        with patch("benchmarks.harness.opencode_native_gateway._forward_pre_evidence",
                   return_value=REPLY) as upstream:
            for i in range(4):
                self.assertEqual(self.gateway._handle_request(
                    self.body(), authorization="Bearer " + self.gateway.token,
                ), REPLY)
            with self.assertRaisesRegex(ValueError, "request-budget"):
                self.gateway._handle_request(
                    self.body(), authorization="Bearer " + self.gateway.token,
                )
            self.assertEqual(upstream.call_count, 4)

    def test_failed_provider_submission_fails_once_without_retry(self):
        with patch("benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
                   side_effect=ValueError("provider-submission-unverified")) as dispatched:
            with self.assertRaisesRegex(ValueError, "provider-submission-unverified"):
                self.gateway._handle_request(
                    self.body(tool=True), authorization="Bearer " + self.gateway.token,
                )
            with self.assertRaisesRegex(ValueError, "gateway-extra-request"):
                self.gateway._handle_request(
                    self.body(tool=True), authorization="Bearer " + self.gateway.token,
                )
            self.assertEqual(dispatched.call_count, 1)
            self.assertFalse(self.gateway.result()["provider_response_relay_qualified"])

    def test_socket_server_accepts_only_the_local_model_gateway_path(self):
        with patch("benchmarks.harness.opencode_native_gateway._forward_pre_evidence",
                   return_value=REPLY):
            port = self.gateway.start()
            self.assertIsInstance(port, int)
            body = self.body()
            req = Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=body, headers={"Authorization": "Bearer " + self.gateway.token},
                method="POST",
            )
            with urlopen(req, timeout=2) as response:
                self.assertEqual(response.read(), REPLY)
            bad = Request(
                f"http://127.0.0.1:{port}/v1/other",
                data=body, method="POST",
            )
            with self.assertRaises(HTTPError) as cm:
                urlopen(bad, timeout=2)
            self.assertEqual(cm.exception.code, 404)

    def test_frozen_gateway_config_provider_allowlist_and_create_only(self):
        cfg = gateway_config(port=42424, model_id="gpt-fixture")
        self.assertEqual(cfg["enabled_providers"], ["agentscookbook-captured"])
        self.assertEqual(cfg["model"], "agentscookbook-captured/gpt-fixture")
        self.assertEqual(cfg["provider"]["agentscookbook-captured"]["options"]["baseURL"],
                         "http://127.0.0.1:42424/v1")
        self.assertNotIn("secret", json.dumps(cfg))
        path = self.root / "private-opencode.json"
        fingerprint = write_gateway_config(path, cfg)
        self.assertEqual(fingerprint, hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaises(FileExistsError):
            write_gateway_config(path, cfg)
        with self.assertRaises(ValueError):
            gateway_config(port=0, model_id="gpt-fixture")
        with self.assertRaises(ValueError):
            gateway_config(port=42424, model_id="bad model with space")

    def test_binary_pin_rejects_unexpected_bytes_and_version(self):
        binary = self.root / "fake-opencode"
        binary.write_text("#!/bin/sh\necho native-fixture-0.1.0\n")
        binary.chmod(0o700)
        digest = sha256_file(binary)
        proof = admit_opencode_binary(
            binary, expected_sha256=digest,
            expected_version="native-fixture-0.1.0",
        )
        self.assertEqual(proof["executable_sha256"], digest)
        with self.assertRaisesRegex(ValueError, "version-drift"):
            admit_opencode_binary(binary, expected_sha256=digest,
                                  expected_version="other-version")
        with self.assertRaisesRegex(ValueError, "executable-drift"):
            admit_opencode_binary(binary, expected_sha256=H("tampered"),
                                  expected_version="native-fixture-0.1.0")
        with self.assertRaises(ValueError):
            admit_opencode_binary(binary, expected_sha256=digest,
                                  expected_version="")


class LauncherTests(unittest.TestCase):
    def test_no_native_work_before_executable_and_source_admission(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "workspace"
            (repo / ".git").mkdir(parents=True)
            exe = root / "opencode"
            exe.write_text("#!/bin/sh\necho native-0.1\n")
            exe.chmod(0o700)
            key = root / "host.key"
            key.write_bytes(KEY)
            key.chmod(0o600)
            data = dict(
                manifest=manifest(), trial_id=manifest()["assignments"][0]["trial_id"],
                opencode_executable=exe, expected_binary_sha256=H("wrong"),
                expected_version="native-0.1", run_root=root / "run",
                workspace=repo, prompt="Find source owner",
                allow_unconfined_execution=True,
                host_key_file=key, host_identity="gateway-host",
                current=source(), replaced=None, upstream=UPSTREAM,
                approved_origin="https://api.example.invalid",
                upstream_api_key="private-provider-secret", catalog_sha256=H("catalog"),
                oracle_sha256=H("oracle"), workspace_sha256=H("workspace"),
            )
            with patch("benchmarks.harness.opencode_native_trial.subprocess.run") as run:
                with self.assertRaises(ValueError):
                    launch_native_trial(**data)
                run.assert_not_called()
                self.assertFalse(data["run_root"].exists())

    def test_unconfined_native_execution_requires_explicit_approval(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            (workspace / ".git").mkdir(parents=True)
            binary = root / "fake-opencode"
            binary.write_text("#!/bin/sh\\necho fixture-version\\n")
            binary.chmod(0o700)
            key = root / "key"
            key.write_bytes(KEY)
            key.chmod(0o600)
            cell = manifest()["assignments"][0]
            kwargs = dict(
                manifest=manifest(), trial_id=cell["trial_id"],
                opencode_executable=binary, expected_binary_sha256=sha256_file(binary),
                expected_version="fixture-version", run_root=root / "new",
                workspace=workspace, prompt="find source",
                host_key_file=key, host_identity="test-host",
                current=source(), replaced=None, upstream=UPSTREAM,
                approved_origin="https://api.example.invalid",
                upstream_api_key="not-a-real-key",
                catalog_sha256=H("catalog"), oracle_sha256=H("oracle"),
                workspace_sha256=H("workspace"),
            )
            with self.assertRaisesRegex(ValueError, "unconfined-native-launch"):
                launch_native_trial(**kwargs)
            self.assertFalse(kwargs["run_root"].exists())
            with patch("benchmarks.harness.opencode_native_trial.subprocess.run") as run:
                (workspace / "opencode.json").write_text("{}")
                with self.assertRaisesRegex(ValueError, "uncontrolled-project-opencode-config"):
                    launch_native_trial(**kwargs, allow_unconfined_execution=True)
                run.assert_not_called()
            self.assertFalse(kwargs["run_root"].exists())

    def test_launch_environment_is_isolated_and_not_a_native_provenance_certificate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "workspace"
            (repo / ".git").mkdir(parents=True)
            exe = root / "opencode"
            exe.write_text("#!/bin/sh\necho native-0.1\n")
            exe.chmod(0o700)
            key = root / "host.key"
            key.write_bytes(KEY)
            key.chmod(0o600)
            m = manifest()
            cell = next(row for row in m["assignments"] if row["arm"] == "control")
            def spawn(argv, **kwargs):
                if argv[1] == "--version":
                    return SimpleNamespace(returncode=0, stdout=b"native-0.1\n", stderr=b"")
                env = kwargs["env"]
                self.assertIn("OPENCODE_CONFIG", env)
                self.assertNotIn("BENCHMARK_UPSTREAM_API_KEY", env)
                self.assertNotIn("private-provider-secret", json.dumps(env))
                self.assertEqual(argv[:4], [str(exe), "run", "--model",
                                            "agentscookbook-captured/gpt-fixture"])
                self.assertEqual(kwargs["cwd"], repo)
                return SimpleNamespace(returncode=0)
            with patch("benchmarks.harness.opencode_native_trial.subprocess.run",
                       side_effect=spawn):
                report = launch_native_trial(
                    m, trial_id=cell["trial_id"], opencode_executable=exe,
                    expected_binary_sha256=sha256_file(exe),
                    expected_version="native-0.1",
                    run_root=root / "run", workspace=repo,
                    prompt="Find source owner", host_key_file=key,
                    allow_unconfined_execution=True,
                    host_identity="gateway-host", current=source(), replaced=None,
                    upstream=UPSTREAM, approved_origin="https://api.example.invalid",
                    upstream_api_key="private-provider-secret",
                    catalog_sha256=H("catalog"), oracle_sha256=H("oracle"),
                    workspace_sha256=H("workspace"),
                )
            self.assertEqual(report["native_exit_code"], 0)
            self.assertFalse(report["native_gateway_route_completed"])
            self.assertFalse(report["native_process_origin_proven"])
            self.assertFalse(report["native_model_input_delivered_proven"])
            self.assertFalse(report["host_receipt_finalized"])
            self.assertTrue((root / "run" / "opencode.json").exists())


if __name__ == "__main__":
    unittest.main()
