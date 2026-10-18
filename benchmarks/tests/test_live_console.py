from __future__ import annotations

import unittest
import copy
from pathlib import Path

from benchmarks.harness.live_console import (
    LiveCampaignProgress,
    LiveTaskMatrix,
    TrialHeartbeat,
    render_campaign_admission,
    render_run_blockers,
    render_trial_failure,
)
from benchmarks.harness.runner import TrialRunResult
from benchmarks.harness.suite import SuiteDefinition, load_suite


SUITE = Path("benchmarks/suites/repository-intelligence/heldout-v1")


class LiveTaskMatrixTests(unittest.TestCase):
    def _prefix_opencode_rows(self):
        suite = load_suite(SUITE)
        rows = [
            row
            for row in suite.trial_definitions()
            if row["task_id"] == "locate-prefix-path-enumerator"
            and row["condition_id"].endswith("opencode-native")
        ]
        return suite, rows

    def _receipt(
        self,
        suite,
        row,
        status,
        *,
        gradeable=True,
        invoked=None,
        subject_mcp_calls=None,
        tool_names=None,
        tool_observability=None,
        tool_result_evidence=None,
    ):
        condition = next(
            item for item in suite.experiment["conditions"]
            if item["id"] == row["condition_id"]
        )
        return {
            "definition_id": row["definition_id"],
            "status": status,
            "task": suite.tasks[row["task_id"]],
            "condition": suite.expanded_condition(condition),
            "execution": {
                "trial_index": row["trial"],
                "replicate_id": row["replicate_id"],
                "admitted_state_sha256": "same-input",
            },
            "authority": {
                "agent": {"observed": {}},
                "harness": {"same": True},
                "environment": {"same": True},
                "mutation": None,
            },
            "scoring": {
                "oracle_grade": {
                    "semantic_gradeable": gradeable,
                    "semantic_success": status == "PASS" if gradeable else False,
                    "semantic_status": (
                        "CORRECT"
                        if gradeable and status == "PASS"
                        else "INCORRECT"
                        if gradeable and status == "FAIL"
                        else "UNSCORABLE"
                        if not gradeable
                        else None
                    ),
                    "expected": suite.tasks[row["task_id"]]["oracle"]["configuration"][
                        "expected"
                    ],
                    "normalized_actual": (
                        suite.tasks[row["task_id"]]["oracle"]["configuration"][
                            "expected"
                        ]
                        if gradeable and status == "PASS"
                        else {
                            "path": "hashmarks/codemap/repository_file_discovery.py",
                            "symbol": "_iter_admitted_repository_files",
                        }
                        if gradeable
                        else None
                    ),
                }
            },
            "measurements": {
                "agent": {
                    "subject_tool_invoked": invoked,
                    "subject_mcp_calls": subject_mcp_calls,
                    "subject_tool_names": list(tool_names or ()),
                    "subject_tool_observability": tool_observability,
                    "subject_tool_result_evidence": list(
                        tool_result_evidence or ()
                    ),
                }
            },
        }

    def _status(self, rows, complete=()):
        completed = set(complete)
        return {
            "rows": [
                {
                    "definition_id": row["definition_id"],
                    "state": "COMPLETE" if row["definition_id"] in completed else "PENDING",
                }
                for row in rows
            ],
            "complete_trials": len(completed),
            "pending_trials": len(rows) - len(completed),
            "interrupted_trials": 0,
        }

    def test_renders_compact_task_matrix_and_paired_transitions(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        statuses = {
            ("none-opencode-native", 0): "FAIL",
            ("none-opencode-native", 1): "FAIL",
            ("none-opencode-native", 2): "INCOMPLETE",
            ("hashmarks-opencode-native", 0): "PASS",
            ("hashmarks-opencode-native", 1): "PASS",
            ("hashmarks-opencode-native", 2): "FAIL",
            ("enola-opencode-native", 0): "FAIL",
            ("enola-opencode-native", 1): "PASS",
            ("enola-opencode-native", 2): "PASS",
        }
        projection = LiveTaskMatrix(suite, rows)

        rendered = None
        for row in rows:
            value = projection.record(
                row,
                self._receipt(
                    suite,
                    row,
                    statuses[(row["condition_id"], row["trial"])],
                    invoked=(
                        row["trial"] != 2
                        if row["condition_id"] == "hashmarks-opencode-native"
                        else None
                    ),
                    subject_mcp_calls=(
                        row["trial"] + 1
                        if row["condition_id"] == "hashmarks-opencode-native"
                        and row["trial"] != 2
                        else 0
                    ),
                    tool_names=(
                        ["task_evidence", "find"]
                        if row["condition_id"] == "hashmarks-opencode-native"
                        and row["trial"] != 2
                        else []
                    ),
                    tool_observability=(
                        "complete"
                        if row["condition_id"] == "hashmarks-opencode-native"
                        else None
                    ),
                    tool_result_evidence=(
                        [
                            {
                                "operation": "task_evidence",
                                "status": "completed",
                                "result_bytes": 10,
                                "error_present": False,
                                "result_basis": "test",
                                "outcome": "successful-result-observed",
                            }
                        ]
                        if row["condition_id"] == "hashmarks-opencode-native"
                        and row["trial"] == 0
                        else []
                    ),
                ),
            )
            if value is not None:
                self.assertIsNone(rendered)
                rendered = value

        self.assertIsNotNone(rendered)
        assert rendered is not None
        self.assertIn(
            "RESULT locate-prefix-path-enumerator [opencode-native]",
            rendered,
        )
        self.assertIn(
            "Replicate ID | Bare       | Hashmarks | Enola",
            rendered,
        )
        self.assertIn(
            "6201         | FAIL       | PASS      | FAIL ",
            rendered,
        )
        self.assertIn(
            "6202         | FAIL       | PASS      | PASS ",
            rendered,
        )
        self.assertIn(
            "6203         | INCOMPLETE | FAIL      | PASS ",
            rendered,
        )
        self.assertIn("Repository-location outcome topology", rendered)
        self.assertIn("Same directory", rendered)
        self.assertIn("Same symbol", rendered)
        self.assertIn("Ungradeable", rendered)
        self.assertIn("Subject-configured condition vs Bare (all pairs)", rendered)
        self.assertIn(
            "Hashmarks | 2    | 0         | 0          | 0          | 1",
            rendered,
        )
        self.assertIn(
            "Enola     | 1    | 0         | 1          | 0          | 1",
            rendered,
        )
        self.assertIn("Paired outcome by subject use", rendered)
        self.assertIn("subject-use observed", rendered)
        self.assertIn("not attributable to subject tool", rendered)
        self.assertIn("invocation unknown", rendered)
        self.assertIn("Hashmarks | invoked", rendered)
        self.assertIn("Hashmarks | not-invoked", rendered)
        self.assertIn("Enola     | unknown", rendered)
        self.assertIn("Paired outcome by contracted treatment", rendered)
        self.assertIn("contracted-successful-result", rendered)
        self.assertIn("contracted-treatment-observed", rendered)
        self.assertIn("subject-not-invoked", rendered)
        self.assertIn("contracted-result-unproven", rendered)
        self.assertIn("Subject tool use", rendered)
        self.assertIn(
            "Hashmarks | 2       | 1           | 0       | 3              | find,task_evidence",
            rendered,
        )
        self.assertIn(
            "Enola     | 0       | 0           | 3       | 0              | -",
            rendered,
        )
        self.assertIn("complete", rendered)
        self.assertIn("unknown", rendered)
        self.assertIn("Subject use by replicate", rendered)
        self.assertIn("6201", rendered)
        self.assertIn("used find,task_evidence calls=1 [complete]", rendered)
        self.assertIn("not-used calls=0 [complete]", rendered)

    def test_zero_subject_exposure_is_explicit(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        projection = LiveTaskMatrix(suite, rows)

        rendered = None
        for row in rows:
            condition = row["condition_id"]
            value = projection.record(
                row,
                self._receipt(
                    suite,
                    row,
                    "PASS",
                    invoked=(
                        True
                        if condition == "hashmarks-opencode-native"
                        else False
                        if condition == "enola-opencode-native"
                        else None
                    ),
                    subject_mcp_calls=(
                        1 if condition == "hashmarks-opencode-native" else 0
                    ),
                    tool_names=(
                        ["task_evidence"]
                        if condition == "hashmarks-opencode-native"
                        else []
                    ),
                    tool_observability=(
                        "complete"
                        if condition.endswith("opencode-native")
                        and condition != "none-opencode-native"
                        else None
                    ),
                ),
            )
            if value is not None:
                rendered = value

        assert rendered is not None
        self.assertIn(
            "Subject-configured condition vs Bare (all pairs)",
            rendered,
        )
        self.assertIn("Exposure note", rendered)
        self.assertIn(
            "Enola on locate-prefix-path-enumerator: configured condition, "
            "0/3 subject invocations; outcomes are "
            "not attributable to the subject tool",
            rendered,
        )

    def test_campaign_admission_renderer_exposes_model_free_step_progress(self) -> None:
        line = render_campaign_admission(
            {
                "stage": "condition-authority",
                "index": 7,
                "total": 36,
                "task_id": "locate-stale-index-removal",
                "condition_id": "hashmarks-opencode-native",
            },
            elapsed=94.0,
        )
        self.assertEqual(
            line,
            "ADMISSION [7/36] locate-stale-index-removal / "
            "hashmarks-opencode-native | checking authority | admission elapsed 1m34s",
        )
        completed = render_campaign_admission(
            {
                "stage": "condition-authority-complete",
                "index": 7,
                "total": 36,
                "task_id": "locate-stale-index-removal",
                "condition_id": "hashmarks-opencode-native",
                "timings_ms": {
                    "materialize": 410,
                    "snapshot": 80,
                    "participant_prepare": 2310,
                    "oracle_health": 2,
                    "condition_authority": 120,
                    "total": 2950,
                },
            },
            elapsed=97.0,
        )
        self.assertIn("authority OK", completed)
        self.assertIn("materialize 410ms", completed)
        self.assertIn("prepare 2310ms", completed)
        self.assertIn("authority 120ms", completed)
        self.assertIn("total 2950ms", completed)
        self.assertIn(
            "harness authority | verified | 38ms",
            render_campaign_admission(
                {
                    "stage": "campaign-harness-authority",
                    "status": "verified",
                    "duration_ms": 38,
                },
                elapsed=0.04,
            ),
        )
        self.assertIn(
            "oracle review | checking",
            render_campaign_admission(
                {"stage": "oracle-review", "status": "checking"},
                elapsed=2.0,
            ),
        )
        self.assertIn(
            "campaign authority | published",
            render_campaign_admission(
                {"stage": "campaign-authority", "status": "published"},
                elapsed=120.0,
            ),
        )

    def test_progress_reports_step_elapsed_remaining_and_eta(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        progress = LiveCampaignProgress(
            suite, rows, self._status(rows, complete=[rows[1]["definition_id"]])
        )
        start = progress.start_line(rows[0], elapsed=65.0)
        self.assertIn("[1/9 this run]", start)
        self.assertIn("task 1/1 locate-prefix-path-enumerator", start)
        self.assertIn("replicate 1/3", start)
        self.assertIn("run elapsed 1m05s", start)
        self.assertIn("verified 1/9", start)
        self.assertIn("pending 8", start)
        self.assertIn("execution ETA estimating...", start)

        executed = TrialRunResult(
            trial_id="a" * 64,
            definition_id=rows[0]["definition_id"],
            status="PASS",
            result_dir=Path("/tmp/result"),
            reused=False,
        )
        finished = progress.finish_line(
            executed,
            elapsed=125.0,
            trial_seconds=60.0,
        )
        self.assertIn("PROGRESS processed 1/9 (11.1%)", finished)
        self.assertIn("verified 2/9", finished)
        self.assertIn("executed", finished)
        self.assertIn("avg 1m00s/execution", finished)
        self.assertIn("execution ETA 7m00s", finished)

        reused = TrialRunResult(
            trial_id="c" * 64,
            definition_id=rows[1]["definition_id"],
            status="PASS",
            result_dir=Path("/tmp/reused"),
            reused=True,
        )
        resumed = progress.finish_line(
            reused,
            elapsed=126.0,
            trial_seconds=0.1,
        )
        self.assertIn("reused", resumed)
        self.assertIn("verified 2/9", resumed)
        self.assertIn("avg 1m00s/execution", resumed)
        self.assertIn("execution ETA 7m00s", resumed)

    def test_progress_marks_qualification_blocked_only_for_unresolved_outcomes(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        progress = LiveCampaignProgress(suite, rows, self._status(rows))

        failed = progress.finish_line(
            TrialRunResult(
                trial_id="a" * 64,
                definition_id=rows[0]["definition_id"],
                status="FAIL",
                result_dir=Path("/tmp/fail"),
                reused=False,
            ),
            elapsed=30.0,
            trial_seconds=30.0,
        )
        self.assertNotIn("qualification BLOCKED", failed)

        incomplete = progress.finish_line(
            TrialRunResult(
                trial_id="b" * 64,
                definition_id=rows[1]["definition_id"],
                status="INCOMPLETE",
                result_dir=Path("/tmp/incomplete"),
                reused=False,
            ),
            elapsed=60.0,
            trial_seconds=30.0,
        )
        self.assertIn("qualification BLOCKED", incomplete)
        self.assertIn("unresolved 1 outcome", incomplete)
        self.assertIn("continuing diagnostic evidence", incomplete)

        next_line = progress.start_line(rows[2], elapsed=60.0)
        self.assertIn("qualification BLOCKED", next_line)
        self.assertIn("unresolved 1 outcome", next_line)

        resumed_status = self._status(
            rows,
            complete=[rows[0]["definition_id"]],
        )
        resumed_status["unresolved_outcome_trials"] = 1
        resumed = LiveCampaignProgress(suite, rows, resumed_status)
        resumed_line = resumed.start_line(rows[1], elapsed=0.0)
        self.assertIn("qualification BLOCKED", resumed_line)
        self.assertIn("unresolved 1 outcome", resumed_line)

    def test_eta_uses_condition_specific_runtime_samples(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        progress = LiveCampaignProgress(suite, rows, self._status(rows))

        for row, seconds in ((rows[0], 60.0), (rows[1], 60.0), (rows[3], 180.0)):
            progress.finish_line(
                TrialRunResult(
                    trial_id="a" * 64,
                    definition_id=row["definition_id"],
                    status="PASS",
                    result_dir=Path("/tmp/result"),
                    reused=False,
                ),
                elapsed=seconds,
                trial_seconds=seconds,
            )

        start = progress.start_line(rows[4], elapsed=300.0)
        self.assertIn("pending 6", start)
        self.assertIn("execution ETA 12m00s", start)

    def test_eta_includes_interrupted_trial_and_recovered_execution_sample(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        status = self._status(rows)
        status["rows"][1]["state"] = "INTERRUPTED"
        status["pending_trials"] -= 1
        status["interrupted_trials"] = 1
        progress = LiveCampaignProgress(suite, rows, status)

        self.assertIn(
            "execution ETA estimating...",
            progress.start_line(rows[0], elapsed=0.0),
        )
        progress.finish_line(
            TrialRunResult(
                trial_id="a" * 64,
                definition_id=rows[0]["definition_id"],
                status="PASS",
                result_dir=Path("/tmp/result"),
                reused=False,
            ),
            elapsed=60.0,
            trial_seconds=60.0,
        )
        self.assertIn(
            "execution ETA 8m00s",
            progress.start_line(rows[1], elapsed=60.0),
        )

        recovered = progress.finish_line(
            TrialRunResult(
                trial_id="b" * 64,
                definition_id=rows[1]["definition_id"],
                status="FAIL",
                result_dir=Path("/tmp/recovered"),
                reused=False,
                recovered=True,
            ),
            elapsed=180.0,
            trial_seconds=120.0,
        )
        self.assertIn("interrupted 0", recovered)
        self.assertIn("avg 1m30s/execution", recovered)
        self.assertIn("execution ETA 10m30s", recovered)

    def test_run_blockers_repeat_operational_diagnostic_at_end(self) -> None:
        failure = {
            "trial_id": "trial-one",
            "task_id": "locate-prefix-path-enumerator",
            "condition_id": "none-opencode-native",
            "replicate_id": 6201,
            "reason_code": "agent-terminal-failed",
            "receipt": "/tmp/evidence/result.json",
        }
        trace = {"trials": [{
            "trial_id": "trial-one",
            "terminal_failure_cause": "context-overflow",
        }]}

        rendered = render_run_blockers([failure], trace)

        assert rendered is not None
        self.assertIn("RUN BLOCKERS 1 operational failure", rendered)
        self.assertIn("Causes: context-overflow 1", rendered)
        self.assertIn("replicate 6201: context-overflow", rendered)
        self.assertTrue(rendered.endswith("receipt /tmp/evidence/result.json"))
        self.assertNotIn("Traceback", rendered)

    def test_failure_envelope_is_agent_readable_and_preserves_diagnostic(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        result = TrialRunResult(
            trial_id="a" * 64,
            definition_id=rows[0]["definition_id"],
            status="INCOMPLETE",
            result_dir=Path("/tmp/evidence"),
            reused=False,
            reason="agent terminal event was turn.failed",
            stage="agent-execution",
            reason_code="agent-terminal-failed",
            diagnostic="Traceback (most recent call last):\nValueError: boom",
        )
        receipt = self._receipt(suite, rows[0], "INCOMPLETE")
        receipt["reason"] = "agent terminal event was turn.failed"
        receipt["diagnostic"] = {
            "stage": "agent-execution",
            "reason_code": "agent-terminal-failed",
            "detail": "Traceback (most recent call last):\nValueError: boom",
        }
        rendered = render_trial_failure(
            row=rows[0],
            subject="none",
            result=result,
            receipt=receipt,
        )
        assert rendered is not None
        self.assertIn("FAILURE locate-prefix-path-enumerator", rendered)
        self.assertIn("Replicate ordinal: 1", rendered)
        self.assertIn("Replicate ID: 6201", rendered)
        self.assertIn("Status: INCOMPLETE", rendered)
        self.assertIn("Stage: agent-execution", rendered)
        self.assertIn("Reason code: agent-terminal-failed", rendered)
        self.assertIn("Evidence: /tmp/evidence", rendered)
        self.assertIn("Receipt: /tmp/evidence/result.json", rendered)
        self.assertIn("Semantic outcome: INCOMPLETE", rendered)
        self.assertIn("Format compliant: unknown", rendered)
        self.assertIn("--- diagnostic ---", rendered)
        self.assertIn("ValueError: boom", rendered)

    def test_failure_envelope_uses_persisted_receipt_as_diagnostic_authority(
        self,
    ) -> None:
        suite, rows = self._prefix_opencode_rows()
        result = TrialRunResult(
            trial_id="a" * 64,
            definition_id=rows[0]["definition_id"],
            status="FAIL",
            result_dir=Path("/tmp/evidence"),
            reused=False,
            reason="stale transient reason",
            stage="stale-stage",
            reason_code="stale-code",
            diagnostic="stale transient diagnostic",
        )
        receipt = self._receipt(suite, rows[0], "INCOMPLETE")
        receipt["reason"] = "agent terminal event was turn.failed"
        receipt["diagnostic"] = {
            "stage": "agent-execution",
            "reason_code": "agent-terminal-failed",
            "detail": (
                "OpenCode export diagnostic:\n"
                "Error: terminal assistant message has no final text\n"
                "    at extractFinalAnswer"
            ),
        }

        rendered = render_trial_failure(
            row=rows[0],
            subject="none",
            result=result,
            receipt=receipt,
        )

        assert rendered is not None
        self.assertIn("Status: INCOMPLETE", rendered)
        self.assertIn("Stage: agent-execution", rendered)
        self.assertIn("Reason code: agent-terminal-failed", rendered)
        self.assertIn("Reason: agent terminal event was turn.failed", rendered)
        self.assertIn("at extractFinalAnswer", rendered)
        self.assertNotIn("stale transient", rendered)
        self.assertNotIn("stale-stage", rendered)
        self.assertNotIn("stale-code", rendered)

    def test_assisted_failure_shows_subject_tool_attribution(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        row = next(
            item
            for item in rows
            if item["condition_id"] == "hashmarks-opencode-native"
        )
        result = TrialRunResult(
            trial_id="a" * 64,
            definition_id=row["definition_id"],
            status="FAIL",
            result_dir=Path("/tmp/evidence"),
            reused=False,
            reason="repository location differs from frozen oracle",
            stage="oracle-grading",
            reason_code="oracle-mismatch",
        )
        receipt = self._receipt(
            suite,
            row,
            "FAIL",
            invoked=True,
            subject_mcp_calls=2,
            tool_names=["find", "task_evidence"],
            tool_observability="complete",
        )
        rendered = render_trial_failure(
            row=row,
            subject="hashmarks",
            result=result,
            receipt=receipt,
        )
        assert rendered is not None
        self.assertIn("Semantic outcome: FAIL", rendered)
        self.assertIn("Format compliant: unknown", rendered)
        self.assertIn("Subject tool invoked: yes", rendered)
        self.assertIn("Subject MCP calls observed: 2", rendered)
        self.assertIn("Subject tools observed: find,task_evidence", rendered)
        self.assertIn("Subject tool observation: complete", rendered)

    def test_recovered_failure_describes_numbered_retry(self) -> None:
        _, rows = self._prefix_opencode_rows()
        result = TrialRunResult(
            trial_id="a" * 64,
            definition_id="b" * 64,
            status="INCOMPLETE",
            result_dir=Path("/tmp/evidence"),
            reused=False,
            reason="previous trial launch was interrupted before a complete receipt",
            stage="campaign-recovery",
            reason_code="interrupted-launch",
            recovered=True,
        )
        rendered = render_trial_failure(
            row=rows[0],
            subject="none",
            result=result,
        )
        assert rendered is not None
        self.assertIn("Recovery: prior interrupted attempt preserved", rendered)
        self.assertIn("execution retried as a numbered attempt", rendered)

    def test_does_not_render_until_selected_task_agent_group_is_complete(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        projection = LiveTaskMatrix(suite, rows)

        for row in rows[:-1]:
            self.assertIsNone(projection.record(row, self._receipt(suite, row, "PASS")))

        self.assertIsNotNone(
            projection.record(rows[-1], self._receipt(suite, rows[-1], "PASS"))
        )

    def test_ungradeable_fail_is_excluded_from_live_pairs(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        projection = LiveTaskMatrix(suite, rows)
        rendered = None
        for row in rows:
            gradeable = not (row["condition_id"] == "none-opencode-native" and row["trial"] == 0)
            status = "FAIL" if row["condition_id"] == "none-opencode-native" else "PASS"
            rendered = projection.record(
                row, self._receipt(suite, row, status, gradeable=gradeable)
            ) or rendered
        assert rendered is not None
        self.assertIn("6201         | UNGRADABLE", rendered)
        self.assertIn("Hashmarks | 2    | 0         | 0          | 0          | 1", rendered)

    def test_same_subject_conditions_have_distinct_columns(self) -> None:
        suite, original = self._prefix_opencode_rows()
        experiment = copy.deepcopy(suite.experiment)
        extra = copy.deepcopy(next(
            item for item in experiment["conditions"]
            if item["id"] == "hashmarks-opencode-native"
        ))
        extra["id"] = "hashmarks-opencode-extra"
        experiment["conditions"].append(extra)
        expanded_suite = SuiteDefinition(
            suite.root, experiment, suite.tasks, suite.subjects, suite.agents
        )
        rows = [
            row for row in expanded_suite.trial_definitions()
            if row["task_id"] == original[0]["task_id"]
            and row["condition_id"].endswith("opencode-native")
            or row["task_id"] == original[0]["task_id"]
            and row["condition_id"] == extra["id"]
        ]
        projection = LiveTaskMatrix(expanded_suite, rows)
        rendered = None
        for row in rows:
            rendered = projection.record(
                row, self._receipt(expanded_suite, row, "PASS")
            ) or rendered
        assert rendered is not None
        self.assertIn("Hashmarks (hashmarks-opencode-native)", rendered)
        self.assertIn("Hashmarks (hashmarks-opencode-extra)", rendered)

    def test_heartbeat_reports_stage_only_after_interval(self) -> None:
        suite, rows = self._prefix_opencode_rows()
        progress = LiveCampaignProgress(suite, rows, self._status(rows))
        now = [100.0]
        emitted = []
        heartbeat = TrialHeartbeat(
            progress=progress,
            row=rows[0],
            run_started=90.0,
            emit=emitted.append,
            interval=30.0,
            clock=lambda: now[0],
        )
        self.assertIsNone(heartbeat.tick(129.0))
        heartbeat.update_stage("agent-execution")
        line = heartbeat.tick(130.0)
        assert line is not None
        self.assertIn("stage agent-execution", line)
        self.assertIn("trial elapsed 30s", line)
        self.assertIn("verified 0/9", line)
        self.assertIsNone(heartbeat.tick(131.0))
        heartbeat.__exit__(None, None, None)
        self.assertIsNone(heartbeat.tick(160.0))
        self.assertEqual(emitted, [])


if __name__ == "__main__":
    unittest.main()
