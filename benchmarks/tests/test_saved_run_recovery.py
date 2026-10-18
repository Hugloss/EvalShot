"""Durable run selection and crash-boundary regression tests."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from benchmarks.__main__ import (
    _canonical_campaign_persistence_gap,
    _execute_run,
    _persist_completed_run_reports,
    _selection_metadata,
    _validate_reporting_contract,
    main,
)
from benchmarks.harness.campaign import campaign_status
from benchmarks.harness.campaign_authority import (
    CampaignAuthorityError,
    claim_launch,
    launch_state,
    read_campaign,
    read_interrupted_attempts,
    record_interrupted_attempt,
    start_attempt_events,
)
from benchmarks.harness.events import append_event
from benchmarks.harness.identity import canonical_json, digest
from benchmarks.harness.run_store import (
    RunStoreError,
    active_definition,
    active_trial,
    exclusive_store,
    list_saved_runs,
    prepare_saved_run,
    select_saved_run,
)
from benchmarks.harness.receipt import is_complete_receipt
from benchmarks.harness.report import ReportError
from benchmarks.harness.bundle_writer import _sync
from benchmarks.harness.runner import TrialRunResult, _publish_bundle
from scripts.agent_economics.bounded_process import retain_lock_in_subprocesses, run_bounded


def _campaign(results: Path, definitions: list[str]) -> dict:
    payload = {
        "contract": "benchmark-campaign-authority.v3",
        "selected_definitions": definitions,
        "agents": {"opencode-native": {}},
    }
    payload["campaign_id"] = digest(payload)
    directory = results / ".campaign"
    directory.mkdir(parents=True)
    (directory / "authority.json").write_bytes(canonical_json(payload))
    return payload


class SavedRunRecoveryTests(unittest.TestCase):
    def test_admission_progress_reaches_prepare_new_run_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = Path("benchmarks/suites/repository-intelligence/heldout-v1")
            config = SimpleNamespace(
                runtime_environment=lambda: {},
                require=lambda *_keys: None,
            )

            def admit_with_progress(**kwargs):
                progress = kwargs["on_progress"]
                progress({"stage": "oracle-review", "status": "checking"})
                existing = (kwargs["results_root"] / ".campaign/authority.json").exists()
                progress({
                    "stage": "campaign-authority",
                    "status": "reused" if existing else "published",
                })
                if existing:
                    return read_campaign(kwargs["results_root"])
                return _campaign(
                    kwargs["results_root"],
                    [str(row["definition_id"]) for row in kwargs["rows"]],
                )

            common = [
                "--suite", str(suite),
                "--root", str(root),
                "--env-file", str(root / "unused.env"),
                "--agent", "opencode-native",
            ]
            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch("benchmarks.__main__.admit_campaign", side_effect=admit_with_progress) as admit,
                mock.patch(
                    "benchmarks.__main__.verify_saved_campaign",
                    side_effect=lambda **kwargs: read_campaign(kwargs["results_root"]),
                ) as verify_resume,
                mock.patch("benchmarks.__main__._execute_run", return_value=0) as execute,
                mock.patch(
                    "benchmarks.__main__._validate_reporting_contract",
                    return_value=(suite / "score.py", Path("score.json")),
                ),
            ):
                for command in (
                    ["prepare", "--new", *common],
                    ["run", "--new", *common],
                    ["run", "--resume", "--run-id", "000002", *common],
                ):
                    with self.subTest(command=command[:2]):
                        stdout, stderr = io.StringIO(), io.StringIO()
                        with redirect_stdout(stdout), redirect_stderr(stderr):
                            self.assertEqual(main(command), 0)
                        if "--resume" in command:
                            self.assertIn(
                                "RESUME campaign authority | verified |",
                                stderr.getvalue(),
                            )
                            self.assertNotIn("ADMISSION [", stderr.getvalue())
                        else:
                            self.assertIn(
                                "ADMISSION oracle review | checking",
                                stderr.getvalue(),
                            )
                            self.assertIn(
                                "ADMISSION campaign authority | published",
                                stderr.getvalue(),
                            )
                            self.assertIn("admission elapsed", stderr.getvalue())
                        if command[0] == "prepare":
                            self.assertEqual(json.loads(stdout.getvalue())["run_id"], "000001")
                        else:
                            self.assertEqual(stdout.getvalue(), "")

            self.assertEqual(admit.call_count, 2)
            verify_resume.assert_called_once()
            self.assertEqual(execute.call_count, 2)

    def test_execute_run_reuses_complete_receipt_without_trial_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            result_dir = results / ("b" * 64)
            result_dir.mkdir(parents=True)
            receipt = {
                "definition_id": "a" * 64,
                "status": "PASS",
                "scoring": {"oracle_grade": {}},
                "measurements": {"agent": {}},
            }
            (result_dir / "result.json").write_text(
                json.dumps(receipt),
                encoding="utf-8",
            )
            row = {
                "definition_id": "a" * 64,
                "task_id": "task-a",
                "condition_id": "bare",
                "trial": 0,
                "replicate_id": 9,
            }
            suite = SimpleNamespace(
                experiment={
                    "conditions": [
                        {
                            "id": "bare",
                            "agent": "opencode-native",
                            "subject": "none",
                            "trials": 1,
                        }
                    ]
                }
            )
            status = {
                "complete_trials": 1,
                "pending_trials": 0,
                "interrupted_trials": 0,
                "rows": [
                    {
                        **row,
                        "state": "COMPLETE",
                        "trial_ids": ["b" * 64],
                    }
                ],
                "outcomes": {"PASS": 1},
                "qualified": True,
            }
            reused = TrialRunResult(
                trial_id="b" * 64,
                definition_id="a" * 64,
                status="PASS",
                result_dir=result_dir,
                reused=True,
            )
            args = SimpleNamespace(
                root=root,
                harness_root=root,
                source=None,
                codex_auth=None,
            )
            paths = SimpleNamespace(
                root=root,
                cache=root / "cache",
                work=root / "work",
                results=results,
                run_id="000001",
            )

            with (
                mock.patch(
                    "benchmarks.__main__.campaign_status",
                    side_effect=[status, status],
                ),
                mock.patch(
                    "benchmarks.harness.campaign_execution.reuse_completed_trial",
                    return_value=reused,
                ) as reuse,
                mock.patch(
                    "benchmarks.__main__.run_trial",
                    side_effect=AssertionError(
                        "completed receipt must not enter participant admission"
                    ),
                ) as run,
                mock.patch(
                    "benchmarks.__main__._persist_completed_run_reports",
                    return_value={
                        "status": root / "reports/status.json",
                        "report": root / "reports/report.json",
                        "decision_evidence": root / "reports/decision-evidence.json",
                        "trace_diagnostics": root / "reports/trace-diagnostics.json",
                        "report_set": root / "reports/report-set.json",
                        "score": root / "reports/score.json",
                    },
                ) as persist_reports,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    _execute_run(
                        args,
                        suite,
                        [row],
                        paths,
                        {"campaign_id": "c" * 64},
                        {},
                        SimpleNamespace(),
                    ),
                    0,
                )

            reuse.assert_called_once_with(
                results_root=results,
                definition_id="a" * 64,
                trial_id="b" * 64,
            )
            run.assert_not_called()
            persist_reports.assert_called_once()

    def test_execute_run_uses_receipt_status_for_final_blocker_and_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            result_dir = results / ("b" * 64)
            result_dir.mkdir(parents=True)
            receipt = {
                "trial_id": "b" * 64,
                "definition_id": "a" * 64,
                "status": "INCOMPLETE",
                "reason": "agent terminal event was turn.failed",
                "diagnostic": {
                    "stage": "agent-execution",
                    "reason_code": "agent-terminal-failed",
                    "detail": (
                        "OpenCode export diagnostic:\n"
                        "Error: terminal assistant message has no final text\n"
                        "    at extractFinalAnswer"
                    ),
                },
                "scoring": {"oracle_grade": {}},
                "measurements": {"agent": {}},
            }
            (result_dir / "result.json").write_text(
                json.dumps(receipt),
                encoding="utf-8",
            )
            row = {
                "definition_id": "a" * 64,
                "task_id": "task-a",
                "condition_id": "bare",
                "trial": 0,
                "replicate_id": 9,
            }
            suite = SimpleNamespace(
                experiment={
                    "conditions": [
                        {
                            "id": "bare",
                            "agent": "opencode-native",
                            "subject": "none",
                            "trials": 1,
                        }
                    ]
                }
            )
            status = {
                "complete_trials": 1,
                "pending_trials": 0,
                "interrupted_trials": 0,
                "unresolved_outcome_trials": 1,
                "rows": [
                    {
                        **row,
                        "state": "COMPLETE",
                        "trial_ids": ["b" * 64],
                    }
                ],
                "outcomes": {"INCOMPLETE": 1},
                "qualified": False,
            }
            stale = TrialRunResult(
                trial_id="b" * 64,
                definition_id="a" * 64,
                status="FAIL",
                result_dir=result_dir,
                reused=True,
                reason="stale transient reason",
                stage="stale-stage",
                reason_code="stale-code",
                diagnostic="stale transient diagnostic",
            )
            args = SimpleNamespace(
                root=root,
                harness_root=root,
                source=None,
                codex_auth=None,
            )
            paths = SimpleNamespace(
                root=root,
                cache=root / "cache",
                work=root / "work",
                results=results,
                run_id="000001",
            )
            stdout, stderr = io.StringIO(), io.StringIO()

            with (
                mock.patch(
                    "benchmarks.__main__.campaign_status",
                    side_effect=[status, status],
                ),
                mock.patch(
                    "benchmarks.harness.campaign_execution.reuse_completed_trial",
                    return_value=stale,
                ),
                mock.patch(
                    "benchmarks.__main__.run_trial",
                    side_effect=AssertionError(
                        "completed receipt must not enter participant admission"
                    ),
                ),
                mock.patch(
                    "benchmarks.__main__._persist_completed_run_reports",
                    return_value={
                        "status": root / "reports/status.json",
                        "report": root / "reports/report.json",
                        "decision_evidence": root / "reports/decision-evidence.json",
                        "trace_diagnostics": root / "reports/trace-diagnostics.json",
                        "report_set": root / "reports/report-set.json",
                        "score": root / "reports/score.json",
                    },
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                self.assertEqual(
                    _execute_run(
                        args,
                        suite,
                        [row],
                        paths,
                        {"campaign_id": "c" * 64},
                        {},
                        SimpleNamespace(),
                    ),
                    0,
                )

            emitted = json.loads(stdout.getvalue())
            self.assertEqual(emitted[0]["status"], "INCOMPLETE")
            self.assertEqual(
                emitted[0]["reason"],
                "agent terminal event was turn.failed",
            )
            terminal = stderr.getvalue()
            self.assertIn("task-a / bare: INCOMPLETE", terminal)
            self.assertIn(
                f"trace-diagnostics {root / 'reports/trace-diagnostics.json'}",
                terminal,
            )
            self.assertIn("RUN BLOCKERS 1 operational failure", terminal)
            self.assertIn(f"Receipt: {result_dir / 'result.json'}", terminal)
            self.assertIn("Reason code: agent-terminal-failed", terminal)
            self.assertIn("at extractFinalAnswer", terminal)
            self.assertNotIn("stale transient", terminal)
            self.assertNotIn("stale-stage", terminal)
            self.assertNotIn("stale-code", terminal)

    def test_completed_nonqualified_run_persists_derived_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            _campaign(results, ["a" * 64])
            suite_root = root / "suite"
            suite_root.mkdir()
            score_script = suite_root / "score.py"
            score_script.write_text("# score fixture\n", encoding="utf-8")
            suite = SimpleNamespace(
                root=suite_root,
                experiment={
                    "conditions": [
                        {
                            "id": "bare",
                            "agent": "opencode-native",
                            "subject": "none",
                        }
                    ]
                },
            )
            row = {
                "definition_id": "a" * 64,
                "task_id": "task-a",
                "condition_id": "bare",
                "trial": 0,
            }
            args = SimpleNamespace(
                task=[],
                agent=["opencode-native"],
                subject=[],
                condition=None,
            )
            paths = SimpleNamespace(
                root=root,
                results=results,
                run_id="000006",
                as_dict=lambda: {
                    "root": str(root),
                    "results": str(results),
                    "cache": None,
                    "work": None,
                    "run_id": "000006",
                },
            )
            final_status = {
                "complete": True,
                "complete_trials": 1,
                "expected_trials": 1,
                "pending_trials": 0,
                "interrupted_trials": 0,
                "conflicting_trials": 0,
                "corrupt_bundles": [],
                "foreign_bundles": [],
                "outcomes": {"INCOMPLETE": 1},
                "unresolved_outcome_trials": 1,
                "qualified": False,
            }
            config = SimpleNamespace(
                path=lambda key: {
                    "BENCHMARK_SCORE_SCRIPT_PATH": score_script,
                    "BENCHMARK_SCORE_OUTPUT_PATH": Path("score.json"),
                }.get(key)
            )

            def score_run(invocation, **_kwargs):
                self.assertIn("--definition-id", invocation)
                self.assertEqual(
                    invocation[invocation.index("--definition-id") + 1],
                    "a" * 64,
                )
                output = Path(invocation[invocation.index("--output") + 1])
                output.write_text(
                    json.dumps({"campaign_qualification": {"status": "NOT_QUALIFIED"}}),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with (
                mock.patch(
                    "benchmarks.__main__.build_report",
                    return_value={
                        "campaign_qualification": {"status": "NOT_QUALIFIED"}
                    },
                ),
                mock.patch(
                    "benchmarks.__main__.build_trace_diagnostics",
                    return_value={
                        "summary": {
                            "repository_intelligence_quality": {
                                "state": "observed",
                                "claim_scope": "descriptive-diagnostic-only",
                                "subjects": [
                                    {
                                        "subject_id": "hashmarks",
                                        "operation": "task_evidence",
                                        "calls": 1,
                                    }
                                ],
                            }
                        }
                    },
                ),
                mock.patch(
                    "benchmarks.__main__.subprocess.run",
                    side_effect=score_run,
                ),
                mock.patch(
                    "benchmarks.__main__.verify_campaign_suite_authority"
                ),
            ):
                written = _persist_completed_run_reports(
                    args=args,
                    config=config,
                    suite=suite,
                    rows=[row],
                    paths=paths,
                    runtime_source={},
                    final_status=final_status,
                )

            self.assertTrue(written["status"].is_file())
            self.assertTrue(written["report"].is_file())
            self.assertTrue(written["decision_evidence"].is_file())
            self.assertTrue(written["report_set"].is_file())
            self.assertTrue(written["score"].is_file())
            stored_status = json.loads(written["status"].read_text(encoding="utf-8"))
            self.assertFalse(stored_status["qualified"])
            self.assertEqual(stored_status["unresolved_outcome_trials"], 1)
            self.assertEqual(
                stored_status["derived_report_set"]["status"],
                "VERIFIED",
            )
            self.assertTrue(
                stored_status["derived_report_set"]["generation_id"]
            )
            stored_report = json.loads(written["report"].read_text(encoding="utf-8"))
            self.assertEqual(
                stored_report["campaign_qualification"]["status"],
                "NOT_QUALIFIED",
            )
            stored_decision = json.loads(
                written["decision_evidence"].read_text(encoding="utf-8")
            )
            self.assertEqual(
                stored_decision["campaign"]["qualification"]["status"],
                "NOT_QUALIFIED",
            )
            self.assertTrue(stored_decision["authority"]["derived_only"])
            self.assertEqual(
                stored_decision["authority"]["sources"],
                ["report.json", "trace-diagnostics.json"],
            )
            self.assertEqual(
                stored_decision["repository_intelligence_quality"]["state"],
                "observed",
            )
            stored_trace = json.loads(
                written["trace_diagnostics"].read_text(encoding="utf-8")
            )
            self.assertEqual(
                stored_trace["summary"]["repository_intelligence_quality"]["state"],
                stored_decision["repository_intelligence_quality"]["state"],
            )

    def test_run_rejects_invalid_score_output_before_campaign_admission(self) -> None:
        suite = (
            Path(__file__).resolve().parents[1]
            / "suites/repository-intelligence/heldout-v1"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runs"
            config = SimpleNamespace(
                runtime_environment=lambda: {},
                require=lambda *_keys: None,
                path=lambda key: {
                    "BENCHMARK_SCORE_SCRIPT_PATH": suite / "score.py",
                    "BENCHMARK_SCORE_OUTPUT_PATH": Path(temporary) / "score.json",
                }.get(key),
            )
            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch("benchmarks.__main__.admit_campaign") as admit,
                mock.patch("benchmarks.__main__._execute_run") as execute,
                self.assertRaisesRegex(
                    SystemExit,
                    "BENCHMARK_SCORE_OUTPUT_PATH must be a filename",
                ),
            ):
                main(
                    [
                        "run",
                        "--new",
                        "--suite",
                        str(suite),
                        "--root",
                        str(root),
                        "--harness-root",
                        str(Path(__file__).resolve().parents[2]),
                        "--env-file",
                        str(Path(temporary) / "unused.env"),
                        "--agent",
                        "opencode-native",
                        "--task",
                        "locate-prefix-path-enumerator",
                    ]
                )
            admit.assert_not_called()
            execute.assert_not_called()
            self.assertFalse(root.exists())

    def test_reporting_contract_rejects_scorer_without_exact_definition_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            score = root / "score.py"
            score.write_text(
                "import argparse\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--results')\n"
                "p.add_argument('--output')\n"
                "p.add_argument('--agent')\n"
                "p.parse_args()\n",
                encoding="utf-8",
            )
            config = SimpleNamespace(
                path=lambda key: {
                    "BENCHMARK_SCORE_SCRIPT_PATH": score,
                    "BENCHMARK_SCORE_OUTPUT_PATH": Path("score.json"),
                }.get(key)
            )
            with self.assertRaisesRegex(ReportError, "--definition-id"):
                _validate_reporting_contract(
                    config,
                    SimpleNamespace(root=root),
                    {},
                )

    def test_exact_definition_match_cannot_hide_suite_authority_drift(self) -> None:
        suite_path = (
            Path(__file__).resolve().parents[1]
            / "suites/repository-intelligence/heldout-v1"
        )
        from benchmarks.harness.suite import load_suite

        suite = load_suite(suite_path)
        row = next(
            row
            for row in suite.trial_definitions()
            if row["task_id"] == "locate-prefix-path-enumerator"
            and row["condition_id"] == "none-opencode-native"
        )
        definition = str(row["definition_id"])
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary) / "results"
            payload = {
                "contract": "benchmark-campaign-authority.v3",
                "selected_definitions": [definition],
                "agents": {"opencode-native": {}},
                "suite_identity": "0" * 64,
            }
            payload["campaign_id"] = digest(payload)
            directory = results / ".campaign"
            directory.mkdir(parents=True)
            (directory / "authority.json").write_bytes(canonical_json(payload))

            self.assertEqual(
                _canonical_campaign_persistence_gap(
                    suite=suite,
                    results_root=results,
                    rows=[row],
                ),
                "saved campaign suite authority differs from current suite",
            )

    def test_subset_status_and_report_do_not_replace_canonical_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary)
            run_root = store / "runs/000001"
            results = run_root / "results"
            first, second = "a" * 64, "b" * 64
            _campaign(results, [first, second])
            reports = run_root / "reports"
            reports.mkdir()
            canonical = {
                "status.json": "canonical status\n",
                "report.json": "canonical report\n",
                "decision-evidence.json": "canonical decision\n",
            }
            for name, value in canonical.items():
                (reports / name).write_text(value, encoding="utf-8")

            condition = {
                "id": "bare",
                "agent": "opencode-native",
                "subject": "none",
            }
            suite = SimpleNamespace(experiment={"conditions": [condition]})
            row = {
                "definition_id": first,
                "task_id": "task-a",
                "condition_id": "bare",
                "trial": 0,
                "replicate_id": 1,
            }
            config = SimpleNamespace(runtime_environment=lambda: {})
            status = {
                "conflicting_trials": 0,
                "corrupt_bundles": [],
                "foreign_bundles": [],
                "qualified": True,
            }
            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch("benchmarks.__main__.load_suite", return_value=suite),
                mock.patch("benchmarks.__main__._select", return_value=[row]),
                mock.patch("benchmarks.__main__.campaign_status", return_value=status),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(
                    main(
                        [
                            "status",
                            "--suite",
                            "unused",
                            "--root",
                            str(store),
                        ]
                    ),
                    0,
                )
            self.assertIn("STATUS inspection only", stderr.getvalue())

            report_payload = {"campaign_qualification": {"status": "QUALIFIED"}}
            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch("benchmarks.__main__.load_suite", return_value=suite),
                mock.patch("benchmarks.__main__._select", return_value=[row]),
                mock.patch("benchmarks.__main__.build_report", return_value=report_payload),
                mock.patch("benchmarks.__main__.build_decision_evidence") as decision,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(
                    main(
                        [
                            "report",
                            "--suite",
                            "unused",
                            "--root",
                            str(store),
                        ]
                    ),
                    0,
                )
            decision.assert_not_called()
            self.assertIn("REPORT inspection only", stderr.getvalue())
            for name, value in canonical.items():
                self.assertEqual((reports / name).read_text(encoding="utf-8"), value)

    def test_allow_incomplete_report_is_inspection_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary)
            run_root = store / "runs/000001"
            results = run_root / "results"
            first = "a" * 64
            _campaign(results, [first])
            reports = run_root / "reports"
            reports.mkdir()
            (reports / "report.json").write_text("canonical report\n", encoding="utf-8")
            (reports / "decision-evidence.json").write_text(
                "canonical decision\n", encoding="utf-8"
            )
            suite = SimpleNamespace(
                experiment={
                    "conditions": [
                        {
                            "id": "bare",
                            "agent": "opencode-native",
                            "subject": "none",
                        }
                    ]
                }
            )
            row = {
                "definition_id": first,
                "task_id": "task-a",
                "condition_id": "bare",
                "trial": 0,
                "replicate_id": 1,
            }
            config = SimpleNamespace(runtime_environment=lambda: {})
            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch("benchmarks.__main__.load_suite", return_value=suite),
                mock.patch("benchmarks.__main__._select", return_value=[row]),
                mock.patch(
                    "benchmarks.__main__.build_report",
                    return_value={"campaign_qualification": {"status": "NOT_QUALIFIED"}},
                ),
                mock.patch("benchmarks.__main__.build_decision_evidence") as decision,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(
                    main(
                        [
                            "report",
                            "--suite",
                            "unused",
                            "--root",
                            str(store),
                            "--allow-incomplete",
                        ]
                    ),
                    0,
                )
            decision.assert_not_called()
            self.assertIn("--allow-incomplete is inspection-only", stderr.getvalue())
            self.assertEqual(
                (reports / "report.json").read_text(encoding="utf-8"),
                "canonical report\n",
            )
            self.assertEqual(
                (reports / "decision-evidence.json").read_text(encoding="utf-8"),
                "canonical decision\n",
            )

    def test_score_failure_preserves_existing_derived_report_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            _campaign(results, ["a" * 64])
            suite_root = root / "suite"
            suite_root.mkdir()
            score_script = suite_root / "score.py"
            score_script.write_text("# score fixture\n", encoding="utf-8")
            suite = SimpleNamespace(
                root=suite_root,
                experiment={
                    "conditions": [
                        {
                            "id": "bare",
                            "agent": "opencode-native",
                            "subject": "none",
                        }
                    ]
                },
            )
            row = {
                "definition_id": "a" * 64,
                "task_id": "task-a",
                "condition_id": "bare",
                "trial": 0,
            }
            args = SimpleNamespace(
                task=[],
                agent=["opencode-native"],
                subject=[],
                condition=None,
            )
            paths = SimpleNamespace(
                root=root,
                results=results,
                run_id="000009",
                as_dict=lambda: {
                    "root": str(root),
                    "results": str(results),
                    "cache": None,
                    "work": None,
                    "run_id": "000009",
                },
            )
            config = SimpleNamespace(
                path=lambda key: {
                    "BENCHMARK_SCORE_SCRIPT_PATH": score_script,
                    "BENCHMARK_SCORE_OUTPUT_PATH": Path("score.json"),
                }.get(key)
            )
            reports = root / "reports"
            reports.mkdir()
            previous = {}
            for name in (
                "status.json",
                "report.json",
                "decision-evidence.json",
                "score.json",
            ):
                value = f"previous {name}\n"
                previous[name] = value
                (reports / name).write_text(value, encoding="utf-8")

            with (
                mock.patch(
                    "benchmarks.__main__.build_report",
                    return_value={
                        "campaign_qualification": {"status": "NOT_QUALIFIED"}
                    },
                ),
                mock.patch(
                    "benchmarks.__main__.subprocess.run",
                    return_value=SimpleNamespace(
                        returncode=2,
                        stdout="",
                        stderr="score failed",
                    ),
                ),
                mock.patch(
                    "benchmarks.__main__.verify_campaign_suite_authority"
                ),
                self.assertRaisesRegex(ReportError, "score generation failed"),
            ):
                _persist_completed_run_reports(
                    args=args,
                    config=config,
                    suite=suite,
                    rows=[row],
                    paths=paths,
                    runtime_source={},
                    final_status={"qualified": False},
                )

            for name, value in previous.items():
                self.assertEqual((reports / name).read_text(encoding="utf-8"), value)

    def test_selection_metadata_is_resolved_from_exact_rows(self) -> None:
        suite = SimpleNamespace(
            experiment={
                "conditions": [
                    {
                        "id": "bare",
                        "agent": "opencode-native",
                        "subject": "none",
                    },
                    {
                        "id": "assisted",
                        "agent": "opencode-native",
                        "subject": "hashmarks",
                    },
                ]
            }
        )
        rows = [
            {"task_id": "task-b", "condition_id": "assisted"},
            {"task_id": "task-a", "condition_id": "bare"},
            {"task_id": "task-a", "condition_id": "assisted"},
        ]
        self.assertEqual(
            _selection_metadata(suite, rows),
            {
                "tasks": ["task-a", "task-b"],
                "agents": ["opencode-native"],
                "subjects": ["hashmarks", "none"],
                "condition": None,
                "bare_control_included": True,
            },
        )
        self.assertEqual(
            _selection_metadata(suite, [rows[0]]),
            {
                "tasks": ["task-b"],
                "agents": ["opencode-native"],
                "subjects": ["hashmarks"],
                "condition": "assisted",
                "bare_control_included": False,
            },
        )

    def test_reports_command_regenerates_complete_set_through_one_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary) / "campaigns"
            run_root = store / "runs/000001"
            definition = "a" * 64
            _campaign(run_root / "results", [definition])
            suite = SimpleNamespace(
                experiment={
                    "conditions": [
                        {
                            "id": "bare",
                            "agent": "opencode-native",
                            "subject": "none",
                        }
                    ]
                }
            )
            row = {
                "definition_id": definition,
                "task_id": "task-a",
                "condition_id": "bare",
                "trial": 0,
                "replicate_id": 1,
            }
            config = SimpleNamespace(runtime_environment=lambda: {})
            final_status = {"complete": True, "qualified": True}
            written = {
                "status": run_root / "reports/status.json",
                "report": run_root / "reports/report.json",
                "decision_evidence": run_root / "reports/decision-evidence.json",
                "trace_diagnostics": run_root / "reports/trace-diagnostics.json",
                "score": run_root / "reports/score.json",
            }
            stdout = io.StringIO()
            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch("benchmarks.__main__.load_suite", return_value=suite),
                mock.patch(
                    "benchmarks.__main__.select_scoring_definitions",
                    return_value=[row],
                ) as select_frozen,
                mock.patch(
                    "benchmarks.__main__._validate_reporting_contract",
                    return_value=(Path("score.py"), Path("score.json")),
                ),
                mock.patch(
                    "benchmarks.__main__.verify_campaign_suite_authority"
                ) as verify_suite,
                mock.patch(
                    "benchmarks.__main__.campaign_status",
                    return_value=final_status,
                ),
                mock.patch(
                    "benchmarks.__main__._persist_completed_run_reports",
                    return_value=written,
                ) as persist,
                redirect_stdout(stdout),
            ):
                self.assertEqual(
                    main(
                        [
                            "reports",
                            "--suite",
                            "unused",
                            "--root",
                            str(store),
                            "--env-file",
                            str(Path(temporary) / "unused.env"),
                            "--agent",
                            "opencode-native",
                        ]
                    ),
                    0,
                )
            verify_suite.assert_called_once()
            select_frozen.assert_called_once_with(
                suite,
                agents=("opencode-native",),
                definition_ids=(definition,),
            )
            persist.assert_called_once()
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["run_id"], "000001")
            self.assertEqual(payload["reports"]["score"], str(written["score"]))
            self.assertEqual(
                payload["reports"]["trace_diagnostics"],
                str(written["trace_diagnostics"]),
            )

    def test_new_runs_are_numbered_and_latest_never_overwrites_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def admit(run_root: Path):
                return _campaign(run_root / "results", ["a" * 64])

            with exclusive_store(root):
                first, _ = prepare_saved_run(root, admit)
            marker = first.root / "results" / "keep.txt"
            marker.write_text("first run", encoding="utf-8")
            with exclusive_store(root):
                second, _ = prepare_saved_run(root, admit)
            self.assertEqual([run.run_id for run in list_saved_runs(root)], ["000001", "000002"])
            self.assertEqual(select_saved_run(root), second)
            self.assertEqual(select_saved_run(root, "000001"), first)
            self.assertEqual(marker.read_text(encoding="utf-8"), "first run")
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["runs", "--root", str(root)]), 0)
            self.assertEqual(json.loads(output.getvalue())["latest"], "000002")

    def test_legacy_run_stays_selectable_and_corrupt_newest_blocks_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _campaign(root / "results", ["a" * 64])
            self.assertEqual(select_saved_run(root).run_id, "legacy")
            with exclusive_store(root):
                newer, _ = prepare_saved_run(root, lambda path: _campaign(path / "results", ["b" * 64]))
            self.assertEqual(select_saved_run(root), newer)
            self.assertEqual(select_saved_run(root, "legacy").root, root)
            (newer.root / "results/.campaign/authority.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RunStoreError, "invalid saved run"):
                select_saved_run(root)

    def test_second_runner_reports_active_run_and_definition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with exclusive_store(root):
                with active_trial(root, "000004", "definition-abc"):
                    with self.assertRaisesRegex(
                        RunStoreError,
                        "active run 000004, definition definition-abc",
                    ):
                        with exclusive_store(root):
                            self.fail("lock should exclude a second runner")

    def test_bounded_child_inherits_the_store_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with exclusive_store(root) as lock_fd:
                with retain_lock_in_subprocesses(lock_fd):
                    with mock.patch(
                        "scripts.agent_economics.bounded_process.subprocess.Popen",
                        side_effect=FileNotFoundError,
                    ) as popen:
                        run_bounded(repository_root=root, argv=["missing-command"])
                self.assertEqual(popen.call_args.kwargs["pass_fds"], (lock_fd,))

    def test_model_child_keeps_lock_after_parent_releases_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with exclusive_store(root) as lock_fd:
                reader, writer = os.pipe()
                child = subprocess.Popen(
                    [sys.executable, "-c", "import os,sys; os.write(1,b'ready\\n'); os.read(int(sys.argv[1]),1)", str(reader)],
                    pass_fds=(lock_fd, reader),
                    stdout=subprocess.PIPE,
                )
                os.close(reader)
                self.assertEqual(child.stdout.readline(), b"ready\n")
            try:
                with self.assertRaisesRegex(RunStoreError, "another benchmark command"):
                    with exclusive_store(root):
                        self.fail("live child must retain lock")
            finally:
                os.write(writer, b"x")
                os.close(writer)
                child.wait(timeout=5)
                child.stdout.close()
            with exclusive_store(root):
                pass

    def test_interruption_record_is_idempotent_after_failed_claim_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "results"
            definition = "a" * 64
            trial = "b" * 64
            campaign = _campaign(root, [definition])
            self.assertEqual(claim_launch(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial), 1)
            claim_path = root / ".campaign/claims" / f"{definition}.json"
            real_unlink = Path.unlink

            def fail_claim_unlink(path, *args, **kwargs):
                if path == claim_path:
                    raise OSError("simulated crash before claim retirement")
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", fail_claim_unlink):
                with self.assertRaisesRegex(OSError, "simulated crash"):
                    record_interrupted_attempt(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial)
            self.assertTrue(claim_path.exists())
            self.assertEqual(len(read_interrupted_attempts(root, campaign["campaign_id"])[definition]), 1)
            self.assertEqual(record_interrupted_attempt(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial), 1)
            self.assertEqual(launch_state(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial), "UNCLAIMED")
            self.assertEqual(claim_launch(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial), 2)

    def test_failed_claim_publication_leaves_no_partial_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "results"
            definition = "a" * 64
            campaign = _campaign(root, [definition])
            with mock.patch(
                "benchmarks.harness.campaign_authority.os.link",
                side_effect=OSError("publication failed"),
            ):
                with self.assertRaisesRegex(OSError, "publication failed"):
                    claim_launch(
                        results_root=root, campaign=campaign,
                        definition_id=definition, trial_id="b" * 64,
                    )
            self.assertFalse((root / ".campaign/claims" / f"{definition}.json").exists())

    def test_published_bundle_survives_directory_sync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "results"
            root.mkdir()
            events = Path(temporary) / "events.jsonl"
            seal = Path(temporary) / "events.jsonl.seal.json"
            events.write_bytes(b"event\n")
            seal.write_bytes(b"seal")
            trial = "b" * 64

            def sync(path: Path) -> None:
                if path == root:
                    raise OSError("simulated directory sync failure")
                _sync(path)

            with (
                mock.patch("benchmarks.harness.runner._validate_result_receipt"),
                mock.patch("benchmarks.harness.bundle_writer.verify_bundle", return_value=(True, None)),
                mock.patch("benchmarks.harness.bundle_writer._sync", side_effect=sync),
            ):
                with self.assertRaisesRegex(OSError, "simulated directory sync failure"):
                    _publish_bundle(
                        results_root=root, trial_id=trial, event_path=events,
                        event_seal_path=seal, agent_trace="", receipt={"execution": {}},
                    )
            self.assertTrue((root / trial).is_dir())
            self.assertTrue(is_complete_receipt(root / trial))
            self.assertEqual((root / trial / "events.jsonl").read_bytes(), b"event\n")

    def test_interrupted_attempt_preserves_available_event_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "results"
            definition = "a" * 64
            trial = "b" * 64
            campaign = _campaign(root, [definition])
            attempt = claim_launch(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial)
            events = start_attempt_events(root, definition, attempt, b"")
            append_event(events, trial_id=trial, sequence=0, kind="agent.started", payload={"attempt": 1})
            original = events.read_bytes()
            record_interrupted_attempt(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial)
            evidence = root / ".campaign/interrupted-events" / definition / "000001.jsonl"
            self.assertEqual(evidence.read_bytes(), original)
            record = read_interrupted_attempts(root, campaign["campaign_id"])[definition][0]
            self.assertEqual(record["events_bytes"], len(original))
            self.assertEqual(record["contract"], "benchmark-interruption-attempt.v2")
            evidence.write_bytes(b"tampered")
            with self.assertRaisesRegex(CampaignAuthorityError, "interruption events changed"):
                read_interrupted_attempts(root, campaign["campaign_id"])

    def test_corrupt_final_bundle_is_not_recorded_as_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "results"
            definition = "a" * 64
            trial = "b" * 64
            campaign = _campaign(root, [definition])
            claim_launch(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial)
            (root / trial).mkdir()
            self.assertEqual(launch_state(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial), "CORRUPT")
            with self.assertRaisesRegex(CampaignAuthorityError, "existing trial bundle"):
                record_interrupted_attempt(results_root=root, campaign=campaign, definition_id=definition, trial_id=trial)
            self.assertEqual(read_interrupted_attempts(root, campaign["campaign_id"]), {})

    def test_status_counts_only_selected_interruptions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "results"
            first, second = "a" * 64, "b" * 64
            campaign = _campaign(root, [first, second])
            claim_launch(results_root=root, campaign=campaign, definition_id=second, trial_id="c" * 64)
            record_interrupted_attempt(results_root=root, campaign=campaign, definition_id=second, trial_id="c" * 64)
            suite = SimpleNamespace(trial_definitions=lambda: [
                {"definition_id": key, "task_id": key, "condition_id": "condition", "trial": 0, "replicate_id": 1}
                for key in (first, second)
            ])
            status = campaign_status(suite=suite, results_root=root, selected_definitions={first})
            self.assertEqual(status["recovered_interruption_attempts"], 0)
            status = campaign_status(suite=suite, results_root=root, selected_definitions={second})
            self.assertEqual(status["recovered_interruption_attempts"], 1)
            self.assertEqual(status["health"]["integrity"], "PASS")

    def test_status_separates_live_claim_from_stale_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary)
            root = store / "results"
            definition = "a" * 64
            campaign = _campaign(root, [definition])
            claim_launch(results_root=root, campaign=campaign, definition_id=definition, trial_id="b" * 64)
            suite = SimpleNamespace(trial_definitions=lambda: [{
                "definition_id": definition, "task_id": "task", "condition_id": "condition",
                "trial": 0, "replicate_id": 1,
            }])
            with exclusive_store(store):
                with active_trial(store, "000001", definition):
                    status = campaign_status(
                        suite=suite, results_root=root, selected_definitions={definition},
                        active_definition=active_definition(store, "000001"),
                    )
                    self.assertEqual(status["running_trials"], 1)
                    self.assertEqual(status["interrupted_trials"], 0)
                    self.assertIsNone(active_definition(store, "000002"))
            status = campaign_status(suite=suite, results_root=root, selected_definitions={definition})
            self.assertEqual(status["interrupted_trials"], 1)


if __name__ == "__main__":
    unittest.main()
