"""Model-free factorial isolation and interaction attack regressions."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from benchmarks.harbor_matrix import HarborMatrixError, load_matrix, matrix_factorial
from benchmarks.matrix_profiles import harbor_suite, load_profile
from benchmarks.harness.factorial_attribution import factorial_quartet


TOOLS = ["repository_context", "task_evidence", "find", "change_impact"]
CONTRACT = {
    "components": ["task_evidence", "find"],
    "arms": {
        "neither": "hashmarks-no-task-evidence-find",
        "a_only": "hashmarks-no-find",
        "b_only": "hashmarks-no-task-evidence",
        "both": "hashmarks",
    },
}


def _arms(*, statuses: dict[str, str] | None = None) -> dict:
    statuses = statuses or {
        "neither": "FAIL", "a_only": "FAIL",
        "b_only": "FAIL", "both": "PASS",
    }
    allowed = {
        "neither": ["repository_context", "change_impact"],
        "a_only": ["repository_context", "task_evidence", "change_impact"],
        "b_only": ["repository_context", "find", "change_impact"],
        "both": TOOLS,
    }
    result = {}
    for role, names in allowed.items():
        result[role] = {
            "receipt": {
                "task_id": "owner", "model": "model", "harness": "codex",
                "replicate_id": 1, "subject": CONTRACT["arms"][role],
                "status": statuses[role],
                "execution": {
                    "mcp_treatment": {
                        "tools": names, "full_contract": role == "both",
                        "source_contract_identity": "sha256:contract",
                        "repository_intelligence_query_surfaces": [],
                    },
                },
            },
            "trace": {
                "available": True, "tool_order_complete": True,
                "subject_tools": (
                    ["mcp__hashmarks__task_evidence", "mcp__hashmarks__find"]
                    if role == "both" else []
                ),
            },
        }
    return result


class FactorialContracts(unittest.TestCase):
    def test_frozen_matrix_accepts_exact_tool_catalog_isolation(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        matrix = load_matrix(
            repo / "benchmarks/harbor/repository-intelligence-task-evidence-find-factorial-v1.json"
        )
        self.assertEqual(matrix_factorial(matrix), CONTRACT)
        self.assertEqual(len(matrix["subjects"]), 5)
        smoke, _ = harbor_suite(load_profile("harbor-factorial-smoke"))
        full, _ = harbor_suite(load_profile("harbor-factorial-full"))
        self.assertEqual(len(smoke.trial_definitions()), 15)
        self.assertEqual(len(full.trial_definitions()), 135)

    def test_interaction_never_claims_causality(self) -> None:
        result = factorial_quartet(_arms(), CONTRACT)
        self.assertTrue(result["treatment_qualified"])
        self.assertEqual(result["interaction"], 1)
        self.assertFalse(result["positive_causal_proof_claimed"])
        self.assertTrue(result["observed_component_use"]["both"]["task_evidence"])
        self.assertFalse(result["observed_component_use"]["neither"]["task_evidence"])

    def test_not_every_positive_result_is_interaction(self) -> None:
        result = factorial_quartet(
            _arms(statuses={role: "PASS" for role in CONTRACT["arms"]}), CONTRACT
        )
        self.assertEqual(result["interaction"], 0)

    def test_disallowed_and_unknown_calls_fail_closed(self) -> None:
        for role, tool in (
            ("neither", "mcp__hashmarks__task_evidence"),
            ("a_only", "mcp__hashmarks__find"),
            ("b_only", "mcp__hashmarks__task_evidence"),
            ("both", "mcp__hashmarks__unrecognized"),
        ):
            arms = _arms()
            arms[role]["trace"]["subject_tools"].append(tool)
            with self.subTest(role=role):
                result = factorial_quartet(arms, CONTRACT)
                self.assertFalse(result["treatment_qualified"])
                self.assertIsNone(result["interaction"])

    def test_missing_tool_order_and_source_mismatch_fail_closed(self) -> None:
        first = _arms()
        first["both"]["trace"]["tool_order_complete"] = False
        self.assertEqual(
            factorial_quartet(first, CONTRACT)["qualification_reason"],
            "both:incomplete-tool-order",
        )
        second = _arms()
        second["a_only"]["receipt"]["execution"]["mcp_treatment"][
            "source_contract_identity"
        ] = "sha256:different"
        self.assertEqual(
            factorial_quartet(second, CONTRACT)["qualification_reason"],
            "arm-source-or-subject-mismatch",
        )
        third = _arms()
        third["b_only"]["receipt"]["status"] = "INCOMPLETE"
        self.assertEqual(
            factorial_quartet(third, CONTRACT)["qualification_reason"],
            "incomplete-factorial-outcome",
        )

    def test_forged_projection_and_duplicate_component_not_admitted(self) -> None:
        arms = _arms()
        arms["a_only"]["receipt"]["execution"]["mcp_treatment"]["tools"].append("find")
        self.assertEqual(
            factorial_quartet(arms, CONTRACT)["qualification_reason"],
            "arm-projection-not-exact",
        )
        duplicate = copy.deepcopy(CONTRACT)
        duplicate["components"] = ["find", "find"]
        with self.assertRaises(ValueError):
            factorial_quartet(_arms(), duplicate)


if __name__ == "__main__":
    unittest.main()
