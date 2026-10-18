"""Focused regressions for controlled Harbor component ablations."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness.ablation_attribution import (
    build_ablation_report,
    quartet_projection,
)
from benchmarks.harness.mechanism_attribution import project_atif


FULL_TOOLS = [
    "repository_context",
    "find",
    "task_evidence",
    "change_impact",
]


def _treatment(subject: str) -> dict[str, object] | None:
    if subject == "none":
        return None
    if subject == "hashmarks":
        return {
            "tools": FULL_TOOLS,
            "projection_identity": "sha256:full",
            "source_contract_identity": "sha256:contract",
            "full_contract": True,
        }
    if subject == "hashmarks-no-task-evidence":
        return {
            "tools": [
                "repository_context",
                "find",
                "change_impact",
            ],
            "projection_identity": "sha256:without",
            "source_contract_identity": "sha256:contract",
            "full_contract": False,
        }
    if subject == "hashmarks-task-evidence-only":
        return {
            "tools": ["task_evidence"],
            "projection_identity": "sha256:only",
            "source_contract_identity": "sha256:contract",
            "full_contract": False,
        }
    raise AssertionError(subject)


def _projection(
    subject: str,
    *,
    status: str,
    task_evidence_invoked: bool = False,
) -> dict[str, object]:
    tools = (
        ["mcp__hashmarks__task_evidence"]
        if task_evidence_invoked
        else (
            ["mcp__hashmarks__find"]
            if subject not in ("none", "hashmarks-task-evidence-only")
            else []
        )
    )
    return {
        "receipt": {
            "backend": "harbor",
            "task_id": "locate-owner",
            "harness": "codex",
            "subject": subject,
            "model": "provider/model",
            "replicate_id": 6201,
            "status": status,
            "execution": {
                "campaign_id": "campaign",
                "mcp_treatment": _treatment(subject),
            },
        },
        "trace": {
            "available": True,
            "tool_order_complete": True,
            "subject_tools": tools,
        },
        "answer": None,
    }


def _quartet(
    *,
    bare: str = "FAIL",
    full: str = "PASS",
    removed: str = "FAIL",
    only: str = "PASS",
    full_invoked: bool = True,
    only_invoked: bool = True,
) -> dict[str, dict[str, object]]:
    return {
        "none": _projection("none", status=bare),
        "hashmarks": _projection(
            "hashmarks",
            status=full,
            task_evidence_invoked=full_invoked,
        ),
        "hashmarks-no-task-evidence": _projection(
            "hashmarks-no-task-evidence",
            status=removed,
        ),
        "hashmarks-task-evidence-only": _projection(
            "hashmarks-task-evidence-only",
            status=only,
            task_evidence_invoked=only_invoked,
        ),
    }


class HarborAblationAttributionTests(unittest.TestCase):
    def _report_from_quartets(
        self,
        quartets: list[dict[str, dict[str, object]]],
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projections = {}
            for index, arms in enumerate(quartets):
                for subject, row in arms.items():
                    row["receipt"]["replicate_id"] = 6201 + index
                    directory = root / f"{index:02d}-{subject}"
                    directory.mkdir()
                    projections[directory.name] = row
            with mock.patch(
                "benchmarks.harness.ablation_attribution."
                "load_harbor_bundle_projection",
                side_effect=lambda directory: projections[directory.name],
            ):
                return build_ablation_report(root)

    def test_matched_quartet_can_support_necessity_and_sufficiency_contrasts(
        self,
    ) -> None:
        result = quartet_projection(_quartet())

        self.assertTrue(result["treatment_authority_valid"])
        self.assertEqual(
            result["necessity"],
            "SUPPORTED_NECESSITY_CONTRAST",
        )
        self.assertEqual(
            result["sufficiency"],
            "SUPPORTED_SUFFICIENCY_CONTRAST",
        )
        self.assertEqual(
            result["classification"],
            "NECESSARY_AND_SUFFICIENT_CONTRAST",
        )
        self.assertFalse(result["positive_causal_proof_claimed"])

    def test_positive_score_contrast_is_unattributable_without_invocation(
        self,
    ) -> None:
        result = quartet_projection(
            _quartet(
                full_invoked=False,
                only_invoked=False,
            )
        )

        self.assertEqual(
            result["necessity"],
            "UNATTRIBUTABLE_FULL_NEVER_INVOKED_COMPONENT",
        )
        self.assertEqual(
            result["sufficiency"],
            "UNATTRIBUTABLE_ONLY_ARM_NEVER_INVOKED_COMPONENT",
        )
        self.assertEqual(
            result["classification"],
            "NO_ISOLATED_COMPONENT_SIGNAL",
        )

    def test_same_analyzer_attributes_find_without_component_specific_code(
        self,
    ) -> None:
        contract = {
            "component": "find",
            "arms": {
                "bare": "none",
                "full": "hashmarks",
                "remove": "hashmarks-no-find",
                "only": "hashmarks-find-only",
            },
        }
        treatments = {
            "none": None,
            "hashmarks": {
                "tools": FULL_TOOLS,
                "projection_identity": "sha256:full",
                "source_contract_identity": "sha256:contract",
                "full_contract": True,
            },
            "hashmarks-no-find": {
                "tools": [
                    "repository_context",
                    "task_evidence",
                    "change_impact",
                ],
                "projection_identity": "sha256:without-find",
                "source_contract_identity": "sha256:contract",
                "full_contract": False,
            },
            "hashmarks-find-only": {
                "tools": ["find"],
                "projection_identity": "sha256:find-only",
                "source_contract_identity": "sha256:contract",
                "full_contract": False,
            },
        }

        def projection(
            subject: str,
            status: str,
            tools: list[str],
        ) -> dict[str, object]:
            return {
                "receipt": {
                    "backend": "harbor",
                    "task_id": "lookup-known-symbol",
                    "harness": "codex",
                    "subject": subject,
                    "model": "provider/model",
                    "replicate_id": 6201,
                    "status": status,
                    "execution": {
                        "campaign_id": "campaign",
                        "mcp_treatment": treatments[subject],
                        "ablation": contract,
                    },
                },
                "trace": {
                    "available": True,
                    "tool_order_complete": True,
                    "subject_tools": tools,
                },
                "answer": None,
            }

        result = quartet_projection(
            {
                "bare": projection("none", "FAIL", []),
                "full": projection(
                    "hashmarks",
                    "PASS",
                    ["mcp__hashmarks__find"],
                ),
                "remove": projection(
                    "hashmarks-no-find",
                    "FAIL",
                    ["mcp__hashmarks__task_evidence"],
                ),
                "only": projection(
                    "hashmarks-find-only",
                    "PASS",
                    ["mcp__hashmarks__find"],
                ),
            },
            contract=contract,
        )

        self.assertEqual(result["component"], "find")
        self.assertEqual(
            result["necessity"],
            "SUPPORTED_NECESSITY_CONTRAST",
        )
        self.assertEqual(
            result["sufficiency"],
            "SUPPORTED_SUFFICIENCY_CONTRAST",
        )
        self.assertEqual(
            result["classification"],
            "NECESSARY_AND_SUFFICIENT_CONTRAST",
        )
        self.assertEqual(
            result["component_invoked"],
            {"full": True, "only": True},
        )

    def test_selector_ablation_requires_exact_query_surface_invocation(
        self,
    ) -> None:
        contract = {
            "component": "repository_intelligence_query",
            "selector": {
                "argument": "surface_name",
                "value": "verification-explanation",
            },
            "arms": {
                "bare": "none",
                "full": "hashmarks",
                "remove": "hashmarks-no-verification-explanation",
                "only": "hashmarks-verification-explanation-only",
            },
        }
        full_tools = [*FULL_TOOLS, "repository_intelligence_query"]
        full_surfaces = ["verification-explanation", "freshness"]
        treatments = {
            "none": None,
            "hashmarks": {
                "tools": full_tools,
                "repository_intelligence_query_surfaces": full_surfaces,
                "projection_identity": "sha256:full",
                "source_contract_identity": "sha256:contract",
                "full_contract": True,
            },
            "hashmarks-no-verification-explanation": {
                "tools": full_tools,
                "repository_intelligence_query_surfaces": ["freshness"],
                "projection_identity": "sha256:without-surface",
                "source_contract_identity": "sha256:contract",
                "full_contract": False,
            },
            "hashmarks-verification-explanation-only": {
                "tools": ["repository_intelligence_query"],
                "repository_intelligence_query_surfaces": [
                    "verification-explanation"
                ],
                "projection_identity": "sha256:surface-only",
                "source_contract_identity": "sha256:contract",
                "full_contract": False,
            },
        }

        def projection(
            subject: str,
            status: str,
            surface: str | None = None,
        ) -> dict[str, object]:
            tools = (
                ["mcp__hashmarks__repository_intelligence_query"]
                if surface is not None
                else []
            )
            selectors = (
                [
                    {
                        "tool": "mcp__hashmarks__repository_intelligence_query",
                        "surface_name": surface,
                    }
                ]
                if surface is not None
                else []
            )
            return {
                "receipt": {
                    "backend": "harbor",
                    "task_id": "verification-00",
                    "harness": "codex",
                    "subject": subject,
                    "model": "provider/model",
                    "replicate_id": 6201,
                    "status": status,
                    "execution": {
                        "campaign_id": "campaign",
                        "mcp_treatment": treatments[subject],
                        "ablation": contract,
                    },
                },
                "trace": {
                    "available": True,
                    "tool_order_complete": True,
                    "subject_tools": tools,
                    "subject_call_selectors": selectors,
                },
                "answer": None,
            }

        result = quartet_projection(
            {
                "bare": projection("none", "FAIL"),
                "full": projection(
                    "hashmarks",
                    "PASS",
                    "verification-explanation",
                ),
                "remove": projection(
                    "hashmarks-no-verification-explanation",
                    "FAIL",
                ),
                "only": projection(
                    "hashmarks-verification-explanation-only",
                    "PASS",
                    "verification-explanation",
                ),
            },
            contract=contract,
        )

        self.assertTrue(result["treatment_authority_valid"])
        self.assertEqual(
            result["selector"],
            {
                "argument": "surface_name",
                "value": "verification-explanation",
            },
        )
        self.assertEqual(
            result["component_invoked"],
            {"full": True, "only": True},
        )
        self.assertEqual(
            result["classification"],
            "NECESSARY_AND_SUFFICIENT_CONTRAST",
        )

        wrong_full = quartet_projection(
            {
                "bare": projection("none", "FAIL"),
                "full": projection("hashmarks", "PASS", "freshness"),
                "remove": projection(
                    "hashmarks-no-verification-explanation",
                    "FAIL",
                ),
                "only": projection(
                    "hashmarks-verification-explanation-only",
                    "PASS",
                    "verification-explanation",
                ),
            },
            contract=contract,
        )
        self.assertTrue(wrong_full["treatment_authority_valid"])
        self.assertFalse(wrong_full["component_invoked"]["full"])
        self.assertEqual(
            wrong_full["necessity"],
            "UNATTRIBUTABLE_FULL_NEVER_INVOKED_COMPONENT",
        )

    def test_selector_only_arm_calling_withheld_surface_is_unqualified(
        self,
    ) -> None:
        contract = {
            "component": "repository_intelligence_query",
            "selector": {
                "argument": "surface_name",
                "value": "verification-explanation",
            },
            "arms": {
                "bare": "none",
                "full": "hashmarks",
                "remove": "hashmarks-no-verification-explanation",
                "only": "hashmarks-verification-explanation-only",
            },
        }
        full_tools = [*FULL_TOOLS, "repository_intelligence_query"]
        treatments = {
            "none": None,
            "hashmarks": {
                "tools": full_tools,
                "repository_intelligence_query_surfaces": [
                    "verification-explanation",
                    "freshness",
                ],
                "projection_identity": "sha256:full",
                "source_contract_identity": "sha256:contract",
                "full_contract": True,
            },
            "hashmarks-no-verification-explanation": {
                "tools": full_tools,
                "repository_intelligence_query_surfaces": ["freshness"],
                "projection_identity": "sha256:without",
                "source_contract_identity": "sha256:contract",
                "full_contract": False,
            },
            "hashmarks-verification-explanation-only": {
                "tools": ["repository_intelligence_query"],
                "repository_intelligence_query_surfaces": [
                    "verification-explanation"
                ],
                "projection_identity": "sha256:only",
                "source_contract_identity": "sha256:contract",
                "full_contract": False,
            },
        }

        def arm(subject: str, surface: str | None) -> dict[str, object]:
            selectors = (
                [
                    {
                        "tool": "mcp__hashmarks__repository_intelligence_query",
                        "surface_name": surface,
                    }
                ]
                if surface is not None
                else []
            )
            return {
                "receipt": {
                    "task_id": "verification-00",
                    "harness": "codex",
                    "subject": subject,
                    "model": "provider/model",
                    "replicate_id": 6201,
                    "status": "PASS",
                    "execution": {
                        "campaign_id": "campaign",
                        "mcp_treatment": treatments[subject],
                    },
                },
                "trace": {
                    "available": True,
                    "tool_order_complete": True,
                    "subject_tools": (
                        ["mcp__hashmarks__repository_intelligence_query"]
                        if surface is not None
                        else []
                    ),
                    "subject_call_selectors": selectors,
                },
            }

        result = quartet_projection(
            {
                "bare": arm("none", None),
                "full": arm("hashmarks", "verification-explanation"),
                "remove": arm(
                    "hashmarks-no-verification-explanation",
                    None,
                ),
                "only": arm(
                    "hashmarks-verification-explanation-only",
                    "freshness",
                ),
            },
            contract=contract,
        )

        self.assertFalse(result["treatment_authority_valid"])
        self.assertIn(
            "UNQUALIFIED_DISALLOWED_CALL",
            str(result["treatment_authority_error"]),
        )

    def test_removal_pass_means_task_evidence_was_not_necessary_in_replicate(
        self,
    ) -> None:
        result = quartet_projection(
            _quartet(removed="PASS")
        )

        self.assertEqual(
            result["necessity"],
            "NOT_NECESSARY_IN_THIS_REPLICATE",
        )
        self.assertEqual(
            result["sufficiency"],
            "SUPPORTED_SUFFICIENCY_CONTRAST",
        )
        self.assertEqual(
            result["classification"],
            "SUFFICIENCY_SIGNAL",
        )

    def test_malformed_removal_arm_fails_treatment_authority(self) -> None:
        arms = _quartet()
        receipt = arms["hashmarks-no-task-evidence"]["receipt"]
        receipt["execution"]["mcp_treatment"]["tools"] = ["find"]

        result = quartet_projection(arms)

        self.assertFalse(result["treatment_authority_valid"])
        self.assertEqual(
            result["treatment_authority_error"],
            "removal-arm-not-single-tool-ablation",
        )
        self.assertEqual(
            result["necessity"],
            "UNQUALIFIED_TREATMENT_AUTHORITY",
        )
        self.assertEqual(
            result["sufficiency"],
            "UNQUALIFIED_TREATMENT_AUTHORITY",
        )

    def test_frozen_projection_does_not_prove_host_advertised_catalog(self) -> None:
        result = quartet_projection(_quartet())
        self.assertTrue(result["treatment_authority_valid"])
        self.assertFalse(result["observed_catalog_advertisement_proven"])
        for observed in result["observed_call_projection"].values():
            self.assertEqual(observed["status"], "NO_DISALLOWED_CALL_OBSERVED")
            self.assertFalse(observed["catalog_advertisement_proven"])

    def test_removal_arm_invoking_withheld_task_evidence_invalidates_contrast(
        self,
    ) -> None:
        arms = _quartet()
        arms["hashmarks-no-task-evidence"]["trace"]["subject_tools"] = [
            "mcp__hashmarks__task_evidence"
        ]
        result = quartet_projection(arms)
        self.assertFalse(result["treatment_authority_valid"])
        self.assertIn(
            "UNQUALIFIED_DISALLOWED_CALL",
            result["treatment_authority_error"],
        )
        self.assertEqual(
            result["observed_call_projection"]["hashmarks-no-task-evidence"][
                "disallowed_calls"
            ],
            ["mcp__hashmarks__task_evidence"],
        )
        self.assertEqual(
            result["classification"], "UNQUALIFIED_TREATMENT_AUTHORITY"
        )
        self.assertFalse(result["positive_causal_proof_claimed"])

    def test_task_evidence_only_arm_calling_find_invalidates_contrast(self) -> None:
        arms = _quartet()
        arms["hashmarks-task-evidence-only"]["trace"]["subject_tools"] = [
            "mcp__hashmarks__task_evidence",
            "mcp__hashmarks__find",
        ]
        result = quartet_projection(arms)
        self.assertFalse(result["treatment_authority_valid"])
        self.assertIn(
            "hashmarks-task-evidence-only",
            result["treatment_authority_error"],
        )
        self.assertEqual(
            result["sufficiency"], "UNQUALIFIED_TREATMENT_AUTHORITY"
        )

    def test_bare_arm_hashmarks_call_is_not_an_admitted_control(self) -> None:
        arms = _quartet()
        arms["none"]["trace"]["subject_tools"] = ["mcp__hashmarks__find"]
        result = quartet_projection(arms)
        self.assertFalse(result["treatment_authority_valid"])
        self.assertIn("none", result["treatment_authority_error"])

    def test_unknown_hashmarks_call_is_not_assumed_to_be_allowed(self) -> None:
        arms = _quartet()
        arms["hashmarks"]["trace"]["subject_tools"] = [
            "mcp__hashmarks__mystery",
            "mcp__hashmarks__task_evidence",
        ]
        result = quartet_projection(arms)
        self.assertFalse(result["treatment_authority_valid"])
        self.assertEqual(
            result["observed_call_projection"]["hashmarks"]["status"],
            "UNQUALIFIED_UNRESOLVED_CALL",
        )
        self.assertEqual(
            result["observed_call_projection"]["hashmarks"]["unresolved_calls"],
            ["mcp__hashmarks__mystery"],
        )

    def test_missing_or_partial_trace_cannot_admit_a_positive_ablation(self) -> None:
        for trace in (
            {"available": False},
            {"available": True, "subject_tools": []},
            {"available": True, "tool_order_complete": False, "subject_tools": []},
        ):
            arms = _quartet()
            arms["hashmarks-no-task-evidence"]["trace"] = trace
            result = quartet_projection(arms)
            self.assertFalse(result["treatment_authority_valid"])
            self.assertEqual(
                result["observed_call_projection"][
                    "hashmarks-no-task-evidence"
                ]["status"],
                "UNQUALIFIED_TRACE_INCOMPLETE",
            )

    def test_partially_parsed_atif_cannot_qualify_component_ablation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trajectory.json"
            path.write_text(json.dumps({
                "schema_version": "ATIF-v1.8",
                "steps": [{
                    "source": "agent",
                    "tool_calls": [
                        {"function_name": "mcp__hashmarks__find"},
                        {"function_name": None},
                    ],
                }],
            }), encoding="utf-8")
            partial = project_atif(path)
        self.assertFalse(partial["tool_order_complete"])
        self.assertEqual(partial["subject_tools"], ["mcp__hashmarks__find"])

        quartet = _quartet()
        quartet["hashmarks-no-task-evidence"]["trace"] = partial
        result = quartet_projection(quartet)
        self.assertFalse(result["treatment_authority_valid"])
        self.assertIn(
            "UNQUALIFIED_TRACE_INCOMPLETE", result["treatment_authority_error"]
        )
        self.assertEqual(result["classification"], "UNQUALIFIED_TREATMENT_AUTHORITY")
        self.assertFalse(result["positive_causal_proof_claimed"])
        report = self._report_from_quartets([quartet])
        self.assertEqual(report["summary"]["qualified_complete_quartets"], 0)
        self.assertIsNone(
            report["by_harness"]["codex"]["contrasts"]["removal_drop"]
        )

    def test_aggregate_uses_only_complete_treatment_qualified_quartets(
        self,
    ) -> None:
        valid = _quartet()
        bad_tools = _quartet(
            bare="PASS",
            full="FAIL",
            removed="PASS",
            only="FAIL",
        )
        bad_tools["hashmarks-no-task-evidence"]["trace"]["subject_tools"] = [
            "mcp__hashmarks__task_evidence"
        ]
        incomplete = _quartet(
            bare="PASS",
            full="FAIL",
            removed="PASS",
            only="FAIL",
        )
        incomplete["hashmarks-task-evidence-only"]["receipt"][
            "status"
        ] = "INCOMPLETE"

        report = self._report_from_quartets(
            [valid, bad_tools, incomplete]
        )

        summary = report["summary"]
        self.assertEqual(summary["matched_quartets"], 3)
        self.assertEqual(summary["qualified_complete_quartets"], 1)
        self.assertEqual(summary["treatment_unqualified_quartets"], 1)
        self.assertEqual(summary["incomplete_outcome_quartets"], 1)
        self.assertEqual(summary["excluded_matched_quartets"], 2)
        by_harness = report["by_harness"]["codex"]
        self.assertEqual(by_harness["matched_quartets"], 3)
        self.assertEqual(by_harness["qualified_complete_quartets"], 1)
        self.assertEqual(
            by_harness["success_rate"],
            {
                "bare": 0.0,
                "full": 1.0,
                "remove": 0.0,
                "only": 1.0,
            },
        )
        self.assertEqual(
            by_harness["contrasts"]["removal_drop"],
            1.0,
        )
        self.assertEqual(
            by_harness["contrasts"]["only_uplift_vs_bare"],
            1.0,
        )

    @staticmethod
    def _semantic_component(
        *, alignment: str = "ALIGNED_ONLY", timing: str = "BEFORE_NATIVE_DISCOVERY",
    ) -> dict[str, object]:
        return {
            "qualified": True,
            "reason": None,
            "claim_alignment": alignment,
            "arrival_timing": timing,
            "final_answer_overlap": "ALIGNED_REPEATED",
            "aligned_fields": ["winner"],
            "divergent_fields": [],
            "conflicted_fields": [],
            "aligned_repeated_in_final": ["winner"],
            "divergent_repeated_in_final": [],
            "aligned_claim_arrival": timing,
            "divergent_claim_arrival": "NOT_OBSERVED",
            "claim_arrival_order": "ALIGNED_ONLY",
            "causal_influence_claimed": False,
        }

    def test_ablation_semantic_diagnostics_require_exact_invocation_and_oracle(self) -> None:
        arms = _quartet()
        arms["hashmarks"]["component_semantic_information"] = self._semantic_component()
        arms["hashmarks-task-evidence-only"][
            "component_semantic_information"
        ] = self._semantic_component(timing="AFTER_NATIVE_DISCOVERY")
        row = quartet_projection(arms)
        semantic = row["semantic_component_evidence"]
        self.assertTrue(semantic["qualified"])
        self.assertIsNone(semantic["reason"])
        self.assertEqual(semantic["full"]["claim_alignment"], "ALIGNED_ONLY")
        self.assertEqual(semantic["only"]["arrival_timing"], "AFTER_NATIVE_DISCOVERY")
        self.assertFalse(semantic["positive_causal_proof_claimed"])

        no_invocation = _quartet(full_invoked=False)
        no_invocation["hashmarks"]["component_semantic_information"] = self._semantic_component()
        no_invocation["hashmarks-task-evidence-only"][
            "component_semantic_information"
        ] = self._semantic_component()
        rejected = quartet_projection(no_invocation)["semantic_component_evidence"]
        self.assertFalse(rejected["qualified"])
        self.assertEqual(rejected["reason"], "full-component-not-invoked-or-unknown")

    def test_component_semantic_denominator_does_not_change_ordinary_ablation(self) -> None:
        valid = _quartet()
        valid["hashmarks"]["component_semantic_information"] = self._semantic_component()
        valid["hashmarks-task-evidence-only"][
            "component_semantic_information"
        ] = self._semantic_component(timing="AFTER_NATIVE_DISCOVERY")

        absent = _quartet()
        absent["hashmarks"]["component_semantic_information"] = self._semantic_component()
        absent["hashmarks-task-evidence-only"][
            "component_semantic_information"
        ] = {"qualified": False, "reason": "unstructured-subject-observation"}

        invalid = _quartet()
        invalid["hashmarks-no-task-evidence"]["trace"]["subject_tools"] = [
            "mcp__hashmarks__task_evidence"
        ]

        report = self._report_from_quartets([valid, absent, invalid])
        summary = report["summary"]
        self.assertEqual(summary["matched_quartets"], 3)
        self.assertEqual(summary["qualified_complete_quartets"], 2)
        self.assertEqual(summary["semantic_qualified_quartets"], 1)
        self.assertEqual(
            summary["semantic_exclusion_reasons"],
            {
                "only:unstructured-subject-observation": 1,
                "treatment-unqualified": 1,
            },
        )
        expected = (
            "NECESSARY_AND_SUFFICIENT_CONTRAST|PASS|PASS|"
            "ALIGNED_ONLY|BEFORE_NATIVE_DISCOVERY|ALIGNED_REPEATED|"
            "ALIGNED_ONLY|AFTER_NATIVE_DISCOVERY|ALIGNED_REPEATED"
        )
        self.assertEqual(summary["semantic_outcome_cross_tab"], {expected: 1})
        self.assertEqual(
            report["by_harness"]["codex"]["semantic_qualified_quartets"], 1
        )
        self.assertEqual(
            report["by_harness"]["codex"]["qualified_complete_quartets"], 2
        )
        self.assertFalse(summary["positive_causal_proof_claimed"])

    def test_ablation_report_bundles_semantic_information_outcomes_without_regrading(self) -> None:
        valid = _quartet()
        valid["hashmarks"]["component_semantic_information"] = self._semantic_component(
            timing="AFTER_NATIVE_DISCOVERY"
        )
        valid["hashmarks-task-evidence-only"][
            "component_semantic_information"
        ] = self._semantic_component(timing="BEFORE_NATIVE_DISCOVERY")
        report = self._report_from_quartets([valid])
        self.assertEqual(report["summary"]["semantic_qualified_quartets"], 1)
        diagnostic = report["quartets"][0]["semantic_information_outcomes"]
        self.assertTrue(diagnostic["qualified"])
        self.assertEqual([row["outcome_transition"] for row in diagnostic["comparisons"]], [
            "FAIL_TO_PASS", "FAIL_TO_PASS",
        ])
        self.assertEqual(
            diagnostic["comparisons"][0]["exposure"]["aligned_claim_arrival"],
            "AFTER_NATIVE_DISCOVERY",
        )
        self.assertEqual(
            diagnostic["comparisons"][1]["exposure"]["aligned_claim_arrival"],
            "BEFORE_NATIVE_DISCOVERY",
        )
        summary = report["summary"]["semantic_information_outcomes"]
        self.assertEqual(summary["qualified_quartets"], 1)
        self.assertEqual(summary["excluded_quartets"], 0)
        self.assertEqual(len(summary["conditional_outcome_cells"]), 2)
        self.assertFalse(summary["causal_influence_claimed"])
        self.assertEqual(report["by_harness"]["codex"]["qualified_complete_quartets"], 1)

    def test_incomplete_outcomes_excluded_even_with_valid_information(self) -> None:
        arms = _quartet(only="INCOMPLETE")
        arms["hashmarks"]["component_semantic_information"] = self._semantic_component()
        arms["hashmarks-task-evidence-only"][
            "component_semantic_information"
        ] = self._semantic_component()
        row = quartet_projection(arms)
        self.assertFalse(row["semantic_component_evidence"]["qualified"])
        self.assertEqual(
            row["semantic_component_evidence"]["reason"],
            "incomplete-quartet-outcomes",
        )
        report = self._report_from_quartets([arms])
        self.assertEqual(report["summary"]["semantic_qualified_quartets"], 0)
        self.assertEqual(report["summary"]["semantic_outcome_cross_tab"], {})

    def test_no_qualified_complete_quartet_means_unknown_aggregate(self) -> None:
        bad_tools = _quartet()
        bad_tools["hashmarks-task-evidence-only"]["trace"]["subject_tools"] = [
            "mcp__hashmarks__find"
        ]

        report = self._report_from_quartets([bad_tools])

        summary = report["summary"]
        self.assertEqual(summary["matched_quartets"], 1)
        self.assertEqual(summary["qualified_complete_quartets"], 0)
        self.assertEqual(summary["excluded_matched_quartets"], 1)
        by_harness = report["by_harness"]["codex"]
        self.assertEqual(by_harness["qualified_complete_quartets"], 0)
        self.assertTrue(
            all(
                value is None
                for value in by_harness["success_rate"].values()
            )
        )
        self.assertIsNone(
            by_harness["contrasts"]["full_uplift_vs_bare"]
        )
        self.assertIsNone(by_harness["contrasts"]["removal_drop"])
        self.assertIsNone(
            by_harness["contrasts"]["only_uplift_vs_bare"]
        )

    def test_incomplete_or_unmatched_groups_do_not_enter_aggregate(self) -> None:
        partial = _quartet()
        partial.pop("hashmarks-task-evidence-only")

        report = self._report_from_quartets([partial])

        self.assertEqual(report["summary"]["incomplete_groups"], 1)
        self.assertEqual(
            report["summary"]["qualified_complete_quartets"],
            0,
        )
        self.assertEqual(report["by_harness"], {})

    def test_ordinary_two_arm_results_are_not_an_ablation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = build_ablation_report(Path(tmp))

        self.assertFalse(report["applicable"])
        self.assertEqual(report["summary"]["matched_quartets"], 0)


if __name__ == "__main__":
    unittest.main()
