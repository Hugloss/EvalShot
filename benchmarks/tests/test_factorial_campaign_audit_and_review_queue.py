"""Adversarial regressions for campaign coverage and independent-review handoff."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from benchmarks.harbor_matrix import load_matrix
from benchmarks.harness.factorial_campaign_audit import (
    audit_factorial_campaign,
    audit_bundle_directory,
)
from benchmarks.harness.independent_review_queue import (
    build_repo_review_queue,
    build_review_queue,
)


MATRIX = {
    "harnesses": ["codex"],
    "subjects": [
        "none", "hashmarks", "hashmarks-no-task-evidence",
        "hashmarks-no-find", "hashmarks-no-task-evidence-find",
    ],
    "modes": {"smoke": {"tasks": ["owner"], "attempts": 1}},
    "factorial": {
        "components": ["task_evidence", "find"],
        "arms": {
            "neither": "hashmarks-no-task-evidence-find",
            "a_only": "hashmarks-no-find",
            "b_only": "hashmarks-no-task-evidence",
            "both": "hashmarks",
        },
    },
}
TOOLS = ("repository_context", "task_evidence", "find", "change_impact")


def projections() -> list[dict]:
    specs = [
        ("none", [], []),
        ("hashmarks-no-task-evidence-find", ["repository_context", "change_impact"], []),
        ("hashmarks-no-find", ["repository_context", "task_evidence", "change_impact"],
         ["mcp__hashmarks__task_evidence"]),
        ("hashmarks-no-task-evidence", ["repository_context", "find", "change_impact"],
         ["mcp__hashmarks__find"]),
        ("hashmarks", list(TOOLS), ["mcp__hashmarks__task_evidence", "mcp__hashmarks__find"]),
    ]
    rows = []
    for subject, tools, called in specs:
        execution = {"campaign_id": "campaign-v1"}
        if subject != "none":
            execution["mcp_treatment"] = {
                "source_contract_identity": "sha256:frozen-source",
                "full_contract": subject == "hashmarks",
                "tools": tools,
                "repository_intelligence_query_surfaces": [],
            }
        rows.append({
            "receipt": {
                "backend": "harbor", "harness": "codex", "task_id": "owner",
                "model": "model-a", "replicate_id": 1, "subject": subject,
                "status": "PASS", "execution": execution,
            },
            "trace": {
                "available": True, "tool_order_complete": True,
                "subject_tools": called,
            },
        })
    return rows


def audit(rows: list[dict], **kwargs: object) -> dict:
    return audit_factorial_campaign(
        rows, MATRIX, mode="smoke", campaign_id="campaign-v1", **kwargs,
    )


class ExactFactorialCampaignCoverage(unittest.TestCase):
    def test_complete_matrix_is_coverage_only_not_empirical_effect_or_delivery(self) -> None:
        result = audit(projections())
        self.assertEqual(result["coverage_state"], "COMPLETE")
        self.assertEqual(result["matrix_expected_cells"], 5)
        self.assertEqual(result["observed_unique_cells"], 5)
        self.assertEqual(result["qualified_complete_quartets"], 1)
        self.assertEqual(result["issues"], {})
        self.assertFalse(result["empirical_effect_qualified"])
        self.assertFalse(result["model_input_delivery_attested"])
        self.assertFalse(result["independent_oracle_review_attested"])

    def test_missing_and_duplicate_cells_fail_even_when_a_quartet_qualifies(self) -> None:
        rows = projections()
        missing = audit(rows[1:])
        self.assertEqual(missing["qualified_complete_quartets"], 1)
        self.assertEqual(missing["issues"]["missing-grid-cell"], 1)
        self.assertEqual(missing["coverage_state"], "INCOMPLETE")
        doubled = audit(rows + [copy.deepcopy(rows[-1])])
        self.assertEqual(doubled["issues"]["duplicate-grid-cell"], 1)

    def test_foreign_campaign_and_foreign_subject_rejected(self) -> None:
        rows = projections()
        rows[0]["receipt"]["execution"]["campaign_id"] = "other"
        self.assertEqual(audit(rows)["issues"]["foreign-or-missing-campaign-id"], 1)
        rows = projections()
        rows[0]["receipt"]["subject"] = "enola"
        self.assertEqual(audit(rows)["issues"]["foreign-grid-cell"], 1)
        rows = projections()
        rows[0]["receipt"]["subject"] = ["none"]
        self.assertIn("foreign-grid-cell", audit(rows)["issues"])

    def test_incomplete_status_and_control_contamination_rejected(self) -> None:
        rows = projections()
        rows[0]["receipt"]["status"] = "INCOMPLETE"
        self.assertIn("incomplete-or-ungradeable-status", audit(rows)["issues"])
        rows = projections()
        rows[0]["trace"]["subject_tools"] = ["mcp__hashmarks__find"]
        self.assertIn("contaminated-bare-control", audit(rows)["issues"])

    def test_source_drift_and_model_mismatch_fail_even_with_correct_outcomes(self) -> None:
        rows = projections()
        rows[-1]["receipt"]["execution"]["mcp_treatment"][
            "source_contract_identity"
        ] = "sha256:replaced"
        self.assertIn("source-contract-drift", audit(rows)["issues"])
        rows = projections()
        rows[-1]["receipt"]["model"] = "model-b"
        self.assertIn("missing-or-mixed-model-per-harness", audit(rows)["issues"])

    def test_unavailable_trace_wrong_operation_duplicate_replicate(self) -> None:
        rows = projections()
        rows[-1]["trace"]["tool_order_complete"] = False
        self.assertIn("incomplete-tool-order", audit(rows)["issues"])
        rows = projections()
        rows[-1]["trace"]["subject_tools"] = ["mcp__hashmarks__unrecognized"]
        self.assertIn("factorial:both:unknown-operation-spelling", audit(rows)["issues"])
        rows = projections()
        rows[0]["receipt"]["replicate_id"] = True
        self.assertIn("foreign-grid-cell", audit(rows)["issues"])

    def test_invalid_matrix_and_corrupt_directory_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            audit_factorial_campaign(projections(), MATRIX, mode="matrix", campaign_id="campaign-v1")
        result = audit(projections(), corrupt_bundles=1)
        self.assertIn("corrupt-or-unverifiable-bundle", result["issues"])
        canonical = load_matrix(Path(__file__).resolve().parents[2] /
            "benchmarks/harbor/repository-intelligence-task-evidence-find-factorial-v1.json")
        empty = audit_factorial_campaign([], canonical, mode="matrix", campaign_id="new")
        self.assertEqual(empty["matrix_expected_cells"], 135)
        self.assertEqual(empty["matrix_expected_quartets"], 27)
        self.assertFalse(empty["empirical_effect_qualified"])
        self.assertEqual(empty["coverage_state"], "INCOMPLETE")
        self.assertEqual(empty["issues"]["missing-grid-cell"], 135)
        with self.assertRaises(ValueError):
            audit_factorial_campaign(
                projections(), MATRIX, mode="smoke", campaign_id="",
            )


class IndependentReviewQueue(unittest.TestCase):
    def test_current_frozen_corpora_are_only_actionable_queues(self) -> None:
        root = Path(__file__).resolve().parents[2]
        result = build_repo_review_queue(root)
        self.assertEqual(result["multidomain_cases_total"], 60)
        self.assertEqual(result["multidomain_review_required"], 60)
        skill_cases = json.loads(
            (root / "evals/sharp-skill-cases.json").read_text(encoding="utf-8")
        )["cases"]
        evaluated_skills = {case["skill"] for case in skill_cases}
        self.assertIn("canonical-consumer-contract-review", evaluated_skills)
        # Source availability belongs to the evaluator's explicit cookbook
        # input; the repository-local review queue describes frozen cases.
        for name in evaluated_skills:
            self.assertTrue({"positive", "control"}.issubset(
                {case["kind"] for case in skill_cases if case["skill"] == name}
            ))
        self.assertEqual(result["skills_total"], len(evaluated_skills))
        self.assertEqual(result["skills_missing_confusion"], 54)
        self.assertFalse(result["independent_review_completed"])
        self.assertTrue(all(
            not row["independent_approval_supplied_by_this_queue"]
            for row in result["multidomain_queue"]
        ))

    def test_mutating_a_review_does_not_change_frozen_case_identity(self) -> None:
        case = {"id": "a", "family": "logs", "repository_name": "r",
                "expected": {"path": "src/a.py"},
                "review": {"state": "pending"}}
        s = {"cases": [
            {"id": "positive", "skill": "owner-review", "kind": "positive"},
        ]}
        before = build_review_queue(s, {"cases": [case]})
        case["review"]["state"] = "escalated"
        after = build_review_queue(s, {"cases": [case]})
        self.assertEqual(before["multidomain_queue"][0]["frozen_case_sha256"],
                         after["multidomain_queue"][0]["frozen_case_sha256"])
        self.assertEqual(after["skills_missing_confusion"], 1)
        self.assertTrue(after["skill_confusion_queue"][0]["fixture_and_oracle_required"])
        case["expected"]["path"] = "src/b.py"
        changed = build_review_queue(s, {"cases": [case]})
        self.assertNotEqual(after["multidomain_queue"][0]["frozen_case_sha256"],
                            changed["multidomain_queue"][0]["frozen_case_sha256"])

    def test_self_declared_approved_case_still_needs_independent_verification(self) -> None:
        skill = {"cases": [{"id": "x", "skill": "owner", "kind": "confusion"}]}
        md = {"cases": [{"id": "a", "review": {
            "state": "approved", "approvals": [],
        }}]}
        queue = build_review_queue(skill, md)
        self.assertEqual(queue["multidomain_review_required"], 1)
        self.assertEqual(queue["multidomain_queue"][0]["required_action"],
                         "verify-independent-approval")
        self.assertFalse(queue["independent_review_completed"])

    def test_duplicate_and_unknown_review_authority_rejected(self) -> None:
        case = {"id": "a", "review": {"state": "pending"}}
        s = {"cases": [{"id": "x", "skill": "owner", "kind": "control"}]}
        with self.assertRaises(ValueError):
            build_review_queue(s, {"cases": [case, copy.deepcopy(case)]})
        with self.assertRaises(ValueError):
            build_review_queue(s, {"cases": [{"id": "a", "review": {"state": "self-approved"}}]})
        with self.assertRaises(ValueError):
            build_review_queue({"cases": s["cases"] * 2}, {"cases": [case]})


if __name__ == "__main__":
    unittest.main()
