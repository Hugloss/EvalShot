"""Boundary regressions for the public Oh-Goon adapter; no model or native stores."""

from __future__ import annotations

import hashlib
import json
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
from benchmarks.harness.admission import _agent_authority
from benchmarks.harness.campaign_authority import _global_agent_authority
from benchmarks.harness.model import TrialContext
from benchmarks.harness.readiness import ReadinessCheck, check_runtime_readiness
from benchmarks.harness.runtime_authority import required_runtime_authority
from benchmarks.harness.suite import load_suite
from benchmarks.harness.workspace import isolated_environment

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
