"""Observable contracts for the thin Make benchmark entrypoints."""

from __future__ import annotations

import subprocess
import io
import json
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from benchmarks.__main__ import (
    _assert_saved_run_agents,
    _assert_saved_run_selection,
    _guard_automatic_start,
    _refresh_canonical_report,
    main,
)
from benchmarks.harness.runner import TrialRunResult, TrialRunnerError
from benchmarks.harness.run_store import RunStoreError, SavedRun, exclusive_store
from benchmarks.harness.selection import select_definitions
from benchmarks.harness.suite import load_suite

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
ENV_EXAMPLE = ROOT / ".env.example"
BENCHMARK_LAUNCHER = ROOT / "benchmark"


class BenchmarkMakeEntrypointTests(unittest.TestCase):
    def test_canonical_report_refresh_uses_full_generation_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runs/000001"
            results = root / "results"
            results.mkdir(parents=True)
            report_path = root / "reports/report.json"
            report_path.parent.mkdir(parents=True)
            report_payload = {"run_id": "000001", "expected_trials": 1}
            report_path.write_text(
                json.dumps(report_payload) + "\n",
                encoding="utf-8",
            )
            paths = mock.Mock()
            paths.run_id = "000001"
            paths.root = root
            paths.results = results
            args = mock.Mock()
            config = mock.Mock()
            suite = mock.Mock()
            rows = [{"definition_id": "definition"}]
            final_status = {"complete": True, "qualified": True}

            with (
                mock.patch(
                    "benchmarks.__main__.exclusive_store",
                    return_value=nullcontext(),
                ),
                mock.patch("benchmarks.__main__._same_saved_run_under_lock"),
                mock.patch(
                    "benchmarks.__main__._canonical_campaign_persistence_gap",
                    return_value=None,
                ),
                mock.patch(
                    "benchmarks.__main__.campaign_status",
                    return_value=final_status,
                ),
                mock.patch(
                    "benchmarks.__main__._persist_completed_run_reports",
                    return_value={"report": report_path},
                ) as persist,
            ):
                report, error = _refresh_canonical_report(
                    store_root=Path(temporary),
                    paths=paths,
                    suite=suite,
                    rows=rows,
                    args=args,
                    config=config,
                    runtime_source={"TOKEN": "value"},
                )

            self.assertIsNone(error)
            self.assertEqual(report, report_payload)
            persist.assert_called_once_with(
                args=args,
                config=config,
                suite=suite,
                rows=rows,
                paths=paths,
                runtime_source={"TOKEN": "value"},
                final_status=final_status,
            )

    def test_local_campaign_example_is_durable_and_ignored(self) -> None:
        env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8-sig")
        self.assertIn(
            "BENCHMARK_CAMPAIGN_ROOT=.benchmark-runs/heldout-v1",
            env_example,
        )
        self.assertIn(
            "BENCHMARK_SCORE_OUTPUT_PATH=score.json",
            env_example,
        )
        self.assertIn(".benchmark-runs/", gitignore)
        self.assertNotIn(
            "BENCHMARK_CAMPAIGN_ROOT=/tmp/agentscookbook-heldout-v1",
            env_example,
        )

    def test_native_launcher_owns_benchmark_module_resolution(self) -> None:
        launcher = BENCHMARK_LAUNCHER.read_text(encoding="utf-8")
        self.assertTrue(BENCHMARK_LAUNCHER.stat().st_mode & 0o111)
        self.assertIn('ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)', launcher)
        self.assertIn('cd "$ROOT"', launcher)
        self.assertIn('exec uv run --no-project python -m benchmarks "$@"', launcher)

    def test_make_delegates_configuration_to_cli(self) -> None:
        makefile = MAKEFILE.read_text(encoding="utf-8")
        self.assertNotIn("-include .env", makefile)
        self.assertNotIn("awk -F=", makefile)
        self.assertNotIn("export PYTHONPATH", makefile)
        self.assertNotIn(".venv/bin/python", makefile)
        for target, command in (
            ("benchmark", "run --auto"),
            ("benchmark-check", "check"),
            ("benchmark-doctor", "doctor"),
            ("benchmark-check-all", "preflight"),
            ("benchmark-new", "run --new"),
            ("benchmark-resume", "run --resume"),
            ("benchmark-status", "status"),
            ("benchmark-report", "report"),
            ("benchmark-reports", "reports"),
            ("benchmark-score", "score"),
        ):
            with self.subTest(target=target):
                self.assertIn(f"{target}:\n", makefile)
                self.assertIn(f"./benchmark {command}", makefile)
        self.assertIn('MATRIX ?= heldout', makefile)
        self.assertIn('HARBOR_MATRIX ?= harbor-full', makefile)
        self.assertIn(
            'HARBOR_ABLATION_MATRIX ?= harbor-ablation-full',
            makefile,
        )
        self.assertIn(
            'HARBOR_FIND_ABLATION_MATRIX ?= harbor-find-ablation-full',
            makefile,
        )
        self.assertIn(
            'HARBOR_CHANGE_IMPACT_ABLATION_MATRIX ?= '
            'harbor-change-impact-ablation-full',
            makefile,
        )
        self.assertIn(
            'HARBOR_POST_CHANGE_ABLATION_MATRIX ?= '
            'harbor-post-change-ablation-full',
            makefile,
        )
        self.assertIn(
            'HARBOR_CORRELATE_EVIDENCE_ABLATION_MATRIX ?= '
            'harbor-correlate-evidence-ablation-full',
            makefile,
        )
        self.assertIn(
            'HARBOR_REPOSITORY_DECLARATIONS_ABLATION_MATRIX ?= '
            'harbor-repository-declarations-ablation-full',
            makefile,
        )
        self.assertIn(
            'HARBOR_DEPENDENCY_CODEMAP_ABLATION_MATRIX ?= '
            'harbor-dependency-codemap-ablation-full',
            makefile,
        )
        self.assertIn(
            'HARBOR_VERIFICATION_EXPLANATION_ABLATION_MATRIX ?= '
            'harbor-verification-explanation-ablation-full',
            makefile,
        )
        self.assertIn('--matrix "$(MATRIX)"', makefile)
        self.assertIn("benchmark-harness-explain:\n", makefile)
        self.assertIn(
            './benchmark explain --env-file "$(BENCHMARK_ENV)"',
            makefile,
        )
        self.assertIn('--matrix "$(HARBOR_MATRIX)"', makefile)
        self.assertIn("benchmark-harness-ablation:\n", makefile)
        self.assertIn(
            './benchmark ablation --env-file "$(BENCHMARK_ENV)"',
            makefile,
        )
        self.assertIn('--matrix "$(HARBOR_ABLATION_MATRIX)"', makefile)
        self.assertIn("benchmark-harness-find-ablation:\n", makefile)
        self.assertIn('--matrix "$(HARBOR_FIND_ABLATION_MATRIX)"', makefile)
        self.assertIn(
            "benchmark-harness-change-impact-ablation:\n",
            makefile,
        )
        self.assertIn(
            '--matrix "$(HARBOR_CHANGE_IMPACT_ABLATION_MATRIX)"',
            makefile,
        )
        self.assertIn(
            "benchmark-harness-post-change-ablation:\n",
            makefile,
        )
        self.assertIn(
            '--matrix "$(HARBOR_POST_CHANGE_ABLATION_MATRIX)"',
            makefile,
        )
        self.assertIn(
            "benchmark-harness-correlate-evidence-ablation:\n",
            makefile,
        )
        self.assertIn(
            '--matrix "$(HARBOR_CORRELATE_EVIDENCE_ABLATION_MATRIX)"',
            makefile,
        )
        self.assertIn(
            "benchmark-harness-repository-declarations-ablation:\n",
            makefile,
        )
        self.assertIn(
            '--matrix "$(HARBOR_REPOSITORY_DECLARATIONS_ABLATION_MATRIX)"',
            makefile,
        )
        self.assertIn(
            "benchmark-harness-dependency-codemap-ablation:\n",
            makefile,
        )
        self.assertIn(
            '--matrix "$(HARBOR_DEPENDENCY_CODEMAP_ABLATION_MATRIX)"',
            makefile,
        )
        self.assertIn(
            "benchmark-harness-verification-explanation-ablation:\n",
            makefile,
        )
        self.assertIn(
            '--matrix "$(HARBOR_VERIFICATION_EXPLANATION_ABLATION_MATRIX)"',
            makefile,
        )
        self.assertIn('run --resume --run-id "$(RUN_ID)"', makefile)
        self.assertIn("benchmark-oracle-review:", makefile)
        self.assertIn(
            "./benchmark oracle-review \\\n"
            "\t\t--suite benchmarks/suites/repository-intelligence/heldout-v1 \\\n"
            "\t\t--execute",
            makefile,
        )
        self.assertIn("benchmark-qualify-localization:", makefile)
        self.assertIn(
            "mktemp -d .benchmark-runs/heldout-v1/qualification.XXXXXX",
            makefile,
        )
        self.assertIn('--root "$$qualification_root" --require-qualified', makefile)
        self.assertIn("--subject none --subject hashmarks --subject enola", makefile)
        self.assertIn("--task locate-repository-content-identity", makefile)
        self.assertIn("--task locate-terminal-run-check", makefile)
        self.assertNotIn("release-check:", makefile)
        self.assertIn("BENCHMARK_AGENT=\n", ENV_EXAMPLE.read_text(encoding="utf-8"))

    def test_score_agent_selection_must_match_frozen_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saved = SavedRun("000001", Path(tmp) / "runs/000001")
            with mock.patch(
                "benchmarks.__main__.read_campaign",
                return_value={
                    "selected_definitions": [],
                    "agents": {"opencode-native": {}},
                },
            ):
                with self.assertRaisesRegex(
                    RunStoreError,
                    "agent selection does not match BENCHMARK_AGENT",
                ):
                    _assert_saved_run_agents(saved, ["codex-native"])

    def test_score_dispatches_exact_frozen_definition_selection(self) -> None:
        suite = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        score_script = suite / "score.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = SavedRun("000001", root / "runs/000001")
            output = root / "score.json"
            config = mock.Mock()
            config.runtime_environment.return_value = {}
            manifest = {
                "selected_definitions": ["b" * 64, "a" * 64],
                "agents": {"opencode-native": {}},
            }

            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch("benchmarks.__main__.select_saved_run", return_value=saved),
                mock.patch(
                    "benchmarks.__main__._assert_saved_run_agents",
                    return_value=manifest,
                ),
                mock.patch(
                    "benchmarks.__main__.verify_campaign_suite_authority"
                ) as verify_suite,
                mock.patch("benchmarks.__main__.subprocess.run") as run,
            ):
                run.return_value.returncode = 0
                run.return_value.stdout = (
                    "--results --output --agent --definition-id"
                )
                run.return_value.stderr = ""
                self.assertEqual(
                    main(
                        [
                            "score",
                            "--env-file",
                            str(root / "unused.env"),
                            "--suite",
                            str(suite),
                            "--root",
                            str(root),
                            "--score-script",
                            str(score_script),
                            "--output",
                            str(output),
                            "--agent",
                            "opencode-native",
                        ]
                    ),
                    0,
                )

            verify_suite.assert_called_once()
            self.assertIs(verify_suite.call_args.kwargs["campaign"], manifest)
            invocation = run.call_args.args[0]
            observed = [
                invocation[index + 1]
                for index, value in enumerate(invocation)
                if value == "--definition-id"
            ]
            self.assertEqual(observed, ["a" * 64, "b" * 64])

    def test_canonical_score_refresh_uses_full_generation_persistence(self) -> None:
        suite_path = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        suite = load_suite(suite_path)
        definition = str(suite.trial_definitions()[0]["definition_id"])
        score_script = suite_path / "score.py"
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary)
            run_root = store / "runs/000001"
            (run_root / "results").mkdir(parents=True)
            saved = SavedRun("000001", run_root)
            score_path = run_root / "reports/score.json"
            manifest = {
                "selected_definitions": [definition],
                "agents": {"opencode-native": {}},
            }
            config = mock.Mock()
            config.runtime_environment.return_value = {}

            def resolve(args):
                args.score_script = score_script
                args.output = Path("score.json")
                return config

            with (
                mock.patch("benchmarks.__main__._resolve_config", side_effect=resolve),
                mock.patch("benchmarks.__main__._validate_score_cli"),
                mock.patch("benchmarks.__main__.select_saved_run", return_value=saved),
                mock.patch(
                    "benchmarks.__main__._assert_saved_run_agents",
                    return_value=manifest,
                ),
                mock.patch("benchmarks.__main__.verify_campaign_suite_authority"),
                mock.patch(
                    "benchmarks.__main__.select_scoring_definitions",
                    return_value=[suite.trial_definitions()[0]],
                ),
                mock.patch(
                    "benchmarks.__main__.campaign_status",
                    return_value={"complete": True, "qualified": True},
                ),
                mock.patch(
                    "benchmarks.__main__._persist_completed_run_reports",
                    return_value={"score": score_path},
                ) as persist,
            ):
                self.assertEqual(
                    main(
                        [
                            "score",
                            "--env-file",
                            str(store / "unused.env"),
                            "--suite",
                            str(suite_path),
                            "--root",
                            str(store),
                            "--agent",
                            "opencode-native",
                        ]
                    ),
                    0,
                )

            persist.assert_called_once()
            self.assertEqual(
                persist.call_args.kwargs["score_contract"],
                (score_script.resolve(), Path("score.json")),
            )

    def test_tool_probe_smoke_gate_requires_generation_bound_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary)
            run_root = store / "runs/000001"
            reports = run_root / "reports"
            reports.mkdir(parents=True)
            score_path = reports / "score.json"
            score = {
                "schema": "agents-cookbook-tool-probe-score.v3",
                "subject": "hashmarks",
                "required_tool": "hashmarks_task_evidence",
                "expected_trials": 0,
                "observed_trials": 0,
                "required_tool_results": [],
            }
            score_path.write_text(json.dumps(score) + "\n", encoding="utf-8")
            saved = SavedRun("000001", run_root)

            with (
                mock.patch(
                    "benchmarks.__main__.select_saved_run",
                    return_value=saved,
                ),
                mock.patch(
                    "benchmarks.__main__.verified_score_path",
                    return_value=score_path,
                ) as verified,
                mock.patch("benchmarks.__main__.smoke_gate") as gate,
            ):
                self.assertEqual(
                    main(
                        [
                            "tool-probe-smoke-gate",
                            "--root",
                            str(store),
                            "--subject",
                            "hashmarks",
                        ]
                    ),
                    0,
                )

            verified.assert_called_once_with(
                run_root=run_root,
                expected_run_id="000001",
            )
            gate.assert_called_once_with(score, subject="hashmarks")

    def test_manual_score_cannot_race_active_campaign_owner(self) -> None:
        suite = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        score_script = suite / "score.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = SavedRun("000001", root / "runs/000001")
            config = mock.Mock()
            config.runtime_environment.return_value = {}
            with (
                exclusive_store(root),
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch("benchmarks.__main__.select_saved_run", return_value=saved),
                mock.patch("benchmarks.__main__.subprocess.run") as run,
            ):
                run.return_value.returncode = 0
                run.return_value.stdout = (
                    "--results --output --agent --definition-id"
                )
                run.return_value.stderr = ""
                with self.assertRaisesRegex(
                    SystemExit,
                    "another benchmark command",
                ):
                    main(
                        [
                            "score",
                            "--env-file",
                            str(root / "unused.env"),
                            "--suite",
                            str(suite),
                            "--root",
                            str(root),
                            "--score-script",
                            str(score_script),
                            "--output",
                            str(root / "score.json"),
                            "--agent",
                            "opencode-native",
                        ]
                    )
            self.assertEqual(run.call_count, 1)

    def test_resume_selection_must_match_frozen_agents_before_admission(self) -> None:
        suite = load_suite(
            ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        )
        rows = select_definitions(
            suite,
            tasks=("locate-prefix-path-enumerator",),
            agents=("opencode-native",),
        )
        with tempfile.TemporaryDirectory() as tmp:
            saved = SavedRun("000001", Path(tmp) / "runs/000001")
            with mock.patch(
                "benchmarks.__main__.read_campaign",
                return_value={
                    "selected_definitions": ["different"],
                    "agents": {"codex-native": {}},
                },
            ):
                with self.assertRaisesRegex(
                    RunStoreError,
                    "agent selection does not match BENCHMARK_AGENT",
                ):
                    _assert_saved_run_selection(
                        saved,
                        suite=suite,
                        rows=rows,
                    )

    def test_plain_benchmark_requires_explicit_choice_for_unfinished_match(self) -> None:
        suite = load_suite(
            ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        )
        rows = select_definitions(
            suite,
            tasks=("locate-prefix-path-enumerator",),
            agents=("opencode-native",),
        )
        selected = [str(row["definition_id"]) for row in rows]
        with tempfile.TemporaryDirectory() as tmp:
            saved = SavedRun("000001", Path(tmp) / "runs/000001")
            with (
                mock.patch(
                    "benchmarks.__main__.list_saved_runs",
                    return_value=[saved],
                ),
                mock.patch(
                    "benchmarks.__main__.read_campaign",
                    return_value={
                        "selected_definitions": selected,
                        "agents": {"opencode-native": {}},
                    },
                ),
                mock.patch(
                    "benchmarks.__main__.campaign_status",
                    return_value={
                        "complete": False,
                        "complete_trials": 17,
                        "expected_trials": len(rows),
                        "pending_trials": len(rows) - 17,
                        "interrupted_trials": 0,
                    },
                ),
            ):
                with self.assertRaisesRegex(
                    RunStoreError,
                    "choose explicitly: make benchmark-resume or make benchmark-new",
                ):
                    _guard_automatic_start(Path(tmp), suite=suite, rows=rows)

    def test_plain_benchmark_starts_new_when_latest_selection_differs(self) -> None:
        suite = load_suite(
            ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        )
        rows = select_definitions(
            suite,
            tasks=("locate-prefix-path-enumerator",),
            agents=("opencode-native",),
        )
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(io.StringIO()):
            saved = SavedRun("000001", Path(tmp) / "runs/000001")
            with (
                mock.patch(
                    "benchmarks.__main__.list_saved_runs",
                    return_value=[saved],
                ),
                mock.patch(
                    "benchmarks.__main__.read_campaign",
                    return_value={
                        "selected_definitions": ["different"],
                        "agents": {"codex-native": {}},
                    },
                ),
            ):
                _guard_automatic_start(Path(tmp), suite=suite, rows=rows)

    def test_make_dry_run_does_not_expand_configuration_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "BENCHMARK_AGENT=codex-native,opencode-native\n"
                "BENCHMARK_SUITE_PATH=private-suite\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ("make", "-n", "-f", str(MAKEFILE), "benchmark"),
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--env-file ".env"', result.stdout)
        self.assertIn('--matrix "heldout"', result.stdout)
        self.assertNotIn("private-suite", result.stdout)
        self.assertNotIn("codex-native", result.stdout)

    def test_benchmark_new_spacing_typo_fails_before_recipe_expansion(self) -> None:
        result = subprocess.run(
            ("make", "-f", str(MAKEFILE), "benchmark", "-new"),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("make benchmark-new", result.stderr)
        self.assertNotIn("./benchmark run --auto", result.stdout)

    def test_unknown_benchmark_target_fails_before_work(self) -> None:
        result = subprocess.run(
            ("make", "-f", str(MAKEFILE), "benchmark-neww"),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown benchmark target 'benchmark-neww'", result.stderr)
        self.assertIn("BENCHMARK_TARGETS", result.stderr)
        self.assertNotIn("./benchmark", result.stdout)

    def test_missing_env_file_fails_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / ".env"
            with self.assertRaisesRegex(
                SystemExit, "benchmark env file does not exist"
            ):
                main(["run", "--resume", "--run-id", "000001", "--env-file", str(file)])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_status_and_report_are_persisted_in_shareable_reports_dir(self) -> None:
        suite_path = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "runs/000001"
            results = run_root / "results"
            results.mkdir(parents=True)
            (results / "receipt").write_text("present", encoding="utf-8")
            saved = SavedRun("000001", run_root)

            status_payload = {
                "rows": [],
                "complete_trials": 108,
                "pending_trials": 0,
                "interrupted_trials": 0,
                "conflicting_trials": [],
                "corrupt_bundles": [],
                "foreign_bundles": [],
                "qualified": True,
            }
            with (
                mock.patch("benchmarks.__main__.select_saved_run", return_value=saved),
                mock.patch(
                    "benchmarks.__main__.campaign_status",
                    return_value=dict(status_payload),
                ),
                mock.patch(
                    "benchmarks.__main__._canonical_campaign_persistence_gap",
                    return_value=None,
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(["status", "--suite", str(suite_path), "--root", str(root)]),
                    0,
                )

            status_file = run_root / "reports/status.json"
            self.assertTrue(status_file.is_file())
            stored_status = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(stored_status["run_id"], "000001")
            self.assertEqual(
                stored_status["paths"]["reports"],
                str(run_root / "reports"),
            )

            def persist_generation(**_kwargs):
                reports = run_root / "reports"
                reports.mkdir(parents=True, exist_ok=True)
                report_payload = {
                    "schema": {"version": 7},
                    "expected_trials": 108,
                    "run_id": "000001",
                    "reports_dir": str(reports),
                }
                trace_payload = {
                    "schema": "agents-cookbook-trace-diagnostics.v6",
                    "trials": [],
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
                        },
                        "repository_intelligence_search_efficiency": {
                            "state": "observed",
                            "claim_scope": "descriptive-behavioral-only",
                            "correctness_joined": False,
                            "subjects": [],
                        },
                        "repository_intelligence_evidence_to_action": {
                            "state": "observed",
                            "claim_scope": "descriptive-behavioral-only",
                            "oracle_relative": False,
                            "correctness_joined": False,
                            "subjects": [],
                        },
                    },
                }
                decision_payload = {
                    "schema": "agents-cookbook-benchmark-decision-evidence.v8",
                    "run_id": "000001",
                    "authority": {"derived_only": True},
                    "repository_intelligence_quality": trace_payload["summary"][
                        "repository_intelligence_quality"
                    ],
                    "repository_intelligence_search_efficiency": trace_payload[
                        "summary"
                    ]["repository_intelligence_search_efficiency"],
                    "repository_intelligence_evidence_to_action": trace_payload[
                        "summary"
                    ]["repository_intelligence_evidence_to_action"],
                }
                report_path = reports / "report.json"
                trace_path = reports / "trace-diagnostics.json"
                decision_path = reports / "decision-evidence.json"
                report_path.write_text(
                    json.dumps(report_payload) + "\n",
                    encoding="utf-8",
                )
                trace_path.write_text(
                    json.dumps(trace_payload) + "\n",
                    encoding="utf-8",
                )
                decision_path.write_text(
                    json.dumps(decision_payload) + "\n",
                    encoding="utf-8",
                )
                return {
                    "report": report_path,
                    "trace_diagnostics": trace_path,
                    "decision_evidence": decision_path,
                }

            with (
                mock.patch("benchmarks.__main__.select_saved_run", return_value=saved),
                mock.patch(
                    "benchmarks.__main__.build_report",
                    return_value={"schema": {"version": 7}, "expected_trials": 108},
                ),
                mock.patch(
                    "benchmarks.__main__.build_trace_diagnostics",
                    return_value={
                        "schema": "agents-cookbook-trace-diagnostics.v6",
                        "trials": [],
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
                            },
                            "repository_intelligence_search_efficiency": {
                                "state": "observed",
                                "claim_scope": "descriptive-behavioral-only",
                                "correctness_joined": False,
                                "subjects": [
                                    {
                                        "subject_id": "hashmarks",
                                        "evidence_observed_trials": 1,
                                    }
                                ],
                            },
                            "repository_intelligence_evidence_to_action": {
                                "state": "observed",
                                "claim_scope": "descriptive-behavioral-only",
                                "oracle_relative": False,
                                "correctness_joined": False,
                                "subjects": [
                                    {
                                        "subject_id": "hashmarks",
                                        "candidate_evidence_segments": 1,
                                        "candidate_followed_segments": 1,
                                    }
                                ],
                            },
                        },
                    },
                ),
                mock.patch(
                    "benchmarks.__main__._canonical_campaign_persistence_gap",
                    return_value=None,
                ),
                mock.patch(
                    "benchmarks.__main__.campaign_status",
                    return_value={**status_payload, "complete": True},
                ),
                mock.patch(
                    "benchmarks.__main__._persist_completed_run_reports",
                    side_effect=persist_generation,
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main([
                        "report",
                        "--suite",
                        str(suite_path),
                        "--root",
                        str(root),
                        "--agent",
                        "opencode-native",
                    ]),
                    0,
                )

            report_file = run_root / "reports/report.json"
            self.assertTrue(report_file.is_file())
            stored_report = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(stored_report["run_id"], "000001")
            self.assertEqual(
                stored_report["reports_dir"],
                str(run_root / "reports"),
            )
            decision_file = run_root / "reports/decision-evidence.json"
            self.assertTrue(decision_file.is_file())
            stored_decision = json.loads(
                decision_file.read_text(encoding="utf-8")
            )
            self.assertEqual(
                stored_decision["schema"],
                "agents-cookbook-benchmark-decision-evidence.v8",
            )
            self.assertEqual(stored_decision["run_id"], "000001")
            self.assertTrue(stored_decision["authority"]["derived_only"])
            self.assertEqual(
                stored_decision["repository_intelligence_quality"]["state"],
                "observed",
            )
            self.assertEqual(
                stored_decision[
                    "repository_intelligence_search_efficiency"
                ]["state"],
                "observed",
            )
            self.assertFalse(
                stored_decision[
                    "repository_intelligence_search_efficiency"
                ]["correctness_joined"]
            )
            self.assertEqual(
                stored_decision[
                    "repository_intelligence_evidence_to_action"
                ]["state"],
                "observed",
            )
            self.assertFalse(
                stored_decision[
                    "repository_intelligence_evidence_to_action"
                ]["oracle_relative"]
            )
            self.assertEqual(
                stored_decision["repository_intelligence_quality"]["subjects"][0][
                    "calls"
                ],
                1,
            )
            trace_file = run_root / "reports/trace-diagnostics.json"
            self.assertTrue(trace_file.is_file())

    def test_active_campaign_keeps_status_and_report_persistence_inspection_only(
        self,
    ) -> None:
        suite_path = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "runs/000001"
            results = run_root / "results"
            results.mkdir(parents=True)
            (results / "receipt").write_text("present", encoding="utf-8")
            reports = run_root / "reports"
            reports.mkdir()
            status_path = reports / "status.json"
            report_path = reports / "report.json"
            status_path.write_text("newer status\n", encoding="utf-8")
            report_path.write_text("newer report\n", encoding="utf-8")
            saved = SavedRun("000001", run_root)
            status_payload = {
                "rows": [],
                "complete_trials": 0,
                "pending_trials": 1,
                "interrupted_trials": 0,
                "conflicting_trials": [],
                "corrupt_bundles": [],
                "foreign_bundles": [],
                "qualified": False,
            }

            with exclusive_store(root):
                with (
                    mock.patch(
                        "benchmarks.__main__.select_saved_run",
                        return_value=saved,
                    ),
                    mock.patch(
                        "benchmarks.__main__.campaign_status",
                        return_value=dict(status_payload),
                    ),
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(io.StringIO()) as status_stderr,
                ):
                    self.assertEqual(
                        main(
                            [
                                "status",
                                "--suite",
                                str(suite_path),
                                "--root",
                                str(root),
                            ]
                        ),
                        0,
                    )
                self.assertIn("inspection only", status_stderr.getvalue())
                self.assertEqual(
                    status_path.read_text(encoding="utf-8"),
                    "newer status\n",
                )

                with (
                    mock.patch(
                        "benchmarks.__main__.select_saved_run",
                        return_value=saved,
                    ),
                    mock.patch(
                        "benchmarks.__main__.build_report",
                        return_value={
                            "schema": {"version": 12},
                            "expected_trials": 1,
                        },
                    ),
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(io.StringIO()) as report_stderr,
                ):
                    self.assertEqual(
                        main(
                            [
                                "report",
                                "--suite",
                                str(suite_path),
                                "--root",
                                str(root),
                                "--agent",
                                "opencode-native",
                            ]
                        ),
                        0,
                    )
                self.assertIn("inspection only", report_stderr.getvalue())
                self.assertEqual(
                    report_path.read_text(encoding="utf-8"),
                    "newer report\n",
                )

    def test_status_can_require_qualified_campaign(self) -> None:
        suite = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            args = ["status", "--suite", str(suite), "--results", tmp]
            self.assertEqual(main(args), 0)
            self.assertEqual(main([*args, "--require-qualified"]), 2)

    def test_run_keeps_stdout_json_and_prints_verified_summary(self) -> None:
        suite_path = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        suite = load_suite(suite_path)
        rows = select_definitions(
            suite,
            tasks=("locate-prefix-path-enumerator",),
            agents=("opencode-native",),
            condition="hashmarks-opencode-native",
        )
        initial = {
            "rows": [
                {"definition_id": row["definition_id"], "state": "PENDING"}
                for row in rows
            ],
            "complete_trials": 0,
            "pending_trials": len(rows),
            "interrupted_trials": 0,
            "qualified": False,
            "outcomes": {},
        }
        final = {
            **initial,
            "complete_trials": len(rows),
            "pending_trials": 0,
            "qualified": True,
            "outcomes": {"PASS": len(rows)},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = mock.Mock()
            config.runtime_environment.return_value = {}

            def fake_run(**kwargs):
                row = next(item for item in rows if item["trial"] == kwargs["trial_index"])
                result_dir = root / "results" / (str(row["trial"]) * 64)
                result_dir.mkdir(parents=True)
                (result_dir / "result.json").write_text(
                    json.dumps({"definition_id": row["definition_id"], "status": "PASS"}),
                    encoding="utf-8",
                )
                kwargs["on_progress"]("publication")
                return TrialRunResult(
                    trial_id=result_dir.name,
                    definition_id=str(row["definition_id"]),
                    status="PASS",
                    result_dir=result_dir,
                    reused=False,
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch(
                    "benchmarks.__main__._validate_reporting_contract",
                    return_value=(suite_path / "score.py", Path("score.json")),
                ),
                mock.patch("benchmarks.__main__.select_saved_run", return_value=SavedRun("000001", root)),
                mock.patch("benchmarks.__main__._assert_saved_run_selection"),
                mock.patch("benchmarks.__main__.verify_saved_campaign", return_value={"campaign_id": "c" * 64}),
                mock.patch("benchmarks.__main__.campaign_status", side_effect=[initial, final]),
                mock.patch("benchmarks.__main__.run_trial", side_effect=fake_run),
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
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = main([
                    "run", "--resume", "--run-id", "000001", "--env-file", str(root / "unused.env"),
                    "--suite", str(suite_path), "--root", str(root),
                    "--harness-root", str(ROOT), "--task", "locate-prefix-path-enumerator",
                    "--agent", "opencode-native", "--condition", "hashmarks-opencode-native",
                ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(json.loads(stdout.getvalue())), 3)
        self.assertIn("RUN SUMMARY processed 3/3 | verified 3/3", stderr.getvalue())
        self.assertIn("qualified True", stderr.getvalue())
        self.assertIn("REPORTS saved", stderr.getvalue())
        self.assertIn(
            f"trace-diagnostics {root / 'reports/trace-diagnostics.json'}",
            stderr.getvalue(),
        )
        persist_reports.assert_called_once()

    def test_completed_nonqualified_run_is_not_a_shell_failure(self) -> None:
        suite_path = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        suite = load_suite(suite_path)
        rows = select_definitions(
            suite,
            tasks=("locate-prefix-path-enumerator",),
            agents=("opencode-native",),
            condition="hashmarks-opencode-native",
        )
        initial = {
            "rows": [
                {"definition_id": row["definition_id"], "state": "PENDING"}
                for row in rows
            ],
            "complete_trials": 0,
            "pending_trials": len(rows),
            "interrupted_trials": 0,
            "qualified": False,
            "outcomes": {},
        }
        final = {
            **initial,
            "complete_trials": len(rows),
            "pending_trials": 0,
            "unresolved_outcome_trials": 1,
            "qualified": False,
            "outcomes": {"PASS": len(rows) - 1, "INCOMPLETE": 1},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = mock.Mock()
            config.runtime_environment.return_value = {}

            def fake_run(**kwargs):
                row = next(item for item in rows if item["trial"] == kwargs["trial_index"])
                status = "INCOMPLETE" if row["trial"] == 1 else "PASS"
                result_dir = root / "results" / (str(row["trial"]) * 64)
                result_dir.mkdir(parents=True)
                (result_dir / "result.json").write_text(
                    json.dumps({"definition_id": row["definition_id"], "status": status}),
                    encoding="utf-8",
                )
                kwargs["on_progress"]("publication")
                return TrialRunResult(
                    trial_id=result_dir.name,
                    definition_id=str(row["definition_id"]),
                    status=status,
                    result_dir=result_dir,
                    reused=False,
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch(
                    "benchmarks.__main__._validate_reporting_contract",
                    return_value=(suite_path / "score.py", Path("score.json")),
                ),
                mock.patch(
                    "benchmarks.__main__.select_saved_run",
                    return_value=SavedRun("000001", root),
                ),
                mock.patch("benchmarks.__main__._assert_saved_run_selection"),
                mock.patch(
                    "benchmarks.__main__.verify_saved_campaign",
                    return_value={"campaign_id": "c" * 64},
                ),
                mock.patch(
                    "benchmarks.__main__.campaign_status",
                    side_effect=[initial, final],
                ),
                mock.patch("benchmarks.__main__.run_trial", side_effect=fake_run),
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
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = main([
                    "run", "--resume", "--run-id", "000001", "--env-file", str(root / "unused.env"),
                    "--suite", str(suite_path), "--root", str(root),
                    "--harness-root", str(ROOT), "--task", "locate-prefix-path-enumerator",
                    "--agent", "opencode-native", "--condition", "hashmarks-opencode-native",
                ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(json.loads(stdout.getvalue())), 3)
        self.assertIn("qualified False", stderr.getvalue())
        self.assertIn("NOT QUALIFIED: non-outcome receipts 1", stderr.getvalue())
        self.assertIn("REPORTS saved", stderr.getvalue())
        self.assertIn(
            "Next: inspect persisted qualification and diagnosis",
            stderr.getvalue(),
        )
        self.assertIn(
            f"status {root / 'reports/status.json'}",
            stderr.getvalue(),
        )
        self.assertIn(
            f"decision {root / 'reports/decision-evidence.json'}",
            stderr.getvalue(),
        )
        self.assertIn(
            f"trace-diagnostics {root / 'reports/trace-diagnostics.json'}",
            stderr.getvalue(),
        )
        self.assertNotIn("with the same selectors", stderr.getvalue())
        persist_reports.assert_called_once()

    def test_run_abort_reports_active_trial_without_stdout_result(self) -> None:
        suite_path = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        suite = load_suite(suite_path)
        rows = select_definitions(
            suite,
            tasks=("locate-prefix-path-enumerator",),
            agents=("opencode-native",),
            condition="hashmarks-opencode-native",
        )
        status = {
            "rows": [{"definition_id": row["definition_id"], "state": "PENDING"} for row in rows],
            "complete_trials": 0,
            "pending_trials": len(rows),
            "interrupted_trials": 0,
            "qualified": False,
            "outcomes": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = mock.Mock()
            config.runtime_environment.return_value = {}
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch("benchmarks.__main__._resolve_config", return_value=config),
                mock.patch(
                    "benchmarks.__main__._validate_reporting_contract",
                    return_value=(suite_path / "score.py", Path("score.json")),
                ),
                mock.patch("benchmarks.__main__.select_saved_run", return_value=SavedRun("000001", root)),
                mock.patch("benchmarks.__main__._assert_saved_run_selection"),
                mock.patch("benchmarks.__main__.verify_saved_campaign", return_value={"campaign_id": "c" * 64}),
                mock.patch("benchmarks.__main__.campaign_status", side_effect=[status, status]),
                mock.patch("benchmarks.__main__.run_trial", side_effect=TrialRunnerError("launch claim changed")),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = main([
                    "run", "--resume", "--run-id", "000001", "--env-file", str(root / "unused.env"),
                    "--suite", str(suite_path), "--root", str(root),
                    "--harness-root", str(ROOT), "--task", "locate-prefix-path-enumerator",
                    "--agent", "opencode-native", "--condition", "hashmarks-opencode-native",
                ])
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ABORT locate-prefix-path-enumerator", stderr.getvalue())
        self.assertIn("stage admission", stderr.getvalue())
        self.assertIn("CAMPAIGN verified 0/3", stderr.getvalue())

    def test_regrade_score_dispatches_without_runtime_config(self) -> None:
        suite = ROOT / "benchmarks/suites/repository-intelligence/heldout-v1"
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("benchmarks.__main__.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            self.assertEqual(
                main(
                    [
                        "regrade-score",
                        "--suite",
                        str(suite),
                        "--source-results",
                        tmp,
                        "--agent",
                        "opencode-native",
                        "--output",
                        str(Path(tmp).parent / "score.json"),
                    ]
                ),
                0,
            )
            self.assertIn("--regrade-source-results", run.call_args.args[0])

    def test_missing_agent_fails_even_with_explicit_cli_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / ".env"
            file.write_text(
                "BENCHMARK_SUITE_PATH=benchmarks/suites/repository-intelligence/heldout-v1\n"
                "BENCHMARK_CAMPAIGN_ROOT=/tmp/unused-campaign\n"
                "BENCHMARK_HARNESS_REPO_ROOT=.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "BENCHMARK_AGENT"):
                main(["run", "--resume", "--run-id", "000001", "--env-file", str(file), "--agent", "codex-native"])
