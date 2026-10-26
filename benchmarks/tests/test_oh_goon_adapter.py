"""Boundary regressions for the public Oh-Goon adapter; no model or native stores."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.adapters.oh_goon import OhGoonAgent, _public_path
from benchmarks.adapters.registry import AdapterConfigurationError, build_agent
from benchmarks.adapters.subjects import NoneSubject
from benchmarks.harness.admission import _agent_authority, admit_trial
from benchmarks.harness.campaign_authority import _global_agent_authority
from benchmarks.harness.contamination import classify_contamination
from benchmarks.harness.model import Observation, TrialContext
from benchmarks.harness.readiness import ReadinessCheck, check_runtime_readiness
from benchmarks.harness.runtime_authority import required_runtime_authority
from benchmarks.harness.suite import load_suite
from benchmarks.harness.workspace import (
    WorkspaceError,
    initialize_git_subdirectory,
    isolated_environment,
    snapshot,
)

SUITE = (
    Path(__file__).resolve().parents[1]
    / "suites/repository-intelligence/oh-goon-hashmarks-release-pair-v1"
)


def completed(value, return_code=0):
    result = SimpleNamespace(
        return_code=return_code,
        executable_missing=False,
        timed_out=False,
        stdout_truncated=False,
        stderr_truncated=False,
        stdout=json.dumps(value).encode(),
        stderr=b"",
        elapsed_ms=7,
    )
    result.metrics = lambda: {
        "return_code": return_code,
        "timed_out": False,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "executable_missing": False,
    }
    return result


class OhGoonAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="evalshot-oh-goon-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        (self.workspace / "candidate").mkdir(parents=True)
        self.control = self.root / "control"
        environment = isolated_environment(self.control)
        self.home = self.root / "native-private"
        self.home.mkdir()
        environment["BENCHMARK_OH_GOON_NATIVE_HOME"] = str(self.home)
        self.source = self.root / "oh-source"
        (self.source / "backend/src/oh_goon").mkdir(parents=True)
        (self.source / "backend/src/oh_goon/__init__.py").write_text(
            "# public source\n"
        )
        for name in ("pyproject.toml", "uv.lock"):
            (self.source / "backend" / name).write_text("# locked public source\n")

        def git(*argv):
            return (
                subprocess.check_output(
                    ["git", "-C", str(self.source), *argv], stderr=subprocess.DEVNULL
                )
                .decode()
                .strip()
            )

        git("init", "-q")
        git("add", ".")
        git(
            "-c",
            "user.name=EvalShot",
            "-c",
            "user.email=evalshot@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )
        self.receipt = {
            "schema": "evalshot.oh-goon-runtime-supply/v1",
            "oh_goon_source_root": str(self.source),
            "oh_goon_source_commit": git("rev-parse", "HEAD"),
            "oh_goon_source_tree": git("rev-parse", "HEAD^{tree}"),
            "pyproject_sha256": hashlib.sha256(
                (self.source / "backend/pyproject.toml").read_bytes()
            ).hexdigest(),
            "uv_lock_sha256": hashlib.sha256(
                (self.source / "backend/uv.lock").read_bytes()
            ).hexdigest(),
            "hashmarks_version": "0.32.1",
            "hashmarks_implementation_identity": "sha256:producer",
            "dependency_overrides": [],
        }
        self.config = self.root / "public-providers.json"
        self.config.write_text("{}")
        self.receipt_path = self.root / "runtime.json"
        self.receipt_path.write_text(json.dumps(self.receipt))
        for name, value in (
            ("PYTHON", sys.executable),
            ("CONFIG", str(self.config)),
            ("RECEIPT", str(self.receipt_path)),
        ):
            environment["BENCHMARK_OH_GOON_CANDIDATE_" + name] = value
        self.context = TrialContext(self.workspace, self.control, environment)
        self.agent = OhGoonAgent(
            "candidate", "0.32.1", "gpt-6-luna", "low", "benchmark-service", "candidate"
        )
        self.native = self.root / "public-codex/bin/codex"
        self.native.parent.mkdir(parents=True)
        self.native.write_text('#!/bin/sh\necho "codex-cli 0.164.0"\n')
        self.native.chmod(0o755)
        native_sha = hashlib.sha256(self.native.read_bytes()).hexdigest()
        self.loaded = {
            "oh_goon_version": "fixture",
            "oh_goon_module": str(self.source / "backend/src/oh_goon/__init__.py"),
            "hashmarks_version": "0.32.1",
            "hashmarks_implementation_identity": "sha256:producer",
            "model": "gpt-6-luna",
            "reasoning_effort": "low",
            "native_home": str(self.home),
            "native_executable": str(self.native),
            "native_executable_sha256": native_sha,
            "native_runtime_roots": [str(self.native.parent.parent)],
            "workspace_git_root": "verified",
        }
        self.run = {
            "run": {"fleet_run_id": "fleet-original", "status": "COMPLETED"},
            "outcome": "SUCCESS",
        }
        self.handoff = {
            "fleet_run_id": "fleet-original",
            "status": "COMPLETED",
            "outcome": "SUCCESS",
            "source_admissions": [{"source_authority_id": "source-original"}],
        }
        self.tape = {
            "fleet_run_id": "fleet-original",
            "repositories": [
                {
                    "steps": [
                        {
                            "step_execution_id": "step-original",
                            "status": "PASSED",
                            "exit_code": 0,
                            "native_agent_observations": [
                                {
                                    "agent": "codex",
                                    "profile": "benchmark-service",
                                    "settled": True,
                                    "failed": False,
                                    "input_identity": "input-original",
                                    "occurrence": "agents-session.native-agent",
                                    "implementation_identity": "codex:sha256:"
                                    + native_sha,
                                    "execution": {
                                        "native_version": "0.164.0",
                                        "authority": "ORIGINAL_STEP_EXECUTION",
                                        "native_state_separation": "verified",
                                        "process_cleanup": "verified",
                                    },
                                }
                            ],
                        }
                    ]
                }
            ],
        }

    def command(self, context, argv, *, timeout):
        if "-c" in argv and "-I" in argv:
            return completed(self.loaded)
        if "ai" in argv:
            self.calls.append(argv)
            return completed(self.run, self.run_return_code)
        if "handoff" in argv:
            return completed(self.handoff)
        if "tape" in argv:
            if self.after_execution:
                self.after_execution()
            return completed(self.tape)
        # Real Git, owned public fixture, no inference or native stores.
        return self.original_command(context, argv, timeout=timeout)

    def execute(self, *, after_execution=None, run_return_code=0):
        self.calls = []
        self.after_execution = after_execution
        self.run_return_code = run_return_code
        self.original_command = self.agent._command
        with patch.object(OhGoonAgent, "_command", side_effect=self.command):
            prepared = self.agent.prepare(self.context, NoneSubject())
            observed = self.agent.run(self.context, "Frozen task", NoneSubject())
        return prepared, observed

    def parent_workspace(self, name):
        root = self.root / name
        root.mkdir()
        (root / "README.md").write_text("Historical parent source.\n")
        environment = self.context.environment

        def git(*arguments):
            return (
                subprocess.check_output(
                    ["git", "-C", str(root), *arguments],
                    env=environment,
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )

        git("init", "-q")
        git("add", "README.md")
        git(
            "-c",
            "user.name=EvalShot",
            "-c",
            "user.email=evalshot@example.invalid",
            "commit",
            "-qm",
            "parent",
        )
        candidate = root / "candidate"
        candidate.mkdir()
        (candidate / "ranges.py").write_text(
            "def merge_ranges(ranges): return ranges\n"
        )
        return root, candidate, git("rev-parse", "HEAD")

    def test_candidate_git_root_preserves_parent_and_accounts_for_metadata(self):
        parent, candidate, head = self.parent_workspace("historical")
        self.assertEqual(
            Path(
                subprocess.check_output(
                    ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
                    env=self.context.environment,
                )
                .decode()
                .strip()
            ),
            parent,
        )
        initialize_git_subdirectory(
            workspace=parent,
            subdirectory="candidate",
            control_root=self.control,
            environment=self.context.environment,
        )
        self.assertEqual(
            Path(
                subprocess.check_output(
                    ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
                    env=self.context.environment,
                )
                .decode()
                .strip()
            ),
            candidate,
        )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(parent), "rev-parse", "HEAD"],
                env=self.context.environment,
            )
            .decode()
            .strip(),
            head,
        )
        admitted = snapshot(parent)
        self.assertIn("candidate/.git/HEAD", admitted)
        self.assertIn("candidate/.git/config", admitted)
        config = candidate / ".git/config"
        config.write_text(config.read_text() + "\n# unauthorized metadata change\n")
        changed = classify_contamination(
            before=admitted,
            after=snapshot(parent),
            allowed_change_globs=("candidate/ranges.py",),
            allowed_generated_globs=(),
        )
        self.assertTrue(changed["contaminated"])
        self.assertIn("candidate/.git/config", changed["unexpected"]["changed"])

    def test_paired_candidate_git_initialization_has_identical_snapshot_bytes(self):
        observed = []
        for arm in ("baseline-git", "candidate-git"):
            parent, _, _ = self.parent_workspace(arm)
            control = self.root / (arm + "-control")
            environment = isolated_environment(control)
            initialize_git_subdirectory(
                workspace=parent,
                subdirectory="candidate",
                control_root=control,
                environment=environment,
            )
            observed.append(snapshot(parent))
        self.assertEqual(observed[0], observed[1])

    def test_candidate_git_initialization_refuses_escape_before_native_effect(self):
        parent, candidate, _ = self.parent_workspace("escape-parent")
        outside = self.root / "outside"
        outside.mkdir()
        (parent / "alias").symlink_to(outside, target_is_directory=True)
        for subdirectory in ("../outside", "alias"):
            with (
                self.subTest(subdirectory=subdirectory),
                patch("benchmarks.harness.workspace._run_git") as command,
            ):
                with self.assertRaises(WorkspaceError):
                    initialize_git_subdirectory(
                        workspace=parent,
                        subdirectory=subdirectory,
                        control_root=self.control,
                        environment=self.context.environment,
                    )
                command.assert_not_called()
        (candidate / ".git").write_text("untrusted existing metadata")
        with patch("benchmarks.harness.workspace._run_git") as command:
            with self.assertRaises(WorkspaceError):
                initialize_git_subdirectory(
                    workspace=parent,
                    subdirectory="candidate",
                    control_root=self.control,
                    environment=self.context.environment,
                )
            command.assert_not_called()

    def test_trial_initializes_candidate_git_before_admitted_source_freeze(self):
        suite = load_suite(SUITE)
        task = copy.deepcopy(suite.tasks["ranges"])
        task["repository"] = {
            "url": str(self.source),
            "commit": self.receipt["oh_goon_source_commit"],
            "tree": self.receipt["oh_goon_source_tree"],
        }
        suite = dataclasses.replace(suite, tasks={"ranges": task})
        with (
            patch.object(
                OhGoonAgent,
                "prepare",
                return_value=Observation({"available": True}, ""),
            ),
            admit_trial(
                suite=suite,
                task_id="ranges",
                condition_id="candidate-hashmarks-0.32.1",
                trial_index=0,
                harness_root=self.source,
                cache_root=self.root / "cache",
                work_root=self.root / "admission-work",
                local_source=self.source,
                source=self.context.environment,
            ) as admission,
        ):
            candidate = admission.context.workspace / "candidate"
            observed = (
                subprocess.check_output(
                    ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
                    env=admission.context.environment,
                )
                .decode()
                .strip()
            )
            self.assertEqual(Path(observed), candidate)
            self.assertIn("candidate/.git/config", admission.admitted_state)
            self.assertFalse(admission.prework_contamination()["contaminated"])
            self.assertEqual(admission.initial_outcome(), (None, None))

    @unittest.skipUnless(
        os.environ.get("BENCHMARK_OH_GOON_CANDIDATE_PYTHON"),
        "select the public Oh-Goon environment for installed CLI-selection proof",
    )
    def test_installed_oh_cli_selection_admits_only_the_initialized_candidate_root(
        self,
    ):
        parent, candidate, _ = self.parent_workspace("installed-selection")
        catalog = self.root / "selection-providers.json"
        catalog.write_text(
            json.dumps(
                {
                    "native_agents": {
                        "codex": {
                            "executable": str(self.native),
                            "runtime_roots": [str(self.native.parent.parent)],
                        }
                    },
                    "native_profiles": {
                        "benchmark-service": {
                            "backend": "local",
                            "allowed_agents": ["codex"],
                            "workspace_roots": [str(parent)],
                            "timeout_seconds": 30,
                            "codex_service": {
                                "home": str(self.home),
                                "model": "gpt-6-luna",
                                "reasoning_effort": "low",
                            },
                        }
                    },
                }
            )
        )
        script = """
import json, sys
from pathlib import Path
from oh_goon.cli_ai import resolve_native_cli_selection
selected, isolation, git = resolve_native_cli_selection(agent='codex', profile='benchmark-service',
    workspace=Path(sys.argv[1]), config_file=Path(sys.argv[2]))
print(json.dumps({'agent': selected.agent, 'root': str(git.root(Path(sys.argv[1]))),
    'model': selected.profile.codex_service.model, 'reasoning_effort': selected.profile.codex_service.reasoning_effort}))
"""
        invocation = [
            os.environ["BENCHMARK_OH_GOON_CANDIDATE_PYTHON"],
            "-I",
            "-B",
            "-c",
            script,
            str(candidate),
            str(catalog),
        ]
        rejected = subprocess.run(
            invocation,
            env=self.context.environment,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(b"existing Git repository root", rejected.stderr)
        initialize_git_subdirectory(
            workspace=parent,
            subdirectory="candidate",
            control_root=self.control,
            environment=self.context.environment,
        )
        before = snapshot(parent)
        admitted = subprocess.run(
            invocation,
            env=self.context.environment,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(admitted.returncode, 0, admitted.stderr.decode())
        self.assertEqual(
            json.loads(admitted.stdout),
            {
                "agent": "codex",
                "root": str(candidate),
                "model": "gpt-6-luna",
                "reasoning_effort": "low",
            },
        )
        self.assertEqual(snapshot(parent), before)

    def test_selected_virtual_environment_launcher_is_preserved(self):
        alias = self.root / "venv/bin/python"
        alias.parent.mkdir(parents=True)
        alias.symlink_to(sys.executable)
        self.assertEqual(_public_path(alias, self.home), alias)

    def test_native_store_input_is_refused_before_contents_or_process(self):
        forbidden = self.home / "auth.json"
        forbidden.write_text("native-private-canary")
        self.context.environment["BENCHMARK_OH_GOON_CANDIDATE_CONFIG"] = str(forbidden)
        with self.guarded_reads(), patch.object(OhGoonAgent, "_command") as command:
            with self.assertRaisesRegex(
                ValueError, "inside the native authentication home"
            ):
                self.agent.prepare(self.context, NoneSubject())
            command.assert_not_called()
        self.assertEqual(forbidden.read_text(), "native-private-canary")

    @contextmanager
    def guarded_reads(self):
        original_read = Path.read_bytes
        original_open = Path.open
        home = self.home.resolve()

        def check(path):
            if path.resolve().is_relative_to(home):
                raise AssertionError("benchmark attempted a native-store content read")

        def read(path):
            check(path)
            return original_read(path)

        def opened(path, *args, **kwargs):
            check(path)
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "read_bytes", read), patch.object(Path, "open", opened):
            yield

    def prepare_with_native_probe(self):
        self.calls = []
        self.after_execution = None
        self.run_return_code = 0
        self.original_command = self.agent._command
        return patch.object(OhGoonAgent, "_command", side_effect=self.command)

    def test_untracked_native_store_alias_is_rejected_before_identity_reads(self):
        canary = self.home / "auth.json"
        canary.write_text("synthetic-native-private-canary")
        (self.source / "untracked-public-alias").symlink_to(canary)
        with (
            self.guarded_reads(),
            self.prepare_with_native_probe(),
            self.assertRaises(ValueError),
        ):
            self.agent.prepare(self.context, NoneSubject())
        self.assertEqual(self.calls, [])

    def test_dependency_native_store_alias_is_rejected_before_identity_reads(self):
        canary = self.home / "config.toml"
        canary.write_text("synthetic-native-private-canary")
        dependency = self.source / "backend/uv.lock"
        dependency.unlink()
        dependency.symlink_to(canary)
        with (
            self.guarded_reads(),
            self.prepare_with_native_probe(),
            self.assertRaises(ValueError),
        ):
            self.agent.prepare(self.context, NoneSubject())
        self.assertEqual(self.calls, [])

    def test_prepared_authority_native_store_alias_is_rejected_before_read(self):
        with self.prepare_with_native_probe():
            self.agent.prepare(self.context, NoneSubject())
        canary = self.home / "auth.json"
        canary.write_text("synthetic-native-private-canary")
        prepared = self.control / "oh-goon-prepared.json"
        prepared.unlink()
        prepared.symlink_to(canary)
        with (
            self.guarded_reads(),
            self.prepare_with_native_probe(),
            self.assertRaises(ValueError),
        ):
            self.agent.run(self.context, "Frozen task", NoneSubject())
        self.assertEqual(self.calls, [])

    def test_original_run_and_step_identity_and_unknown_metrics_survive(self):
        prepared, observed = self.execute()
        self.assertEqual(observed.payload["terminal_event"]["type"], "turn.completed")
        self.assertEqual(observed.payload["original_tape"], self.tape)
        self.assertEqual(observed.payload["original_handoff"], self.handoff)
        self.assertEqual(set(observed.measurements), {"duration_ms"})
        self.assertNotIn("tool_calls", observed.measurements)
        self.assertIn("oh_goon", self.calls[0])
        self.assertIn("--workspace", self.calls[0])
        self.assertEqual(
            self.calls[0][self.calls[0].index("--workspace") + 1],
            str(self.workspace / "candidate"),
        )
        authority = _agent_authority(self.agent, prepared)
        self.assertEqual(
            authority["observed"]["execution_runtime_identity"]["loaded"][
                "hashmarks_implementation_identity"
            ],
            "sha256:producer",
        )
        global_authority = _global_agent_authority(
            {"agent": {"declared": authority["declared"], **authority["observed"]}}
        )
        self.assertIn("execution_runtime_identity", global_authority)

    def test_failed_original_run_is_retained_without_fabricating_success(self):
        self.run["outcome"] = "ATTENTION_REQUIRED"
        self.handoff["outcome"] = "ATTENTION_REQUIRED"
        self.tape["repositories"][0]["steps"][0]["status"] = "FAILED"
        _, observed = self.execute(run_return_code=1)
        self.assertEqual(observed.payload["terminal_event"]["type"], "turn.failed")
        self.assertEqual(
            observed.payload["original_run"]["outcome"], "ATTENTION_REQUIRED"
        )
        self.assertEqual(observed.payload["original_tape"], self.tape)

    def test_cleanup_failure_keeps_the_successful_original_run_projection(self):
        _, observed = self.execute(run_return_code=2)
        self.assertEqual(observed.payload["original_run"], self.run)
        self.assertEqual(observed.payload["original_run"]["outcome"], "SUCCESS")
        self.assertEqual(observed.payload["process"]["return_code"], 2)

    def test_source_drift_during_native_work_cannot_qualify(self):
        source = self.source / "backend/src/oh_goon/__init__.py"
        _, observed = self.execute(
            after_execution=lambda: source.write_text("# changed during work\n")
        )
        self.assertEqual(observed.payload["terminal_event"]["type"], "turn.failed")
        self.assertIn(
            "post-execution authority unavailable",
            observed.payload["terminal_event"]["reason"],
        )
        self.assertEqual(observed.payload["original_run"], self.run)

    def test_loaded_producer_mismatch_fails_before_model_work(self):
        self.loaded["hashmarks_implementation_identity"] = "sha256:other-producer"
        self.calls = []
        self.after_execution = None
        self.run_return_code = 0
        self.original_command = self.agent._command
        with (
            patch.object(OhGoonAgent, "_command", side_effect=self.command),
            self.assertRaisesRegex(ValueError, "loaded runtime"),
        ):
            self.agent.prepare(self.context, NoneSubject())
        self.assertEqual(self.calls, [])

    def test_native_executable_replacement_before_work_is_refused(self):
        with self.prepare_with_native_probe():
            self.agent.prepare(self.context, NoneSubject())
            self.native.write_text('#!/bin/sh\necho "codex-cli 0.165.0"\n')
            with self.assertRaisesRegex(
                ValueError, "native Codex executable authority"
            ):
                self.agent.run(self.context, "Frozen task", NoneSubject())
        self.assertEqual(self.calls, [])

    def test_native_executable_replacement_during_work_preserves_incomplete_run(self):
        _, observed = self.execute(
            after_execution=lambda: self.native.write_text(
                '#!/bin/sh\necho "codex-cli 0.165.0"\n'
            )
        )
        self.assertEqual(observed.payload["terminal_event"]["type"], "turn.failed")
        self.assertIn(
            "native Codex executable authority",
            observed.payload["terminal_event"]["reason"],
        )
        self.assertEqual(observed.payload["original_run"], self.run)

    def test_public_config_drift_during_native_work_cannot_qualify(self):
        _, observed = self.execute(
            after_execution=lambda: self.config.write_text('{"changed":true}')
        )
        self.assertEqual(observed.payload["terminal_event"]["type"], "turn.failed")
        self.assertIn(
            "changed during inference", observed.payload["terminal_event"]["reason"]
        )

    def test_frozen_experiment_has_six_exact_matched_definitions(self):
        suite = load_suite(SUITE)
        rows = suite.trial_definitions()
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["replicate_id"] for row in rows}, {9101})
        self.assertEqual(len({row["definition_id"] for row in rows}), 6)
        required = required_runtime_authority(suite, rows)
        self.assertEqual(len(required), 7)
        for definition in suite.agents.values():
            build_agent(
                definition, budgets={"timeout_seconds": 300}, suite_root=suite.root
            )
        self.assertEqual(
            suite.tasks["deploy-order"]["contamination"]["allowed_change_globs"],
            ["candidate/deploy_order/graph.py", "candidate/deploy_order/__main__.py"],
        )

    def release_pair_score_module(self):
        spec = importlib.util.spec_from_file_location(
            "release_pair_score", SUITE / "score.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_suite_satisfies_actual_campaign_reporting_frontdoor(self):
        from benchmarks.__main__ import _validate_reporting_contract
        from benchmarks.config import BenchmarkConfig

        script, output = _validate_reporting_contract(
            BenchmarkConfig.load(None, host={}), load_suite(SUITE), {}
        )
        self.assertEqual(script, SUITE / "score.py")
        self.assertEqual(output, Path("score.json"))

    def test_scorer_refuses_dropped_arm_or_unfavorable_assignment(self):
        module = self.release_pair_score_module()
        agents = ("oh-goon-baseline", "oh-goon-candidate")
        with self.assertRaisesRegex(ValueError, "both frozen runtime arms"):
            module.score(
                suite_root=SUITE, results=self.root / "results", agents=agents[:1]
            )
        definitions = tuple(
            row["definition_id"] for row in load_suite(SUITE).trial_definitions()
        )
        with self.assertRaisesRegex(ValueError, "all six frozen assignments"):
            module.score(
                suite_root=SUITE,
                results=self.root / "results",
                agents=agents,
                definition_ids=definitions[:-1],
            )

    def test_scorer_keeps_failed_outcomes_and_unknown_metrics(self):
        module = self.release_pair_score_module()
        report = {
            "expected_trials": 6,
            "observed_trials": 6,
            "status_counts": {"PASS": 5, "FAIL": 1},
            "conditions": {
                "baseline": {"input_tokens": {"observations": 0, "mean": None}}
            },
            "diagnostics": {},
            "campaign_qualification": {"status": "QUALIFIED"},
            "cross_agent_observations": [
                {"task_id": "cidr-policy", "baseline": "FAIL", "candidate": "PASS"}
            ],
        }
        with patch.object(module, "build_report", return_value=report) as build:
            observed = module.score(
                suite_root=SUITE,
                results=self.root / "results",
                agents=("oh-goon-baseline", "oh-goon-candidate"),
            )
        self.assertTrue(build.call_args.kwargs["require_complete"])
        self.assertEqual(len(build.call_args.kwargs["selected_definitions"]), 6)
        self.assertEqual(observed["status_counts"], report["status_counts"])
        self.assertEqual(observed["conditions"], report["conditions"])
        self.assertEqual(
            observed["runtime_arm_observations"], report["cross_agent_observations"]
        )
        self.assertIsNone(observed["authority"]["overall_winner"])

    def test_readiness_reports_integrated_evidence_without_a_bare_claim(self):
        suite = load_suite(SUITE)
        with patch(
            "benchmarks.harness.readiness._agent_native_check",
            return_value=ReadinessCheck("runtime", "READY"),
        ):
            report = check_runtime_readiness(suite)
        self.assertEqual(len(report.checks), 2)
        for check in report.checks:
            self.assertIn("integrated Hashmarks", check.label)
            self.assertNotIn("bare", check.label)

    def test_unobservable_tool_budget_and_escaping_candidate_are_refused(self):
        suite = load_suite(SUITE)
        definition = suite.agents["oh-goon-candidate"]
        with self.assertRaisesRegex(
            AdapterConfigurationError, "authoritative tool-call"
        ):
            build_agent(
                definition,
                budgets={"timeout_seconds": 300, "max_tool_calls": 3},
                suite_root=suite.root,
            )
        definition = json.loads(json.dumps(definition))
        definition["configuration"]["workspace_subdirectory"] = "../private"
        with self.assertRaisesRegex(AdapterConfigurationError, "contained"):
            build_agent(
                definition, budgets={"timeout_seconds": 300}, suite_root=suite.root
            )


if __name__ == "__main__":
    unittest.main()
