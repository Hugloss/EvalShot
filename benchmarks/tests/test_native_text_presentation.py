"""E269–E272: fresh text-only presentation study, exact host boundary.

Model-free fixtures test frozen identity, exact UTF-8 Chat Completions
surfaces, provider submission, signed receipts and paired nonclaims.
No real OpenCode/native SDK or external model call is implied.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.host_input_attestation import (
    canonical, verify_host_attestations,
)
from benchmarks.harness.intervention_audit import _facet_compare, validate_design
from benchmarks.harness.native_chat_transport_parity import (
    audit_frozen_chat_treatments, inspect_selected_content,
)
from benchmarks.harness.opencode_native_gateway import NativeOpenCodeGateway
from benchmarks.harness.trusted_host_capture import TrustedModelRequestCapture
from benchmarks.harness.trusted_treatments import (
    build_manifest, select_treatment, verify_manifest, sha,
)
from benchmarks.tests.test_opencode_native_gateway import (
    H, KEY, UPSTREAM, REPLY, manifest as legacy_manifest,
    request, source,
)
from benchmarks.tests.test_native_chat_transport_parity import bound_request

SCHEMA = "agentscookbook.host-attested-intervention-design.v1"


def text_manifest():
    return build_manifest({
        "schema": SCHEMA,
        "study": "text_presentation",
        "campaign_id": "native-chat-compact-vs-indented-new-campaign",
        "model": "gpt-fixture",
        "harnesses": ["opencode-native"],
        "tasks": ["source-owner"],
        "replicates": 1,
        "arms": {
            "control": "compact-json-text",
            "variant": "indented-json-text",
        },
    }, source_contract_identity="sha256:hashmarks-text-source-v1",
       host_build_sha256=H("host-v2"),
       assignment_seed_sha256=H("fresh-assignment-v2"))


class NativeTextStudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.key = self.root / "host.key"
        self.key.write_bytes(KEY)
        self.key.chmod(0o600)
        self.plan = text_manifest()
        self.cells = {row["arm"]: row for row in self.plan["assignments"]}
        self.selections = {
            arm: select_treatment(
                self.plan, trial_id=cell["trial_id"],
                harness=cell["harness"], task=cell["task"],
                replicate=cell["replicate"], arm=arm,
                current=source(),
            ) for arm, cell in self.cells.items()
        }

    def tearDown(self):
        self.tmp.cleanup()

    def gateway(self, arm):
        return NativeOpenCodeGateway(
            self.plan, trial_id=self.cells[arm]["trial_id"],
            host_key_file=self.key, host_identity="pinned-independent-host",
            current=source(), replaced=None,
            gateway_token="some-privileged-local-secret-12345678",
            upstream=UPSTREAM, approved_origin="https://api.example.invalid",
            upstream_api_key="only-host-has-provider-key",
            catalog_sha256=H("catalog"), oracle_sha256=H("oracle"),
            workspace_sha256=H("workspace"),
            enforce_chat_tool_wire_contract=True,
        )

    def test_new_design_has_new_manifest_and_no_legacy_rewrite(self):
        previous = legacy_manifest()
        self.assertNotEqual(self.plan["manifest_sha256"], previous["manifest_sha256"])
        self.assertNotEqual(self.plan["design_sha256"], previous["design_sha256"])
        self.assertEqual(verify_manifest(self.plan), self.plan)
        self.assertEqual(verify_manifest(previous), previous)
        broken = dict(self.plan)
        broken["design"] = previous["design"]
        with self.assertRaises(ValueError):
            verify_manifest(broken)
        with self.assertRaisesRegex(ValueError, "text-presentation-arms"):
            validate_design({
                **self.plan["design"],
                "arms": {"control": "structured", "variant": "text"},
            })

    def test_control_and_variant_are_both_exact_chat_text_different_surfaces(self):
        control = self.selections["control"]
        variant = self.selections["variant"]
        self.assertEqual(control["presentation"], "compact-json-text")
        self.assertEqual(variant["presentation"], "indented-json-text")
        self.assertIs(type(control["selected_content"]), str)
        self.assertIs(type(variant["selected_content"]), str)
        self.assertEqual(control["selected_content"], canonical(source()["content"]).decode())
        self.assertEqual(json.loads(control["selected_content"]), source()["content"])
        self.assertEqual(json.loads(variant["selected_content"]), source()["content"])
        self.assertNotEqual(control["selected_content"], variant["selected_content"])
        self.assertNotEqual(control["surface_sha256"], variant["surface_sha256"])
        self.assertEqual(control["semantic_sha256"], variant["semantic_sha256"])
        self.assertEqual(control["generation_sha256"], variant["generation_sha256"])
        for selected in (control, variant):
            wire = inspect_selected_content(selected)
            self.assertTrue(wire["chat_tool_content_wire_compatible"])
            self.assertEqual(wire["model_input_representation"],
                             "openai-chat-tool-plain-text-v1")
            self.assertFalse(wire["provider_consumption_proven"])

    def test_full_frozen_pair_study_wide_admission_passes_model_free_only(self):
        sources = self.root / "sources"
        sources.mkdir()
        for cell in self.plan["assignments"]:
            (sources / (cell["trial_id"] + ".json")).write_bytes(
                canonical({"current": source(), "replaced": None})
            )
        admitted = audit_frozen_chat_treatments(self.plan, sources_root=sources)
        self.assertEqual(admitted["trials"], 2)
        self.assertEqual(admitted["transport_admissible_pairs"], 1)
        self.assertTrue(admitted["fully_compatible_for_paired_study"])
        self.assertEqual(admitted["representation_counts"], {
            "openai-chat-tool-plain-text-v1": 2,
        })
        self.assertEqual(admitted["model_backed_runs_performed"], 0)
        self.assertFalse(admitted["causal_hashmarks_uplift_proven"])

    def test_original_source_has_no_visible_format_contrast_fail_before_work(self):
        empty = {"generation_sha256": H("empty-generation"),
                 "semantic_sha256": sha({}), "content": {}}
        with self.assertRaisesRegex(ValueError, "no-distinct-visible-surfaces"):
            select_treatment(
                self.plan, trial_id=self.cells["variant"]["trial_id"],
                harness="opencode-native", task="source-owner",
                replicate=1, arm="variant", current=empty,
            )
        for wrong in ({"owner": "forged.py"}, {"owner": None}):
            tampered = {**source(), "content": wrong}
            with self.assertRaises(ValueError):
                select_treatment(
                    self.plan, trial_id=self.cells["control"]["trial_id"],
                    harness="opencode-native", task="source-owner",
                    replicate=1, arm="control", current=tampered,
                )

    def test_pair_facets_reject_swapped_labels_semantics_or_generation(self):
        control, variant = self.selections["control"], self.selections["variant"]
        base = {"catalog_sha256": H("catalog"), "prompt_sha256": H("prompt"),
                "oracle_sha256": H("oracle"), "workspace_sha256": H("workspace")}
        left = {**base, **{key: control[key] for key in (
            "presentation", "surface_sha256", "semantic_sha256",
            "generation_sha256", "current_generation_sha256",
        )}}
        right = {**base, **{key: variant[key] for key in (
            "presentation", "surface_sha256", "semantic_sha256",
            "generation_sha256", "current_generation_sha256",
        )}}
        self.assertIsNone(_facet_compare(left, right, "text_presentation"))
        bad = [
            {**right, "presentation": "structured"},
            {**right, "semantic_sha256": H("other-semantic")},
            {**right, "generation_sha256": H("other-generation")},
            {**right, "surface_sha256": left["surface_sha256"]},
            {**right, "prompt_sha256": H("other-prompt")},
        ]
        for changed in bad:
            with self.subTest(changed=changed):
                self.assertIsNotNone(_facet_compare(left, changed, "text_presentation"))

    def test_actual_gateway_request_once_per_arm_exact_wire_bytes(self):
        originals = []
        for arm in ("control", "variant"):
            gateway = self.gateway(arm)
            expected = self.selections[arm]["selected_content"]
            packet = bound_request(selected=expected)
            assistant_call = json.loads(packet)["messages"][-2]["tool_calls"][0]
            # Synthetic provider response is observed by the host gateway
            # before any native client continuation may claim this call.
            issued = canonical({"choices": [{
                "index": 0,
                "message": {"role": "assistant", "tool_calls": [assistant_call]},
                "finish_reason": "tool_calls",
            }]})
            first_request = canonical({
                "model": "gpt-fixture",
                "messages": [{"role": "user", "content": "find owner"}],
            })
            try:
                with patch(
                    "benchmarks.harness.opencode_native_gateway._forward_pre_evidence",
                    return_value=issued,
                ) as forwarded, patch(
                    "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
                    side_effect=lambda **kw: (originals.append((arm, kw)), REPLY)[1],
                ) as sent:
                    self.assertEqual(
                        gateway._handle_request(
                            first_request, authorization="Bearer " + gateway.token,
                        ), issued,
                    )
                    forwarded.assert_called_once()
                    # The formerly valid fixture omitted the assistant
                    # tool_calls ancestor. It must fail before submission.
                    with self.assertRaisesRegex(
                        ValueError, "native-chat-assistant-tool-call-absent"
                    ):
                        gateway._handle_request(
                            request(selected=expected, tool=True),
                            authorization="Bearer " + gateway.token,
                        )
                    sent.assert_not_called()
                    other = self.selections[
                        "variant" if arm == "control" else "control"
                    ]["selected_content"]
                    with self.assertRaisesRegex(ValueError, "not-byte-identical"):
                        gateway._handle_request(
                            bound_request(selected=other),
                            authorization="Bearer " + gateway.token,
                        )
                    sent.assert_not_called()
                    self.assertEqual(
                        gateway._handle_request(packet,
                            authorization="Bearer " + gateway.token),
                        REPLY,
                    )
                    sent.assert_called_once()
                    self.assertEqual(
                        sent.call_args.kwargs["serialized_model_request"], packet,
                    )
                    with self.assertRaises(ValueError):
                        gateway._handle_request(
                            bound_request(selected=expected),
                            authorization="Bearer " + gateway.token,
                        )
                self.assertEqual(gateway.result()["post_evidence_submissions"], 1)
                self.assertTrue(gateway.result()["native_chat_tool_wire_contract_enforced"])
            finally:
                gateway.close()
        self.assertEqual(len(originals), 2)
        self.assertNotEqual(
            originals[0][1]["serialized_model_request"],
            originals[1][1]["serialized_model_request"],
        )

    def test_host_records_observed_original_text_pair_without_fake_model_attention(self):
        for arm in ("control", "variant"):
            cell = self.cells[arm]
            capture = TrustedModelRequestCapture(
                self.plan, trial_id=cell["trial_id"],
                harness=cell["harness"], task=cell["task"],
                replicate=cell["replicate"], arm=arm,
                host_identity="pinned-independent-host",
                host_key_file=self.key,
            )
            actual = request(selected=self.selections[arm]["selected_content"],
                             tool=True)
            observed = json.loads(actual)
            prompt = {"request_settings": {
                k: v for k, v in observed.items() if k != "messages"
            }, "non_tool_messages": [m for m in observed["messages"]
                                     if m["role"] != "tool"]}
            receipt = capture.observe_outbound_request(
                serialized_model_request=actual,
                tool_call_id="native-tool-1",
                returned_packet=source()["content"],
                request_sequence=1, current=source(),
                catalog_sha256=H("catalog"),
                prompt_sha256=hashlib.sha256(canonical(prompt)).hexdigest(),
                oracle_sha256=H("oracle"), workspace_sha256=H("workspace"),
            )
            self.assertEqual(receipt["host_request_observed"], "true")
            self.assertEqual(receipt["model_attention_proven"], "false")
            self.assertEqual(
                capture._recorded[0]["intervention"]["presentation"],
                self.selections[arm]["presentation"],
            )


if __name__ == "__main__":
    unittest.main()
