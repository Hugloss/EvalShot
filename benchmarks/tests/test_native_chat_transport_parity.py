"""E265-E268 native Chat tool-content transport attack ring.

All sources, provider responses, OpenCode binaries and assignments are
synthetic. The suite proves syntax and intervention-admission contracts,
not genuine model calls, SDK compatibility or an empirical effect.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.native_chat_transport_parity import (
    audit_frozen_chat_treatments, inspect_selected_content,
    require_chat_tool_content,
)
from benchmarks.harness.opencode_native_gateway import NativeOpenCodeGateway
from benchmarks.harness.opencode_native_trial import launch_native_trial
from benchmarks.harness.trusted_treatments import select_treatment
from benchmarks.tests.test_opencode_native_gateway import (
    H, KEY, UPSTREAM, manifest, request, source,
)

RESPONSE = b'{"choices":[{"index":0,"message":{"role":"assistant","content":"owner"}}]}'


def selected(*, arm: str) -> dict:
    plan = manifest()
    cell = next(c for c in plan["assignments"] if c["arm"] == arm)
    return select_treatment(
        plan, trial_id=cell["trial_id"],
        harness=cell["harness"], task=cell["task"],
        replicate=cell["replicate"], arm=arm,
        current=source(),
    )


def bound_request(*, selected: object) -> bytes:
    body = json.loads(request(selected=selected, tool=True))
    body["messages"].insert(-1, {
        "role": "assistant",
        "tool_calls": [{
            "id": "native-tool-1",
            "type": "function",
            "function": {"name": "hashmarks_task_evidence", "arguments": "{}"},
        }],
    })
    return canonical(body)


class NativeChatWireContractAttacks(unittest.TestCase):
    def test_original_structured_json_is_not_openai_role_tool_text(self):
        visible = selected(arm="control")
        self.assertIsInstance(visible["selected_content"], dict)
        report = inspect_selected_content(visible)
        self.assertEqual(report["model_input_representation"],
                         "unsupported-nested-json-tool-content")
        self.assertFalse(report["chat_tool_content_wire_compatible"])
        with self.assertRaisesRegex(ValueError, "shape-not-supported"):
            require_chat_tool_content(visible, actual_tool_content=visible["selected_content"])
        # Converting the JSON object to a semantically identical text
        # is not sufficient evidence of the frozen structured surface.
        with self.assertRaisesRegex(ValueError, "shape-not-supported"):
            require_chat_tool_content(
                visible, actual_tool_content=json.dumps(visible["selected_content"]),
            )

    def test_text_arm_requires_exact_string_and_surface_hash(self):
        visible = selected(arm="variant")
        expected = visible["selected_content"]
        self.assertIsInstance(expected, str)
        self.assertTrue(inspect_selected_content(visible)["chat_tool_content_wire_compatible"])
        require_chat_tool_content(visible, actual_tool_content=expected)
        for forged in (
            json.loads(expected),  # parsed object is a different wire type
            json.dumps(json.loads(expected), indent=2),  # same semantics, wrong bytes
            [expected],  # array of text is not the selected surface
            expected + "\n",  # byte drift
            None,
            True,
        ):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                require_chat_tool_content(visible, actual_tool_content=forged)

    def test_frozen_study_pair_is_blocked_without_compatible_control(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = manifest()
            for cell in plan["assignments"]:
                (root / (cell["trial_id"] + ".json")).write_bytes(canonical({
                    "current": source(), "replaced": None,
                }))
            result = audit_frozen_chat_treatments(plan, sources_root=root)
            self.assertEqual(result["trials"], 2)
            self.assertEqual(result["pairs"], 1)
            self.assertEqual(result["transport_admissible_pairs"], 0)
            self.assertFalse(result["fully_compatible_for_paired_study"])
            self.assertEqual(result["representation_counts"], {
                "openai-chat-tool-plain-text-v1": 1,
                "unsupported-nested-json-tool-content": 1,
            })
            self.assertFalse(result["causal_hashmarks_uplift_proven"])
            # Unknown extra external authority must not silently disappear.
            (root / "foreign.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "foreign"):
                audit_frozen_chat_treatments(plan, sources_root=root)

    def test_provider_request_is_not_rewritten_or_json_coerced(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            key = root / "host.key"
            key.write_bytes(KEY)
            key.chmod(0o600)
            plan = manifest()
            cell = next(c for c in plan["assignments"] if c["arm"] == "variant")
            gateway = NativeOpenCodeGateway(
                plan, trial_id=cell["trial_id"], host_key_file=key,
                host_identity="trusted-host", current=source(), replaced=None,
                gateway_token="local-privileged-bearer-value-1234567",
                upstream=UPSTREAM,
                approved_origin="https://api.example.invalid",
                upstream_api_key="host-only-key",
                catalog_sha256=H("catalog"),
                oracle_sha256=H("oracle"),
                workspace_sha256=H("workspace"),
                enforce_chat_tool_wire_contract=True,
            )
            body = bound_request(
                selected=gateway.selected["selected_content"],
            )
            observed = []
            issued = canonical({"choices": [{
                "message": {"role": "assistant", "tool_calls": [{
                    "id": "native-tool-1", "type": "function",
                    "function": {"name": "hashmarks_task_evidence",
                                 "arguments": "{}"},
                }]}, "finish_reason": "tool_calls",
            }]})
            with patch(
                "benchmarks.harness.opencode_native_gateway._forward_pre_evidence",
                return_value=issued,
            ), patch(
                "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
                side_effect=lambda **kw: (observed.append(kw), RESPONSE)[1],
            ) as upstream:
                gateway._handle_request(
                    request(selected=None, tool=False),
                    authorization="Bearer " + gateway.token,
                )
                other = bound_request(
                    selected=json.loads(gateway.selected["selected_content"]),
                )
                with self.assertRaisesRegex(ValueError, "not-byte-identical"):
                    gateway._handle_request(
                        other, authorization="Bearer " + gateway.token,
                    )
                upstream.assert_not_called()
                result = gateway._handle_request(
                    body, authorization="Bearer " + gateway.token,
                )
                self.assertEqual(result, RESPONSE)
                self.assertEqual(len(observed), 1)
                self.assertEqual(observed[0]["serialized_model_request"], body)
                self.assertEqual(gateway.result()["post_evidence_submissions"], 1)
                self.assertTrue(gateway.result()["native_chat_tool_wire_contract_enforced"])

    def test_isolated_control_fails_before_gateway_and_workspace_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            (work / ".git").mkdir(parents=True)
            executable = root / "opencode"
            executable.write_text("#!/bin/sh\necho fake-version\n")
            executable.chmod(0o700)
            hashmarks = root / "hashmarks"
            hashmarks.write_text("#!/bin/sh\nexit 0\n")
            hashmarks.chmod(0o700)
            key = root / "host.key"
            key.write_bytes(KEY)
            key.chmod(0o600)
            plan = manifest()
            cell = next(c for c in plan["assignments"] if c["arm"] == "control")
            run_root = root / "new-run"
            with patch("benchmarks.harness.opencode_native_trial.admit_netns_substrate",
                       return_value={"bwrap": "/usr/bin/bwrap", "python": "/usr/bin/python3"}
                       ), patch(
                "benchmarks.harness.opencode_native_trial.native_export_preflight"
            ), patch(
                "benchmarks.harness.opencode_native_trial.probe_netns_kernel",
                return_value={"kernel_probe_qualified": True},
            ), patch(
                "benchmarks.harness.opencode_native_trial.NativeOpenCodeGateway"
            ) as gateway, patch(
                "benchmarks.harness.opencode_native_trial.subprocess.run",
                wraps=None,
            ) as launch:
                # Version probe needs a deterministic native return.
                from types import SimpleNamespace
                launch.return_value = SimpleNamespace(
                    returncode=0, stdout=b"fake-version\n", stderr=b"",
                )
                with self.assertRaisesRegex(ValueError, "shape-not-supported-before-model-work"):
                    launch_native_trial(
                        plan, trial_id=cell["trial_id"],
                        opencode_executable=executable,
                        expected_binary_sha256=hashlib.sha256(
                            executable.read_bytes(),
                        ).hexdigest(),
                        expected_version="fake-version", run_root=run_root,
                        workspace=work, prompt="Find owner",
                        host_key_file=key, host_identity="host",
                        current=source(), replaced=None,
                        upstream=UPSTREAM, approved_origin="https://api.example.invalid",
                        upstream_api_key="fake-provider-key",
                        catalog_sha256=H("catalog"),
                        oracle_sha256=H("oracle"),
                        workspace_sha256=H("workspace"),
                        use_netns_isolation=True,
                        hashmarks_executable=hashmarks,
                        expected_hashmarks_sha256=hashlib.sha256(
                            hashmarks.read_bytes(),
                        ).hexdigest(),
                    )
                gateway.assert_not_called()
                self.assertFalse(run_root.exists())
                self.assertEqual(launch.call_count, 1)  # only --version, never model


if __name__ == "__main__":
    unittest.main()
