"""Unapproved known-symbol baseline must fail before model work."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.harness.identity import digest
from benchmarks.harness.suite import (
    SuiteDefinition, SuiteError, _validate_definition, load_runtime_suite,
    load_suite,
)


BENCHMARKS = Path(__file__).resolve().parents[1]
STAGED = BENCHMARKS / "dogfood/staged-suites/known-symbol-native-baseline-v1"
HISTORICAL = BENCHMARKS / "suites/repository-intelligence/heldout-v1"
PILOT = BENCHMARKS / "suites/repository-intelligence/native-evidence-pilot-v1"
ACTIVE_ROOT = BENCHMARKS / "suites/repository-intelligence"
TASKS = (
    "lookup-known-symbol-paths-under",
    "lookup-known-symbol-sync-remove-stale-paths",
)
SUBJECTS = ("none", "hashmarks")
IDS = (9901, 9902, 9903)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


class StagedKnownSymbolBaselineTests(unittest.TestCase):
    def test_protocol_explicitly_forbids_model_work_until_review(self):
        protocol = _json(STAGED / "protocol.json")
        self.assertEqual(
            protocol["state"], "BLOCKED_PENDING_INDEPENDENT_ORACLE_REVIEW"
        )
        self.assertIs(protocol["require_review_before_any_model_work"], True)
        self.assertIs(protocol["historical_trials_reused"], False)
        self.assertIs(protocol["historical_pilot_2026_10_21_qualified"], False)
        self.assertIn("INDEPENDENT_PINNED_SOURCE_ORACLE_REVIEW",
                      protocol["remaining_gates"])
        self.assertIn("CANONICAL_ORACLE_REVIEW_VALIDATION",
                      protocol["remaining_gates"])
        self.assertIn("ANY_CANDIDATE_READY", protocol["rejected_claims"])

    def test_exact_original_task_bytes_and_git_blob_identities(self):
        manifest = _json(STAGED / "protocol.json")
        self.assertEqual(set(manifest["task_transfers"]), set(TASKS))
        for task_id in TASKS:
            source = HISTORICAL / "tasks" / (task_id + ".json")
            staged = STAGED / "tasks" / (task_id + ".json")
            with self.subTest(task_id=task_id):
                self.assertEqual(staged.read_bytes(), source.read_bytes())
                self.assertEqual(
                    _git_blob_sha1(staged.read_bytes()),
                    manifest["task_transfers"][task_id]["git_blob_sha1"],
                )
                self.assertEqual(
                    manifest["task_transfers"][task_id]["from"],
                    source.relative_to(BENCHMARKS.parent).as_posix(),
                )
                task = _json(staged)
                _validate_definition(task, schema_name="task", source=staged)
                self.assertEqual(task["id"], task_id)
                self.assertEqual(task["repository"]["commit"],
                                 manifest["source"]["commit"])
                self.assertEqual(task["repository"]["tree"],
                                 manifest["source"]["tree"])
                self.assertEqual(task["mode"], "read_only")
                self.assertEqual(task["mutation"], None)

    def test_participants_are_byte_identical_to_native_pilot(self):
        transfers = _json(STAGED / "protocol.json")["participant_transfers"]
        self.assertEqual(set(transfers), {
            "agents/codex-native.json",
            "subjects/none.json",
            "subjects/hashmarks.json",
        })
        for target, details in transfers.items():
            with self.subTest(file=target):
                original = BENCHMARKS.parent / details["from"]
                copy = STAGED / target
                self.assertEqual(copy.read_bytes(), original.read_bytes())
                self.assertEqual(_git_blob_sha1(copy.read_bytes()),
                                 details["git_blob_sha1"])

    def test_experiment_runtime_definitions_use_existing_loader(self):
        loaded = load_runtime_suite(STAGED)
        self.assertEqual(set(loaded.subjects), set(SUBJECTS))
        self.assertEqual(set(loaded.agents), {"codex-native"})
        self.assertEqual(loaded.experiment["tasks"], list(TASKS))
        self.assertEqual(loaded.experiment["oracle_reviews"],
                         "qualification/oracle-reviews.json")

    def test_two_native_arms_have_identical_agent_and_replicate_ids(self):
        loaded = load_runtime_suite(STAGED)
        conditions = loaded.experiment["conditions"]
        self.assertEqual({c["id"] for c in conditions},
                         {"none-codex-native", "hashmarks-codex-native"})
        self.assertEqual({c["agent"] for c in conditions}, {"codex-native"})
        self.assertEqual({c["subject"] for c in conditions}, set(SUBJECTS))
        for condition in conditions:
            self.assertEqual(condition["trials"], 3)
            self.assertEqual(condition["replicate_ids"], list(IDS))
            self.assertNotIn("context", condition)
        self.assertEqual(loaded.experiment["subject_exposure_contract"],
                         {"require_probe_contract": True})

    def test_twelve_complete_counterbalanced_assignment_slots(self):
        runtime = load_runtime_suite(STAGED)
        full = SuiteDefinition(
            root=runtime.root,
            experiment=runtime.experiment,
            tasks={task: _json(STAGED / "tasks" / (task + ".json"))
                   for task in TASKS},
            agents=runtime.agents,
            subjects=runtime.subjects,
        )
        trials = full.trial_definitions()
        self.assertEqual(len(trials), 12)
        self.assertEqual(len({t["definition_id"] for t in trials}), 12)
        self.assertEqual(Counter(t["condition_id"] for t in trials),
                         {"none-codex-native": 6, "hashmarks-codex-native": 6})
        blocks = [trials[i:i + 2] for i in range(0, len(trials), 2)]
        self.assertEqual(len(blocks), 6)
        for block in blocks:
            self.assertEqual(len({(t["task_id"], t["replicate_id"])
                                 for t in block}), 1)
            self.assertEqual({t["condition_id"] for t in block},
                             {"none-codex-native", "hashmarks-codex-native"})
        self.assertEqual(
            Counter(block[0]["condition_id"] for block in blocks),
            {"none-codex-native": 3, "hashmarks-codex-native": 3},
        )
        self.assertEqual(
            Counter((t["task_id"], t["replicate_id"]) for t in trials),
            {(task, replicate): 2 for task in TASKS for replicate in IDS},
        )

    def test_native_inference_settings_not_owned_by_benchmark(self):
        runtime = load_runtime_suite(STAGED)
        configuration = runtime.agents["codex-native"]["configuration"]
        self.assertIs(configuration["native_host"], True)
        self.assertEqual(configuration["controlled_subject"], "hashmarks")
        self.assertFalse(set(configuration).intersection({
            "model", "provider", "temperature", "top_p", "max_tokens",
            "reasoning_effort", "model_context_window",
        }))
        self.assertEqual(runtime.subjects["none"]["kind"], "control")
        self.assertEqual(runtime.subjects["hashmarks"]["exposure_probe"],
                         {"required_tool": "hashmarks_task_evidence"})
        self.assertFalse(
            set(runtime.experiment["conditions"][0]).intersection({
                "model", "provider", "reasoning_effort", "temperature"
            })
        )

    def test_independent_review_file_is_intentionally_absent_and_loader_rejects(self):
        self.assertFalse((STAGED / "qualification/oracle-reviews.json").exists())
        with self.assertRaisesRegex(SuiteError, "oracle review evidence is missing"):
            load_suite(STAGED)

    def test_partial_or_forged_reviews_do_not_unlock_runtime(self):
        # The synthetic temp fixture tests refusal only; it does not create
        # actual independent approvals or sign a reviewer record.
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "suite"
            destination.mkdir()
            for name in ("experiment.json",):
                (destination / name).write_bytes((STAGED / name).read_bytes())
            for directory in ("tasks", "agents", "subjects", "qualification"):
                (destination / directory).mkdir()
            for folder in ("tasks", "agents", "subjects"):
                for source in (STAGED / folder).glob("*.json"):
                    (destination / folder / source.name).write_bytes(
                        source.read_bytes()
                    )
            (destination / "qualification/oracle-reviews.json").write_text(
                json.dumps({"schema": "agents-cookbook-oracle-reviews.v1", "tasks": {}})
            )
            with self.assertRaisesRegex(SuiteError, "oracle review missing"):
                load_suite(destination)

    def test_staging_does_not_expand_active_suite_inventory(self):
        self.assertFalse(STAGED.is_relative_to(ACTIVE_ROOT))
        active = {str(p) for p in ACTIVE_ROOT.glob("*/tasks/*.json")}
        for task in TASKS:
            self.assertNotIn(str(STAGED / "tasks" / f"{task}.json"), active)
        self.assertEqual(set(_json(HISTORICAL / "experiment.json")["tasks"])
                         .intersection(TASKS), set())

    def test_excludes_bad_source_candidate_and_prior_results(self):
        task_names = {p.stem for p in (STAGED / "tasks").glob("*.json")}
        self.assertEqual(task_names, set(TASKS))
        self.assertNotIn("lookup-known-symbol-run-poll-delay", task_names)
        self.assertFalse((STAGED / "results").exists())
        self.assertFalse((STAGED / "score.json").exists())
        self.assertFalse((STAGED / "score.py").exists())
        protocol = _json(STAGED / "protocol.json")
        self.assertIs(protocol["controls"]["primary_effectiveness_claim_allowed"],
                      False)

    def test_analysis_contract_does_not_claim_statistical_power(self):
        exp = load_runtime_suite(STAGED).experiment
        study = exp["analysis_contract"]
        self.assertEqual(study["minimum_pairs"], 6)
        self.assertEqual(study["minimum_replicates"], 3)
        self.assertEqual(study["below_minimum"], "descriptive-only")
        self.assertIs(study["cross_agent_ranking"], False)
        protocol = _json(STAGED / "protocol.json")
        self.assertIs(protocol["controls"]["model_sampling_seed_proven"], False)
        self.assertIs(protocol["controls"]["random_assignment_proven"], False)
        self.assertIs(
            protocol["controls"]["actual_launch_order_independently_attested"], False
        )


if __name__ == "__main__":
    unittest.main()
