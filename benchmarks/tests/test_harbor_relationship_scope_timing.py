"""Qualify the arrival and follow-through of Hashmarks semantic scopes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.mechanism_attribution import pair_projection
from benchmarks.harness.relationship_scope_timing import (
    ASSOCIATED,
    OUTGOING,
    project_relationship_scope_timing,
)

TOOL = "mcp__hashmarks__task_evidence"
DETAIL = "mcp__hashmarks__structural_locality"
SUBJECT = "src/engine.py::normalize_widget"


def _packet(*, outgoing: int = 1, associated: int = 1) -> dict:
    return {
        "ownership": {
            "status": "resolved",
            "proof_scope_complete": True,
            "authority": "repository-ownership-only",
            "owner": {"path": "src/engine.py", "qualname": "normalize_widget"},
        },
        "semantic_relationships": {
            "subject": SUBJECT,
            "observation_state": (
                "direct-claims-observed" if outgoing else "definition-observed-no-direct-claims"
            ),
            "observed_relationship_count": outgoing,
            "observed_relationship_count_scope": OUTGOING,
            "associated_observed_relationship_count": associated,
            "associated_observed_relationship_count_scope": ASSOCIATED,
            "negative_evidence_admissible": False,
            "evidence": {
                "observed_relationship_count": associated,
                "negative_evidence_admissible": False,
            },
        },
    }


def _step(name: str, call_id: str, *, arguments=None, response=None) -> dict:
    record = {
        "source": "agent",
        "message": "Never interpret this message as semantic evidence",
        "reasoning_content": "unobservable",
        "tool_calls": [{
            "function_name": name,
            "tool_call_id": call_id,
            "arguments": arguments or {},
        }],
    }
    if response is not None:
        record["observation"] = {
            "results": [{"source_call_id": call_id, "content": response}]
        }
    return record


def _run(steps: list[dict]) -> dict:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "trajectory.json"
        path.write_text(
            json.dumps({"schema_version": "ATIF-v1.8", "steps": steps}),
            encoding="utf-8",
        )
        return project_relationship_scope_timing(path)


class RelationshipScopeTimingTests(unittest.TestCase):
    def test_scoped_outgoing_return_before_native_with_exact_followup(self) -> None:
        result = _run([
            _step(TOOL, "h1", response={"structuredContent": {"result": _packet()}}),
            _step(
                DETAIL,
                "h2",
                arguments={"target": SUBJECT, "result_mode": "relationships"},
            ),
            _step("rg", "n1", arguments={"pattern": "normalize_widget"}),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["state"], "SCOPED_SEMANTIC_RETURN")
        self.assertEqual(result["summary_count_scope"], OUTGOING)
        self.assertEqual(result["summary_count"], 1)
        self.assertEqual(result["associated_count"], 1)
        self.assertEqual(result["arrival_timing"], "BEFORE_NATIVE_DISCOVERY")
        self.assertEqual(result["detail_followthrough"], "EXACT_DETAIL_REQUEST_AFTER_RETURN")
        self.assertEqual(result["first_semantic_return_step"], 1)
        self.assertEqual(result["first_exact_detail_request_step"], 2)
        self.assertEqual(result["native_search_before_return"], 0)
        self.assertEqual(result["native_search_after_return"], 1)
        self.assertFalse(result["observed_use_proven"])
        self.assertFalse(result["agent_attention_proven"])
        self.assertFalse(result["causal_influence_claimed"])

    def test_incoming_only_is_not_mistaken_for_missing_relationships(self) -> None:
        result = _run([
            _step(TOOL, "h1", response=_packet(outgoing=0, associated=1)),
            _step("rg", "n1", arguments={"pattern": "reference"}),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["summary_count"], 0)
        self.assertEqual(result["associated_count"], 1)
        self.assertEqual(result["summary_count_scope"], OUTGOING)
        self.assertEqual(result["associated_count_scope"], ASSOCIATED)
        self.assertEqual(result["detail_followthrough"], "NO_EXACT_DETAIL_REQUEST_OBSERVED")

    def test_late_arrival_is_based_on_linked_observation_not_invocation(self) -> None:
        first = _step(TOOL, "h1")
        native = _step("rg", "n1")
        environment = {
            "source": "environment",
            "observation": {
                "results": [{"source_call_id": "h1", "content": _packet()}]
            },
        }
        result = _run([first, native, environment])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["first_semantic_return_step"], 3)
        self.assertEqual(result["arrival_timing"], "AFTER_NATIVE_DISCOVERY")
        self.assertEqual(result["native_search_before_return"], 1)

    def test_simultaneous_return_and_native_discovery_has_unknown_order(self) -> None:
        result = _run([{
            "source": "agent",
            "tool_calls": [
                {"function_name": TOOL, "tool_call_id": "h1", "arguments": {}},
                {"function_name": "rg", "tool_call_id": "n1", "arguments": {}},
            ],
            "observation": {
                "results": [{"source_call_id": "h1", "content": _packet()}]
            },
        }])
        self.assertEqual(result["arrival_timing"], "SAME_STEP_UNORDERED")
        self.assertEqual(result["native_search_before_return"], 0)
        self.assertEqual(result["native_search_after_return"], 0)
        self.assertEqual(result["native_search_same_step"], 1)

    def test_wrong_detail_target_does_not_claim_followthrough(self) -> None:
        result = _run([
            _step(TOOL, "h1", response=_packet()),
            _step(
                DETAIL, "h2", arguments={
                    "target": "src/other.py::normalize_widget",
                    "result_mode": "relationships",
                }
            ),
            _step(DETAIL, "h3", arguments={"target": SUBJECT, "result_mode": "default"}),
        ])
        self.assertEqual(result["detail_followthrough"], "NO_EXACT_DETAIL_REQUEST_OBSERVED")

    def test_mcp_json_text_block_is_scoped_return(self) -> None:
        response = [{"type": "text", "text": json.dumps({"result": _packet()})}]
        result = _run([_step(TOOL, "h1", response={"content": response})])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["arrival_timing"], "NO_NATIVE_DISCOVERY")

    def test_hidden_router_before_return_cannot_prove_first_choice(self) -> None:
        result = _run([
            _step("functions.exec", "router-1", arguments={"input": "opaque"}),
            _step(TOOL, "h1", response=_packet()),
            _step("rg", "native-1", arguments={"pattern": "owner"}),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["arrival_timing"], "UNKNOWN_OPAQUE_ROUTER")
        self.assertEqual(result["first_native_discovery_step"], 3)
        self.assertEqual(result["native_search_observability"], "PARTIAL_OPAQUE_TOOL")
        self.assertEqual(result["native_search_before_return"], 0)
        self.assertEqual(result["native_search_after_return"], 1)
        self.assertFalse(result["causal_influence_claimed"])

    def test_hidden_router_after_return_cannot_prove_no_discovery_or_detail(self) -> None:
        result = _run([
            _step(TOOL, "h1", response=_packet(outgoing=0, associated=1)),
            _step("functions.exec", "router-1"),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["arrival_timing"], "UNKNOWN_OPAQUE_ROUTER")
        self.assertEqual(result["detail_followthrough"], "UNKNOWN_OPAQUE_DETAIL_REQUEST")
        self.assertEqual(result["native_search_observability"], "PARTIAL_OPAQUE_TOOL")
        self.assertEqual(result["native_search_after_return"], 0)
        self.assertFalse(result["agent_attention_proven"])

    def test_explicit_native_discovery_before_opaque_router_stays_observed(self) -> None:
        result = _run([
            _step("rg", "native-1", arguments={"pattern": "owner"}),
            _step("functions.exec", "router-1"),
            _step(TOOL, "h1", response=_packet()),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["arrival_timing"], "AFTER_NATIVE_DISCOVERY")
        self.assertEqual(result["native_search_before_return"], 1)
        self.assertEqual(result["native_search_observability"], "PARTIAL_OPAQUE_TOOL")

    def test_explicit_exact_detail_remains_positive_despite_opaque_router(self) -> None:
        result = _run([
            _step(TOOL, "h1", response=_packet()),
            _step("functions.exec", "router-1"),
            _step(
                DETAIL, "detail-1",
                arguments={"target": SUBJECT, "result_mode": "relationships"},
            ),
        ])
        self.assertEqual(
            result["detail_followthrough"], "EXACT_DETAIL_REQUEST_AFTER_RETURN"
        )
        self.assertEqual(result["first_exact_detail_request_step"], 3)

    def test_shell_discovery_does_not_prove_no_hidden_detail_or_exact_search(self) -> None:
        result = _run([
            _step(TOOL, "h1", response=_packet()),
            _step("shell", "shell-1", arguments={"command": "rg widget src"}),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["arrival_timing"], "BEFORE_NATIVE_DISCOVERY")
        self.assertEqual(result["detail_followthrough"], "UNKNOWN_OPAQUE_DETAIL_REQUEST")
        self.assertEqual(result["native_search_observability"], "PARTIAL_OPAQUE_TOOL")
        self.assertEqual(result["native_search_after_return"], 0)

    def test_unrelated_tool_return_before_call_invalidates_timeline(self) -> None:
        early = {
            "source": "environment",
            "observation": {
                "results": [
                    {"source_call_id": "native-1", "content": {"path": "src/x.py"}}
                ]
            },
        }
        result = _run([
            early,
            _step(TOOL, "h1", response=_packet()),
            _step("read_file", "native-1", arguments={"path": "src/x.py"}),
        ])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "atif-order-or-link-invalid")

    def test_unknown_text_and_error_are_not_evidence_absence(self) -> None:
        for response in (
            [{"type": "text", "text": "unstructured response"}],
            {"isError": True, "content": [{"type": "text", "text": "{}"}]},
            {"semantic_relationships": {"observed_relationship_count": 0}},
        ):
            with self.subTest(response=response):
                result = _run([_step(TOOL, "h1", response=response)])
                self.assertFalse(result["qualified"])
                self.assertIn(result["reason"], {
                    "unstructured-subject-result",
                    "invalid-semantic-observation",
                })

    def test_cross_presentation_parity_is_required(self) -> None:
        packet = _packet(outgoing=0, associated=1)
        same = {
            "structuredContent": {"result": packet},
            "content": [{"type": "text", "text": json.dumps({"result": packet})}],
        }
        qualified = _run([_step(TOOL, "h1", response=same)])
        self.assertTrue(qualified["qualified"])
        self.assertEqual(qualified["summary_count"], 0)
        self.assertEqual(qualified["associated_count"], 1)

        divergent = _packet(outgoing=1, associated=1)
        mismatched = {
            "structuredContent": {"result": packet},
            "content": [{"type": "text", "text": json.dumps({"result": divergent})}],
        }
        unqualified = _run([_step(TOOL, "h1", response=mismatched)])
        self.assertFalse(unqualified["qualified"])
        self.assertEqual(unqualified["reason"], "invalid-semantic-observation")

    def test_unqualified_owner_does_not_become_qualified_scope(self) -> None:
        for field, value in (
            ("status", "ambiguous"),
            ("proof_scope_complete", False),
            ("authority", "caller-claimed"),
        ):
            packet = _packet()
            packet["ownership"][field] = value
            with self.subTest(field=field):
                result = _run([_step(TOOL, "h1", response=packet)])
                self.assertFalse(result["qualified"])
                self.assertEqual(result["reason"], "invalid-semantic-observation")

    def test_mismatched_count_or_scope_is_unqualified(self) -> None:
        for alteration in (
            ("associated_observed_relationship_count", 0),
            ("observed_relationship_count_scope", "complete-repository-graph"),
            ("negative_evidence_admissible", True),
            ("subject", "src/other.py::normalize_widget"),
            ("observation_state", "definition-observed-no-direct-claims"),
        ):
            packet = _packet()
            packet["semantic_relationships"][alteration[0]] = alteration[1]
            with self.subTest(field=alteration[0]):
                result = _run([_step(TOOL, "h1", response=packet)])
                self.assertFalse(result["qualified"])
                self.assertEqual(result["reason"], "invalid-semantic-observation")

    def test_incomplete_trace_and_duplicate_link_fail_closed(self) -> None:
        missing = _run([_step(TOOL, "h1")])
        self.assertFalse(missing["qualified"])
        self.assertEqual(missing["reason"], "missing-task-evidence-result")
        duplicate = _step(TOOL, "h1", response=_packet())
        duplicate["observation"]["results"].append({
            "source_call_id": "h1", "content": _packet()
        })
        result = _run([duplicate])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "atif-order-or-link-invalid")

    def test_identical_multiple_returns_are_counted_without_double_attribution(self) -> None:
        packet = _packet(outgoing=0, associated=1)
        result = _run([
            _step(TOOL, "h1", response={"result": packet}),
            _step("rg", "n1", arguments={"pattern": "widget"}),
            _step(TOOL, "h2", response={"structuredContent": packet}),
            _step(
                DETAIL, "h3", arguments={
                    "target": SUBJECT, "result_mode": "relationships",
                },
            ),
        ])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["scoped_return_count"], 2)
        self.assertEqual(result["first_semantic_return_step"], 1)
        self.assertEqual(result["arrival_timing"], "BEFORE_NATIVE_DISCOVERY")
        self.assertEqual(
            result["detail_followthrough"], "EXACT_DETAIL_REQUEST_AFTER_RETURN"
        )
        self.assertEqual(result["summary_count"], 0)
        self.assertEqual(result["associated_count"], 1)
        self.assertEqual(len(result["captured_semantic_record_sha256"]), 64)
        self.assertFalse(result["causal_influence_claimed"])

    def test_multiple_returns_with_disagreeing_counts_fail_closed(self) -> None:
        result = _run([
            _step(TOOL, "h1", response=_packet(outgoing=0, associated=1)),
            _step(TOOL, "h2", response=_packet(outgoing=1, associated=1)),
        ])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "conflicting-scoped-semantic-returns")
        self.assertIsNone(result["summary_count"])
        self.assertFalse(result["agent_attention_proven"])

    def test_matching_counts_with_different_producer_claims_are_not_parity(self) -> None:
        first = _packet()
        second = _packet()
        second["semantic_relationships"]["producer_bindings"] = ["other-producer"]
        result = _run([
            _step(TOOL, "h1", response=first),
            _step(TOOL, "h2", response=second),
        ])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "conflicting-scoped-semantic-returns")

        disagreeing_views = {
            "structuredContent": first,
            "content": [{"type": "text", "text": json.dumps(second)}],
        }
        result = _run([_step(TOOL, "h1", response=disagreeing_views)])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "invalid-semantic-observation")

    def test_different_qualified_owners_must_not_be_merged_into_one_subject(self) -> None:
        other = _packet()
        other["ownership"]["owner"]["path"] = "src/other.py"
        other["semantic_relationships"]["subject"] = (
            "src/other.py::normalize_widget"
        )
        result = _run([
            _step(TOOL, "h1", response=_packet()),
            _step(TOOL, "h2", response=other),
        ])
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "conflicting-scoped-semantic-returns")

    def test_orphan_observation_and_malformed_unrelated_step_deny_complete_order(self) -> None:
        orphan = {
            "source": "environment",
            "observation": {
                "results": [{"source_call_id": "ghost", "content": _packet()}]
            },
        }
        for trace in (
            [orphan],
            [_step(TOOL, "h1", response=_packet()), orphan],
            [_step(TOOL, "h1", response=_packet()), {
                "source": "environment", "observation": "malformed",
            }],
        ):
            with self.subTest(trace=trace):
                result = _run(trace)
                self.assertFalse(result["qualified"])
                self.assertEqual(result["reason"], "atif-order-or-link-invalid")

    def test_never_invoked_or_no_scope_is_not_a_claim_of_absence(self) -> None:
        never = _run([_step("read_file", "n1")])
        self.assertTrue(never["qualified"])
        self.assertEqual(never["state"], "NEVER_INVOKED")
        empty = _run([_step(TOOL, "h1", response={"owner": "missing"})])
        self.assertTrue(empty["qualified"])
        self.assertEqual(empty["state"], "NO_SCOPE_RECORD")
        self.assertFalse(empty["causal_influence_claimed"])

    def test_pair_reports_scope_without_upgrading_causal_authority(self) -> None:
        scope = _run([
            _step(TOOL, "h1", response=_packet(outgoing=0, associated=1)),
            _step("rg", "n1"),
        ])
        def arm(subject: str, native_search: int, relationship=None) -> dict:
            return {
                "receipt": {
                    "task_id": "case",
                    "harness": "opencode",
                    "model": "model",
                    "replicate_id": 1,
                    "status": "PASS",
                },
                "trace": {
                    "available": True,
                    "tool_order_complete": True,
                    "treatment": "OBSERVED_SUCCESSFUL_RESULT",
                    "subject_routing_timing": "FIRST_CHOICE",
                    "subject_target_followthrough": "UNKNOWN",
                    "native_discovery_calls": native_search,
                    "native_search_calls": native_search,
                    "native_read_calls": 0,
                    "tool_calls": native_search + 1,
                    "total_tokens": 100,
                },
                "answer": None,
                "relationship_scope_timing": relationship,
            }
        pair = pair_projection(arm("none", 3), arm("hashmarks", 1, scope))
        self.assertEqual(pair["native_search_delta"], -2)
        self.assertEqual(pair["relationship_scope_evidence"]["associated_count"], 1)
        self.assertFalse(pair["positive_causal_proof_claimed"])


if __name__ == "__main__":
    unittest.main()
