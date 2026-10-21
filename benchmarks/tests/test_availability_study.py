"""Native-format readback and assignment-preserving availability measurement.

Fixtures are representative synthetic CLI/export records, not authenticated live
captures. They test format handling without asserting host/model delivery.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.availability_study import (
    build_availability_study,
    inspect_native_trace,
)
from benchmarks.harness.identity import digest
from benchmarks.harness.campaign_authority import campaign_suite_identity
from benchmarks.harness.bundle_writer import publish_bundle
from benchmarks.harness.identity import canonical_json
from benchmarks.harness.report import ReportError
from benchmarks.harness.suite import SuiteDefinition
from benchmarks.adapters.codex import _metrics, parse_codex_jsonl


FIXTURES = Path(__file__).with_name("fixtures")
ANSWER = '{"path":"src/owner.py","symbol":"resolve"}'


def native(adapter="codex", *, invoked=False, opaque=False):
    name = "availability-codex.jsonl" if adapter == "codex" else "availability-opencode.json"
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    if adapter == "codex" and invoked:
        events = [json.loads(line) for line in raw.splitlines()]
        events.insert(2, {"type": "item.completed", "item": {
            "id": "hm-1", "type": "mcp_tool_call", "server": "hashmarks",
            "tool": "task_evidence", "status": "completed",
            "result": {"content": [{"type": "text", "text": '{"schema":"hashmarks.task-evidence.v4"}'}]},
        }})
        raw = "\n".join(json.dumps(e) for e in events)
    elif adapter == "opencode-native":
        exported = json.loads(raw)
        if opaque:
            exported["messages"][0]["parts"][0]["tool"] = "execute"
        if invoked:
            exported["messages"][0]["parts"].insert(0, {
                "type": "tool", "tool": "hashmarks_task_evidence", "callID": "hm-1",
                "state": {"status": "completed", "output": '{"schema":"hashmarks.task-evidence.v4"}'},
            })
        raw = json.dumps(exported)
    return raw


def suite(adapter="codex"):
    conditions = [{"id": c, "subject": s, "agent": "native", "trials": 2, "seed": 10}
                  for c, s in (("bare", "none"), ("available", "hashmarks"))]
    return SuiteDefinition(
        Path("."), {"id": "availability-test", "version": 1, "tasks": ["task"], "conditions": conditions},
        {"task": {"id": "task", "version": 1}},
        {"none": {"id": "none", "kind": "control"}, "hashmarks": {"id": "hashmarks"}},
        {"native": {"id": "native", "adapter": adapter, "configuration": {"native_host": True}}},
    )


def catalog():
    identity = {"subject_id": "hashmarks", "required_tool": "hashmarks_task_evidence",
                "required_operation": "task_evidence", "protocol_version": "2024-11-05",
                "tool_names": ["task_evidence"]}
    return {**identity, "catalog_sha256": digest(identity), "required_tool_visible": True}


class AvailabilityStudyTests(unittest.TestCase):
    def inspect(self, raw, adapter="codex", complete=True):
        return inspect_native_trace(raw, adapter=adapter, subject="hashmarks", terminal_complete=complete)

    def test_both_native_formats_prove_observed_zero_but_never_model_delivery(self):
        for adapter in ("codex", "opencode-native"):
            with self.subTest(adapter=adapter):
                observed = self.inspect(native(adapter), adapter)
                self.assertEqual(observed["trace_state"], "COMPLETE_MCP_TELEMETRY")
                self.assertIs(observed["subject_invoked"], False)
                self.assertEqual(observed["final_answer"], ANSWER)
                self.assertEqual(observed["model_request_inclusion"], "UNKNOWN")

    def test_partial_missing_and_malformed_traces_never_prove_zero(self):
        for raw in ("", "{}", "\n".join(native().splitlines()[2:]), native() + '\n{broken', native() + '\n{"type":"turn.started"}', native() + '\n{"type":"item.started","item":{"id":"pending","type":"mcp_tool_call","server":"hashmarks","tool":"find"}}'):
            with self.subTest(raw=raw[:30]):
                self.assertIsNone(self.inspect(raw)["subject_invoked"])
        self.assertIsNone(self.inspect(native(), complete=False)["subject_invoked"])
        self.assertIsNone(self.inspect(native("opencode-native", opaque=True), "opencode-native")["subject_invoked"])
        unknown_tool = native().replace('"type":"command_execution"', '"type":"future_tool_call"')
        self.assertIsNone(self.inspect(unknown_tool)["subject_invoked"])
        exported = json.loads(native("opencode-native"))
        exported["messages"][0]["parts"].insert(0, {"type": "future-tool-event", "tool": "hashmarks_task_evidence"})
        self.assertIsNone(self.inspect(json.dumps(exported), "opencode-native")["subject_invoked"])
        exported = json.loads(native("opencode-native", invoked=True))
        exported["messages"][0]["parts"][0]["tool"] = "mcp__hashmarks__task_evidence"
        self.assertIsNone(self.inspect(json.dumps(exported), "opencode-native")["subject_invoked"])

    def test_positive_in_partial_native_trace_is_retained(self):
        observed = self.inspect(native("opencode-native", invoked=True, opaque=True), "opencode-native")
        self.assertIs(observed["subject_invoked"], True)
        self.assertIs(observed["successful_return_observed"], True)
        self.assertEqual(observed["trace_state"], "UNKNOWN")

    def report(self, *, missing=False, invoked=False, catalog_known=True, mismatch=False, tainted=False, incomplete=False):
        frozen = suite()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipts = []
            for i, definition in enumerate(frozen.trial_definitions()):
                if missing and i == 3:
                    continue
                trial = f"trial-{i}"
                directory = root / trial
                directory.mkdir()
                has_subject = definition["condition_id"] == "available"
                (directory / "trace.jsonl").write_text(native(invoked=invoked and has_subject and i == 2), encoding="utf-8")
                receipts.append({
                    "definition_id": definition["definition_id"], "trial_id": trial, "status": "PASS",
                    "authority": {"subject": {"available": True}},
                    "execution": {"agent_answer": "different" if mismatch and i == 2 else ANSWER,
                                  "campaign_id": "frozen-test-campaign",
                                  "agent_terminal": {"type": "turn.completed"},
                                  "artifacts": {"agent_trace": {"path": "trace.jsonl"}}},
                    "scoring": {"oracle_grade": {"valid": True, "passed": True}},
                    "measurements": {"agent": {"subject_tool_configured": has_subject}},
                })
                if incomplete and i == 2:
                    receipts[-1]["status"] = "INCOMPLETE"
                    receipts[-1]["execution"]["agent_terminal"] = None
                    receipts[-1]["execution"]["agent_answer"] = None
                    (directory / "trace.jsonl").write_text("")
            manifest = {"campaign_id": "frozen-test-campaign", "subject_tool_catalog_proofs": [catalog()] if catalog_known else [],
                        "suite_identity": campaign_suite_identity(frozen),
                        "selected_definitions": [d["definition_id"] for d in frozen.trial_definitions()]}
            canonical = {"campaign_qualification": {"evidence_tainted": tainted, "subject_exposure": {"status": "FAIL"}}}
            # Existing canonical report/bundle validation is tested independently;
            # these seams isolate study semantics from publication scaffolding.
            with patch("benchmarks.harness.availability_study._receipts", return_value=receipts), \
                 patch("benchmarks.harness.availability_study.build_report", return_value=canonical), \
                 patch("benchmarks.harness.availability_study.read_campaign", return_value=manifest):
                return build_availability_study(suite=frozen, results_root=root,
                                                baseline_condition="bare", available_condition="available")

    def test_zero_adoption_is_valid_only_for_explicit_availability_study(self):
        report = self.report()
        self.assertTrue(report["study_measurement_qualified"])
        self.assertEqual(report["historical_subject_use_qualification"], "FAIL")
        self.assertEqual(report["voluntary_adoption"], {"assigned_trials": 2, "invoked": 0, "not_invoked": 2, "unknown": 0, "rate": 0.0})
        self.assertEqual(report["primary_availability_intention_to_treat"]["expected_pairs"], 2)

    def test_missing_trial_keeps_assigned_denominator_and_unknown_outcome(self):
        report = self.report(missing=True)
        arm = report["primary_availability_intention_to_treat"]["conditions"]["available"]
        self.assertEqual(arm["assigned_trials"], 2)
        self.assertEqual(arm["unknown_outcomes"], 1)
        self.assertIsNone(arm["success_rate"])
        self.assertEqual(arm["success_rate_bounds"], [0.5, 1.0])
        self.assertFalse(report["study_measurement_qualified"])

    def test_process_failure_with_null_terminal_keeps_unknown_outcome_and_adoption(self):
        report = self.report(incomplete=True)
        self.assertFalse(report["study_measurement_qualified"])
        arm = report["primary_availability_intention_to_treat"]["conditions"]["available"]
        self.assertEqual(arm["assigned_trials"], 2)
        self.assertEqual(arm["unknown_outcomes"], 1)
        self.assertEqual(arm["success_rate_bounds"], [0.5, 1.0])
        self.assertIsNone(arm["success_rate"])
        self.assertEqual(report["voluntary_adoption"]["unknown"], 1)
        self.assertIsNone(report["voluntary_adoption"]["rate"])

    def test_returned_subset_cannot_replace_primary_or_become_delivered_subset(self):
        report = self.report(invoked=True)
        self.assertEqual(report["successful_return_subset_diagnostic"]["assigned_trials"], 1)
        self.assertEqual(report["primary_availability_intention_to_treat"]["conditions"]["available"]["assigned_trials"], 2)
        self.assertEqual(report["model_delivered_subset_diagnostic"]["state"], "UNKNOWN")
        self.assertFalse(report["causal_effect_proven"])

    def test_catalog_and_answer_join_are_independent_qualification_requirements(self):
        self.assertFalse(self.report(catalog_known=False)["study_measurement_qualified"])
        report = self.report(mismatch=True)
        self.assertFalse(report["study_measurement_qualified"])
        self.assertIn("native-answer-receipt-join-unproven", report["trials"][2]["issues"])

    def test_authority_epoch_taint_suppresses_paired_comparison(self):
        report = self.report(tainted=True)
        self.assertFalse(report["study_measurement_qualified"])
        self.assertIsNone(report["primary_availability_intention_to_treat"]["mean_paired_delta"])
        self.assertTrue(all(p["delta"] is None for p in report["primary_availability_intention_to_treat"]["pairs"]))

    def test_forced_or_nonnative_configuration_is_not_optional_availability(self):
        frozen = suite()
        frozen.agents["native"]["configuration"]["native_host"] = False
        with self.assertRaisesRegex(ReportError, "optional-tool native"):
            build_availability_study(suite=frozen, results_root=Path("missing"), baseline_condition="bare", available_condition="available")

    def test_real_checksum_bound_bundles_keep_zero_adoption_valid_and_reject_tampering(self):
        frozen = suite()
        frozen.experiment.update(suite="test", scoring={"id": "test", "version": 1, "metrics": ["success"]})
        frozen.tasks["task"].update(prompt="find owner", oracle={"adapter": "expected-json"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {"contract": "benchmark-campaign-authority.v5",
                        "suite_identity": campaign_suite_identity(frozen),
                        "selected_definitions": [d["definition_id"] for d in frozen.trial_definitions()],
                        "subject_tool_catalog_proofs": [catalog()]}
            manifest["campaign_id"] = digest(manifest)
            (root / ".campaign").mkdir()
            (root / ".campaign/authority.json").write_bytes(canonical_json(manifest))
            for i, definition in enumerate(frozen.trial_definitions()):
                condition = next(c for c in frozen.experiment["conditions"] if c["id"] == definition["condition_id"])
                raw = native()
                seal = {"events_sha256": hashlib.sha256(b"").hexdigest(), "event_count": 0}
                receipt = {
                    "definition_id": definition["definition_id"], "trial_id": f"trial-{i}",
                    "experiment": frozen.experiment, "task": frozen.tasks["task"],
                    "condition": frozen.expanded_condition(condition), "status": "PASS",
                    "authority": {"subject": {"available": True}},
                    "execution": {"trial_index": definition["trial"], "seed": definition["seed"],
                                  "campaign_id": manifest["campaign_id"],
                                  "events": seal, "agent_terminal": {"type": "turn.completed"}, "agent_answer": ANSWER},
                    "scoring": {"oracle_grade": {"valid": True, "passed": True}},
                    "measurements": {"agent": _metrics(parse_codex_jsonl(raw.encode())[0], subject_server="hashmarks" if condition["subject"] == "hashmarks" else None)},
                }
                publish_bundle(results_root=root, trial_id=receipt["trial_id"], receipt=receipt, artifacts={
                    "events": ("events.jsonl", b""), "events_seal": ("events-seal.json", canonical_json(seal)),
                    "agent_trace": ("agent-trace.jsonl", raw.encode()),
                })
            report = build_availability_study(suite=frozen, results_root=root, baseline_condition="bare", available_condition="available")
            self.assertTrue(report["study_measurement_qualified"])
            self.assertEqual(report["historical_subject_use_qualification"], "FAIL")
            (root / "trial-0/agent-trace.jsonl").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ReportError, "invalid published result bundle"):
                build_availability_study(suite=frozen, results_root=root, baseline_condition="bare", available_condition="available")


if __name__ == "__main__":
    unittest.main()
