"""Blinded first-pass packets must not leak owner truth or grant admission."""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_blind_review import (
    ACTIVE_RELATIVE, PARTICIPANT_SOURCE, PARTICIPANTS, SCHEMA,
    SOURCE_TASK_ROOT, STAGED_RELATIVE, TASKS,
    BlindReviewError, main, prepare_blind_review,
)
from benchmarks.harness.identity import digest
from benchmarks.harness.suite import SuiteError, load_suite

ROOT = Path(__file__).resolve().parents[2]


def _copy(relative: Path, destination: Path) -> None:
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / relative, target)


def _blob(raw: bytes) -> str:
    return subprocess.run(["git", "hash-object", "--stdin"],
                          input=raw, capture_output=True,
                          check=True).stdout.decode("ascii").strip()


class BlindReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stage = self.root / STAGED_RELATIVE
        shutil.copytree(ROOT / STAGED_RELATIVE, self.stage)
        for task_id in TASKS:
            _copy(SOURCE_TASK_ROOT / f"{task_id}.json", self.root)
        for relative in PARTICIPANTS:
            _copy(PARTICIPANT_SOURCE / relative, self.root)

    def render(self):
        return prepare_blind_review(staged=self.stage, repository_root=self.root)

    def protocol(self, mutate):
        target = self.stage / "protocol.json"
        data = json.loads(target.read_text())
        mutate(data)
        target.write_text(json.dumps(data))

    def task(self, task_id, mutate):
        original = self.root / SOURCE_TASK_ROOT / f"{task_id}.json"
        staged = self.stage / "tasks" / f"{task_id}.json"
        data = json.loads(original.read_text())
        mutate(data)
        raw = (json.dumps(data, indent=2) + "\n").encode()
        original.write_bytes(raw)
        staged.write_bytes(raw)
        self.protocol(
            lambda p: p["task_transfers"][task_id].update(
                {"git_blob_sha1": _blob(raw)}
            )
        )

    def test_allowlist_contains_source_question_not_expected_owner(self):
        result = self.render()
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["qualification"], "SOURCE_BLINDED_QUESTION_ONLY")
        self.assertFalse(result["experiment_admitted"])
        self.assertEqual(result["trial_executions"], 0)
        self.assertEqual(result["independent_oracles_approved"], 0)
        self.assertEqual(result["native_control_headroom"], "UNKNOWN")
        self.assertEqual(len(result["packets"]), 2)
        serialized = json.dumps(result)
        for task_id in TASKS:
            source = json.loads((self.stage / "tasks" / f"{task_id}.json").read_text())
            expected = source["oracle"]["configuration"]["expected"]
            self.assertNotIn(expected["path"], serialized)
            packet = next(x for x in result["packets"] if x["task_id"] == task_id)
            self.assertEqual(packet["frozen_prompt"], source["prompt"])
            self.assertEqual(packet["task_digest"], digest(source))
            self.assertEqual(packet["repository"], source["repository"])
            self.assertEqual(packet["review_status"], "FIRST_PASS_UNREVIEWED")
            self.assertEqual(packet["proposed_owner"], "WITHHELD")
            self.assertEqual(packet["semantic_decision"], "NOT_MADE")
            self.assertNotIn("oracle", packet)
            self.assertNotIn("expected", packet)
            self.assertNotIn("rationale", packet)

    def test_never_leaks_previous_answers_or_review_depth(self):
        raw = json.dumps(self.render())
        for string in (
            "WorkspaceMapStore.paths_under",
            "IndexingLifecycleMixin._sync_remove_stale_paths",
            "minimum_independent_reviews",
            "review_requirement",
            "approved_tasks",
        ):
            self.assertNotIn(string, raw)

    def test_canonical_loader_still_refuses_unreviewed_suite(self):
        self.render()
        with self.assertRaisesRegex(SuiteError, "oracle review evidence is missing"):
            load_suite(self.stage)

    def test_protocol_may_not_change_pre_registered_count(self):
        self.protocol(lambda p: p["controls"].update({"assignments": 14}))
        with self.assertRaisesRegex(BlindReviewError, "frozen native paired trial plan"):
            self.render()

    def test_execution_order_cannot_be_changed_to_all_control_first(self):
        experiment = self.stage / "experiment.json"
        data = json.loads(experiment.read_text())
        for index in range(0, len(data["execution_order"]), 2):
            block = data["execution_order"][index:index + 2]
            block.sort(key=lambda row: row["condition_id"] != "none-codex-native")
            data["execution_order"][index:index + 2] = block
        experiment.write_text(json.dumps(data))
        with self.assertRaisesRegex(BlindReviewError, "canonical frozen assignment"):
            self.render()

    def test_trial_replicate_reassignment_refused_even_if_manifest_is_self_consistent(self):
        experiment = self.stage / "experiment.json"
        data = json.loads(experiment.read_text())
        substitutions = {9901: 9911, 9902: 9912, 9903: 9913}
        for condition in data["conditions"]:
            condition["replicate_ids"] = [
                substitutions[replicate] for replicate in condition["replicate_ids"]
            ]
        for assigned in data["execution_order"]:
            assigned["replicate_id"] = substitutions[assigned["replicate_id"]]
        experiment.write_text(json.dumps(data))
        with self.assertRaisesRegex(BlindReviewError, "frozen native paired trial plan"):
            self.render()

    def test_staged_bytes_changed_are_rejected(self):
        p = self.stage / "tasks" / f"{TASKS[0]}.json"
        p.write_text(p.read_text() + "\n")
        with self.assertRaisesRegex(BlindReviewError, "definition differs"):
            self.render()

    def test_source_task_drift_is_rejected(self):
        p = self.root / SOURCE_TASK_ROOT / f"{TASKS[0]}.json"
        p.write_text(p.read_text() + "\n")
        with self.assertRaisesRegex(BlindReviewError, "definition differs"):
            self.render()

    def test_protocol_blob_identity_is_verified(self):
        self.protocol(
            lambda p: p["task_transfers"][TASKS[0]].update(
                {"git_blob_sha1": "0" * 40}
            )
        )
        with self.assertRaisesRegex(BlindReviewError, "Git object identity"):
            self.render()

    def test_participant_cannot_be_changed_without_review(self):
        p = self.stage / "agents/codex-native.json"
        p.write_text(p.read_text() + "\n")
        with self.assertRaisesRegex(BlindReviewError, "definition differs"):
            self.render()

    def test_protocol_cannot_replace_or_add_task(self):
        self.protocol(lambda p: p["task_transfers"].pop(TASKS[0]))
        with self.assertRaisesRegex(BlindReviewError, "unexpected frozen task"):
            self.render()

    def test_source_review_owner_cannot_be_changed(self):
        self.protocol(
            lambda p: p.update({"canonical_oracle_review_owner": "dogfood.local"})
        )
        with self.assertRaisesRegex(BlindReviewError, "canonical reviewer ownership"):
            self.render()

    def test_fake_review_file_does_not_unlock_first_pass_packet(self):
        p = self.stage / "qualification/oracle-reviews.json"
        p.parent.mkdir(parents=True)
        p.write_text('{"schema":"agents-cookbook-oracle-reviews.v1","tasks":{}}')
        with self.assertRaisesRegex(BlindReviewError, "canonical review owner"):
            self.render()

    def test_prompt_oracle_path_leak_blocks_export_even_if_pins_updated(self):
        task_id = TASKS[0]
        task = json.loads((self.stage / "tasks" / f"{task_id}.json").read_text())
        path = task["oracle"]["configuration"]["expected"]["path"]
        self.task(task_id, lambda t: t.update({"prompt": t["prompt"] + "\n" + path}))
        with self.assertRaisesRegex(BlindReviewError, "exposes its oracle"):
            self.render()

    def test_changed_oracle_expected_path_is_still_not_exported(self):
        hidden = "some/private/expected_owner.py"
        self.task(
            TASKS[0],
            lambda task: task["oracle"]["configuration"]["expected"].update(
                {"path": hidden}
            ),
        )
        self.assertNotIn(hidden, json.dumps(self.render()))

    def test_non_readonly_task_rejected_by_original_schema_or_guard(self):
        self.task(TASKS[0], lambda task: task.update({"mode": "edit"}))
        with self.assertRaises((BlindReviewError, SuiteError)):
            self.render()

    def test_staging_cannot_live_inside_active_suite_root(self):
        active = self.root / ACTIVE_RELATIVE / "unapproved"
        shutil.copytree(self.stage, active)
        with self.assertRaisesRegex(BlindReviewError, "active suite inventory"):
            prepare_blind_review(staged=active, repository_root=self.root)

    def test_missing_original_participant_rejected(self):
        (self.root / PARTICIPANT_SOURCE / "agents/codex-native.json").unlink()
        with self.assertRaisesRegex(BlindReviewError, "missing or unsafe"):
            self.render()

    def test_repeated_export_is_deterministic_and_non_mutating(self):
        originals = {
            p.relative_to(self.root): p.read_bytes()
            for p in self.root.rglob("*.json")
        }
        first = self.render()
        self.assertEqual(first, self.render())
        self.assertEqual(len({x["packet_id"] for x in first["packets"]}), 2)
        self.assertEqual(originals, {
            p.relative_to(self.root): p.read_bytes()
            for p in self.root.rglob("*.json")
        })

    def test_cli_writes_only_separate_blinded_review_artifact(self):
        output = self.root / "output" / "review.json"
        with redirect_stdout(StringIO()) as stdout:
            code = main([
                "--repository-root", str(self.root), "--suite",
                str(STAGED_RELATIVE), "--output", str(output),
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), json.loads(output.read_text()))
        self.assertFalse((self.stage / "qualification/oracle-reviews.json").exists())

    def test_cli_refuses_output_inside_staged_or_active_suites(self):
        for destination in (
            self.stage / "review.json",
            self.root / ACTIVE_RELATIVE / "review.json",
        ):
            with self.subTest(destination=destination):
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                    main([
                        "--repository-root", str(self.root), "--suite",
                        str(STAGED_RELATIVE), "--output", str(destination),
                    ])
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
