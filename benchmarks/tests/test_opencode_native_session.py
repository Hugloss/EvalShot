"""E253/E254 regression ring: actual native-session parts vs host transport.

Synthetic input is never described as an empirical model-backed OpenCode run.
The host MAC is test-owned. These tests exercise exact call/packet equality
and prove a gateway request cannot invent a missing native tool observation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.host_input_attestation import canonical, verify_host_attestations
from benchmarks.harness.opencode_native_export import (
    acquire_native_export, select_native_session,
    _run_bounded_native, native_export_preflight,
)
from benchmarks.harness.opencode_native_session import (
    finalize_native_export, inspect_native_export,
)
from benchmarks.harness.trusted_host_capture import TrustedModelRequestCapture
from benchmarks.harness.trusted_treatments import select_treatment
from benchmarks.tests.test_opencode_native_gateway import manifest, source

KEY = b"separate-trusted-host-only-key-for-native-eval"
H = lambda val: hashlib.sha256(str(val).encode()).hexdigest()


def native_export(*, title: str, session_id: str, workspace: Path,
                  call_id: str = "native-call-1", tool: str = "hashmarks_task_evidence",
                  packet: object | None = None,
                  final: bool = True) -> bytes:
    packet = source()["content"] if packet is None else packet
    parts = [{
        "type": "tool", "tool": tool, "callID": call_id,
        "state": {"status": "completed", "output": json.dumps(packet)},
    }]
    if final:
        parts.append({"type": "text", "text": "src/owner.py"})
    return canonical({
        "info": {
            "id": session_id, "title": title,
            "directory": str(workspace.resolve()),
        },
        "messages": [{
            "info": {
                "role": "assistant",
                "providerID": "agentscookbook-captured",
                "modelID": "gpt-fixture",
            },
            "parts": parts,
        }],
    })


class NativeSessionBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.run_root = self.root / "host-output"
        self.run_root.mkdir(mode=0o700)
        self.key = self.root / "host.key"
        self.key.write_bytes(KEY)
        self.key.chmod(0o600)
        self.plan = manifest()
        self.cell = next(c for c in self.plan["assignments"] if c["arm"] == "control")
        self.title = "agentscookbook:" + self.cell["trial_id"] + ":test"
        self.session_id = "ses_test_123"
        self.capture = TrustedModelRequestCapture(
            self.plan, trial_id=self.cell["trial_id"],
            harness="opencode-native", task="source-owner",
            replicate=1, arm="control", host_identity="real-provider-host",
            host_key_file=self.key,
        )
        selected = select_treatment(
            self.plan, trial_id=self.cell["trial_id"],
            harness="opencode-native", task="source-owner", replicate=1,
            arm="control", current=source(),
        )
        request = canonical({
            "model": "gpt-fixture", "messages": [
                {"role": "user", "content": "Find owner"},
                {"role": "tool", "tool_call_id": "native-call-1",
                 "content": selected["selected_content"]},
            ],
        })
        prompt = {
            "request_settings": {"model": "gpt-fixture"},
            "non_tool_messages": [{"role": "user", "content": "Find owner"}],
        }
        self.capture.observe_outbound_request(
            serialized_model_request=request,
            tool_call_id="native-call-1", returned_packet=source()["content"],
            request_sequence=1, current=source(),
            catalog_sha256=H("catalog"),
            prompt_sha256=hashlib.sha256(canonical(prompt)).hexdigest(),
            oracle_sha256=H("oracle"), workspace_sha256=H("workspace"),
        )
        self.capture.begin_provider_transport()
        self.capture.record_provider_submission(
            tool_call_id="native-call-1",
            serialized_model_request=request,
            endpoint_sha256=H("endpoint"), status=200,
            response_sha256=H("response"), response_bytes=120,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def raw(self, **kwargs):
        return native_export(
            title=self.title, session_id=self.session_id,
            workspace=self.workspace, **kwargs,
        )

    def inspect(self, raw: bytes):
        row = self.capture._recorded[0]
        return inspect_native_export(
            raw, title=self.title, session_id=self.session_id,
            workspace=self.workspace, expected_provider="agentscookbook-captured",
            expected_model="gpt-fixture",
            expected_packet=source()["content"],
            expected_call_id_sha256=row["call_id_sha256"],
            expected_packet_sha256=row["packet_sha256"],
        )

    def test_raw_native_export_binds_exact_call_packet_and_completed_final(self):
        result = self.inspect(self.raw())
        self.assertEqual(result["export_sha256"], hashlib.sha256(self.raw()).hexdigest())
        self.assertEqual(result["atif"]["steps"][0]["tool_calls"][0]["tool_call_id"],
                         "native-call-1")
        self.assertFalse(result["native_process_origin_proven"])
        self.assertFalse(result["independent_native_export_proven"])

    def test_finish_host_receipt_requires_native_observation_and_reverifies(self):
        result = finalize_native_export(
            self.raw(), capture=self.capture, session_id=self.session_id,
            title=self.title, workspace=self.workspace, run_root=self.run_root,
            expected_provider="agentscookbook-captured",
            expected_model="gpt-fixture", original_packet=source()["content"],
        )
        self.assertTrue(result["native_session_to_gateway_packet_bound"])
        self.assertTrue(result["signed_host_input_and_transport_verified"])
        self.assertFalse(result["native_process_origin_proven"])
        saved = self.run_root / "native-session.json"
        self.assertEqual(saved.read_bytes(), self.raw())
        trace = self.run_root / "trajectory.json"
        assert (self.run_root / "host-attestation.json").exists()
        report = verify_host_attestations(
            trace, self.run_root / "host-attestation.json", KEY,
            campaign_id=self.plan["design"]["campaign_id"],
            trial_id=self.cell["trial_id"],
        )
        self.assertEqual(report["provider_submission_state"], "SUBMITTED")
        with self.assertRaisesRegex(ValueError, "reuse-or-overwrite"):
            finalize_native_export(
                self.raw(), capture=self.capture,
                session_id=self.session_id, title=self.title,
                workspace=self.workspace, run_root=self.run_root,
                expected_provider="agentscookbook-captured",
                expected_model="gpt-fixture",
                original_packet=source()["content"],
            )

    def test_native_pre_subject_search_preserves_gateway_receipt_and_full_trace(self):
        native = json.loads(self.raw())
        native["messages"][0]["parts"].insert(0, {
            "type": "tool", "tool": "grep", "callID": "native-grep-first",
            "state": {"status": "completed",
                      "input": {"pattern": "resolve"},
                      "output": "src/unrelated.py"},
        })
        saved = finalize_native_export(
            canonical(native), capture=self.capture, session_id=self.session_id,
            title=self.title, workspace=self.workspace, run_root=self.run_root,
            expected_provider="agentscookbook-captured",
            expected_model="gpt-fixture", original_packet=source()["content"],
        )
        self.assertTrue(saved["signed_host_input_and_transport_verified"])
        atif = json.loads((self.run_root / "trajectory.json").read_bytes())
        self.assertEqual(
            [step["tool_calls"][0]["function_name"] for step in atif["steps"]],
            ["grep", "mcp__hashmarks__task_evidence"],
        )
        self.assertEqual(atif["source_capture_exhaustiveness"], "UNKNOWN")
        report = verify_host_attestations(
            self.run_root / "trajectory.json",
            self.run_root / "host-attestation.json", KEY,
            campaign_id=self.plan["design"]["campaign_id"],
            trial_id=self.cell["trial_id"],
        )
        self.assertEqual(report["verified_model_input_packets"], 1)
        self.assertEqual(report["provider_submission_state"], "SUBMITTED")

    def test_native_identity_and_model_tampering_reject_before_artifacts(self):
        bad = [
            self.raw(call_id="foreign-call-id"),
            self.raw(packet={"owner": "unrelated.py"}),
            self.raw(tool="bash"),
            self.raw(final=False),
        ]
        forged = json.loads(self.raw())
        forged["info"]["directory"] = "/tmp/foreign"
        bad.append(canonical(forged))
        forged = json.loads(self.raw())
        forged["messages"][0]["info"]["providerID"] = "another-provider"
        bad.append(canonical(forged))
        forged = json.loads(self.raw())
        forged["messages"][0]["parts"].append(copy.deepcopy(
            forged["messages"][0]["parts"][0]
        ))
        bad.append(canonical(forged))
        for raw in bad:
            with self.subTest(raw=raw[:60]):
                with self.assertRaises(ValueError):
                    finalize_native_export(
                        raw, capture=self.capture,
                        session_id=self.session_id, title=self.title,
                        workspace=self.workspace, run_root=self.run_root,
                        expected_provider="agentscookbook-captured",
                        expected_model="gpt-fixture",
                        original_packet=source()["content"],
                    )
                self.assertFalse((self.run_root / "native-session.json").exists())

    def test_no_host_transport_is_never_promoted_from_native_export(self):
        self.capture._recorded[0].pop("transport")
        with self.assertRaisesRegex(ValueError, "no-complete-host-observed"):
            finalize_native_export(
                self.raw(), capture=self.capture, session_id=self.session_id,
                title=self.title, workspace=self.workspace, run_root=self.run_root,
                expected_provider="agentscookbook-captured",
                expected_model="gpt-fixture", original_packet=source()["content"],
            )


class NativeExportSelectionTests(unittest.TestCase):
    def test_unique_epoch_title_and_directory_required(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            record = {
                "id": "ses_verified",
                "title": "agentscookbook:trial:nonce",
                "directory": str(workspace.resolve()), "updated": 9999,
            }
            selected = select_native_session(
                canonical([record]), exact_title=record["title"],
                workspace=workspace, started_at_ms=9000,
            )
            self.assertEqual(selected, record["id"])
            for entries in (
                [record, record],
                [{**record, "updated": 200}],
                [{**record, "directory": "/other"}],
                [{**record, "title": "some-other-session"}],
            ):
                with self.assertRaises(ValueError):
                    select_native_session(
                        canonical(entries), exact_title=record["title"],
                        workspace=workspace, started_at_ms=9000,
                    )

    def test_native_capture_limit_is_required_before_export_process(self):
        with tempfile.TemporaryDirectory() as td:
            with patch(
                "benchmarks.harness.opencode_native_export.shutil.which",
                return_value=None,
            ), patch(
                "benchmarks.harness.opencode_native_export.subprocess.run",
            ) as launched:
                with self.assertRaisesRegex(ValueError, "prlimit-unavailable"):
                    native_export_preflight()
                with self.assertRaisesRegex(ValueError, "prlimit-unavailable"):
                    _run_bounded_native(
                        ["/trusted/opencode", "export", "ses_dummy"],
                        workspace=Path(td), environment={"HOME": td},
                        max_bytes=1024,
                    )
                launched.assert_not_called()

    def test_acquisition_uses_exact_opencode_session_commands(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            index = canonical([{
                "id": "ses_source", "title": "unique-session-title",
                "directory": str(workspace.resolve()), "updated": 5000,
            }])
            exported = b'{"info":{"id":"ses_source"},"messages":[]}'
            with patch(
                "benchmarks.harness.opencode_native_export._run_bounded_native",
                side_effect=[index, exported],
            ) as native:
                session_id, raw = acquire_native_export(
                    Path("/trusted/opencode"), workspace=workspace,
                    environment={"HOME": td},
                    exact_title="unique-session-title", started_at_ms=4000,
                )
            self.assertEqual(session_id, "ses_source")
            self.assertEqual(raw, exported)
            self.assertEqual(native.call_count, 2)
            self.assertEqual(native.call_args_list[0].args[0][1:3], ["session", "list"])
            self.assertEqual(native.call_args_list[1].args[0][1:],
                             ["export", "ses_source"])


if __name__ == "__main__":
    unittest.main()
