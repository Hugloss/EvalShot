"""Full immutable-bundle integration for Harbor semantic evidence summaries."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.bundle_writer import publish_bundle
from benchmarks.harness.identity import canonical_json, digest
from benchmarks.harness.mechanism_attribution import (
    build_mechanism_report,
    load_harbor_bundle_projection,
)


def _trajectory(subject: str) -> dict:
    if subject == "none":
        tool = "rg"
        result = "native exploration"
    else:
        tool = "mcp__hashmarks__repository_declarations"
        result = {"winner": "not-selected", "comparison": "equivalent"}
    return {
        "schema_version": "ATIF-v1.8",
        "steps": [
            {
                "source": "agent",
                "tool_calls": [{
                    "tool_call_id": "call-1",
                    "function_name": tool,
                    "arguments": {"task": "compare declarations"},
                }],
                "observation": {"results": [
                    {"source_call_id": "call-1", "content": result},
                ]},
            },
            {
                "source": "agent",
                "tool_calls": [{
                    "tool_call_id": "call-2",
                    "function_name": "rg",
                    "arguments": {"pattern": "declarations"},
                }],
                "observation": {"results": [
                    {"source_call_id": "call-2", "content": "native check"},
                ]},
            },
        ],
    }


def _answer(subject: str) -> dict:
    expected = {"winner": "not-selected", "comparison": "differing"}
    observed = (
        {"winner": "selected", "comparison": "differing"}
        if subject == "none" else dict(expected)
    )
    correct = ["comparison"] if subject == "none" else ["comparison", "winner"]
    return {
        "schema": "agentscookbook.harbor-answer-evidence.v1",
        "observed": observed,
        "expected": expected,
        "semantic_case_id": "declarations-01",
        "match": subject == "hashmarks",
        "error": None,
        "tracked_clean": True,
        "oracle": {
            "schema": "agents-cookbook-lexigram-oracle.v1",
            "rubric": {
                "correct_fields": correct,
                "missing_fields": [],
                "incorrect_fields": ["winner"] if subject == "none" else [],
            },
        },
    }


class SemanticReportIntegrationTests(unittest.TestCase):
    def test_immutable_pair_exposes_qualified_semantic_outcome_cross_tab(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            campaign_id = "semantic-evidence"
            for subject in ("none", "hashmarks"):
                definition_id = f"definition-{subject}"
                trial_id = digest({
                    "campaign_id": campaign_id,
                    "definition_id": definition_id,
                })
                job_name = f"h{trial_id[:24]}-a000001"
                passed = subject == "hashmarks"
                harbor_result = {
                    "schema": "agentscookbook.harbor-harness-trial.v1",
                    "trial_id": job_name,
                    "task": "declarations-01",
                    "harness": "codex",
                    "subject": subject,
                    "attempt": 1,
                    "model": "provider/model",
                    "status": "COMPLETE",
                    "success": passed,
                    "reward": 1.0 if passed else 0.0,
                    "duration_ms": 1,
                    "harbor_return_code": 0,
                    "harbor_job_root": f"/jobs/{job_name}",
                    "reward_path": f"/jobs/{job_name}/reward.txt",
                    "mcp_exposed": subject == "hashmarks",
                    "stderr_tail": "",
                }
                receipt = {
                    "backend": "harbor",
                    "definition_id": definition_id,
                    "trial_id": trial_id,
                    "task_id": "declarations-01",
                    "condition_id": f"{subject}-codex",
                    "trial": 0,
                    "replicate_id": 1,
                    "harness": "codex",
                    "subject": subject,
                    "model": "provider/model",
                    "status": "PASS" if passed else "FAIL",
                    "harbor": harbor_result,
                    "execution": {
                        "campaign_id": campaign_id,
                        "launch_attempt": 1,
                        "job_name": job_name,
                    },
                }
                publish_bundle(
                    results_root=root,
                    trial_id=trial_id,
                    artifacts={
                        "harbor_result": ("harbor-result.json", canonical_json(harbor_result)),
                        "reward": ("reward.txt", b"1\n" if passed else b"0\n"),
                        "trajectory": ("trajectory.json", canonical_json(_trajectory(subject))),
                        "answer": ("answer.json", canonical_json(_answer(subject))),
                    },
                    receipt=receipt,
                )

            report = build_mechanism_report(root)

        self.assertEqual(report["summary"]["paired_observations"], 1)
        self.assertEqual(report["summary"]["semantic_qualified_pairs"], 1)
        self.assertEqual(
            report["summary"]["semantic_outcome_cross_tab"],
            {
                "FAIL_TO_PASS|BEFORE_NATIVE_DISCOVERY|MIXED_OR_CONFLICTING|ALIGNED_REPEATED": 1,
            },
        )
        self.assertEqual(report["summary"]["semantic_exclusion_reasons"], {})
        pair = report["pairs"][0]
        self.assertEqual(pair["semantic_information_evidence"]["aligned_fields"], ["winner"])
        self.assertEqual(
            pair["semantic_information_evidence"]["divergent_fields"],
            ["comparison"],
        )
        self.assertFalse(pair["positive_causal_proof_claimed"])
        self.assertNotIn('"not-selected"', json.dumps(report["pairs"]))


    def test_frozen_answer_for_another_task_denies_semantic_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial_id = digest({"campaign_id": "case-binding", "definition_id": "unmatched-task"})
            subject = "hashmarks"
            job_name = f"h{trial_id[:24]}-a000001"
            harbor_result = {
                "schema": "agentscookbook.harbor-harness-trial.v1",
                "trial_id": job_name,
                "task": "declarations-02",
                "harness": "codex",
                "subject": subject,
                "attempt": 1,
                "model": "provider/model",
                "status": "COMPLETE",
                "success": True,
                "reward": 1.0,
                "duration_ms": 1,
                "harbor_return_code": 0,
                "harbor_job_root": f"/jobs/{job_name}",
                "reward_path": f"/jobs/{job_name}/reward.txt",
                "mcp_exposed": True,
                "stderr_tail": "",
            }
            receipt = {
                "backend": "harbor",
                "definition_id": "unmatched-task",
                "trial_id": trial_id,
                "task_id": "declarations-02",
                "condition_id": "hashmarks-codex",
                "trial": 0,
                "subject": subject,
                "model": "provider/model",
                "status": "PASS",
                "harness": "codex",
                "replicate_id": 1,
                "harbor": harbor_result,
                "execution": {
                    "campaign_id": "case-binding",
                    "launch_attempt": 1,
                    "job_name": job_name,
                },
            }
            directory = publish_bundle(
                results_root=root,
                trial_id=trial_id,
                artifacts={
                    "harbor_result": ("harbor-result.json", canonical_json(harbor_result)),
                    "reward": ("reward.txt", b"1\n"),
                    "trajectory": ("trajectory.json", canonical_json(_trajectory(subject))),
                    "answer": ("answer.json", canonical_json(_answer(subject))),
                },
                receipt=receipt,
            )
            projection = load_harbor_bundle_projection(directory)

        self.assertFalse(projection["semantic_information"]["qualified"])
        self.assertEqual(
            projection["semantic_information"]["reason"],
            "semantic-case-identity-mismatch-or-missing",
        )


if __name__ == "__main__":
    unittest.main()
