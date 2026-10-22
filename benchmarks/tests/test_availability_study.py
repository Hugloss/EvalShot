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
    _tool_order_summary,
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

    def test_completed_tool_order_is_observation_not_model_tool_choice(self):
        for adapter in ("codex", "opencode-native"):
            with self.subTest(adapter=adapter):
                baseline = self.inspect(native(adapter), adapter)
                first = baseline["completed_tool_order"]
                self.assertEqual(first["state"], "OBSERVED_COMPLETION_ORDER")
                self.assertFalse(first["subject_first_completed"])
                self.assertIsNone(first["subject_first_completed_ordinal"])
                assisted = self.inspect(native(adapter, invoked=True), adapter)
                routing = assisted["completed_tool_order"]
                self.assertEqual(routing["state"], "OBSERVED_COMPLETION_ORDER")
                self.assertTrue(routing["subject_first_completed"])
                self.assertEqual(routing["subject_first_completed_ordinal"], 1)
                self.assertEqual(
                    routing["first_completed_class"],
                    "subject-repository-intelligence",
                )
                self.assertEqual(assisted["model_request_inclusion"], "UNKNOWN")

    def test_partial_trace_does_not_claim_first_tool_order(self):
        observed = self.inspect(native("opencode-native", invoked=True, opaque=True), "opencode-native")
        self.assertTrue(observed["subject_invoked"])
        self.assertEqual(
            observed["completed_tool_order"],
            {
                "state": "UNKNOWN",
                "first_completed_tool": None,
                "first_completed_class": None,
                "subject_first_completed": None,
                "subject_first_completed_ordinal": None,
            },
        )

    def test_codex_start_order_is_not_inferred_from_completion_order(self):
        events = [json.loads(line) for line in native("codex").splitlines()]
        # Hashmarks starts first but the native command completes first.
        events[2:2] = [
            {"type": "item.started", "item": {
                "id": "hm-1", "type": "mcp_tool_call",
                "server": "hashmarks", "tool": "task_evidence",
            }},
            {"type": "item.started", "item": {
                "id": "cmd-1", "type": "command_execution",
            }},
        ]
        events.insert(5, {"type": "item.completed", "item": {
            "id": "hm-1", "type": "mcp_tool_call",
            "server": "hashmarks", "tool": "task_evidence",
            "status": "completed", "result": {"content": []},
        }})
        result = self.inspect("\n".join(json.dumps(row) for row in events))
        self.assertEqual(result["trace_state"], "COMPLETE_MCP_TELEMETRY")
        self.assertTrue(result["subject_invoked"])
        self.assertEqual(
            result["started_tool_order"], {
                "state": "OBSERVED_START_ORDER",
                "first_started_tool": "mcp:hashmarks/task_evidence",
                "first_started_class": "subject-repository-intelligence",
                "subject_first_started": True,
                "subject_first_started_ordinal": 1,
            },
        )
        self.assertEqual(
            result["completed_tool_order"]["first_completed_tool"],
            "command_execution",
        )
        self.assertEqual(
            result["completed_tool_order"]["subject_first_completed_ordinal"], 2
        )
        self.assertEqual(result["model_request_inclusion"], "UNKNOWN")

    def test_codex_start_completion_item_binding_rejects_changed_tool_identity(self):
        baseline = [json.loads(row) for row in native("codex").splitlines()]
        for label, started, completed in (
            (
                "mcp-becomes-command",
                {"id": "cmd-1", "type": "mcp_tool_call",
                 "server": "hashmarks", "tool": "task_evidence"},
                {"id": "cmd-1", "type": "command_execution"},
            ),
            (
                "command-becomes-mcp",
                {"id": "cmd-1", "type": "command_execution"},
                {"id": "cmd-1", "type": "mcp_tool_call",
                 "server": "hashmarks", "tool": "task_evidence"},
            ),
            (
                "mcp-tool-changes",
                {"id": "cmd-1", "type": "mcp_tool_call",
                 "server": "hashmarks", "tool": "find"},
                {"id": "cmd-1", "type": "mcp_tool_call",
                 "server": "hashmarks", "tool": "task_evidence"},
            ),
            (
                "mcp-server-changes",
                {"id": "cmd-1", "type": "mcp_tool_call",
                 "server": "hashmarks", "tool": "task_evidence"},
                {"id": "cmd-1", "type": "mcp_tool_call",
                 "server": "foreign", "tool": "task_evidence"},
            ),
        ):
            with self.subTest(label=label):
                events = json.loads(json.dumps(baseline))
                events.insert(2, {"type": "item.started", "item": started})
                events[3]["item"] = completed
                observed = self.inspect("\n".join(json.dumps(e) for e in events))
                self.assertEqual(observed["trace_state"], "UNKNOWN")
                # A genuine completed subject event remains positive
                # operational evidence even when the start/completion binding
                # makes the whole trial unsuitable for rate qualification.
                completed_subject = (
                    completed["type"] == "mcp_tool_call"
                    and completed.get("server") == "hashmarks"
                )
                self.assertIs(
                    observed["subject_invoked"],
                    True if completed_subject else None,
                )
                started_subject = (
                    started["type"] == "mcp_tool_call"
                    and started.get("server") == "hashmarks"
                )
                self.assertIs(
                    observed["subject_start_observed"],
                    True if started_subject else None,
                )
                self.assertEqual(observed["started_tool_order"]["state"], "UNKNOWN")
                self.assertEqual(observed["completed_tool_order"]["state"], "UNKNOWN")
                self.assertEqual(observed["model_request_inclusion"], "UNKNOWN")

    def test_codex_matching_start_completion_allows_historical_completion_scope(self):
        events = [json.loads(row) for row in native("codex").splitlines()]
        events.insert(2, {"type": "item.started", "item": {
            "id": "cmd-1", "type": "command_execution"
        }})
        observed = self.inspect("\n".join(json.dumps(e) for e in events))
        self.assertEqual(observed["trace_state"], "COMPLETE_MCP_TELEMETRY")
        self.assertFalse(observed["subject_invoked"])
        self.assertEqual(
            observed["started_tool_order"]["first_started_tool"],
            "command_execution",
        )
        self.assertEqual(
            observed["completed_tool_order"]["first_completed_tool"],
            "command_execution",
        )

    def test_codex_missing_start_and_duplicate_item_ids_are_not_first_choice_proof(self):
        # Historical completion-only native exports cannot prove start order.
        baseline = self.inspect(native("codex"))
        self.assertEqual(baseline["trace_state"], "COMPLETE_MCP_TELEMETRY")
        self.assertEqual(baseline["started_tool_order"]["state"], "UNKNOWN")
        self.assertFalse(baseline["subject_invoked"])

        events = [json.loads(line) for line in native("codex").splitlines()]
        duplicate = dict(events[2])
        events.insert(3, duplicate)
        result = self.inspect("\n".join(json.dumps(row) for row in events))
        self.assertEqual(result["trace_state"], "UNKNOWN")
        self.assertIsNone(result["subject_invoked"])
        self.assertEqual(result["started_tool_order"]["state"], "UNKNOWN")
        self.assertEqual(result["completed_tool_order"]["state"], "UNKNOWN")

        reversed_lifecycle = [json.loads(line) for line in native("codex").splitlines()]
        reversed_lifecycle.insert(3, {"type": "item.started", "item": {
            "id": "cmd-1", "type": "command_execution",
        }})
        late_start = self.inspect(
            "\n".join(json.dumps(row) for row in reversed_lifecycle)
        )
        self.assertEqual(late_start["trace_state"], "UNKNOWN")
        self.assertIsNone(late_start["subject_invoked"])
        self.assertEqual(late_start["started_tool_order"]["state"], "UNKNOWN")

        empty_id = [json.loads(line) for line in native("codex").splitlines()]
        empty_id[2]["item"]["id"] = ""
        rejected = self.inspect(
            "\n".join(json.dumps(row) for row in empty_id)
        )
        self.assertEqual(rejected["trace_state"], "UNKNOWN")
        self.assertIsNone(rejected["subject_invoked"])

        unsupported = self.inspect(native("opencode-native"), "opencode-native")
        self.assertEqual(unsupported["started_tool_order"]["state"], "UNKNOWN")

    def test_started_order_summary_separates_observed_and_unknown_assignments(self):
        summary = _tool_order_summary(
            [
                {"started_tool_order": {
                    "state": "OBSERVED_START_ORDER", "first_started_tool": "mcp:hashmarks/task_evidence",
                    "subject_first_started": True,
                }},
                {"started_tool_order": {
                    "state": "OBSERVED_START_ORDER", "first_started_tool": "command_execution",
                    "subject_first_started": False,
                }},
                {"started_tool_order": {
                    "state": "OBSERVED_START_ORDER", "first_started_tool": None,
                    "subject_first_started": False,
                }},
                {"started_tool_order": {
                    "state": "UNKNOWN", "first_started_tool": None,
                    "subject_first_started": None,
                }},
            ],
            field="started_tool_order",
            phase="started",
            observed_state="OBSERVED_START_ORDER",
        )
        self.assertEqual(summary, {
            "assigned_trials": 4,
            "subject_first_started": 1,
            "other_tool_first_started": 1,
            "no_tracked_tool": 1,
            "unknown": 1,
            "claim_scope": (
                "observed-started-tool-order-only;"
                "not-native-tool-choice-or-model-request-proof"
            ),
        })
        legacy = self.report(invoked=True)
        self.assertEqual(
            legacy["started_tool_order_diagnostic"]["unknown"], 2
        )
        self.assertEqual(
            legacy["started_tool_order_diagnostic"]["assigned_trials"], 2
        )
        self.assertEqual(
            legacy["completed_tool_order_diagnostic"]["unknown"], 0
        )
        self.assertEqual(legacy["host_catalog_inclusion"], "UNKNOWN")

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

    def test_native_subject_start_survives_incomplete_return_without_inventing_success(self):
        raw = "\n".join(json.dumps(event) for event in (
            {"type": "thread.started", "thread_id": "fixture"},
            {"type": "turn.started"},
            {"type": "item.started", "item": {
                "id": "hm-start", "type": "mcp_tool_call",
                "server": "hashmarks", "tool": "task_evidence",
            }},
        ))
        observed = self.inspect(raw, complete=False)
        self.assertEqual(observed["trace_state"], "UNKNOWN")
        self.assertIs(observed["subject_start_observed"], True)
        self.assertIsNone(observed["subject_invoked"])
        self.assertIsNone(observed["successful_return_observed"])
        self.assertEqual(observed["started_tool_order"]["state"], "UNKNOWN")
        self.assertEqual(observed["model_request_inclusion"], "UNKNOWN")
        self.assertEqual(observed["model_attention"], "UNKNOWN")

    def test_native_start_requires_one_live_thread_and_turn(self):
        started = {"type": "item.started", "item": {
            "id": "hm", "type": "mcp_tool_call",
            "server": "hashmarks", "tool": "task_evidence",
        }}
        thread = {"type": "thread.started", "thread_id": "test"}
        turn = {"type": "turn.started"}
        terminal = {"type": "turn.failed", "reason": "interrupted"}
        scenarios = (
            ([started], False),
            ([thread, started, turn], False),
            ([thread, turn, terminal, started], False),
            ([thread, turn, started, terminal], True),
            ([thread, turn, started], True),
            ([thread, turn, turn, started], False),
        )
        for events, expected in scenarios:
            with self.subTest(events=events):
                report = self.inspect(
                    "\n".join(json.dumps(event) for event in events),
                    complete=False,
                )
                self.assertEqual(report["trace_state"], "UNKNOWN")
                self.assertIs(report["subject_start_observed"], True if expected else None)
                self.assertIsNone(report["subject_invoked"])
                self.assertIsNone(report["successful_return_observed"])

    def test_completed_call_does_not_fabricate_native_start_or_host_delivery(self):
        observed = self.inspect(native("codex", invoked=True))
        self.assertTrue(observed["subject_invoked"])
        self.assertIsNone(observed["subject_start_observed"])
        self.assertEqual(observed["started_tool_order"]["state"], "UNKNOWN")
        for adapter in ("codex", "opencode-native"):
            with self.subTest(adapter=adapter):
                neutral = self.inspect(native(adapter), adapter)
                self.assertIsNone(neutral["subject_start_observed"])
        foreign = self.inspect(
            "\n".join(json.dumps(event) for event in (
                {"type": "thread.started", "thread_id": "fixture"},
                {"type": "turn.started"},
                {"type": "item.started", "item": {
                    "id": "foreign", "type": "mcp_tool_call",
                    "server": "unrelated", "tool": "task_evidence",
                }},
            )), complete=False,
        )
        self.assertIsNone(foreign["subject_start_observed"])

    def test_start_summary_keeps_missing_start_proof_unknown(self):
        report = self.report(invoked=True)
        self.assertEqual(
            report["native_subject_start_diagnostic"],
            {
                "assigned_trials": 2,
                "observed_starts": 0,
                "not_established": 2,
                "claim_scope": "observed-native-mcp-start-only;not-adoption-or-return",
            },
        )
        self.assertEqual(report["voluntary_adoption"]["invoked"], 1)

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
        self.assertEqual(
            report["voluntary_adoption"],
            {
                "assigned_trials": 2,
                "invoked": 0,
                "not_invoked": 2,
                "unknown": 0,
                "rate": 0.0,
                "rate_qualification": "qualified",
            },
        )
        self.assertEqual(report["primary_availability_intention_to_treat"]["expected_pairs"], 2)

    def test_completed_tool_order_summary_preserves_assigned_arms_and_unknowns(self):
        normal = self.report(invoked=True)
        self.assertEqual(
            normal["completed_tool_order_diagnostic"],
            {
                "assigned_trials": 2,
                "subject_first_completed": 1,
                "other_tool_first_completed": 1,
                "no_tracked_tool": 0,
                "unknown": 0,
                "claim_scope": (
                    "observed-completed-tool-order-only;"
                    "not-native-tool-choice-or-model-request-proof"
                ),
            },
        )
        incomplete = self.report(incomplete=True, invoked=True)
        summary = incomplete["completed_tool_order_diagnostic"]
        self.assertEqual(summary["assigned_trials"], 2)
        self.assertEqual(summary["subject_first_completed"], 0)
        self.assertEqual(summary["other_tool_first_completed"], 1)
        self.assertEqual(summary["unknown"], 1)
        self.assertFalse(incomplete["study_measurement_qualified"])
        self.assertEqual(incomplete["host_catalog_inclusion"], "UNKNOWN")

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

    def test_unqualified_adoption_counts_cannot_be_reported_as_a_rate(self):
        # A complete trace can count a call even if the experiment's
        # independently graded answer, catalog, or authority is unqualified.
        for reason in ("answer-mismatch", "catalog-missing", "authority-tainted"):
            with self.subTest(reason=reason):
                report = self.report(
                    invoked=True,
                    mismatch=reason == "answer-mismatch",
                    catalog_known=reason != "catalog-missing",
                    tainted=reason == "authority-tainted",
                )
                self.assertFalse(report["study_measurement_qualified"])
                adoption = report["voluntary_adoption"]
                self.assertEqual(adoption["assigned_trials"], 2)
                self.assertEqual(adoption["invoked"], 1)
                self.assertEqual(adoption["not_invoked"], 1)
                self.assertEqual(adoption["unknown"], 0)
                self.assertIsNone(adoption["rate"])
                self.assertEqual(
                    adoption["rate_qualification"],
                    "unqualified-operational-counts-only",
                )

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
