"""Provider-issued native call ↔ complete OpenCode export ↔ host receipt.

All provider responses and native exports are synthetic. No real model, MCP
subprocess, or independent native process attestation occurs in this ring.
"""

from __future__ import annotations

import copy
import hashlib
import json
import unittest

from benchmarks.harness.host_input_attestation import canonical, verify_host_attestations
from benchmarks.harness.native_provider_tool_provenance import (
    extract_provider_issued_tool, require_issued_tool_continuation,
)
from benchmarks.harness.opencode_native_session import (
    finalize_native_export, inspect_native_export,
)
from benchmarks.tests.test_opencode_native_session import (
    NativeSessionBindingTests, source,
)

ARGS = {"path": "src/owner.py", "selector": {"kind": "owner", "limit": 3}}


def issued_call(*, args=None, name="hashmarks_task_evidence", call_id="native-call-1",
                pretty=False):
    arguments = json.dumps(
        ARGS if args is None else args, sort_keys=pretty,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    doc = {"choices": [{
        "index": 0, "finish_reason": "tool_calls",
        "message": {"role": "assistant", "tool_calls": [{
            "type": "function", "id": call_id,
            "function": {"name": name, "arguments": arguments},
        }]},
    }]}
    return extract_provider_issued_tool(canonical(doc), streamed=False)


class NativeProviderSessionChainTests(NativeSessionBindingTests):
    def _native(self, *, arguments=None, call_id="native-call-1",
                tool="mcp__hashmarks__task_evidence"):
        document = json.loads(self.raw(call_id=call_id, tool=tool))
        document["messages"][0]["parts"][0]["state"]["input"] = (
            copy.deepcopy(ARGS) if arguments is None else arguments
        )
        return canonical(document)

    def strict_inspect(self, raw, issued):
        row = self.capture._recorded[0]
        return inspect_native_export(
            raw, session_id=self.session_id, title=self.title,
            workspace=self.workspace,
            expected_provider="agentscookbook-captured",
            expected_model="gpt-fixture", expected_packet=source()["content"],
            expected_call_id_sha256=row["call_id_sha256"],
            expected_packet_sha256=row["packet_sha256"],
            provider_issued=issued, require_provider_issued=True,
        )

    def strict_finalize(self, raw, issued):
        return finalize_native_export(
            raw, capture=self.capture, session_id=self.session_id,
            title=self.title, workspace=self.workspace,
            run_root=self.run_root, expected_provider="agentscookbook-captured",
            expected_model="gpt-fixture", original_packet=source()["content"],
            provider_issued=issued, require_provider_issued=True,
        )

    def test_exact_full_input_and_approved_aliases_bind_before_receipt(self):
        issued = issued_call(pretty=True)
        raw = self._native()
        result = self.strict_inspect(raw, issued)
        self.assertTrue(result["provider_issued_native_arguments_bound"])
        self.assertEqual(
            result["provider_issued_response_sha256"],
            issued["provider_response_sha256"],
        )
        done = self.strict_finalize(raw, issued)
        self.assertTrue(done["provider_issued_native_arguments_bound"])
        self.assertTrue(done["signed_host_input_and_transport_verified"])
        attestation = self.run_root / "host-attestation.json"
        report = verify_host_attestations(
            self.run_root / "trajectory.json", attestation,
            self.capture._key,
            campaign_id=self.plan["design"]["campaign_id"],
            trial_id=self.cell["trial_id"],
        )
        self.assertEqual(report["delivery_state"], "PROVEN")
        self.assertEqual(report["provider_submission_state"], "SUBMITTED")
        self.assertFalse(report["model_attention_proven"])
        self.assertFalse(done["independent_native_export_proven"])

    def test_missing_provider_issue_and_changed_native_full_arguments_fail_before_files(self):
        good = issued_call()
        cases = (
            (self._native(), None),
            (self._native(arguments={"path": "src/owner.py"}), good),
            (self._native(arguments={**ARGS, "selector": {"kind": "other"}}), good),
            (self._native(arguments={**ARGS, "extra": "hidden"}), good),
            (self._native(tool="mcp__hashmarks__find"), good),
            (self._native(), issued_call(call_id="other-call")),
            (self._native(), issued_call(name="hashmarks_find")),
            (self._native(arguments={"selector": [1, 2], "path": "src/owner.py"}), good),
        )
        for i, (raw, issued) in enumerate(cases):
            with self.subTest(i=i):
                with self.assertRaisesRegex(ValueError, "not-equivalent-to-provider-issued"):
                    self.strict_finalize(raw, issued)
                self.assertFalse((self.run_root / "native-session.json").exists())
                self.assertFalse((self.run_root / "trajectory.json").exists())
                self.assertFalse((self.run_root / "host-attestation.json").exists())

    def test_legacy_synthetic_export_not_upgraded_without_provider_proof(self):
        inspected = self.inspect(self.raw())
        self.assertFalse(inspected["provider_issued_native_arguments_bound"])
        self.assertIsNone(inspected["provider_issued_response_sha256"])
        row = self.capture._recorded[0]
        with self.assertRaises(ValueError):
            inspect_native_export(
                self.raw(), session_id=self.session_id,
                title=self.title, workspace=self.workspace,
                expected_provider="agentscookbook-captured",
                expected_model="gpt-fixture", expected_packet=source()["content"],
                expected_call_id_sha256=row["call_id_sha256"],
                expected_packet_sha256=row["packet_sha256"],
                require_provider_issued=True,
            )


if __name__ == "__main__":
    unittest.main()
