"""E260: no fabricated pilot outcomes from incomplete netns execution receipts.

Only synthetic model-free fixtures. Cohort projection is mocked here because
its real native export and host HMAC checks have a separate regression ring.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.native_netns_pilot_readiness import evaluate_native_netns_pilot
from benchmarks.harness.opencode_native_trial import SCHEMA as TRIAL_SCHEMA
from benchmarks.harness.native_netns_probe import validate_kernel_probe
from benchmarks.harness.native_chat_pair_admission import SCHEMA as PAIR_SCHEMA
from benchmarks.harness.native_mount_probe import GUEST_PROBE, SCHEMA as MOUNT_SCHEMA
from benchmarks.harness.native_netns_host_attestation import (
    sign_isolation_report, write_isolation_attestation,
)
from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.native_host_execve import (
    SCHEMA as EXECVE_SCHEMA, DOMAIN as EXECVE_DOMAIN, SEARCH_EXECUTABLES,
)
from benchmarks.tests.test_opencode_native_gateway import KEY, H, manifest


class NativeNetnsPilotAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.results = self.root / "results"
        self.results.mkdir()
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.frozen = manifest()
        self._make_execution_receipts()
        self.cohort = {
            "native_session_cohort_observationally_consistent": True,
            "verified_native_trial_exports": 2,
        }
        # This mocked compatible population tests receipt validation only.
        # The real audit correctly rejects structured-vs-text native pairs.
        self.synthetic_wire_pairs = {
            "fully_compatible_for_paired_study": True,
            "rows": [
                {
                    "trial_id": cell["trial_id"],
                    "model_input_representation": "openai-chat-tool-plain-text-v1",
                    "chat_tool_content_wire_compatible": True,
                }
                for cell in self.frozen["assignments"]
            ],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _make_execution_receipts(self):
        for cell in self.frozen["assignments"]:
            folder = self.results / cell["trial_id"]
            folder.mkdir()
            report = {
                "schema": TRIAL_SCHEMA,
                "trial_id": cell["trial_id"],
                "manifest_sha256": self.frozen["manifest_sha256"],
                "native_exit_code": 0,
                "native_trial_evidence_qualified": True,
                "host_receipt_finalized": True,
                "native_gateway_route_completed": True,
                "native_chat_pair_preflight": {
                    "schema": PAIR_SCHEMA,
                    "trial_id": cell["trial_id"], "pair_id": cell["pair_id"],
                    "manifest_sha256": self.frozen["manifest_sha256"],
                    "arm": cell["arm"],
                    "representations": {
                        "control": "openai-chat-tool-plain-text-v1",
                        "variant": "openai-chat-tool-plain-text-v1",
                    },
                    "selected_wire_compatible": True,
                    "paired_transport_compatible": True,
                    "diagnostic_only": False,
                    "native_model_runs_performed_by_admission": 0,
                    "model_attention_proven": False,
                    "causal_uplift_proven": False,
                },
                "config_sha256": H("pinned-confined-config"),
                "native_mount_preflight": {
                    "schema": MOUNT_SCHEMA,
                    "runtime_mount_probe_qualified": True,
                    "bwrap_sha256": H("approved-bwrap"),
                    "python_sha256": H("approved-python"),
                    "worker_sha256": H("pinned-worker"),
                    "probe_program_sha256": __import__("hashlib").sha256(
                        GUEST_PROBE.encode("utf-8")
                    ).hexdigest(),
                    "config_sha256": H("pinned-confined-config"),
                    "workspace_binding_observed": True,
                    "readonly_config_observed": True,
                    "isolated_xdg_write_observed": True,
                    "gateway_socket_observed": True,
                    "host_key_not_mounted_observed": True,
                    "provider_model_execution_observed": False,
                    "independent_process_origin_proven": False,
                },
                "native_kernel_preflight": validate_kernel_probe({
                    "guest_netns_inode": 9124,
                    "interfaces": ["lo"],
                    "loopback_bind_ok": True,
                    "outbound_errno": errno.ENETUNREACH,
                }, host_netns_inode=1112, bwrap_sha256=H("approved-bwrap"),
                   python_sha256=H("approved-python")),
                "linux_network_namespace_requested": True,
                "host_network_egress_intentionally_disabled": True,
                "kernel_distinct_netns_relay_observed": True,
                "native_sandbox_binary_sha256": H("approved-bwrap"),
                "frozen_hashmarks_executable_sha256": H("approved-hashmarks"),
                "observed_provider_response_relays": 1,
                "native_process_origin_proven": False,
                "causal_improvement_proven": False,
                "gateway": {
                    "kernel_netns_peer_check_required": True,
                    "native_chat_tool_wire_contract_enforced": True,
                    "provider_issued_tool_observed": True,
                    "provider_issued_tool_continuation_verified": True,
                    "provider_issued_response_sha256": H("provider-issued-response"),
                    "distinct_netns_peer_connections": 1,
                },
                "provider_issued_native_export_arguments_bound": True,
                "native_session_binding": {
                    "native_session_to_gateway_packet_bound": True,
                    "signed_host_input_and_transport_verified": True,
                    "provider_issued_native_arguments_bound": True,
                    "provider_issued_response_sha256": H("provider-issued-response"),
                },
            }
            execution_bytes = canonical(report) + b"\n"
            provider_bytes = b'{"synthetic-provider-packet-receipt":true}'
            (folder / "trial-execution.json").write_bytes(execution_bytes)
            (folder / "host-attestation.json").write_bytes(provider_bytes)
            signed = sign_isolation_report(
                report, execution_bytes=execution_bytes,
                provider_attestation_bytes=provider_bytes,
                host_identity="privileged-host", host_key=KEY,
            )
            write_isolation_attestation(
                folder / "trial-execution.attestation.json", signed,
            )

    def assess(self):
        with patch(
            "benchmarks.harness.native_netns_pilot_readiness.audit_frozen_chat_treatments",
            return_value=self.synthetic_wire_pairs,
        ), patch(
            "benchmarks.harness.native_netns_pilot_readiness.audit_native_session_cohort",
            return_value=self.cohort,
        ) as cohort:
            result = evaluate_native_netns_pilot(
                self.frozen, results_root=self.results,
                sources_root=self.sources, workspace=self.workspace,
                host_key=KEY, host_identity="privileged-host",
                approved_endpoint_sha256=H("endpoint"),
                expected_bwrap_sha256=H("approved-bwrap"),
                expected_hashmarks_sha256=H("approved-hashmarks"),
            )
        self.assertEqual(cohort.call_count, 1)
        return result

    def test_complete_observations_are_not_model_effect_or_review_proof(self):
        result = self.assess()
        self.assertTrue(result["paired_pilot_observations_ready_for_independent_grading"])
        self.assertTrue(result["native_kernel_preflight_receipts_host_mac_verified"])
        self.assertTrue(result["native_mount_preflight_receipts_host_mac_verified"])
        self.assertFalse(result["point_in_time_mount_probe_is_independent_process_proof"])
        self.assertFalse(result["point_in_time_kernel_probe_is_exhaustive_egress_proof"])
        self.assertEqual(result["verified_local_isolation_receipts"], 2)
        for flag in (
            "exclusive_network_egress_independently_audited",
            "native_executable_origin_independently_proven",
            "independent_oracle_grading_completed",
            "pre_work_timestamp_independently_proven",
            "randomized_causal_hashmarks_uplift_proven",
        ):
            self.assertFalse(result[flag])

    def test_wrong_binary_pin_or_untrusted_isolation_claim_rejects(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        path = self.results / trial / "trial-execution.json"
        base = json.loads(path.read_text())
        for key, changed in (
            ("native_sandbox_binary_sha256", H("wrong")),
            ("frozen_hashmarks_executable_sha256", H("wrong")),
            ("kernel_distinct_netns_relay_observed", False),
            ("native_gateway_route_completed", False),
            ("native_process_origin_proven", True),
            ("observed_provider_response_relays", 2),
        ):
            with self.subTest(key=key):
                tampered = copy.deepcopy(base)
                tampered[key] = changed
                path.write_text(json.dumps(tampered))
                report = self.assess()
                self.assertFalse(report["paired_pilot_observations_ready_for_independent_grading"])
                self.assertIn("inconsistent-or-self-upgraded-native-isolation-receipt",
                              report["blockers"])
        path.write_text(json.dumps(base))

    def test_missing_or_unsupported_kernel_probe_never_qualifies(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        path = self.results / trial / "trial-execution.json"
        original = path.read_bytes()
        report = json.loads(original)
        for patch_value in (
            None,
            {**report["native_kernel_preflight"], "guest_netns_inode": 1112},
            {**report["native_kernel_preflight"], "representative_external_tcp_denied": False},
            {**report["native_kernel_preflight"], "exclusive_egress_independently_attested": True},
            {**report["native_kernel_preflight"], "bwrap_sha256": H("wrong")},
        ):
            with self.subTest(patch=patch_value):
                updated = copy.deepcopy(report)
                updated["native_kernel_preflight"] = patch_value
                path.write_bytes(canonical(updated) + b"\n")
                result = self.assess()
                self.assertFalse(result["paired_pilot_observations_ready_for_independent_grading"])
                self.assertIn("inconsistent-or-self-upgraded-native-isolation-receipt", result["blockers"])
        path.write_bytes(original)

    def test_missing_or_fake_provider_issued_tool_cannot_promote_signed_pilot(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        receipt = self.results / trial / "trial-execution.json"
        before = receipt.read_bytes()
        for key, tamper in (
            ("provider_issued_tool_observed", False),
            ("provider_issued_tool_continuation_verified", False),
            ("provider_issued_response_sha256", "not-a-sha"),
        ):
            with self.subTest(key=key):
                report = json.loads(before)
                report["gateway"][key] = tamper
                receipt.write_bytes(canonical(report) + b"\n")
                verdict = self.assess()
                self.assertFalse(verdict["paired_pilot_observations_ready_for_independent_grading"])
                self.assertIn("inconsistent-or-self-upgraded-native-isolation-receipt",
                              verdict["blockers"])
                with self.assertRaises(ValueError):
                    sign_isolation_report(
                        report, execution_bytes=receipt.read_bytes(),
                        provider_attestation_bytes=b"synthetic-provider",
                        host_identity="privileged-host", host_key=KEY,
                    )
        receipt.write_bytes(before)

    def test_provider_response_sha_changes_without_reauthorization(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        receipt = self.results / trial / "trial-execution.json"
        report = json.loads(receipt.read_bytes())
        report["gateway"]["provider_issued_response_sha256"] = H("other-response")
        receipt.write_bytes(canonical(report) + b"\n")
        verdict = self.assess()
        self.assertFalse(verdict["paired_pilot_observations_ready_for_independent_grading"])
        # E283 rejects mismatched provider/native identity before the
        # later host-isolation MAC check; this is an earlier failure gate.
        self.assertIn("incomplete-native-session-transport-binding", verdict["blockers"])

    def test_realistic_incompatible_pair_or_diagnostic_never_qualifies(self):
        self.synthetic_wire_pairs["fully_compatible_for_paired_study"] = False
        rejected = self.assess()
        self.assertFalse(rejected["paired_pilot_observations_ready_for_independent_grading"])
        self.assertIn("incompatible-frozen-native-chat-pairs", rejected["blockers"])
        self.synthetic_wire_pairs["fully_compatible_for_paired_study"] = True
        trial = next(c["trial_id"] for c in self.frozen["assignments"]
                     if c["arm"] == "variant")
        receipt = self.results / trial / "trial-execution.json"
        info = json.loads(receipt.read_bytes())
        info["native_chat_pair_preflight"]["diagnostic_only"] = True
        info["native_chat_pair_preflight"]["paired_transport_compatible"] = False
        info["native_chat_pair_preflight"]["representations"]["control"] = (
            "unsupported-nested-json-tool-content"
        )
        receipt.write_bytes(canonical(info) + b"\n")
        rejected = self.assess()
        self.assertFalse(rejected["paired_pilot_observations_ready_for_independent_grading"])
        self.assertIn("native-chat-diagnostic-or-incompatible-paired-receipt",
                      rejected["blockers"])

    def test_missing_or_mutated_mount_layout_cannot_be_signed_or_counted(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        path = self.results / trial / "trial-execution.json"
        saved = path.read_bytes()
        report = json.loads(saved)
        invalid = (
            None,
            {**report["native_mount_preflight"], "readonly_config_observed": False},
            {**report["native_mount_preflight"], "host_key_not_mounted_observed": False},
            {**report["native_mount_preflight"], "worker_sha256": "not-a-digest"},
            {**report["native_mount_preflight"], "provider_model_execution_observed": True},
            {**report["native_mount_preflight"], "config_sha256": H("different-config")},
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                changed = copy.deepcopy(report)
                changed["native_mount_preflight"] = candidate
                path.write_bytes(canonical(changed) + b"\n")
                verdict = self.assess()
                self.assertFalse(verdict["paired_pilot_observations_ready_for_independent_grading"])
                self.assertIn("inconsistent-or-self-upgraded-native-isolation-receipt",
                              verdict["blockers"])
                with self.assertRaises(ValueError):
                    sign_isolation_report(
                        changed, execution_bytes=path.read_bytes(),
                        provider_attestation_bytes=b"synthetic-provider",
                        host_identity="privileged-host", host_key=KEY,
                    )
        path.write_bytes(saved)

    def test_unsigned_mount_probe_drift_is_blocked_by_mac(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        path = self.results / trial / "trial-execution.json"
        report = json.loads(path.read_bytes())
        report["native_mount_preflight"]["worker_sha256"] = H("another-valid-sha")
        path.write_bytes(canonical(report) + b"\n")
        result = self.assess()
        self.assertFalse(result["paired_pilot_observations_ready_for_independent_grading"])
        self.assertIn("host-isolation-attestation-unverified", result["blockers"])

    def test_host_custodian_refuses_to_sign_missing_kernel_admission(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        path = self.results / trial / "trial-execution.json"
        report = json.loads(path.read_bytes())
        report["native_kernel_preflight"] = None
        with self.assertRaisesRegex(ValueError, "unqualified-netns-host-observation"):
            sign_isolation_report(
                report, execution_bytes=canonical(report) + b"\n",
                provider_attestation_bytes=b'{"synthetic-provider-packet-receipt":true}',
                host_identity="privileged-host", host_key=KEY,
            )

    def test_unsigned_kernel_probe_mutation_is_refused_even_if_shape_valid(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        path = self.results / trial / "trial-execution.json"
        report = json.loads(path.read_bytes())
        report["native_kernel_preflight"]["guest_netns_inode"] += 1
        path.write_bytes(canonical(report) + b"\n")
        denied = self.assess()
        self.assertFalse(denied["paired_pilot_observations_ready_for_independent_grading"])
        self.assertIn("host-isolation-attestation-unverified", denied["blockers"])

    def test_unsigned_self_upgrade_cannot_fake_kernel_peer_evidence(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        folder = self.results / trial
        original = folder / "trial-execution.json"
        receipt = json.loads(original.read_bytes())
        # All other local flags remain admissible, so only the external
        # host HMAC detects this unsigned peer-count modification.
        receipt["gateway"]["distinct_netns_peer_connections"] = 2
        original.write_bytes(canonical(receipt) + b"\n")
        audited = self.assess()
        self.assertFalse(audited["paired_pilot_observations_ready_for_independent_grading"])
        self.assertIn("host-isolation-attestation-unverified", audited["blockers"])

    def test_provider_receipt_replacement_cannot_reuse_isolation_signature(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        attested_provider = self.results / trial / "host-attestation.json"
        attested_provider.write_bytes(b'{"foreign-provider-submission":true}')
        audited = self.assess()
        self.assertFalse(audited["paired_pilot_observations_ready_for_independent_grading"])
        self.assertIn("host-isolation-attestation-unverified", audited["blockers"])

    def test_missing_pair_or_unverified_native_cohort_rejects(self):
        trial = self.frozen["assignments"][0]["trial_id"]
        (self.results / trial / "trial-execution.json").unlink()
        result = self.assess()
        self.assertFalse(result["paired_pilot_observations_ready_for_independent_grading"])
        self.assertEqual(result["verified_local_isolation_receipts"], 1)
        self.cohort["native_session_cohort_observationally_consistent"] = False
        self.assertIn("unqualified-native-session-cohort", self.assess()["blockers"])


    def _resign_trial(self, trial_id, report):
        folder = self.results / trial_id
        raw = canonical(report) + b"\n"
        (folder / "trial-execution.json").write_bytes(raw)
        provider = (folder / "host-attestation.json").read_bytes()
        signed = sign_isolation_report(
            report, execution_bytes=raw, provider_attestation_bytes=provider,
            host_identity="privileged-host", host_key=KEY,
        )
        (folder / "trial-execution.attestation.json").write_bytes(
            canonical(signed) + b"\n"
        )

    def _add_execve_fixture(self, trial_id, *, searches=0):
        """Synthetic HMAC receipt exercises read-back, NOT kernel tracing."""
        folder = self.results / trial_id
        report = json.loads((folder / "trial-execution.json").read_bytes())
        obs = {
            "measurement_qualified": True,
            "successful_execve_observations": searches + 1,
            "recognized_search_process_launches": searches,
            "recognized_search_executables": sorted(SEARCH_EXECUTABLES),
            "observation_scope": "strace-observed-successful-execve-executable-path",
            "complete_nested_tool_call_coverage_proven": False,
            "no_hidden_searches_proven": False,
            "tool_call_ancestry_proven": False,
            "model_attention_proven": False,
            "causal_search_savings_proven": False,
        }
        body = {
            "schema": EXECVE_SCHEMA,
            "campaign_id": self.frozen["design"]["campaign_id"],
            "trial_id": trial_id,
            "host_identity": "privileged-host",
            "host_build_sha256": self.frozen["host_build_sha256"],
            "tracer_sha256": H("test-tracer"),
            "trace_sha256": H("test-trace-" + trial_id),
            "exit_code": 0, "observation": obs,
        }
        signed = {**body, "mac_sha256": hmac.new(
            KEY, EXECVE_DOMAIN + canonical(body), hashlib.sha256
        ).hexdigest()}
        data = canonical(signed) + b"\n"
        (folder / "host-execve.attestation.json").write_bytes(data)
        report["native_host_execve_observation_required"] = True
        report["native_host_execve_evidence"] = {
            "state": "HOST_EXECVE_OBSERVED",
            "receipt_sha256": hashlib.sha256(data).hexdigest(),
            "observed_search_launches": searches,
            "host_execve_key_holder_authenticated": True,
            "nested_call_completeness_proven": False,
            "causal_search_savings_proven": False,
        }
        self._resign_trial(trial_id, report)
        return report

    def test_signed_execve_receipt_join_is_rechecked_without_claiming_completeness(self):
        for cell in self.frozen["assignments"]:
            self._add_execve_fixture(cell["trial_id"])
        verdict = self.assess()
        self.assertTrue(verdict["paired_pilot_observations_ready_for_independent_grading"])
        self.assertEqual(verdict["native_execve_observation_requested_trials"], 2)
        self.assertEqual(verdict["native_execve_host_receipts_independently_verified"], 2)
        self.assertFalse(verdict["native_execve_no_hidden_searches_proven"])
        self.assertFalse(verdict["native_execve_per_tool_call_ancestry_proven"])

    def test_execve_missing_tampered_or_replayed_receipt_never_qualifies(self):
        for cell in self.frozen["assignments"]:
            self._add_execve_fixture(cell["trial_id"])
        first, second = [x["trial_id"] for x in self.frozen["assignments"]]
        path = self.results / first / "host-execve.attestation.json"
        good = path.read_bytes()
        other = (self.results / second / "host-execve.attestation.json").read_bytes()
        for raw in (b'{"forged":true}\n', other):
            with self.subTest(raw=raw[:20]):
                path.write_bytes(raw)
                result = self.assess()
                self.assertFalse(result["paired_pilot_observations_ready_for_independent_grading"])
                self.assertIn("native-execve-host-receipt-join-unverified", result["blockers"])
        path.unlink()
        result = self.assess()
        self.assertIn("native-execve-host-receipt-join-unverified", result["blockers"])
        path.write_bytes(good)

    def test_execve_signed_report_count_and_semantic_drift_rejected(self):
        for cell in self.frozen["assignments"]:
            self._add_execve_fixture(cell["trial_id"])
        trial_id = self.frozen["assignments"][0]["trial_id"]
        path = self.results / trial_id / "trial-execution.json"
        original = json.loads(path.read_bytes())
        for field, value in (
            ("observed_search_launches", 17),
            ("nested_call_completeness_proven", True),
            ("receipt_sha256", H("foreign")),
        ):
            with self.subTest(field=field):
                altered = copy.deepcopy(original)
                altered["native_host_execve_evidence"][field] = value
                self._resign_trial(trial_id, altered)
                result = self.assess()
                self.assertIn("native-execve-host-receipt-join-unverified", result["blockers"])
        self._resign_trial(trial_id, original)

    def test_execve_unrequested_or_mixed_arm_observation_rejected(self):
        first = self.frozen["assignments"][0]["trial_id"]
        second = self.frozen["assignments"][1]["trial_id"]
        self._add_execve_fixture(first)
        result = self.assess()
        self.assertIn("mixed-native-execve-observation-cohort", result["blockers"])
        self._add_execve_fixture(second)
        report = json.loads((self.results / first / "trial-execution.json").read_bytes())
        report["native_host_execve_observation_required"] = False
        report["native_host_execve_evidence"] = None
        self._resign_trial(first, report)
        result = self.assess()
        self.assertIn("native-execve-host-receipt-join-unverified", result["blockers"])

    def test_provider_issued_session_response_join_required_at_readback(self):
        trial_id = self.frozen["assignments"][0]["trial_id"]
        original = json.loads(
            (self.results / trial_id / "trial-execution.json").read_bytes()
        )
        for patch in (
            {"provider_issued_native_export_arguments_bound": False},
            {"native_session_binding": {
                **original["native_session_binding"],
                "provider_issued_response_sha256": H("different-response"),
            }},
        ):
            self._resign_trial(trial_id, {**original, **patch})
            result = self.assess()
            self.assertIn("incomplete-native-session-transport-binding", result["blockers"])
        self._resign_trial(trial_id, original)

if __name__ == "__main__":
    unittest.main()
