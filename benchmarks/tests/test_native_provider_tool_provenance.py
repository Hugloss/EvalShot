"""E273–E276: provider-issued native tool-call provenance attack ring.

Every provider response and request here is model-free synthetic source data.
No real model or MCP function is executed in this test suite.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.native_provider_tool_provenance import (
    extract_provider_issued_tool, require_issued_tool_continuation,
)
from benchmarks.harness.opencode_native_gateway import NativeOpenCodeGateway
from benchmarks.tests.test_opencode_native_gateway import (
    H, KEY, UPSTREAM, manifest, source,
)

CALL = {
    "id": "provider-call-1", "type": "function",
    "function": {"name": "hashmarks_task_evidence", "arguments": '{"path":"src"}'},
}
SUCCESS = b'{"choices":[{"message":{"role":"assistant","content":"done"}}]}'


def response(*, tool_call=None) -> bytes:
    return canonical({"choices": [{
        "index": 0,
        "message": {"role": "assistant", "tool_calls": [
            CALL if tool_call is None else tool_call,
        ]},
        "finish_reason": "tool_calls",
    }]})


def sse_response() -> bytes:
    parts = [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "id": "provider-call-1", "type": "function",
            "function": {"name": "hashmarks_task_evidence", "arguments": '{"path":'},
        }]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "function": {"arguments": '"src"}'},
        }]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    return b"".join(b"data: " + canonical(e) + b"\n\n" for e in parts) + b"data: [DONE]\n\n"


def continuation(selected: object, *, tool_call=None, call_id="provider-call-1"):
    return {
        "model": "gpt-fixture",
        "messages": [
            {"role": "user", "content": "find owner"},
            {"role": "assistant", "tool_calls": [
                CALL if tool_call is None else tool_call,
            ]},
            {"role": "tool", "tool_call_id": call_id, "content": selected},
        ],
    }


def pre_request(*, streaming=False):
    return canonical({
        "model": "gpt-fixture",
        "messages": [{"role": "user", "content": "find owner"}],
        "stream": streaming,
    })


class IssuedToolParserTests(unittest.TestCase):
    def test_complete_json_and_split_sse_bind_identical_function_and_argument_bytes(self):
        json_item = extract_provider_issued_tool(response(), streamed=False)
        sse_item = extract_provider_issued_tool(sse_response(), streamed=True)
        self.assertEqual(json_item["call_id"], "provider-call-1")
        self.assertEqual(json_item["function_name"], "hashmarks_task_evidence")
        self.assertEqual(json_item["arguments_sha256"], sse_item["arguments_sha256"])
        self.assertNotEqual(json_item["provider_response_sha256"],
                            sse_item["provider_response_sha256"])
        good = continuation("packet")
        require_issued_tool_continuation(good, call_id="provider-call-1",
                                         provider_issued=json_item)

    def test_no_provider_call_cannot_be_backfilled_by_client(self):
        observed = extract_provider_issued_tool(canonical({
            "choices": [{
                "message": {"role": "assistant", "content": "no tool"},
                "finish_reason": "stop",
            }],
        }), streamed=False)
        self.assertIsNone(observed)
        with self.assertRaisesRegex(ValueError, "not-issued-by-observed-provider"):
            require_issued_tool_continuation(
                continuation("packet"), call_id="provider-call-1",
                provider_issued=observed,
            )

    def test_mismatch_id_function_arguments_or_duplicate_calls_fail(self):
        issued = extract_provider_issued_tool(response(), streamed=False)
        for altered in (
            {**CALL, "id": "forged-call"},
            {**CALL, "function": {**CALL["function"], "name": "hashmarks_find"}},
            {**CALL, "function": {**CALL["function"], "arguments": '{"path":"other"}'}},
        ):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                require_issued_tool_continuation(
                    continuation("packet", tool_call=altered),
                    call_id=altered["id"], provider_issued=issued,
                )
        parallel = json.loads(response())
        parallel["choices"][0]["message"]["tool_calls"].append(CALL)
        with self.assertRaisesRegex(ValueError, "incomplete-or-parallel"):
            extract_provider_issued_tool(canonical(parallel), streamed=False)

    def test_incomplete_or_foreign_provider_issued_call_rejects(self):
        invalid = (
            {**CALL, "function": {**CALL["function"], "name": "bash"}},
            {**CALL, "function": {**CALL["function"], "arguments": "invalid"}},
            {**CALL, "id": ""},
        )
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                extract_provider_issued_tool(response(tool_call=item),
                                             streamed=False)

    def test_stream_must_be_complete_unambiguous_and_single_call(self):
        good = sse_response()
        invalid = [
            good.replace(b"data: [DONE]\n\n", b""),
            good.replace(b'"index":0', b'"index":1', 1),
            good.replace(b'"hashmarks_task_evidence"', b'"bash"'),
            good.replace(b'"tool_calls"', b'"unrecognized"'),
            good.replace(b'"finish_reason":"tool_calls"',
                         b'"finish_reason":"stop"'),
            good + b"data: [DONE]\n\n",
        ]
        for item in invalid:
            with self.subTest(item=item[:60]), self.assertRaises(ValueError):
                extract_provider_issued_tool(item, streamed=True)


class GatewayProviderIssueAttacks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        key = self.root / "key"
        key.write_bytes(KEY)
        key.chmod(0o600)
        frozen = manifest()
        trial = next(c for c in frozen["assignments"] if c["arm"] == "variant")
        self.gw = NativeOpenCodeGateway(
            frozen, trial_id=trial["trial_id"], host_key_file=key,
            host_identity="host", current=source(), replaced=None,
            gateway_token="gateway-private-token-test-native-123456",
            upstream=UPSTREAM, approved_origin="https://api.example.invalid",
            upstream_api_key="host-upstream-secret",
            catalog_sha256=H("catalog"), oracle_sha256=H("oracle"),
            workspace_sha256=H("workspace"),
            enforce_chat_tool_wire_contract=True,
        )

    def tearDown(self):
        self.gw.close()
        self.tmp.cleanup()

    def _call(self, data: bytes):
        return self.gw._handle_request(
            data, authorization="Bearer " + self.gw.token,
        )

    def test_unissued_client_tool_call_cannot_reach_provider(self):
        with patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
            return_value=SUCCESS,
        ) as upstream:
            with self.assertRaisesRegex(ValueError, "not-issued-by-observed-provider"):
                self._call(canonical(continuation(
                    self.gw.selected["selected_content"])))
            upstream.assert_not_called()
        self.assertEqual(self.gw.result()["post_evidence_submissions"], 0)

    def test_pre_response_issues_once_then_exact_native_continue(self):
        issued = response()
        with patch(
            "benchmarks.harness.opencode_native_gateway._forward_pre_evidence",
            return_value=issued,
        ) as pre, patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
            return_value=SUCCESS,
        ) as upstream:
            self.assertEqual(self._call(pre_request()), issued)
            with self.assertRaisesRegex(ValueError, "continuation-absent"):
                self._call(pre_request())
            self.assertEqual(self._call(canonical(continuation(
                self.gw.selected["selected_content"]))), SUCCESS)
            self.assertEqual(pre.call_count, 1)
            self.assertEqual(upstream.call_count, 1)
        report = self.gw.result()
        self.assertTrue(report["provider_issued_tool_observed"])
        self.assertEqual(report["provider_issued_response_sha256"],
                         hashlib.sha256(issued).hexdigest())
        self.assertTrue(report["provider_issued_tool_continuation_verified"])
        self.assertEqual(report["post_evidence_submissions"], 1)

    def test_invalid_or_ambiguous_issued_response_closes_gateway(self):
        bad = canonical({"choices": [{
            "message": {"role": "assistant", "tool_calls": [CALL, CALL]},
            "finish_reason": "tool_calls",
        }]})
        with patch(
            "benchmarks.harness.opencode_native_gateway._forward_pre_evidence",
            return_value=bad,
        ), patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
            return_value=SUCCESS,
        ) as upstream:
            with self.assertRaises(ValueError):
                self._call(pre_request())
            with self.assertRaisesRegex(ValueError, "extra-request"):
                self._call(canonical(continuation(
                    self.gw.selected["selected_content"])))
            upstream.assert_not_called()
        self.assertEqual(self.gw.result()["gateway_state"],
                         "PRE_EVIDENCE_UNQUALIFIED")

    def test_real_streamed_upstream_call_must_match_original_tool_arguments(self):
        stream = sse_response()
        with patch(
            "benchmarks.harness.opencode_native_gateway._forward_pre_evidence",
            return_value=stream,
        ), patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
            return_value=SUCCESS,
        ) as upstream:
            self._call(pre_request(streaming=True))
            changed = copy.deepcopy(CALL)
            changed["function"]["arguments"] = '{"path":"foreign"}'
            with self.assertRaisesRegex(ValueError, "arguments-not-issued"):
                self._call(canonical(continuation(
                    self.gw.selected["selected_content"], tool_call=changed,
                )))
            upstream.assert_not_called()
            self.assertEqual(self._call(canonical(continuation(
                self.gw.selected["selected_content"]))), SUCCESS)
            self.assertEqual(upstream.call_count, 1)


if __name__ == "__main__":
    unittest.main()
