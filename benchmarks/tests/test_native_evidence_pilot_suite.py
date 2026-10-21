"""Frozen pilot population, transferred oracle authority and diagnostic separation."""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from benchmarks.harness.identity import digest
from benchmarks.harness.oracle_reviews import validate_oracle_reviews
from benchmarks.harness.suite import load_suite


BASE = Path(__file__).resolve().parents[1] / "suites/repository-intelligence"
PILOT = BASE / "native-evidence-pilot-v1"
SOURCE = BASE / "heldout-v1"
TASKS = {"locate-prefix-path-enumerator", "locate-refresh-outcome", "repair-partial-receipt-regression"}
CONDITIONS = {"none-codex-native", "hashmarks-codex-native", "hashmarks-codex-required"}
spec = importlib.util.spec_from_file_location("native_evidence_pilot_score", PILOT / "score.py")
pilot_score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot_score)


class NativeEvidencePilotSuiteTests(unittest.TestCase):
    def test_inherited_tasks_mutation_and_reviews_keep_exact_source_authority(self):
        suite = load_suite(PILOT)
        protocol = json.loads((PILOT / "protocol.json").read_text(encoding="utf-8"))
        original_reviews = json.loads((SOURCE / "qualification/oracle-reviews.json").read_text(encoding="utf-8"))["tasks"]
        reviews = json.loads((PILOT / "qualification/oracle-reviews.json").read_text(encoding="utf-8"))["tasks"]
        self.assertEqual(set(suite.tasks), TASKS)
        for task_id in TASKS:
            with self.subTest(task=task_id):
                task_bytes = (SOURCE / "tasks" / f"{task_id}.json").read_bytes()
                self.assertEqual((PILOT / "tasks" / f"{task_id}.json").read_bytes(), task_bytes)
                self.assertEqual(reviews[task_id], original_reviews[task_id])
                self.assertEqual(protocol["task_sources"][task_id]["task_file_sha256"], hashlib.sha256(task_bytes).hexdigest())
                self.assertEqual(protocol["task_sources"][task_id]["task_digest"], digest(suite.tasks[task_id]))
                mutation = suite.tasks[task_id].get("mutation")
                if mutation:
                    self.assertEqual((PILOT / mutation["artifact"]).read_bytes(), (SOURCE / mutation["artifact"]).read_bytes())
                    self.assertEqual(hashlib.sha256((PILOT / mutation["artifact"]).read_bytes()).hexdigest(), mutation["sha256"])
        result = validate_oracle_reviews(suite, require_complete=True)
        self.assertTrue(result["complete"])
        self.assertEqual(result["approved_tasks"], 3)

    def test_eighteen_assignments_use_each_arm_permutation_once_without_rng_claim(self):
        suite = load_suite(PILOT)
        rows = suite.trial_definitions()
        self.assertEqual(len(rows), 18)
        self.assertEqual(len({row["definition_id"] for row in rows}), 18)
        self.assertEqual(Counter(row["condition_id"] for row in rows), {c: 6 for c in CONDITIONS})
        blocks = [rows[i:i + 3] for i in range(0, 18, 3)]
        for block in blocks:
            self.assertEqual(len({(r["task_id"], r["replicate_id"]) for r in block}), 1)
        permutations = [tuple(row["condition_id"] for row in block) for block in blocks]
        self.assertEqual(set(permutations), set(itertools.permutations(CONDITIONS)))
        for ordinal in range(3):
            self.assertEqual(Counter(block[ordinal]["condition_id"] for block in blocks), {c: 2 for c in CONDITIONS})
        protocol = json.loads((PILOT / "protocol.json").read_text())
        self.assertFalse(protocol["replicate_ids_are_model_rng_seeds"])
        self.assertEqual(protocol["assignments"], len(rows))

    def test_only_C_adds_required_use_and_no_arm_overrides_native_inference(self):
        suite = load_suite(PILOT)
        conditions = {c["id"]: c for c in suite.experiment["conditions"]}
        self.assertEqual(conditions["none-codex-native"]["agent"], conditions["hashmarks-codex-native"]["agent"])
        self.assertEqual(conditions["hashmarks-codex-required"]["agent"], "codex-required")
        for name, agent in suite.agents.items():
            config = agent["configuration"]
            self.assertTrue(config["native_host"])
            self.assertFalse(set(config) & {"model", "provider", "reasoning_effort", "temperature", "max_tokens"})
            self.assertEqual(config.get("diagnostic_required_tool"), "hashmarks_task_evidence" if name == "codex-required" else None)

    def test_score_rejects_posthoc_subset_and_missing_policy_arm(self):
        with self.assertRaisesRegex(ValueError, "both native"):
            pilot_score.score(suite_root=PILOT, results=Path("missing"), agents=("codex-native",))
        with self.assertRaisesRegex(ValueError, "eighteen"):
            pilot_score.score(suite_root=PILOT, results=Path("missing"), agents=("codex-native", "codex-required"), definition_ids=("one-selected-result",))

    def diagnostic_score(self, *, missing=False, configured=True):
        suite = load_suite(PILOT)
        answer = '{"path":"src/owner.py","symbol":"resolve"}'
        raw = (Path(__file__).with_name("fixtures") / "availability-codex.jsonl").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipts = []
            assignments = [r for r in suite.trial_definitions() if r["condition_id"] == "hashmarks-codex-required"]
            for i, assignment in enumerate(assignments):
                if missing and i == 0:
                    continue
                trial = f"native-{i}"
                (root / trial).mkdir()
                (root / trial / "trace.jsonl").write_text(raw, encoding="utf-8")
                receipts.append({
                    "definition_id": assignment["definition_id"], "trial_id": trial, "status": "PASS",
                    "authority": {"subject": {"available": True}},
                    "condition": suite.expanded_condition(next(c for c in suite.experiment["conditions"] if c["id"] == assignment["condition_id"])),
                    "execution": {"agent_answer": answer, "agent_terminal": {"type": "turn.completed"}, "artifacts": {"agent_trace": {"path": "trace.jsonl"}}},
                    "scoring": {"oracle_grade": {"valid": True, "passed": True}},
                    "measurements": {"agent": {"subject_tool_invoked": False, "subject_tool_configured": configured}},
                })
            # Publication, oracle evaluation and the A/B study have their own
            # integration tests; this isolates the new score's C population.
            report = {"observed_trials": 17 if missing else 18, "status_counts": {"PASS": 17 if missing else 18},
                      "conditions": {}, "diagnostics": [], "campaign_qualification": {"evidence_tainted": False, "status": "NOT_QUALIFIED"}}
            availability = {"study_measurement_qualified": True, "primary_availability_intention_to_treat": {"expected_pairs": 6}}
            with patch.object(pilot_score, "build_report", return_value=report), \
                 patch.object(pilot_score, "build_availability_study", return_value=availability), \
                 patch.object(pilot_score, "_receipts", return_value=receipts):
                return pilot_score.score(suite_root=PILOT, results=root, agents=("codex-native", "codex-required"))

    def test_C_noncompliance_is_a_measured_diagnostic_not_voluntary_adoption(self):
        result = self.diagnostic_score()
        self.assertTrue(result["pilot_measurement_qualified"])
        self.assertEqual(result["availability_study"]["primary_availability_intention_to_treat"]["expected_pairs"], 6)
        diagnostic = result["instructed_policy_diagnostic"]
        self.assertEqual(diagnostic["assigned_trials"], 6)
        self.assertTrue(all(row["subject_invoked"] is False for row in diagnostic["trials"]))
        self.assertEqual(diagnostic["isolated_evidence_information_effect"], "NOT_ESTABLISHED")
        self.assertEqual(diagnostic["model_request_inclusion"], "UNKNOWN")
        self.assertFalse(result["authority"]["causal_effect_proven"])
        self.assertFalse(result["authority"]["random_assignment_proven"])
        self.assertEqual(result["actual_launch_order_state"], "NOT_INDEPENDENTLY_AUDITED")

    def test_missing_C_keeps_six_assignments_and_unknown_outcome(self):
        result = self.diagnostic_score(missing=True)
        self.assertFalse(result["pilot_measurement_qualified"])
        rows = result["instructed_policy_diagnostic"]["trials"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(sum(row["success"] is None for row in rows), 1)
        self.assertEqual(sum(row["subject_invoked"] is None for row in rows), 1)

    def test_C_without_proven_configuration_cannot_be_called_instruction_noncompliance(self):
        result = self.diagnostic_score(configured=False)
        self.assertFalse(result["pilot_measurement_qualified"])
        self.assertTrue(all("subject-configuration-unproven" in row["issues"] for row in result["instructed_policy_diagnostic"]["trials"]))


if __name__ == "__main__":
    unittest.main()
