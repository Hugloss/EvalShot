from __future__ import annotations

import copy
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from benchmarks.adapters.codex import (
    CodexAgent,
    _final_message,
    _metrics,
    _render_config,
    parse_codex_jsonl,
    seed_codex_auth,
)
from benchmarks.adapters.enola import EnolaSubject
from benchmarks.adapters.hashmarks import HashmarksSubject
from benchmarks.adapters.opencode_native import (
    OpenCodeNativeAgent,
    _metrics as opencode_metrics,
    _native_environment as opencode_native_environment,
    _observe_opencode_executable,
    _observed_model as opencode_observed_model,
)
from benchmarks.adapters.registry import (
    AdapterConfigurationError,
    build_agent,
)
from benchmarks.adapters.runtime import observe_executable
from benchmarks.config import BenchmarkConfig
from benchmarks.harness.admission import (
    TrialAdmissionError,
    admit_trial,
    harness_identity,
    runtime_environment_identity,
)
from benchmarks.harness.bundle import verify_bundle
from benchmarks.harness.campaign import (
    CampaignError,
    campaign_status,
    resolve_campaign_paths,
)
from benchmarks.harness.campaign_authority import AuthorityEpochBinding
from benchmarks.harness.contamination import classify_contamination
from benchmarks.harness.identity import digest
from benchmarks.harness.model import (
    McpExposure,
    Observation,
    ParticipantIdentity,
    SubjectLifecycleMode,
    TrialContext,
)
from benchmarks.harness.mutation import apply_mutation
from benchmarks.harness.preflight import preflight_trial
from benchmarks.harness.receipt import is_complete_receipt
from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.runner import (
    _oracle_failure_diagnostic,
    _reason_for_agent,
    _reason_for_oracle_failure,
    run_trial,
)
from benchmarks.harness.runtime_authority import (
    required_runtime_authority,
    transport_runtime_authority,
)
from benchmarks.harness.selection import SelectionError, select_definitions
from benchmarks.harness.source import _source_cache_lock, materialize_repository
from benchmarks.harness.suite import SuiteDefinition, SuiteError, load_suite
from benchmarks.harness.workspace import WorkspaceError, isolated_environment
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


PILOT = Path("benchmarks/suites/repository-intelligence/pilot-v1")
MATRIX_V2 = Path("benchmarks/suites/repository-intelligence/agent-matrix-v2")
HELDOUT_V1 = Path("benchmarks/suites/repository-intelligence/heldout-v1")
CYCLE = Path("benchmarks/suites/repository-intelligence/enola-cycle-reproduction-v1")
PINNED_COMMIT = "0841a8822f417b8fd03af61c03779df8f1cdc941"
PINNED_TREE = "65a32888329e308615647dda194f0e36c2afe2ac"
MATRIX_V2_COMMIT = "6ce8b0d9230dd9bc5369ddf495ad9404766fbbaf"
MATRIX_V2_TREE = "1e253d251f8e0a874aeca0b05358b36253a714cc"
PILOT_SOURCE_BUNDLE = (
    Path(__file__).resolve().parent / "fixtures" / "cookbook-pilot.bundle"
)


def _legacy_matrix_suite() -> SuiteDefinition:
    """Keep historical receipt tests on the historical seed contract."""
    suite = load_suite(MATRIX_V2)
    experiment = copy.deepcopy(suite.experiment)
    for condition in experiment["conditions"]:
        condition["seed"] = condition.pop("replicate_ids")[0]
    return SuiteDefinition(
        suite.root, experiment, suite.tasks, suite.subjects, suite.agents
    )


def _opencode_trial_environment(control: Path, root: Path) -> dict[str, str]:
    environment = isolated_environment(control)
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / "opencode"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    bwrap = bin_dir / "bwrap"
    bwrap.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    bwrap.chmod(0o755)
    environment.update(
        {
            "PATH": str(bin_dir),
            "BENCHMARK_NATIVE_HOME": str(root / "opencode-home"),
            "BENCHMARK_NATIVE_XDG_CONFIG_HOME": str(root / "opencode-config"),
            "BENCHMARK_OPENCODE_AGENT": "build",
        }
    )
    return environment


class FakeSubject:
    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity("fake-subject", "control", "1")

    def prepare(self, context: TrialContext) -> Observation:
        return Observation(
            {"available": True, "observed_identity": {"version": "1"}},
            "",
        )

    def query(self, context: TrialContext, prompt: str) -> Observation:
        return Observation({}, "")

    def post_change(
        self,
        context: TrialContext,
        changed_paths: tuple[str, ...],
    ) -> Observation:
        return Observation({"changed_paths": list(changed_paths)}, "")

    def cleanup(self, context: TrialContext) -> Observation:
        return Observation({}, "")

    def mcp_exposure(self, context: TrialContext):
        return None

    def generated_globs(self) -> tuple[str, ...]:
        return ()


class FakeAgent:
    def subject_lifecycle_mode(self) -> SubjectLifecycleMode:
        return SubjectLifecycleMode.ADAPTER

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity("fake-agent", "coding_agent", "1")

    def prepare(self, context: TrialContext, exposed_subject) -> Observation:
        return Observation(
            {
                "available": True,
                "version": "fake-agent 1",
                "executable_sha256": "f" * 64,
                "mcp_exposure": None,
                "auth_mode": "not-applicable",
            },
            "",
        )

    def run(self, context: TrialContext, prompt: str, exposed_subject) -> Observation:
        return Observation(
            {
                "terminal_event": {"type": "turn.completed"},
                "terminal_complete": True,
                "final_message": '{"ok":true}',
                "jsonl_parse_errors": [],
                "process": {
                    "executable_missing": False,
                    "timed_out": False,
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
            '{"type":"turn.completed"}\n',
            {"duration_ms": 1.0, "mcp_calls": 0},
        )


class FakeNativeSubject:
    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            "native-subject",
            "repository_intelligence",
            "1",
        )

    def prepare(self, context: TrialContext) -> Observation:
        raise AssertionError("agent-native subject prepare must not run")

    def query(self, context: TrialContext, prompt: str) -> Observation:
        raise AssertionError("agent-native direct query must not run")

    def post_change(
        self,
        context: TrialContext,
        changed_paths: tuple[str, ...],
    ) -> Observation:
        raise AssertionError("agent-native post_change must not run")

    def cleanup(self, context: TrialContext) -> Observation:
        raise AssertionError("agent-native cleanup must not run")

    def mcp_exposure(self, context: TrialContext) -> McpExposure:
        return McpExposure(
            name="native-subject",
            command="native-subject",
            args=(),
            cwd=context.workspace,
            semantic_identity={"source": "subject-adapter"},
        )

    def generated_globs(self) -> tuple[str, ...]:
        return (".native-generated/**",)


class FakeNativeAgent(FakeAgent):
    def subject_lifecycle_mode(self) -> SubjectLifecycleMode:
        return SubjectLifecycleMode.AGENT_NATIVE

    def prepare(self, context: TrialContext, exposed_subject) -> Observation:
        return Observation(
            {
                "available": True,
                "version": "native-agent 1",
                "executable_sha256": "e" * 64,
                "model": "native/model",
                "provider": "native",
                "native_config_sha256": "d" * 64,
                "native_subject_identity": {
                    "verified": True,
                    "subject": "native-subject",
                    "command": "native-subject",
                    "executable_sha256": "c" * 64,
                    "reason_code": None,
                },
                "mcp_exposure": {
                    "name": "native-subject",
                    "source": "benchmark-subject-exposure",
                },
                "observed_identity": {
                    "workspace_binding": {
                        "verified": True,
                        "subject": "native-subject",
                        "method": "test-native-binding",
                        "reason_code": None,
                    }
                },
            },
            "",
        )


class FakeOracle:
    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity("fake-oracle", "oracle", "1")

    def healthcheck(self, context: TrialContext) -> Observation:
        return Observation({"healthy": True}, "")

    def grade(self, context: TrialContext, observation: Observation) -> Observation:
        return Observation({"valid": True, "passed": True}, "")


class PilotExecutionTests(unittest.TestCase):
    def test_harness_identity_changes_with_local_source_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(("git", "init", "-q", str(root)), check=True)
            tracked = root / "runner.py"
            tracked.write_text("before\n")
            subprocess.run(("git", "-C", str(root), "add", "runner.py"), check=True)
            subprocess.run(
                (
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "base",
                ),
                check=True,
            )
            clean = harness_identity(root)
            self.assertEqual(clean["repository"], "Hugloss/EvalShot")
            tracked.write_text("after\n")
            edited = harness_identity(root)
            self.assertNotEqual(
                clean["working_copy_sha256"], edited["working_copy_sha256"]
            )
            self.assertFalse(edited["working_copy_clean"])
            (root / "new.py").write_text("first\n")
            with_untracked = harness_identity(root)
            (root / "new.py").write_text("second\n")
            changed_untracked = harness_identity(root)
            self.assertNotEqual(
                with_untracked["working_copy_sha256"],
                changed_untracked["working_copy_sha256"],
            )

    def test_harness_identity_rejects_head_change_during_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch(
                    "benchmarks.harness.admission._git_value",
                    side_effect=("a" * 40, "b" * 40, "c" * 40),
                ),
                mock.patch(
                    "benchmarks.harness.admission._git_bytes",
                    side_effect=(b"", b""),
                ),
            ):
                with self.assertRaisesRegex(
                    TrialAdmissionError,
                    "harness git authority changed during identity observation",
                ):
                    harness_identity(root)

    def test_native_codex_injects_exact_subject_and_disables_ambient_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            environment = isolated_environment(control)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            codex = bin_dir / "codex"
            codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            codex.chmod(0o755)
            environment["PATH"] = str(bin_dir)
            context = TrialContext(workspace, control, environment)
            exposure = McpExposure(
                name="hashmarks",
                command="/work/Hashmarks/.venv/bin/hashmarks",
                args=("--workspace", ".", "mcp"),
                cwd=workspace,
                semantic_identity={"name": "hashmarks"},
            )

            argv = CodexAgent(native_host=True)._exec_argv(
                context,
                "find owner",
                ("hashmarks", "enola"),
                exposure,
            )

        self.assertIn("--approve-for-me", argv)
        self.assertIn("mcp_servers.hashmarks.enabled=false", argv)
        self.assertIn("mcp_servers.enola.enabled=false", argv)
        self.assertIn("mcp_servers.hashmarks.enabled=true", argv)
        self.assertIn(
            'mcp_servers.hashmarks.command="/work/Hashmarks/.venv/bin/hashmarks"',
            argv,
        )
        self.assertNotIn("--model", argv)

    def test_pilot_suite_freezes_nine_paired_trials(self) -> None:
        suite = load_suite(PILOT)
        self.assertEqual(len(suite.tasks), 3)
        self.assertEqual(len(suite.subjects), 3)
        self.assertEqual(len(suite.agents), 1)
        rows = suite.trial_definitions()
        self.assertEqual(len(rows), 9)
        self.assertEqual(
            {row["condition_id"] for row in rows},
            {"bare-codex", "hashmarks-codex", "enola-codex"},
        )
        self.assertTrue(all(len(row["definition_id"]) == 64 for row in rows))

    def test_agent_matrix_v2_freezes_eighteen_definitions(self) -> None:
        suite = load_suite(MATRIX_V2)
        self.assertEqual(len(suite.tasks), 3)
        self.assertEqual(len(suite.subjects), 3)
        self.assertEqual(len(suite.agents), 2)
        rows = suite.trial_definitions()
        self.assertEqual(len(rows), 18)
        self.assertEqual(len({row["definition_id"] for row in rows}), 18)
        self.assertEqual(
            {row["condition_id"] for row in rows},
            {
                "bare-sol",
                "hashmarks-sol",
                "enola-sol",
                "bare-opencode-native",
                "hashmarks-opencode-native",
                "enola-opencode-native",
            },
        )
        sol = suite.agents["codex-sol"]["configuration"]
        native = suite.agents["opencode-native"]["configuration"]
        self.assertEqual(sol["model"], "gpt-5.6-sol")
        self.assertEqual(sol["reasoning_effort"], "high")
        self.assertNotIn("model", native)
        self.assertNotIn("provider", native)
        self.assertNotIn("api_key", native)
        self.assertEqual(
            suite.agents["opencode-native"]["adapter"],
            "opencode-native",
        )

    def test_matrix_selection_never_adds_bare_control_implicitly(self) -> None:
        suite = load_suite(MATRIX_V2)
        selected = select_definitions(
            suite, agents=("opencode-native",), subjects=("hashmarks",)
        )
        self.assertEqual(len(selected), 3)
        self.assertEqual(
            {row["condition_id"] for row in selected},
            {"hashmarks-opencode-native"},
        )
        both = select_definitions(
            suite, agents=("codex-sol",), subjects=("hashmarks", "enola")
        )
        self.assertEqual(len(both), 6)
        explicit_control = select_definitions(
            suite,
            agents=("opencode-native",),
            subjects=("hashmarks", "none"),
        )
        self.assertEqual(
            {row["condition_id"] for row in explicit_control},
            {"bare-opencode-native", "hashmarks-opencode-native"},
        )
        with self.assertRaises(SelectionError):
            select_definitions(suite, agents=("unknown",))
        self.assertEqual(
            len(select_definitions(suite, agents=("codex-sol",), condition="bare-sol")),
            3,
        )
        with self.assertRaises(SelectionError):
            select_definitions(suite, agents=("opencode-native",), condition="bare-sol")

    def test_suite_loader_enforces_repo_owned_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "pilot"
            shutil.copytree(PILOT, copied)

            task_path = copied / "tasks" / "locate-receipt-completion-owner.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task.pop("mode")
            task_path.write_text(
                json.dumps(task, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(SuiteError):
                load_suite(copied)

        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "pilot"
            shutil.copytree(PILOT, copied)

            subject_path = copied / "subjects" / "none.json"
            subject = json.loads(subject_path.read_text(encoding="utf-8"))
            subject["unexpected"] = True
            subject_path.write_text(
                json.dumps(subject, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(SuiteError):
                load_suite(copied)

    def test_participant_configuration_is_part_of_definition_identity(self) -> None:
        suite = load_suite(PILOT)
        original = next(
            row["definition_id"]
            for row in suite.trial_definitions()
            if row["task_id"] == "locate-receipt-completion-owner"
            and row["condition_id"] == "hashmarks-codex"
        )
        subjects = json.loads(json.dumps(suite.subjects))
        subjects["hashmarks"]["configuration"]["timeout_seconds"] += 1
        changed = SuiteDefinition(
            root=suite.root,
            experiment=suite.experiment,
            tasks=suite.tasks,
            subjects=subjects,
            agents=suite.agents,
        )
        altered = next(
            row["definition_id"]
            for row in changed.trial_definitions()
            if row["task_id"] == "locate-receipt-completion-owner"
            and row["condition_id"] == "hashmarks-codex"
        )
        self.assertNotEqual(original, altered)

    def test_benchmark_env_file_loads_only_declared_runtime_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "# benchmark local config\n"
                "HASHMARKS_BENCH_SOURCE='/work/Hashmarks'\n"
                "ENOLA_BENCH_EXECUTABLE=/tools/enola\n"
                "BENCHMARK_CODEX_EXECUTABLE=/tools/codex\n"
                "BENCHMARK_OPENCODE_EXECUTABLE=/tools/opencode\n"
                "BENCHMARK_OPENCODE_AGENT=build\n"
                "UNRELATED=value\n",
                encoding="utf-8",
            )
            environment = BenchmarkConfig.load(env_file, host={}).runtime_environment()
            self.assertEqual(
                environment,
                {
                    "HASHMARKS_BENCH_SOURCE": "/work/Hashmarks",
                    "BENCHMARK_OPENCODE_AGENT": "build",
                },
            )

    def test_benchmark_env_file_overrides_process_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "HASHMARKS_BENCH_SOURCE=/from-env-file\n",
                encoding="utf-8",
            )
            environment = BenchmarkConfig.load(
                env_file, host={"HASHMARKS_BENCH_SOURCE": "/already-exported"}
            ).runtime_environment()
            self.assertEqual(
                environment["HASHMARKS_BENCH_SOURCE"],
                "/from-env-file",
            )

    def test_selected_conditions_require_exact_runtime_authority(self) -> None:
        suite = load_suite(HELDOUT_V1)
        rows = select_definitions(
            suite,
            agents=("opencode-native",),
            subjects=("hashmarks",),
        )
        self.assertEqual(
            set(required_runtime_authority(suite, rows)),
            {
                "HASHMARKS_BENCH_SOURCE",
                "BENCHMARK_OPENCODE_AGENT",
            },
        )

        codex_rows = select_definitions(
            suite,
            agents=("codex-native",),
            subjects=("enola",),
        )
        self.assertEqual(
            required_runtime_authority(suite, codex_rows),
            (),
        )

    def test_hashmarks_adapter_has_no_path_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            context = TrialContext(
                workspace,
                control,
                isolated_environment(control),
            )
            with mock.patch(
                "benchmarks.adapters.hashmarks.observe_executable"
            ) as observed:
                prepared = HashmarksSubject().prepare(context)
            observed.assert_not_called()
            self.assertFalse(prepared.payload["available"])
            self.assertIn(
                "PATH lookup is not benchmark authority", prepared.payload["reason"]
            )

    def test_hashmarks_source_selects_exact_executable_without_path_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            source = root / "hashmarks-source"
            executable = source / ".venv" / "bin" / "hashmarks"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            environment = _opencode_trial_environment(control, root)
            environment["HASHMARKS_BENCH_SOURCE"] = str(source)
            context = TrialContext(workspace, control, environment)
            unavailable = Observation({"available": False}, "")

            with mock.patch(
                "benchmarks.adapters.hashmarks.observe_executable",
                return_value=unavailable,
            ) as observed:
                HashmarksSubject().prepare(context)

            observed.assert_called_once_with(
                context,
                str(executable.resolve()),
                version_args=("version",),
            )
            self.assertEqual(
                HashmarksSubject().mcp_exposure(context).command,
                str(executable.resolve()),
            )

    def test_hashmarks_scored_source_rejects_uncommitted_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            source = root / "hashmarks-source"
            executable = source / ".venv" / "bin" / "hashmarks"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            environment = isolated_environment(control)
            environment["HASHMARKS_BENCH_SOURCE"] = str(source)
            context = TrialContext(workspace, control, environment)
            git_results = [
                SimpleNamespace(returncode=0, stdout=b"1" * 40 + b"\n"),
                SimpleNamespace(returncode=0, stdout=b"2" * 40 + b"\n"),
                SimpleNamespace(returncode=0, stdout=b"dirty diff"),
                SimpleNamespace(returncode=0, stdout=b""),
            ]

            with mock.patch(
                "benchmarks.adapters.hashmarks.subprocess.run",
                side_effect=git_results,
            ):
                identity, error = HashmarksSubject().source_identity(context)

            self.assertIsNone(identity)
            self.assertEqual(
                error,
                "Hashmarks benchmark source must be a clean committed checkout",
            )

    def test_hashmarks_source_identity_rejects_head_change_during_observation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            source = root / "hashmarks-source"
            executable = source / ".venv" / "bin" / "hashmarks"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            environment = isolated_environment(control)
            environment["HASHMARKS_BENCH_SOURCE"] = str(source)
            context = TrialContext(workspace, control, environment)
            git_results = [
                SimpleNamespace(returncode=0, stdout=b"1" * 40 + b"\n"),
                SimpleNamespace(returncode=0, stdout=b"2" * 40 + b"\n"),
                SimpleNamespace(returncode=0, stdout=b""),
                SimpleNamespace(returncode=0, stdout=b""),
                SimpleNamespace(returncode=0, stdout=b"3" * 40 + b"\n"),
            ]

            with mock.patch(
                "benchmarks.adapters.hashmarks.subprocess.run",
                side_effect=git_results,
            ):
                identity, error = HashmarksSubject().source_identity(context)

            self.assertIsNone(identity)
            self.assertEqual(
                error,
                "Hashmarks benchmark source changed during identity observation",
            )

    def test_enola_adapter_writes_explicit_trial_output_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            control.mkdir()
            context = TrialContext(workspace, control, {})
            path = EnolaSubject()._config(context)
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["repo"], str(workspace))
            self.assertEqual(value["output"]["dir"], ".benchmark-enola")
            self.assertFalse(path.is_relative_to(workspace))
            self.assertEqual(
                EnolaSubject().generated_globs(), (".benchmark-enola/**",)
            )

    def test_enola_adapter_uses_native_path_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            environment = isolated_environment(control)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            executable = bin_dir / "enola"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            environment["PATH"] = str(bin_dir)
            context = TrialContext(workspace, control, environment)

            self.assertEqual(
                EnolaSubject()._executable(context),
                str(executable.resolve()),
            )

    def test_environment_identity_normalizes_disposable_control_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left_control = root / "left"
            right_control = root / "right"
            left = isolated_environment(left_control)
            right = isolated_environment(right_control)
            left["EXTERNAL_AUTHORITY"] = "/stable/tool"
            right["EXTERNAL_AUTHORITY"] = "/stable/tool"

            left_identity = runtime_environment_identity(
                left,
                control_root=left_control,
            )
            right_identity = runtime_environment_identity(
                right,
                control_root=right_control,
            )
            self.assertEqual(left_identity, right_identity)

            right["EXTERNAL_AUTHORITY"] = "/different/tool"
            changed = runtime_environment_identity(
                right,
                control_root=right_control,
            )
            self.assertNotEqual(
                left_identity["environment_sha256"],
                changed["environment_sha256"],
            )

    def test_bounded_process_can_disable_host_environment_inheritance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = (
                "import json, os; "
                "print(json.dumps({"
                "'visible': os.environ.get('VISIBLE'), "
                "'hidden': os.environ.get('HIDDEN_BENCH_VAR')"
                "}))"
            )
            with mock.patch.dict(
                os.environ,
                {"HIDDEN_BENCH_VAR": "secret"},
                clear=False,
            ):
                result = run_bounded(
                    repository_root=root,
                    argv=(sys.executable, "-c", script),
                    environment={"VISIBLE": "yes"},
                    inherit_environment=False,
                )

        self.assertEqual(result.return_code, 0)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["visible"], "yes")
        self.assertIsNone(observed["hidden"])

    def test_runtime_passthrough_is_explicit_and_fail_closed(self) -> None:
        destination: dict[str, str] = {}
        transport_runtime_authority(
            {
                "BENCHMARK_PASSTHROUGH_ENV_KEYS": "PROVIDER_TOKEN",
                "PROVIDER_TOKEN": "secret",
            },
            destination,
        )
        self.assertEqual(destination["PROVIDER_TOKEN"], "secret")

        with self.assertRaisesRegex(
            ValueError,
            "declared benchmark passthrough variable",
        ):
            transport_runtime_authority(
                {"BENCHMARK_PASSTHROUGH_ENV_KEYS": "MISSING_TOKEN"},
                {},
            )

    def test_agent_adapter_receives_frozen_execution_budgets(self) -> None:
        suite = load_suite(PILOT)
        task = suite.tasks["locate-receipt-completion-owner"]
        agent = build_agent(suite.agents["codex"], budgets=task["budgets"])
        self.assertEqual(
            agent.max_output_bytes,
            task["budgets"]["max_output_bytes"],
        )
        self.assertEqual(
            agent.max_tool_calls,
            task["budgets"]["max_tool_calls"],
        )
        self.assertEqual(
            agent.timeout_seconds,
            task["budgets"]["timeout_seconds"],
        )

    def test_codex_adapter_rejects_local_model_provider_config(self) -> None:
        definition = {
            "id": "forbidden",
            "adapter": "codex",
            "identity": {"id": "forbidden", "version": "1"},
            "capabilities": [],
            "configuration": {
                "model": "gemma4:12b",
                "local_provider": "ollama",
            },
        }
        with self.assertRaises(AdapterConfigurationError):
            build_agent(
                definition,
                budgets={"timeout_seconds": 60},
            )

    def test_codex_sol_freezes_reasoning_effort(self) -> None:
        config = _render_config(
            "gpt-5.6-sol",
            None,
            reasoning_effort="high",
        )
        self.assertIn('model = "gpt-5.6-sol"', config)
        self.assertIn('model_reasoning_effort = "high"', config)
        self.assertNotIn("oss_provider", config)

    def test_executable_observation_rejects_bytes_changed_during_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            control.mkdir()
            executable = root / "native-tool"
            executable.write_text("#!/bin/sh\necho v1\n", encoding="utf-8")
            executable.chmod(0o755)
            context = TrialContext(
                workspace,
                control,
                {"PATH": str(root)},
            )
            process = mock.Mock(
                executable_missing=False,
                timed_out=False,
                return_code=0,
                stdout_truncated=False,
                stderr_truncated=False,
                stdout=b"v1\n",
                stderr=b"",
            )
            process.metrics.return_value = {"return_code": 0}

            def replace_during_probe(**kwargs):
                executable.write_text("#!/bin/sh\necho v2\n", encoding="utf-8")
                return process

            with mock.patch(
                "benchmarks.adapters.runtime.run_bounded",
                side_effect=replace_during_probe,
            ):
                observed = observe_executable(context, "native-tool")

            self.assertFalse(observed.payload["available"])
            self.assertFalse(observed.payload["executable_stable"])
            self.assertIsNone(observed.payload["executable_sha256"])
            self.assertIn("changed", observed.payload["reason"])

    def test_opencode_native_adapter_owns_no_model_provider_config(self) -> None:
        suite = load_suite(MATRIX_V2)
        task = suite.tasks["locate-receipt-completion-owner"]
        agent = build_agent(
            suite.agents["opencode-native"],
            budgets=task["budgets"],
        )
        self.assertIsInstance(agent, OpenCodeNativeAgent)

        forbidden = json.loads(json.dumps(suite.agents["opencode-native"]))
        forbidden["configuration"]["model"] = "liteLLM/gemma4"
        with self.assertRaises(AdapterConfigurationError):
            build_agent(forbidden, budgets=task["budgets"])

    def test_opencode_native_environment_uses_discovered_native_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            context = TrialContext(
                workspace,
                control,
                _opencode_trial_environment(control, root),
            )
            environment = opencode_native_environment(context)
            self.assertEqual(
                environment["OPENCODE_BIN"],
                str((root / "bin" / "opencode").resolve()),
            )
            self.assertEqual(
                environment["HOME"],
                str((root / "opencode-home").resolve()),
            )
            self.assertEqual(
                environment["XDG_CONFIG_HOME"],
                str((root / "opencode-config").resolve()),
            )
            self.assertEqual(
                environment["XDG_DATA_HOME"],
                context.environment["XDG_DATA_HOME"],
            )
            self.assertEqual(
                environment["XDG_STATE_HOME"],
                context.environment["XDG_STATE_HOME"],
            )
            self.assertEqual(
                environment["TMPDIR"],
                context.environment["TMPDIR"],
            )
            self.assertNotIn("OPENCODE_CONFIG_CONTENT", environment)

    def test_opencode_executable_authority_is_fresh_per_admission(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            context = TrialContext(
                workspace,
                control,
                _opencode_trial_environment(control, root),
            )
            environment = opencode_native_environment(context)
            first = Observation(
                {
                    "available": True,
                    "version": "opencode 1",
                    "executable_sha256": "a" * 64,
                },
                "opencode 1",
            )
            second = Observation(
                {
                    "available": True,
                    "version": "opencode 2",
                    "executable_sha256": "a" * 64,
                },
                "opencode 2",
            )
            with mock.patch(
                "benchmarks.adapters.opencode_native.observe_executable",
                side_effect=(first, second),
            ) as observed:
                initial = _observe_opencode_executable(context, environment)
                changed = _observe_opencode_executable(context, environment)

            self.assertEqual(observed.call_count, 2)
            self.assertEqual(initial.payload["version"], "opencode 1")
            self.assertEqual(changed.payload["version"], "opencode 2")
            for call in observed.call_args_list:
                self.assertEqual(
                    call.kwargs["environment"],
                    environment,
                )

    def test_opencode_never_reuses_native_config_authority_across_admissions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = Observation(
                {
                    "available": True,
                    "version": "opencode 1",
                    "executable_sha256": "a" * 64,
                },
                "opencode 1",
            )
            inspection = {
                "model": "liteLLM/gemma4",
                "provider": "liteLLM",
                "config_sha256": "b" * 64,
                "mcp_shape": "flat",
                "mcp_servers": [],
            }
            runtime_result = mock.Mock()
            runtime_result.metrics.return_value = {"return_code": 0}
            runtime_result.stderr = b""
            runtime_result.stdout = b"{}"
            calls: list[tuple[str, ...]] = []

            def runtime_call(context, *, args, **kwargs):
                calls.append(args)
                self.assertNotIn("--base-config-file", args)
                self.assertNotIn("--emit-base-config", args)
                return (
                    {
                        "status": "completed",
                        "inspection": inspection,
                        "effective_inspection": inspection,
                        "selected_server": None,
                        "workspace_binding": {"verified": True},
                        "native_subject_identity": None,
                        "overlay_identity": {"shape": "flat"},
                        "base_config_source": "fresh",
                    },
                    runtime_result,
                )

            def context(name: str, scope: str) -> TrialContext:
                workspace = root / f"workspace-{name}"
                workspace.mkdir()
                control = root / f"control-{name}"
                return TrialContext(
                    workspace,
                    control,
                    _opencode_trial_environment(control, root),
                    admission_scope=scope,
                )

            agent = OpenCodeNativeAgent()
            with (
                mock.patch(
                    "benchmarks.adapters.opencode_native._observe_opencode_executable",
                    return_value=executable,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call",
                    side_effect=runtime_call,
                ),
            ):
                first = agent.prepare(context("first", "same-task"), None)
                second = agent.prepare(context("second", "same-task"), None)
                third = agent.prepare(context("third", "different-task"), None)

            self.assertEqual(first.payload["base_config_source"], "fresh")
            self.assertEqual(second.payload["base_config_source"], "fresh")
            self.assertEqual(third.payload["base_config_source"], "fresh")
            self.assertFalse(first.measurements["base_config_reused"])
            self.assertFalse(second.measurements["base_config_reused"])
            self.assertFalse(third.measurements["base_config_reused"])
            self.assertEqual(len(calls), 3)

    def test_opencode_prepare_uses_shared_runtime_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            context = TrialContext(
                workspace,
                control,
                _opencode_trial_environment(control, root),
            )
            executable = Observation(
                {
                    "available": True,
                    "version": "opencode 1",
                    "executable_sha256": "a" * 64,
                },
                "opencode 1",
            )
            inspection = {
                "model": "liteLLM/gemma4",
                "provider": "liteLLM",
                "config_sha256": "b" * 64,
                "mcp_shape": "flat",
                "mcp_servers": [
                    {"name": "hashmarks", "enabled": True},
                    {"name": "enola", "enabled": True},
                ],
            }
            runtime_result = mock.Mock()
            runtime_result.metrics.return_value = {"return_code": 0}
            runtime_result.stderr = b""
            runtime_result.stdout = b"{}"
            with (
                mock.patch.object(
                    HashmarksSubject,
                    "source_identity",
                    return_value=(
                        {
                            "root": "/work/Hashmarks",
                            "commit": "a" * 40,
                            "tree": "b" * 40,
                            "working_copy_sha256": "c" * 64,
                            "working_copy_clean": True,
                        },
                        None,
                    ),
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native.observe_executable",
                    return_value=executable,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call",
                    return_value=(
                        {
                            "status": "completed",
                            "inspection": inspection,
                            "effective_inspection": inspection,
                            "selected_server": None,
                            "overlay_identity": {"shape": "flat"},
                        },
                        runtime_result,
                    ),
                ) as runtime_call,
            ):
                prepared = OpenCodeNativeAgent().prepare(context, None)

            self.assertTrue(prepared.payload["available"])
            self.assertEqual(prepared.payload["model"], "liteLLM/gemma4")
            self.assertEqual(
                prepared.payload["native_config_sha256"],
                "b" * 64,
            )
            runtime_call.assert_called_once()
            self.assertEqual(
                runtime_call.call_args.kwargs["args"],
                (
                    "inspect-config",
                    "--repo",
                    str(workspace),
                    "--benchmark-control-root",
                    str(control),
                    "--agent",
                    "build",
                    "--benchmark-subject",
                    "none",
                ),
            )

    def test_opencode_assisted_prepare_uses_subject_owned_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            source = root / "hashmarks-source"
            installed = source / ".venv" / "bin" / "hashmarks"
            installed.parent.mkdir(parents=True)
            installed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            installed.chmod(0o755)
            environment = _opencode_trial_environment(control, root)
            environment["HASHMARKS_BENCH_SOURCE"] = str(source)
            context = TrialContext(
                workspace,
                control,
                environment,
            )
            executable = Observation(
                {
                    "available": True,
                    "version": "opencode 1",
                    "executable_sha256": "a" * 64,
                },
                "opencode 1",
            )
            inspection = {
                "model": "liteLLM/gemma4",
                "provider": "liteLLM",
                "config_sha256": "b" * 64,
                "mcp_shape": "flat",
                "mcp_servers": [
                    {"name": "hashmarks", "enabled": True},
                    {"name": "enola", "enabled": True},
                ],
            }
            runtime_result = mock.Mock()
            runtime_result.metrics.return_value = {"return_code": 0}
            runtime_result.stderr = b""
            runtime_result.stdout = b"{}"
            subject = HashmarksSubject()
            exposure_path = control / "opencode-benchmark-exposure.json"
            with (
                mock.patch.object(
                    HashmarksSubject,
                    "source_identity",
                    return_value=(
                        {
                            "root": "/work/Hashmarks",
                            "commit": "a" * 40,
                            "tree": "b" * 40,
                            "working_copy_sha256": "c" * 64,
                            "working_copy_clean": True,
                        },
                        None,
                    ),
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native.observe_executable",
                    return_value=executable,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call",
                    return_value=(
                        {
                            "status": "completed",
                            "inspection": inspection,
                            "effective_inspection": inspection,
                            "selected_server": "hashmarks",
                            "native_subject_identity": {
                                "verified": True,
                                "subject": "hashmarks",
                                "command": "hashmarks",
                                "executable_path": str(installed.resolve()),
                                "executable_sha256": "c" * 64,
                                "reason_code": None,
                            },
                            "workspace_binding": {
                                "verified": True,
                                "subject": "hashmarks",
                                "method": "hashmarks-explicit-workspace",
                                "command_executable": str(installed.resolve()),
                                "workspace": str(workspace),
                                "effective_cwd": str(workspace),
                                "resolved_workspace": str(workspace),
                                "reason": None,
                                "reason_code": None,
                            },
                            "overlay_identity": {
                                "shape": "flat",
                                "selected_subject": "hashmarks",
                                "subject_definition_source": "benchmark-subject-exposure",
                                "native_server_shadowed": True,
                                "subject_exposure_sha256": "d" * 64,
                            },
                        },
                        runtime_result,
                    ),
                ) as runtime_call,
            ):
                prepared = OpenCodeNativeAgent().prepare(context, subject)

            self.assertTrue(prepared.payload["available"])
            self.assertEqual(
                prepared.payload["mcp_exposure"]["name"],
                "hashmarks",
            )
            self.assertEqual(
                prepared.payload["mcp_exposure"]["source"],
                "benchmark-subject-exposure",
            )
            self.assertTrue(prepared.payload["mcp_exposure"]["native_server_shadowed"])
            self.assertEqual(
                prepared.payload["native_subject_identity"]["executable_path"],
                str(installed.resolve()),
            )
            runtime_call.assert_called_once()
            self.assertEqual(
                runtime_call.call_args.kwargs["args"],
                (
                    "inspect-config",
                    "--repo",
                    str(workspace),
                    "--benchmark-control-root",
                    str(control),
                    "--agent",
                    "build",
                    "--benchmark-subject",
                    "hashmarks",
                    "--benchmark-exposure-file",
                    str(exposure_path),
                ),
            )
            self.assertTrue(exposure_path.exists())
            exposure = json.loads(exposure_path.read_text(encoding="utf-8"))
            self.assertEqual(exposure["command"][0], str(installed.resolve()))
            self.assertEqual(exposure["command"][-1], "mcp")
            self.assertFalse((workspace / "opencode.json").exists())

    def test_opencode_mcp_probe_failure_preserves_runtime_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            source = root / "hashmarks-source"
            installed = source / ".venv" / "bin" / "hashmarks"
            installed.parent.mkdir(parents=True)
            installed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            installed.chmod(0o755)
            environment = _opencode_trial_environment(control, root)
            environment["HASHMARKS_BENCH_SOURCE"] = str(source)
            context = TrialContext(workspace, control, environment)
            executable = Observation(
                {
                    "available": True,
                    "version": "opencode 1",
                    "executable_sha256": "a" * 64,
                },
                "opencode 1",
            )
            runtime_result = mock.Mock()
            runtime_result.metrics.return_value = {"return_code": 0}
            runtime_result.stderr = b""
            runtime_result.stdout = b"{}"
            reason = (
                "benchmark OpenCode MCP connection hashmarks is not connected; "
                'exit=7; mcp-list="hashmarks: failed"; '
                'stderr="Hashmarks MCP support requires the optional extra"'
            )
            with (
                mock.patch.object(
                    HashmarksSubject,
                    "source_identity",
                    return_value=(
                        {
                            "root": "/work/Hashmarks",
                            "commit": "a" * 40,
                            "tree": "b" * 40,
                            "working_copy_sha256": "c" * 64,
                            "working_copy_clean": True,
                        },
                        None,
                    ),
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native.observe_executable",
                    return_value=executable,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call",
                    return_value=(
                        {
                            "status": "failed",
                            "reason": reason,
                            "failure_stage": "mcp-connection",
                            "inspection": {
                                "model": "liteLLM/gemma4",
                                "provider": "liteLLM",
                                "config_sha256": "b" * 64,
                                "mcp_shape": "flat",
                                "mcp_servers": [],
                            },
                        },
                        runtime_result,
                    ),
                ),
            ):
                prepared = OpenCodeNativeAgent().prepare(
                    context,
                    HashmarksSubject(),
                )

            self.assertFalse(prepared.payload["available"])
            self.assertEqual(prepared.payload["reason"], reason)
            self.assertEqual(prepared.payload["failure_stage"], "mcp-connection")
            self.assertIn("exit=7", prepared.payload["reason"])
            self.assertIn("hashmarks: failed", prepared.payload["reason"])
            self.assertIn(
                "Hashmarks MCP support requires the optional extra",
                prepared.payload["reason"],
            )

    def test_opencode_unverified_native_workspace_binding_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            source = root / "hashmarks-source"
            installed = source / ".venv" / "bin" / "hashmarks"
            installed.parent.mkdir(parents=True)
            installed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            installed.chmod(0o755)
            environment = _opencode_trial_environment(control, root)
            environment["HASHMARKS_BENCH_SOURCE"] = str(source)
            context = TrialContext(
                workspace,
                control,
                environment,
            )
            executable = Observation(
                {
                    "available": True,
                    "version": "opencode 1",
                    "executable_sha256": "a" * 64,
                },
                "opencode 1",
            )
            inspection = {
                "model": "liteLLM/gemma4",
                "provider": "liteLLM",
                "config_sha256": "b" * 64,
                "mcp_shape": "flat",
                "mcp_servers": [
                    {"name": "hashmarks", "enabled": True},
                ],
            }
            runtime_result = mock.Mock()
            runtime_result.metrics.return_value = {"return_code": 0}
            runtime_result.stderr = b""
            runtime_result.stdout = b"{}"
            with (
                mock.patch.object(
                    HashmarksSubject,
                    "source_identity",
                    return_value=(
                        {
                            "root": "/work/Hashmarks",
                            "commit": "a" * 40,
                            "tree": "b" * 40,
                            "working_copy_sha256": "c" * 64,
                            "working_copy_clean": True,
                        },
                        None,
                    ),
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native.observe_executable",
                    return_value=executable,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call",
                    return_value=(
                        {
                            "status": "completed",
                            "inspection": inspection,
                            "effective_inspection": inspection,
                            "selected_server": "hashmarks",
                            "native_subject_identity": {
                                "verified": True,
                                "subject": "hashmarks",
                                "command": "hashmarks",
                                "executable_path": str(installed.resolve()),
                                "executable_sha256": "c" * 64,
                                "reason_code": None,
                            },
                            "workspace_binding": {
                                "verified": False,
                                "subject": "hashmarks",
                                "method": "hashmarks-explicit-workspace",
                                "workspace": str(workspace),
                                "effective_cwd": "/outside",
                                "resolved_workspace": "/outside",
                                "reason": (
                                    "native Hashmarks workspace resolves outside "
                                    "trial workspace: /outside"
                                ),
                                "reason_code": "hashmarks-workspace-outside-trial",
                            },
                            "overlay_identity": {
                                "shape": "flat",
                                "selected_subject": "hashmarks",
                                "subject_definition_source": "benchmark-subject-exposure",
                                "native_server_shadowed": True,
                                "subject_exposure_sha256": "d" * 64,
                            },
                        },
                        runtime_result,
                    ),
                ),
            ):
                prepared = OpenCodeNativeAgent().prepare(
                    context,
                    HashmarksSubject(),
                )

            self.assertFalse(prepared.payload["available"])
            self.assertFalse(prepared.payload["workspace_binding"]["verified"])
            self.assertIn(
                "outside trial workspace",
                prepared.payload["reason"],
            )
            self.assertEqual(
                prepared.payload["observed_identity"]["workspace_binding"],
                {
                    "verified": False,
                    "subject": "hashmarks",
                    "method": "hashmarks-explicit-workspace",
                    "reason_code": "hashmarks-workspace-outside-trial",
                },
            )
            self.assertNotIn(
                "/outside",
                json.dumps(prepared.payload["observed_identity"]["workspace_binding"]),
            )

    def test_runner_preserves_agent_terminal_failure_reason(self) -> None:
        observation = Observation(
            {
                "terminal_event": {
                    "type": "turn.failed",
                    "reason": "OpenCode completed without a final assistant message",
                },
                "jsonl_parse_errors": [],
                "process": {
                    "executable_missing": False,
                    "timed_out": False,
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
            "",
        )
        self.assertEqual(
            _reason_for_agent(observation),
            (
                "agent terminal event was turn.failed: "
                "OpenCode completed without a final assistant message"
            ),
        )

    def test_runner_distinguishes_output_contract_from_wrong_owner(self) -> None:
        ungradeable = Observation(
            {
                "passed": False,
                "valid": True,
                "semantic_status": "UNSCORABLE",
                "semantic_gradeable": False,
                "format_compliant": False,
                "answer_shape": "PROSE_OR_MALFORMED",
                "expected": {
                    "path": "hashmarks/codemap/indexing_lifecycle.py",
                    "symbol": "_sync_remove_stale_paths",
                },
                "actual": None,
                "normalized_actual": None,
                "reason": "agent final_message is not JSON: Expecting value",
                "actual_text": "The task is complete.",
            },
            "",
        )
        code, detail = _oracle_failure_diagnostic(ungradeable)
        self.assertEqual(code, "output-contract-ungradeable")
        payload = json.loads(detail)
        self.assertEqual(payload["semantic_status"], "UNSCORABLE")
        self.assertFalse(payload["semantic_gradeable"])
        self.assertFalse(payload["format_compliant"])
        self.assertEqual(payload["answer_shape"], "PROSE_OR_MALFORMED")

        incorrect = Observation(
            {
                "passed": False,
                "valid": True,
                "semantic_status": "INCORRECT",
                "semantic_gradeable": True,
                "format_compliant": True,
                "answer_shape": "BARE_JSON",
                "expected": {
                    "path": "hashmarks/codemap/repository_index_store.py",
                    "symbol": "paths_under",
                },
                "actual": {
                    "path": "hashmarks/codemap/store_queries.py",
                    "symbol": "paths_under",
                },
                "normalized_actual": {
                    "path": "hashmarks/codemap/store_queries.py",
                    "symbol": "paths_under",
                },
                "reason": "repository location differs from frozen oracle",
            },
            "",
        )
        code, detail = _oracle_failure_diagnostic(incorrect)
        self.assertEqual(code, "oracle-mismatch")
        payload = json.loads(detail)
        self.assertEqual(
            payload["expected"]["path"],
            "hashmarks/codemap/repository_index_store.py",
        )
        self.assertEqual(
            payload["observed"]["path"],
            "hashmarks/codemap/store_queries.py",
        )

    def test_runner_surfaces_bounded_oracle_actual_text_preview(self) -> None:
        grade = Observation(
            {
                "passed": False,
                "valid": True,
                "reason": "agent final_message is not JSON: Expecting value",
                "actual_text": (
                    "```json\n"
                    "{\n"
                    '  "path": "hashmarks/codemap/repository_file_discovery.py",\n'
                    '  "symbol": "_iter_admitted_repository_files"\n'
                    "}\n"
                    "```"
                ),
            },
            "",
        )
        reason = _reason_for_oracle_failure(grade)
        self.assertIn(
            "agent final_message is not JSON: Expecting value",
            reason,
        )
        self.assertIn("actual='", reason)
        self.assertIn("\\n", reason)
        self.assertIn("_iter_admitted_repository_files", reason)

    def test_runner_bounds_long_oracle_actual_text_preview(self) -> None:
        grade = Observation(
            {
                "passed": False,
                "valid": True,
                "reason": "agent final_message is not JSON: Expecting value",
                "actual_text": "x" * 1_000,
            },
            "",
        )
        reason = _reason_for_oracle_failure(grade)
        self.assertLess(len(reason), 340)
        self.assertIn("…", reason)
        self.assertFalse(reason.endswith("x" * 20))

    def test_opencode_textless_successful_process_remains_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            control.mkdir()
            context = TrialContext(
                workspace,
                control,
                {"BENCHMARK_OPENCODE_AGENT": "build"},
            )
            evidence = {
                "selected_server": None,
                "opencode_executable_path": str(Path("/bin/opencode").resolve()),
                "opencode_executable_sha256": "b" * 64,
                "native_config_sha256": "a" * 64,
                "native_mcp_servers": [],
                "subject_exposure_sha256": None,
                "model": "test-provider/test-model",
                "provider": "test-provider",
                "runtime_contract": "agents-cookbook-opencode-runtime/v4",
            }
            (control / "opencode-native-evidence.json").write_text(
                json.dumps(evidence),
                encoding="utf-8",
            )
            runtime_result = mock.Mock(
                elapsed_ms=100,
                stdout=b"{}",
                stderr=b"",
                executable_missing=False,
                stdout_truncated=False,
            )
            runtime_result.metrics.return_value = {"return_code": 0}
            exported = {
                "messages": [
                    {
                        "info": {
                            "role": "assistant",
                            "modelID": "test-model",
                            "providerID": "test-provider",
                        },
                        "parts": [],
                    }
                ]
            }
            envelope = {
                "session_id": "session-123",
                "run": {"status": 0},
                "export": {
                    "status": 0,
                    "stdout": json.dumps(exported),
                },
                "export_attempts": 4,
                "final_text": None,
                "export_parse_error": None,
            }
            agent = OpenCodeNativeAgent()
            with (
                mock.patch(
                    "benchmarks.adapters.opencode_native._native_environment",
                    return_value={"OPENCODE_BIN": "/bin/opencode"},
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._sha256_file",
                    return_value="b" * 64,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call",
                    return_value=(envelope, runtime_result),
                ),
            ):
                observation = agent.run(context, "prompt", None)

            self.assertFalse(observation.payload["terminal_complete"])
            self.assertIsNone(observation.payload["final_message"])
            self.assertEqual(observation.measurements["session_export_attempts"], 4)
            self.assertEqual(
                observation.payload["terminal_event"],
                {
                    "type": "turn.failed",
                    "reason": "OpenCode completed without a final assistant message",
                },
            )

    def test_opencode_run_rejects_replaced_host_executable_before_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            control.mkdir()
            context = TrialContext(
                workspace,
                control,
                {"BENCHMARK_OPENCODE_AGENT": "build"},
            )
            evidence = {
                "selected_server": None,
                "opencode_executable_path": str(Path("/bin/opencode").resolve()),
                "opencode_executable_sha256": "a" * 64,
                "native_config_sha256": "c" * 64,
                "native_mcp_servers": [],
                "subject_exposure_sha256": None,
                "model": "test-provider/test-model",
                "provider": "test-provider",
                "runtime_contract": "agents-cookbook-opencode-runtime/v4",
            }
            (control / "opencode-native-evidence.json").write_text(
                json.dumps(evidence),
                encoding="utf-8",
            )
            agent = OpenCodeNativeAgent()
            with (
                mock.patch(
                    "benchmarks.adapters.opencode_native._native_environment",
                    return_value={"OPENCODE_BIN": "/bin/opencode"},
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._sha256_file",
                    return_value="b" * 64,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call"
                ) as runtime_call,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "OpenCode executable authority changed after admission",
                ):
                    agent.run(context, "prompt", None)
            runtime_call.assert_not_called()

    def test_opencode_rejects_subject_source_drift_before_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            control.mkdir()
            context = TrialContext(
                workspace,
                control,
                {"BENCHMARK_OPENCODE_AGENT": "build"},
            )
            admitted_source = {"commit": "a" * 40, "tree": "b" * 40}
            current_source = {"commit": "c" * 40, "tree": "d" * 40}
            native_subject = {"executable_sha256": "e" * 64}
            evidence = {
                "selected_server": "hashmarks",
                "opencode_executable_path": str(Path("/bin/opencode").resolve()),
                "opencode_executable_sha256": "f" * 64,
                "native_config_sha256": "1" * 64,
                "native_mcp_servers": [],
                "subject_exposure_sha256": "2" * 64,
                "native_subject_identity": native_subject,
                "native_subject_identity_sha256": digest(native_subject),
                "subject_source_identity_sha256": digest(admitted_source),
                "model": "test-provider/test-model",
                "provider": "test-provider",
                "runtime_contract": "agents-cookbook-opencode-runtime/v4",
            }
            (control / "opencode-native-evidence.json").write_text(
                json.dumps(evidence),
                encoding="utf-8",
            )
            subject = mock.Mock()
            subject.source_identity.return_value = (current_source, None)
            agent = OpenCodeNativeAgent()
            with (
                mock.patch(
                    "benchmarks.adapters.opencode_native._native_environment",
                    return_value={"OPENCODE_BIN": "/bin/opencode"},
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._sha256_file",
                    return_value="f" * 64,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call"
                ) as runtime_call,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "subject source authority changed after admission",
                ):
                    agent.run(context, "prompt", subject)
            runtime_call.assert_not_called()

    def test_opencode_rejects_subject_source_drift_during_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            control.mkdir()
            context = TrialContext(
                workspace,
                control,
                {"BENCHMARK_OPENCODE_AGENT": "build"},
            )
            admitted_source = {"commit": "a" * 40, "tree": "b" * 40}
            changed_source = {"commit": "c" * 40, "tree": "d" * 40}
            native_subject = {"executable_sha256": "e" * 64}
            evidence = {
                "selected_server": "hashmarks",
                "opencode_executable_path": str(Path("/bin/opencode").resolve()),
                "opencode_executable_sha256": "f" * 64,
                "native_config_sha256": "1" * 64,
                "native_mcp_servers": [],
                "subject_exposure_sha256": "2" * 64,
                "native_subject_identity": native_subject,
                "native_subject_identity_sha256": digest(native_subject),
                "subject_source_identity_sha256": digest(admitted_source),
                "model": "test-provider/test-model",
                "provider": "test-provider",
                "runtime_contract": "agents-cookbook-opencode-runtime/v4",
            }
            (control / "opencode-native-evidence.json").write_text(
                json.dumps(evidence),
                encoding="utf-8",
            )
            subject = mock.Mock()
            subject.source_identity.side_effect = [
                (admitted_source, None),
                (changed_source, None),
            ]
            runtime_result = mock.Mock(
                elapsed_ms=100,
                stdout=b"{}",
                stderr=b"",
                executable_missing=False,
                stdout_truncated=False,
            )
            runtime_result.metrics.return_value = {"return_code": 0}
            exported = {
                "messages": [
                    {
                        "info": {
                            "role": "assistant",
                            "modelID": "test-model",
                            "providerID": "test-provider",
                        },
                        "parts": [{"type": "text", "text": "done"}],
                    }
                ]
            }
            envelope = {
                "operation_id": "operation-123",
                "session_id": "session-123",
                "authority_revalidation": {"status": "verified"},
                "run": {"status": 0},
                "export": {"status": 0, "stdout": json.dumps(exported)},
                "final_text": "done",
                "export_parse_error": None,
            }
            agent = OpenCodeNativeAgent()
            with (
                mock.patch(
                    "benchmarks.adapters.opencode_native._native_environment",
                    return_value={"OPENCODE_BIN": "/bin/opencode"},
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._sha256_file",
                    return_value="f" * 64,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call",
                    return_value=(envelope, runtime_result),
                ),
            ):
                observation = agent.run(context, "prompt", subject)

            self.assertFalse(observation.payload["terminal_complete"])
            self.assertIsNone(observation.payload["final_message"])
            self.assertEqual(
                observation.payload["terminal_event"]["reason"],
                "benchmark subject source authority changed during inference",
            )
            self.assertEqual(observation.payload["operation_id"], "operation-123")

    def test_opencode_nonzero_run_reports_status_and_bounded_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            control.mkdir()
            context = TrialContext(
                workspace,
                control,
                {"BENCHMARK_OPENCODE_AGENT": "build"},
            )
            evidence = {
                "selected_server": None,
                "opencode_executable_path": str(Path("/bin/opencode").resolve()),
                "opencode_executable_sha256": "b" * 64,
                "native_config_sha256": "a" * 64,
                "native_mcp_servers": [],
                "subject_exposure_sha256": None,
                "model": "test-provider/test-model",
                "provider": "test-provider",
                "runtime_contract": "agents-cookbook-opencode-runtime/v4",
            }
            (control / "opencode-native-evidence.json").write_text(
                json.dumps(evidence),
                encoding="utf-8",
            )
            runtime_result = mock.Mock(
                elapsed_ms=100,
                stdout=b"{}",
                stderr=b"",
                executable_missing=False,
                stdout_truncated=False,
            )
            runtime_result.metrics.return_value = {"return_code": 0}
            exported = {
                "messages": [
                    {
                        "info": {
                            "role": "assistant",
                            "modelID": "test-model",
                            "providerID": "test-provider",
                        },
                        "parts": [],
                    }
                ]
            }
            envelope = {
                "session_id": "session-123",
                "run": {
                    "status": 7,
                    "stderr": "provider failed: " + ("x" * 4_000),
                    "error": None,
                    "signal": None,
                },
                "export": {
                    "status": 0,
                    "stdout": json.dumps(exported),
                },
                "final_text": None,
                "export_parse_error": None,
            }
            agent = OpenCodeNativeAgent()
            with (
                mock.patch(
                    "benchmarks.adapters.opencode_native._native_environment",
                    return_value={"OPENCODE_BIN": "/bin/opencode"},
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._sha256_file",
                    return_value="b" * 64,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call",
                    return_value=(envelope, runtime_result),
                ),
            ):
                observation = agent.run(context, "prompt", None)

            terminal = observation.payload["terminal_event"]
            self.assertFalse(observation.payload["terminal_complete"])
            self.assertEqual(terminal["type"], "turn.failed")
            self.assertIn("OpenCode run exited with status 7", terminal["reason"])
            self.assertIn("provider failed:", terminal["reason"])
            self.assertLess(len(terminal["reason"]), 2_200)
            self.assertTrue(terminal["reason"].endswith("…'"))

    def test_opencode_nonzero_run_falls_back_to_bounded_stdout_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            control.mkdir()
            context = TrialContext(
                workspace,
                control,
                {"BENCHMARK_OPENCODE_AGENT": "build"},
            )
            evidence = {
                "selected_server": None,
                "opencode_executable_path": str(Path("/bin/opencode").resolve()),
                "opencode_executable_sha256": "b" * 64,
                "native_config_sha256": "a" * 64,
                "native_mcp_servers": [],
                "subject_exposure_sha256": None,
                "model": "test-provider/test-model",
                "provider": "test-provider",
                "runtime_contract": "agents-cookbook-opencode-runtime/v4",
            }
            (control / "opencode-native-evidence.json").write_text(
                json.dumps(evidence),
                encoding="utf-8",
            )
            runtime_result = mock.Mock(
                elapsed_ms=100,
                stdout=b"{}",
                stderr=b"",
                executable_missing=False,
                stdout_truncated=False,
            )
            runtime_result.metrics.return_value = {"return_code": 0}
            stdout = (
                '{"type":"progress","message":"' + ("x" * 4_000) + '"}\n'
                '{"type":"error","message":"provider unavailable"}\n'
            )
            envelope = {
                "session_id": None,
                "run": {
                    "status": 1,
                    "stdout": stdout,
                    "stderr": "",
                    "error": None,
                    "signal": None,
                },
                "export": None,
                "final_text": None,
                "export_parse_error": None,
            }
            agent = OpenCodeNativeAgent()
            with (
                mock.patch(
                    "benchmarks.adapters.opencode_native._native_environment",
                    return_value={"OPENCODE_BIN": "/bin/opencode"},
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._sha256_file",
                    return_value="b" * 64,
                ),
                mock.patch(
                    "benchmarks.adapters.opencode_native._runtime_call",
                    return_value=(envelope, runtime_result),
                ),
            ):
                observation = agent.run(context, "prompt", None)

            terminal = observation.payload["terminal_event"]
            self.assertEqual(terminal["type"], "turn.failed")
            self.assertIn("OpenCode run exited with status 1", terminal["reason"])
            self.assertIn("stdout_tail=", terminal["reason"])
            self.assertIn("provider unavailable", terminal["reason"])
            self.assertLess(len(terminal["reason"]), 2_200)

    def test_opencode_export_observes_native_model_and_mcp_adoption(self) -> None:
        exported = {
            "messages": [
                {
                    "info": {
                        "role": "assistant",
                        "providerID": "liteLLM",
                        "modelID": "gemma4",
                        "tokens": {
                            "input": 100,
                            "output": 20,
                            "cache": {"read": 5},
                        },
                    },
                    "parts": [
                        {
                            "type": "tool",
                            "tool": "hashmarks_find",
                            "state": {"output": "evidence"},
                        },
                        {
                            "type": "tool",
                            "tool": "bash",
                            "state": {"output": ""},
                        },
                        {
                            "type": "text",
                            "text": '{"path":"x","symbol":"y"}',
                        },
                    ],
                }
            ]
        }
        model, provider = opencode_observed_model(exported)
        self.assertEqual(model, "liteLLM/gemma4")
        self.assertEqual(provider, "liteLLM")
        metrics = opencode_metrics(
            exported,
            mcp_servers=("hashmarks", "enola"),
            selected_server="hashmarks",
        )
        self.assertEqual(metrics["mcp_calls"], 1)
        self.assertEqual(metrics["subject_mcp_calls"], 1)
        self.assertEqual(metrics["subject_mcp_calls_observed"], 1)
        self.assertEqual(metrics["subject_tool_names"], ["find"])
        self.assertEqual(metrics["subject_tool_observability"], "complete")
        self.assertEqual(
            metrics["subject_tool_result_evidence"],
            [
                {
                    "operation": "find",
                    "status": None,
                    "result_bytes": len("evidence".encode()),
                    "error_present": False,
                    "result_basis": "opencode-direct-export",
                    "outcome": "successful-result-observed",
                }
            ],
        )
        self.assertTrue(metrics["subject_tool_invoked"])
        self.assertEqual(metrics["command_calls"], 1)
        self.assertEqual(metrics["input_tokens"], 100)
        self.assertEqual(metrics["output_tokens"], 20)
        self.assertEqual(metrics["cached_input_tokens"], 5)
        self.assertEqual(metrics["mcp_result_bytes"], len("evidence".encode()))
        self.assertEqual(
            metrics["tool_strategy_observability"],
            "opencode-export-direct+execute-metadata",
        )
        self.assertEqual(
            metrics["tool_sequence"],
            ["hashmarks_find", "bash"],
        )
        self.assertEqual(
            metrics["tool_name_counts"],
            {"bash": 1, "hashmarks_find": 1},
        )
        self.assertEqual(metrics["subject_tool_call_ordinals"], [1])
        self.assertEqual(metrics["subject_first_tool_call_ordinal"], 1)

    def test_opencode_code_mode_counts_child_calls_without_guessing_bytes(self) -> None:
        exported = {
            "messages": [
                {
                    "info": {"role": "assistant"},
                    "parts": [
                        {
                            "type": "tool",
                            "tool": "execute",
                            "state": {
                                "output": "combined script output",
                                "metadata": {
                                    "toolCalls": [
                                        {
                                            "tool": "hashmarks.find",
                                            "status": "completed",
                                        },
                                        {
                                            "tool": "tools.enola.explain",
                                            "status": "completed",
                                        },
                                    ]
                                },
                            },
                        },
                        {
                            "type": "tool",
                            "tool": "hashmarks_find",
                            "state": {"output": "direct result"},
                        },
                    ],
                }
            ]
        }
        metrics = opencode_metrics(
            exported,
            mcp_servers=("hashmarks", "enola"),
            selected_server="hashmarks",
        )
        self.assertEqual(metrics["tool_calls"], 2)
        self.assertEqual(metrics["mcp_calls"], 3)
        self.assertEqual(metrics["subject_mcp_calls"], 2)
        self.assertEqual(metrics["subject_mcp_calls_observed"], 2)
        self.assertEqual(metrics["subject_tool_names"], ["find"])
        self.assertEqual(metrics["subject_tool_observability"], "complete")
        self.assertEqual(
            [row["outcome"] for row in metrics["subject_tool_result_evidence"]],
            ["successful-result-observed", "result-unobserved"],
        )
        self.assertEqual(
            [row["result_basis"] for row in metrics["subject_tool_result_evidence"]],
            ["opencode-direct-export", "opencode-execute-metadata"],
        )
        self.assertTrue(metrics["subject_tool_invoked"])
        self.assertNotIn("mcp_result_bytes", metrics)
        self.assertEqual(
            metrics["tool_strategy_observability"],
            "opencode-export-direct+execute-metadata",
        )
        self.assertEqual(
            metrics["tool_sequence"],
            [
                "execute",
                "nested:hashmarks.find",
                "nested:tools.enola.explain",
                "hashmarks_find",
            ],
        )
        self.assertEqual(metrics["subject_tool_call_ordinals"], [2, 4])
        self.assertEqual(metrics["subject_first_tool_call_ordinal"], 2)

        exported["messages"][0]["parts"][0]["state"].pop("metadata")
        partial = opencode_metrics(
            exported,
            mcp_servers=("hashmarks", "enola"),
            selected_server="hashmarks",
        )
        self.assertNotIn("mcp_calls", partial)
        self.assertNotIn("subject_mcp_calls", partial)
        self.assertNotIn("mcp_result_bytes", partial)
        self.assertEqual(partial["subject_mcp_calls_observed"], 1)
        self.assertEqual(partial["subject_tool_names"], ["find"])
        self.assertEqual(partial["subject_tool_observability"], "partial")
        self.assertEqual(
            partial["subject_tool_result_evidence"],
            [
                {
                    "operation": "find",
                    "status": None,
                    "result_bytes": len("direct result".encode()),
                    "error_present": False,
                    "result_basis": "opencode-direct-export",
                    "outcome": "successful-result-observed",
                }
            ],
        )
        self.assertTrue(partial["subject_tool_invoked"])
        self.assertEqual(
            partial["tool_strategy_observability"],
            "opencode-export-direct-only-partial",
        )
        self.assertEqual(partial["tool_sequence"], ["execute", "hashmarks_find"])
        self.assertEqual(partial["subject_tool_call_ordinals"], [2])
        self.assertEqual(partial["subject_first_tool_call_ordinal"], 2)

    def test_codex_jsonl_separates_tool_availability_from_adoption(self) -> None:
        raw = (
            b"\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "t"}).encode(),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "m1",
                                "type": "mcp_tool_call",
                                "server": "hashmarks",
                                "tool": "find",
                                "arguments": {"query": "owner"},
                                "result": {"content": "evidence"},
                                "error": None,
                                "status": "completed",
                            },
                        }
                    ).encode(),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "c1",
                                "type": "command_execution",
                                "command": "git status",
                                "aggregated_output": "",
                                "exit_code": 0,
                                "status": "completed",
                            },
                        }
                    ).encode(),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "a1",
                                "type": "agent_message",
                                "text": '{"path":"x","symbol":"y"}',
                            },
                        }
                    ).encode(),
                    json.dumps(
                        {
                            "type": "turn.completed",
                            "usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 20,
                                "output_tokens": 12,
                            },
                        }
                    ).encode(),
                ]
            )
            + b"\n"
        )
        events, errors = parse_codex_jsonl(raw)
        self.assertEqual(errors, [])
        metrics = _metrics(events, subject_server="hashmarks")
        self.assertEqual(metrics["mcp_calls"], 1)
        self.assertEqual(metrics["subject_mcp_calls"], 1)
        self.assertTrue(metrics["subject_tool_invoked"])
        self.assertEqual(metrics["command_calls"], 1)
        self.assertEqual(metrics["tool_calls"], 2)
        self.assertTrue(metrics["subject_tool_configured"])
        self.assertEqual(metrics["subject_tool_names"], ["find"])
        self.assertEqual(metrics["subject_tool_observability"], "complete")
        self.assertEqual(
            metrics["subject_tool_result_evidence"][0]["outcome"],
            "successful-result-observed",
        )
        self.assertEqual(
            metrics["subject_tool_result_evidence"][0]["operation"],
            "find",
        )
        self.assertGreater(
            metrics["subject_tool_result_evidence"][0]["result_bytes"],
            0,
        )
        self.assertEqual(metrics["input_tokens"], 100)
        self.assertEqual(metrics["tool_strategy_observability"], "codex-item-completed")
        self.assertEqual(
            metrics["tool_sequence"],
            ["mcp:hashmarks/find", "command_execution"],
        )
        self.assertEqual(
            metrics["tool_name_counts"],
            {"command_execution": 1, "mcp:hashmarks/find": 1},
        )
        self.assertEqual(metrics["subject_tool_call_ordinals"], [1])
        self.assertEqual(metrics["subject_first_tool_call_ordinal"], 1)
        self.assertNotIn("git status", json.dumps(metrics))
        self.assertEqual(
            _final_message(events),
            '{"path":"x","symbol":"y"}',
        )
        continued = events[:-1] + [
            {"type": "item.completed", "item": {"type": "command_execution"}},
            events[-1],
        ]
        self.assertIsNone(_final_message(continued))
        failed = events[:-1] + [{"type": "turn.failed"}]
        self.assertIsNone(_final_message(failed))

        bare = _metrics(events, subject_server=None)
        self.assertEqual(bare["subject_mcp_calls"], 0)
        self.assertFalse(bare["subject_tool_invoked"])

    def test_codex_auth_seed_copies_only_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            environment = isolated_environment(control)
            context = TrialContext(workspace, control, environment)
            auth = root / "auth.json"
            auth.write_text('{"token":"secret"}\n', encoding="utf-8")

            mode = seed_codex_auth(context, auth)

            target = Path(environment["CODEX_HOME"]) / "auth.json"
            self.assertEqual(mode, "seeded-auth-file")
            self.assertEqual(target.read_bytes(), auth.read_bytes())
            self.assertEqual(
                environment["BENCHMARK_CODEX_AUTH_MODE"],
                "seeded-auth-file",
            )
            self.assertNotIn(str(auth), json.dumps(environment))

    def test_contamination_allows_declared_output_but_not_source_residue(self) -> None:
        before = {
            "a.py": {"kind": "file", "size": 1, "sha256": "a"},
        }
        after = {
            "a.py": {"kind": "file", "size": 1, "sha256": "a"},
            ".benchmark-enola/facts.jsonl": {
                "kind": "file",
                "size": 2,
                "sha256": "b",
            },
            "unexpected.txt": {"kind": "file", "size": 1, "sha256": "c"},
        }
        result = classify_contamination(
            before=before,
            after=after,
            allowed_change_globs=(),
            allowed_generated_globs=(".benchmark-enola/**",),
        )
        self.assertTrue(result["contaminated"])
        self.assertEqual(result["unexpected"]["added"], ["unexpected.txt"])

    def test_competing_repository_cache_generation_fails_before_git_work(
        self,
    ) -> None:
        repository = {
            "url": "https://example.invalid/repository.git",
            "commit": "a" * 40,
            "tree": "b" * 40,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            with (
                _source_cache_lock(cache, repository["url"]),
                mock.patch("benchmarks.harness.source._run_git") as run_git,
                mock.patch("benchmarks.harness.source.materialize_git") as materialize,
            ):
                with self.assertRaisesRegex(
                    WorkspaceError,
                    "source cache is already in use",
                ):
                    materialize_repository(
                        repository=repository,
                        destination=root / "workspace",
                        cache_root=cache,
                    )
            run_git.assert_not_called()
            materialize.assert_not_called()

    def test_cycle_fixture_mutation_accounts_for_new_files(self) -> None:
        suite = load_suite(CYCLE)
        task = suite.tasks["recent-orders-without-cycle"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            materialize_repository(
                repository=task["repository"],
                destination=workspace,
                cache_root=root / "cache",
                local_source=PILOT_SOURCE_BUNDLE,
            )
            control = root / "control"
            context = TrialContext(workspace, control, isolated_environment(control))
            mutation = apply_mutation(
                context, suite_root=suite.root, mutation=task["mutation"]
            )
            self.assertEqual(
                mutation.payload["changed_paths"],
                sorted(task["mutation"]["changed_paths"]),
            )

    def test_pilot_mutation_is_exact_and_oracle_discriminates(self) -> None:
        suite = load_suite(PILOT)
        task = suite.tasks["repair-partial-receipt-regression"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            materialize_repository(
                repository=task["repository"],
                destination=workspace,
                cache_root=root / "cache",
                local_source=PILOT_SOURCE_BUNDLE,
            )
            control = root / "control"
            context = TrialContext(
                workspace,
                control,
                isolated_environment(control),
            )
            mutation = apply_mutation(
                context,
                suite_root=suite.root,
                mutation=task["mutation"],
            )
            self.assertEqual(
                mutation.payload["changed_paths"],
                ["benchmarks/harness/receipt.py"],
            )

            environment = dict(context.environment)
            environment["PYTHONPATH"] = str(workspace)
            failed = run_bounded(
                repository_root=workspace,
                argv=(
                    sys.executable,
                    "-m",
                    "unittest",
                    "benchmarks.tests.test_foundation.FoundationTests.test_partial_or_corrupt_receipt_is_not_complete",
                ),
                environment=environment,
                limits=ProcessLimits(timeout_seconds=60),
            )
            self.assertNotEqual(failed.return_code, 0)

            subprocess.run(
                (
                    "git",
                    "checkout",
                    PINNED_COMMIT,
                    "--",
                    "benchmarks/harness/receipt.py",
                ),
                cwd=workspace,
                check=True,
                capture_output=True,
            )
            passed = run_bounded(
                repository_root=workspace,
                argv=(
                    sys.executable,
                    "-m",
                    "unittest",
                    "benchmarks.tests.test_foundation.FoundationTests.test_partial_or_corrupt_receipt_is_not_complete",
                ),
                environment=environment,
                limits=ProcessLimits(timeout_seconds=60),
            )
            self.assertEqual(
                passed.return_code,
                0,
                passed.stderr.decode("utf-8", errors="replace"),
            )

    def test_agent_matrix_v2_mutation_is_exact_and_oracle_discriminates(self) -> None:
        suite = load_suite(MATRIX_V2)
        task = suite.tasks["repair-partial-receipt-regression"]
        self.assertEqual(task["repository"]["commit"], MATRIX_V2_COMMIT)
        self.assertEqual(task["repository"]["tree"], MATRIX_V2_TREE)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            materialize_repository(
                repository=task["repository"],
                destination=workspace,
                cache_root=root / "cache",
                local_source=PILOT_SOURCE_BUNDLE,
            )
            control = root / "control"
            context = TrialContext(
                workspace,
                control,
                isolated_environment(control),
            )
            mutation = apply_mutation(
                context,
                suite_root=suite.root,
                mutation=task["mutation"],
            )
            self.assertEqual(
                mutation.payload["changed_paths"],
                ["benchmarks/harness/receipt.py"],
            )

            environment = dict(context.environment)
            environment["PYTHONPATH"] = str(workspace)
            failed = run_bounded(
                repository_root=workspace,
                argv=(
                    sys.executable,
                    "-m",
                    "unittest",
                    "benchmarks.tests.test_foundation.FoundationTests.test_partial_or_corrupt_receipt_is_not_complete",
                ),
                environment=environment,
                limits=ProcessLimits(timeout_seconds=60),
            )
            self.assertNotEqual(failed.return_code, 0)

            subprocess.run(
                (
                    "git",
                    "checkout",
                    MATRIX_V2_COMMIT,
                    "--",
                    "benchmarks/harness/receipt.py",
                ),
                cwd=workspace,
                check=True,
                capture_output=True,
            )
            passed = run_bounded(
                repository_root=workspace,
                argv=(
                    sys.executable,
                    "-m",
                    "unittest",
                    "benchmarks.tests.test_foundation.FoundationTests.test_partial_or_corrupt_receipt_is_not_complete",
                ),
                environment=environment,
                limits=ProcessLimits(timeout_seconds=60),
            )
            self.assertEqual(
                passed.return_code,
                0,
                passed.stderr.decode("utf-8", errors="replace"),
            )

    def test_report_keeps_cross_agent_rows_descriptive(self) -> None:
        suite = _legacy_matrix_suite()
        task_id = "locate-receipt-completion-owner"
        rows = {
            (row["task_id"], row["condition_id"]): row
            for row in suite.trial_definitions()
        }
        receipts = []
        for condition_id, status, duration in (
            ("bare-sol", "PASS", 100.0),
            ("bare-opencode-native", "FAIL", 250.0),
        ):
            row = rows[(task_id, condition_id)]
            condition = next(
                value
                for value in suite.experiment["conditions"]
                if value["id"] == condition_id
            )
            receipts.append(
                {
                    "definition_id": row["definition_id"],
                    "trial_id": ("a" * 64 if condition_id == "bare-sol" else "b" * 64),
                    "experiment": suite.experiment,
                    "task": suite.tasks[task_id],
                    "condition": suite.expanded_condition(condition),
                    "status": status,
                    "authority": {"subject": {"available": True}},
                    "execution": {"trial_index": 0, "seed": 5201},
                    "measurements": {
                        "agent": {
                            "duration_ms": duration,
                            "tool_calls": 1,
                            "command_calls": 1,
                            "mcp_calls": 0,
                            "subject_mcp_calls": 0,
                            "mcp_result_bytes": 0,
                            "input_tokens": 10,
                            "cached_input_tokens": 0,
                            "output_tokens": 2,
                            "subject_tool_invoked": False,
                        }
                    },
                }
            )

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                require_complete=False,
            )

        self.assertEqual(
            set(report["agent_profiles"]),
            {"codex-sol", "opencode-native"},
        )
        observations = report["cross_agent_observations"]
        self.assertEqual(len(observations), 1)
        self.assertEqual(
            set(observations[0]["agents"]),
            {"codex-sol", "opencode-native"},
        )
        self.assertNotIn("winner", observations[0])
        self.assertTrue(report["authority"]["cross_agent_rows_are_descriptive"])

    def test_report_excludes_unobserved_code_mode_adoption(self) -> None:
        suite = _legacy_matrix_suite()
        condition = next(
            row
            for row in suite.experiment["conditions"]
            if row["id"] == "hashmarks-opencode-native"
        )
        selected = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == condition["id"]
        ][:2]
        receipts = []
        for index, row in enumerate(selected):
            measurements = {"subject_tool_configured": True}
            if index == 0:
                measurements["subject_tool_invoked"] = True
                measurements["subject_mcp_calls"] = 1
            receipts.append(
                {
                    "definition_id": row["definition_id"],
                    "trial_id": ("a" if index == 0 else "b") * 64,
                    "experiment": suite.experiment,
                    "task": suite.tasks[row["task_id"]],
                    "condition": suite.expanded_condition(condition),
                    "status": "PASS",
                    "authority": {"subject": {"available": True}},
                    "execution": {"trial_index": 0, "seed": row["seed"]},
                    "measurements": {"agent": measurements},
                }
            )
        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                selected_definitions={row["definition_id"] for row in selected},
            )
        profile = report["conditions"][condition["id"]]
        self.assertEqual(report["schema"]["version"], 21)
        self.assertEqual(profile["subject_tool_adoption_denominator"], 1)
        self.assertEqual(profile["subject_tool_adoption_rate"], 1.0)
        self.assertEqual(
            profile["metrics"]["subject_mcp_calls"]["observations"],
            1,
        )

        receipts[0]["authority"]["harness"] = {"working_copy_sha256": "a"}
        receipts[1]["authority"]["harness"] = {"working_copy_sha256": "b"}
        with mock.patch("benchmarks.harness.report._receipts", return_value=receipts):
            with self.assertRaisesRegex(ReportError, "mixed harness authority"):
                build_report(
                    suite=suite,
                    results_root=Path("/unused"),
                    selected_definitions={row["definition_id"] for row in selected},
                )

    def test_report_selection_rejects_mixed_native_runtime_authority(self) -> None:
        suite = _legacy_matrix_suite()
        task_id = "locate-receipt-completion-owner"
        definitions = {
            row["condition_id"]: row
            for row in suite.trial_definitions()
            if row["task_id"] == task_id
        }
        receipts = []
        for condition_id, status, model, config_hash in (
            ("bare-opencode-native", "PASS", "liteLLM/gemma4", "a" * 64),
            ("hashmarks-opencode-native", "INVALID", "liteLLM/other", "b" * 64),
        ):
            condition = next(
                row
                for row in suite.experiment["conditions"]
                if row["id"] == condition_id
            )
            receipts.append(
                {
                    "definition_id": definitions[condition_id]["definition_id"],
                    "trial_id": ("c" if status == "PASS" else "d") * 64,
                    "experiment": suite.experiment,
                    "task": suite.tasks[task_id],
                    "condition": suite.expanded_condition(condition),
                    "status": status,
                    "authority": {
                        "subject": {"available": True},
                        "agent": {
                            "observed": {
                                "version": "opencode 1",
                                "executable_sha256": "e" * 64,
                                "auth_mode": "native-opencode",
                                "model": model,
                                "provider": "liteLLM",
                                "native_config_sha256": config_hash,
                            }
                        },
                    },
                    "execution": {"trial_index": 0, "seed": 5201},
                    "measurements": {"agent": {}},
                }
            )
        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
                require_complete=False,
                selected_definitions={
                    definitions["bare-opencode-native"]["definition_id"]
                },
                selection={"agents": ["opencode-native"], "subjects": ["none"]},
            )
            self.assertEqual(report["observed_trials"], 1)
            self.assertEqual(report["selection"]["subjects"], ["none"])
            with self.assertRaisesRegex(ReportError, "mixed observed runtime"):
                build_report(
                    suite=suite,
                    results_root=Path("/unused"),
                    require_complete=False,
                    selected_definitions={
                        row["definition_id"] for row in definitions.values()
                    },
                )

    def test_report_and_status_reject_mixed_subject_authority(self) -> None:
        suite = _legacy_matrix_suite()
        condition = next(
            row
            for row in suite.experiment["conditions"]
            if row["id"] == "hashmarks-opencode-native"
        )
        selected = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == condition["id"]
        ][:2]
        receipts = []
        for index, row in enumerate(selected):
            receipts.append(
                {
                    "definition_id": row["definition_id"],
                    "trial_id": ("f" if index == 0 else "e") * 64,
                    "experiment": suite.experiment,
                    "task": suite.tasks[row["task_id"]],
                    "condition": suite.expanded_condition(condition),
                    "status": "PASS",
                    "authority": {
                        "subject": {
                            "available": True,
                            "declared": {
                                "participant_id": "hashmarks",
                                "kind": "repository_intelligence",
                                "version": "runtime-observed",
                                "provenance": {},
                            },
                            "observed": {
                                "source": "native-agent-runtime",
                                "subject": "hashmarks",
                                "native_subject_identity": {
                                    "verified": True,
                                    "subject": "hashmarks",
                                    "command": "hashmarks",
                                    "executable_sha256": (
                                        ("1" if index == 0 else "2") * 64
                                    ),
                                    "reason_code": None,
                                },
                            },
                        },
                        "agent": {
                            "observed": {
                                "version": "opencode 1",
                                "executable_sha256": "a" * 64,
                                "auth_mode": "native-opencode",
                                "model": "liteLLM/gemma4",
                                "provider": "liteLLM",
                                "native_config_sha256": "b" * 64,
                            }
                        },
                    },
                    "execution": {
                        "trial_index": int(row["trial"]),
                        "seed": int(row["seed"]),
                    },
                    "measurements": {"agent": {}},
                }
            )

        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=receipts,
        ):
            with self.assertRaisesRegex(
                ReportError,
                "mixed observed subject authority",
            ):
                build_report(
                    suite=suite,
                    results_root=Path("/unused"),
                    selected_definitions={row["definition_id"] for row in selected},
                )

        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            by_directory = {}
            for receipt in receipts:
                directory = results / receipt["trial_id"]
                directory.mkdir()
                (directory / "result.json").write_text(
                    json.dumps(receipt),
                    encoding="utf-8",
                )
                by_directory[directory] = receipt
            with mock.patch(
                "benchmarks.harness.campaign.verify_bundle",
                return_value=(True, None),
            ):
                status = campaign_status(
                    suite=suite,
                    results_root=results,
                    selected_definitions={row["definition_id"] for row in selected},
                )
        self.assertTrue(status["complete"])
        self.assertFalse(status["qualified"])
        self.assertIn(
            "mixed observed subject authority",
            status["comparability_error"] or "",
        )

    def test_admission_proves_harness_before_materialization(self) -> None:
        suite = load_suite(MATRIX_V2)
        row = next(
            value
            for value in suite.trial_definitions()
            if value["condition_id"] == "bare-opencode-native"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "benchmarks.harness.admission.harness_identity",
                    side_effect=TrialAdmissionError("dirty harness"),
                ),
                mock.patch(
                    "benchmarks.harness.admission.materialize_repository",
                ) as materialize,
            ):
                with self.assertRaisesRegex(
                    TrialAdmissionError,
                    "dirty harness",
                ):
                    with admit_trial(
                        suite=suite,
                        task_id=str(row["task_id"]),
                        condition_id=str(row["condition_id"]),
                        trial_index=int(row["trial"]),
                        harness_root=Path("."),
                        cache_root=Path(tmp) / "cache",
                        work_root=Path(tmp) / "work",
                    ):
                        self.fail("admission should not yield")
            materialize.assert_not_called()

    def test_agent_native_lifecycle_never_calls_standalone_subject_lifecycle(
        self,
    ) -> None:
        suite = load_suite(MATRIX_V2)
        row = next(
            value
            for value in suite.trial_definitions()
            if value["condition_id"] == "hashmarks-opencode-native"
        )
        mutation = Observation({"identity": None}, "")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def materialize(**kwargs):
                kwargs["destination"].mkdir(parents=True)

            with (
                mock.patch(
                    "benchmarks.harness.admission.harness_identity",
                    return_value={
                        "repository": "fixture",
                        "commit": "1" * 40,
                        "tree": "2" * 40,
                        "contract": "test",
                    },
                ),
                mock.patch(
                    "benchmarks.harness.admission.materialize_repository",
                    side_effect=materialize,
                ),
                mock.patch(
                    "benchmarks.harness.admission.apply_mutation",
                    return_value=mutation,
                ),
                mock.patch(
                    "benchmarks.harness.admission.snapshot",
                    return_value={},
                ),
                mock.patch(
                    "benchmarks.harness.admission.build_subject",
                    return_value=FakeNativeSubject(),
                ),
                mock.patch(
                    "benchmarks.harness.admission.build_agent",
                    return_value=FakeNativeAgent(),
                ),
                mock.patch(
                    "benchmarks.harness.admission.build_oracle",
                    return_value=FakeOracle(),
                ),
            ):
                with admit_trial(
                    suite=suite,
                    task_id=str(row["task_id"]),
                    condition_id=str(row["condition_id"]),
                    trial_index=int(row["trial"]),
                    harness_root=Path("."),
                    cache_root=root / "cache",
                    work_root=root / "work",
                ) as admission:
                    self.assertEqual(
                        admission.subject_lifecycle_mode,
                        SubjectLifecycleMode.AGENT_NATIVE,
                    )
                    self.assertTrue(admission.subject_prepare.payload["available"])
                    self.assertEqual(
                        admission.subject_prepare.payload["lifecycle_owner"],
                        "agent-native",
                    )
                    self.assertEqual(
                        admission.generated_globs(), (".native-generated/**",)
                    )
                    contamination = classify_contamination(
                        before={},
                        after={
                            ".native-generated/receipt.json": {
                                "kind": "file", "size": 1, "sha256": "a"
                            },
                            "unexpected.txt": {
                                "kind": "file", "size": 1, "sha256": "b"
                            },
                        },
                        allowed_change_globs=(),
                        allowed_generated_globs=admission.generated_globs(),
                    )
                    self.assertEqual(
                        contamination["unexpected"],
                        {"added": ["unexpected.txt"]},
                    )
                    self.assertEqual(
                        admission.post_change(("x.py",)).payload["lifecycle_owner"],
                        "agent-native",
                    )
                    self.assertEqual(
                        admission.cleanup_subject().payload["lifecycle_owner"],
                        "agent-native",
                    )

    def test_campaign_root_derives_paths_without_manifest_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "campaign"
            paths = resolve_campaign_paths(
                root=root,
                cache=None,
                work=None,
                results=None,
                need_execution=True,
            )
            self.assertEqual(paths.root, root.resolve())
            self.assertEqual(paths.cache, root.resolve() / "cache")
            self.assertEqual(paths.work, root.resolve() / "work")
            self.assertEqual(paths.results, root.resolve() / "results")

            legacy_preflight = resolve_campaign_paths(
                root=None,
                cache=root / "cache",
                work=root / "work",
                results=None,
                need_execution=True,
                results_optional=True,
            )
            self.assertIsNone(legacy_preflight.results)
            self.assertEqual(legacy_preflight.cache, root / "cache")

            with self.assertRaises(CampaignError):
                resolve_campaign_paths(
                    root=root,
                    cache=root / "other-cache",
                    work=None,
                    results=None,
                    need_execution=True,
                )

    def test_campaign_status_separates_receipts_from_qualification(self) -> None:
        suite = _legacy_matrix_suite()
        selected = [
            row
            for row in suite.trial_definitions()
            if row["condition_id"] == "bare-opencode-native"
        ][:2]
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            first_dir = results / ("a" * 64)
            second_dir = results / ("b" * 64)
            first_dir.mkdir()
            second_dir.mkdir()
            (first_dir / "result.json").write_text(
                json.dumps(
                    {
                        "definition_id": selected[0]["definition_id"],
                        "trial_id": "a" * 64,
                        "status": "PASS",
                    }
                ),
                encoding="utf-8",
            )
            (second_dir / "result.json").write_text(
                json.dumps(
                    {
                        "definition_id": selected[1]["definition_id"],
                        "trial_id": "b" * 64,
                        "status": "INCOMPLETE",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "benchmarks.harness.campaign.verify_bundle",
                return_value=(True, None),
            ):
                status = campaign_status(
                    suite=suite,
                    results_root=results,
                    selected_definitions={
                        str(row["definition_id"]) for row in selected
                    },
                )

            self.assertTrue(status["complete"])
            self.assertFalse(status["qualified"])
            self.assertEqual(status["unresolved_outcome_trials"], 1)
            self.assertEqual(
                status["outcomes"],
                {"INCOMPLETE": 1, "PASS": 1},
            )

    def test_campaign_status_surfaces_conflicts_and_corrupt_bundles(self) -> None:
        suite = load_suite(MATRIX_V2)
        row = next(
            value
            for value in suite.trial_definitions()
            if value["condition_id"] == "bare-opencode-native"
        )
        definition = str(row["definition_id"])
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            for name, trial_id in (("a" * 64, "a" * 64), ("b" * 64, "b" * 64)):
                directory = results / name
                directory.mkdir()
                (directory / "result.json").write_text(
                    json.dumps(
                        {
                            "definition_id": definition,
                            "trial_id": trial_id,
                            "status": "PASS",
                        }
                    ),
                    encoding="utf-8",
                )
            corrupt = results / ("c" * 64)
            corrupt.mkdir()

            def verify(directory: Path):
                if directory == corrupt:
                    return False, "tampered"
                return True, None

            with mock.patch(
                "benchmarks.harness.campaign.verify_bundle",
                side_effect=verify,
            ):
                status = campaign_status(
                    suite=suite,
                    results_root=results,
                    selected_definitions={definition},
                )

        self.assertEqual(status["conflicting_trials"], 1)
        self.assertEqual(len(status["corrupt_bundles"]), 1)
        self.assertFalse(status["complete"])
        self.assertFalse(status["qualified"])
        self.assertEqual(status["rows"][0]["state"], "CONFLICT")

    def test_preflight_reuses_shared_admission_and_rejects_recorded_incomplete(
        self,
    ) -> None:
        suite = load_suite(MATRIX_V2)
        row = next(
            value
            for value in suite.trial_definitions()
            if value["condition_id"] == "bare-opencode-native"
        )
        admission = SimpleNamespace(
            definition_id=str(row["definition_id"]),
            trial_id="c" * 64,
            task=suite.tasks[str(row["task_id"])],
            context=SimpleNamespace(workspace=Path("/workspace")),
            admitted_state={},
            subject=FakeSubject(),
            subject_authority={"available": True},
            agent_authority={"available": True},
            oracle_authority={"healthy": True},
            agent_prepare=Observation(
                {
                    "model": "native/model",
                    "provider": "native",
                },
                "",
            ),
            initial_outcome=lambda: (None, None),
            generated_globs=lambda: (),
            cleanup_subject=lambda: Observation({}, ""),
        )
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            result_dir = results / admission.trial_id
            result_dir.mkdir()
            (result_dir / "result.json").write_text(
                json.dumps({"status": "INCOMPLETE"}),
                encoding="utf-8",
            )
            with (
                mock.patch(
                    "benchmarks.harness.preflight.admit_trial",
                    return_value=nullcontext(admission),
                ),
                mock.patch(
                    "benchmarks.harness.preflight.snapshot",
                    return_value={},
                ),
                mock.patch(
                    "benchmarks.harness.preflight.classify_contamination",
                    return_value={
                        "contaminated": False,
                        "diff": {
                            "added": [],
                            "removed": [],
                            "changed": [],
                        },
                    },
                ),
                mock.patch(
                    "benchmarks.harness.preflight.verify_bundle",
                    return_value=(True, None),
                ),
            ):
                result = preflight_trial(
                    suite=suite,
                    task_id=str(row["task_id"]),
                    condition_id=str(row["condition_id"]),
                    trial_index=int(row["trial"]),
                    harness_root=Path("."),
                    cache_root=Path(tmp) / "cache",
                    work_root=Path(tmp) / "work",
                    results_root=results,
                )

        self.assertEqual(result.status, "RECORDED_INCOMPLETE")
        self.assertEqual(result.existing_result_status, "INCOMPLETE")
        self.assertIn("new campaign root", result.reason or "")

    def test_trial_lifecycle_publishes_and_reuses_verified_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            subprocess.run(("git", "init"), cwd=source, check=True, capture_output=True)
            subprocess.run(
                ("git", "config", "user.email", "bench@example.invalid"),
                cwd=source,
                check=True,
            )
            subprocess.run(
                ("git", "config", "user.name", "Bench"),
                cwd=source,
                check=True,
            )
            (source / "a.txt").write_text("a\n", encoding="utf-8")
            subprocess.run(("git", "add", "a.txt"), cwd=source, check=True)
            subprocess.run(
                ("git", "commit", "-m", "fixture"),
                cwd=source,
                check=True,
                capture_output=True,
            )
            commit = subprocess.run(
                ("git", "rev-parse", "HEAD"),
                cwd=source,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            tree = subprocess.run(
                ("git", "rev-parse", "HEAD^{tree}"),
                cwd=source,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            experiment = {
                "id": "fake-experiment",
                "version": 1,
                "suite": "fake",
                "tasks": ["task"],
                "conditions": [
                    {
                        "id": "condition",
                        "subject": "subject",
                        "agent": "agent",
                        "trials": 1,
                        "seed": 1,
                    }
                ],
                "scoring": {
                    "id": "fake",
                    "version": 1,
                    "metrics": ["task_success"],
                },
            }
            task = {
                "id": "task",
                "version": 1,
                "family": "test",
                "mode": "read_only",
                "repository": {
                    "url": str(source),
                    "commit": commit,
                    "tree": tree,
                },
                "prompt": "answer",
                "mutation": None,
                "oracle": {
                    "adapter": "fake",
                    "identity": {"id": "fake-oracle", "version": "1"},
                    "configuration": {},
                },
                "budgets": {"timeout_seconds": 30},
                "contamination": {
                    "allowed_change_globs": [],
                    "allowed_generated_globs": [],
                },
            }
            suite = SuiteDefinition(
                root=root,
                experiment=experiment,
                tasks={"task": task},
                subjects={
                    "subject": {
                        "id": "subject",
                        "kind": "control",
                        "adapter": "fake",
                        "identity": {"id": "subject", "version": "1"},
                        "capabilities": [],
                        "configuration": {},
                    }
                },
                agents={
                    "agent": {
                        "id": "agent",
                        "adapter": "fake",
                        "identity": {"id": "agent", "version": "1"},
                        "capabilities": [],
                        "configuration": {},
                    }
                },
            )

            patches = (
                mock.patch(
                    "benchmarks.harness.admission.build_subject",
                    return_value=FakeSubject(),
                ),
                mock.patch(
                    "benchmarks.harness.admission.build_agent",
                    return_value=FakeAgent(),
                ),
                mock.patch(
                    "benchmarks.harness.admission.build_oracle",
                    return_value=FakeOracle(),
                ),
                mock.patch(
                    "benchmarks.harness.admission.harness_identity",
                    return_value={
                        "repository": "fixture",
                        "commit": "1" * 40,
                        "tree": "2" * 40,
                        "contract": "test",
                    },
                ),
            )
            for patcher in patches:
                patcher.start()
                self.addCleanup(patcher.stop)

            kwargs = {
                "suite": suite,
                "task_id": "task",
                "condition_id": "condition",
                "trial_index": 0,
                "harness_root": Path("."),
                "cache_root": root / "cache",
                "results_root": root / "results",
                "work_root": root / "work",
                "local_source": source,
            }
            with mock.patch.object(
                FakeAgent, "run", side_effect=AssertionError("model called")
            ):
                checked = preflight_trial(
                    suite=suite,
                    task_id="task",
                    condition_id="condition",
                    trial_index=0,
                    harness_root=Path("."),
                    cache_root=root / "cache",
                    work_root=root / "preflight-work",
                    local_source=source,
                )
            self.assertEqual(checked.status, "READY")
            self.assertFalse((root / "results").exists())
            with mock.patch.object(
                FakeAgent,
                "prepare",
                return_value=Observation(
                    {"available": False, "reason": "missing native MCP"}, ""
                ),
            ):
                blocked = preflight_trial(
                    suite=suite,
                    task_id="task",
                    condition_id="condition",
                    trial_index=0,
                    harness_root=Path("."),
                    cache_root=root / "cache",
                    work_root=root / "preflight-work",
                    local_source=source,
                )
            self.assertEqual(blocked.status, "INCOMPLETE")
            self.assertEqual(blocked.reason, "missing native MCP")
            self.assertFalse((root / "results").exists())
            first = run_trial(**kwargs)
            self.assertEqual(first.status, "PASS")
            self.assertFalse(first.reused)
            self.assertTrue(is_complete_receipt(first.result_dir))
            self.assertTrue((first.result_dir / "events.jsonl").is_file())
            self.assertTrue((first.result_dir / "agent-trace.jsonl").is_file())
            first_receipt = json.loads(
                (first.result_dir / "result.json").read_text(encoding="utf-8")
            )
            admission_cost = first_receipt["measurements"]["admission"]
            self.assertTrue({"materialize", "participant_prepare", "oracle_health", "total"} <= set(admission_cost))
            self.assertTrue(all(type(value) is int and value >= 0 for value in admission_cost.values()))
            self.assertEqual(
                first_receipt["scoring"]["oracle_grade"],
                {"valid": True, "passed": True},
            )
            self.assertIsInstance(
                first_receipt["execution"]["agent_answer"],
                str,
            )
            self.assertRegex(
                first_receipt["execution"]["evidence_identity"],
                r"^[0-9a-f]{64}$",
            )
            self.assertRegex(
                first_receipt["scoring"]["projection_identity"],
                r"^[0-9a-f]{64}$",
            )

            second = run_trial(**kwargs)
            self.assertEqual(second.trial_id, first.trial_id)
            self.assertTrue(second.reused)
            self.assertEqual(second.status, "PASS")
            self.assertEqual(
                json.loads((second.result_dir / "result.json").read_text())["measurements"]["admission"],
                admission_cost,
            )

            report = build_report(
                suite=suite,
                results_root=root / "results",
            )
            self.assertEqual(report["observed_trials"], 1)
            self.assertEqual(report["status_counts"], {"PASS": 1})
            self.assertEqual(
                report["campaign_qualification"]["status"],
                "QUALIFIED",
            )
            self.assertTrue(report["campaign_qualification"]["complete"])
            self.assertEqual(
                report["campaign_qualification"]["invalid_outcomes"],
                0,
            )
            self.assertFalse(report["authority"]["ranking_performed"])
            valid, reason = verify_bundle(first.result_dir)
            self.assertTrue(valid, reason)

            (first.result_dir / "undeclared.txt").write_text("tamper")
            valid, reason = verify_bundle(first.result_dir)
            self.assertFalse(valid)
            self.assertIn("undeclared", reason or "")
            with self.assertRaises(ReportError):
                build_report(
                    suite=suite,
                    results_root=root / "results",
                )

    def test_interrupted_launch_is_recorded_then_retried_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            agent = mock.Mock()
            agent.run.return_value = Observation(
                {
                    "terminal_event": {"type": "turn.completed"},
                    "terminal_complete": True,
                    "final_message": '{"ok":true}',
                    "jsonl_parse_errors": [],
                    "process": {
                        "executable_missing": False,
                        "timed_out": False,
                        "stdout_truncated": False,
                        "stderr_truncated": False,
                    },
                },
                '{"type":"turn.completed"}\n',
                {},
            )
            admission = SimpleNamespace(
                trial_id="a" * 64,
                definition_id="b" * 64,
                admission_timings_ms={},
                context=SimpleNamespace(workspace=workspace),
                legacy_seed=False,
                replicate_id=6201,
                trial_index=0,
                task={
                    "id": "task",
                    "repository": {"path": "."},
                    "prompt": "find owner",
                    "mode": "read_only",
                    "budgets": {"timeout_seconds": 1},
                    "contamination": {
                        "allowed_change_globs": [],
                        "allowed_generated_globs": [],
                    },
                },
                expanded_condition={"id": "condition"},
                admitted_state={"files": {}},
                mutation=Observation({}, "", {}),
                subject=FakeSubject(),
                agent=agent,
                oracle=FakeOracle(),
                auth_mode="not-applicable",
                mutation_authority=None,
                subject_authority={"declared": {"id": "none"}},
                agent_authority={"declared": {"id": "agent"}},
                oracle_authority={"declared": {"id": "oracle", "version": "1"}},
                harness_authority={"commit": "old-authority"},
                environment_authority={"runtime": "old-authority"},
                suite=SimpleNamespace(
                    experiment={"id": "experiment", "suite": "test"}
                ),
                subject_prepare=Observation({}, "", {}),
                agent_prepare=Observation({}, "", {}),
                oracle_health=Observation({}, "", {}),
                cleanup_subject=lambda: Observation(
                    {"lifecycle_owner": "recovery"},
                    "",
                    {},
                ),
                generated_globs=lambda: (),
                post_change=lambda changed_paths: Observation(
                    {"changed_paths": list(changed_paths)},
                    "",
                    {},
                ),
                initial_outcome=lambda: (None, None),
            )
            stages = []
            with (
                mock.patch(
                    "benchmarks.harness.runner.admit_trial",
                    return_value=nullcontext(admission),
                ),
                mock.patch(
                    "benchmarks.harness.runner.verify_trial_authority",
                    return_value=AuthorityEpochBinding(
                        epoch=1,
                        epoch_id="e" * 64,
                        authority={
                            "subject": admission.subject_authority,
                            "agent": admission.agent_authority,
                            "harness": admission.harness_authority,
                            "environment": admission.environment_authority,
                        },
                        transitioned=False,
                    ),
                ),
                mock.patch(
                    "benchmarks.harness.runner.campaign_trial_id",
                    return_value=admission.trial_id,
                ),
                mock.patch(
                    "benchmarks.harness.runner.launch_state",
                    return_value="INTERRUPTED",
                ),
                mock.patch(
                    "benchmarks.harness.runner.record_interrupted_attempt",
                    return_value=1,
                ) as interrupted,
                mock.patch(
                    "benchmarks.harness.runner.claim_launch",
                    return_value=2,
                ) as claim,
                mock.patch(
                    "benchmarks.harness.runner.snapshot",
                    return_value={"files": {}},
                ),
            ):
                result = run_trial(
                    suite=admission.suite,
                    task_id="task",
                    condition_id="condition",
                    trial_index=0,
                    harness_root=root,
                    cache_root=root / "cache",
                    results_root=root / "results",
                    work_root=root / "work",
                    campaign={"campaign_id": "c" * 64},
                    on_progress=stages.append,
                )

            interrupted.assert_called_once()
            claim.assert_called_once()
            agent.run.assert_called_once()
            self.assertEqual(stages[0:2], ["admission", "recovery"])
            self.assertIn("agent-execution", stages)
            self.assertTrue(result.recovered)
            self.assertFalse(result.reused)
            self.assertEqual(result.status, "PASS")
            valid, reason = verify_bundle(result.result_dir)
            self.assertTrue(valid, reason)
            events = (result.result_dir / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn('"kind":"trial.recovered_interruption"', events)
            self.assertIn('"attempt":1', events)
            self.assertIn('"attempt":2', events)

    def test_report_economics_keep_invalid_trial_costs(self) -> None:
        experiment = {
            "id": "economics",
            "version": 1,
            "suite": "fake",
            "tasks": ["task"],
            "conditions": [
                {
                    "id": "bare",
                    "subject": "none",
                    "agent": "agent",
                    "trials": 1,
                    "seed": 7,
                }
            ],
            "scoring": {"id": "s", "version": 1, "metrics": ["task_success"]},
        }
        task = {
            "id": "task",
            "version": 1,
            "repository": {
                "url": "https://example.invalid/repo.git",
                "commit": "1" * 40,
                "tree": "2" * 40,
            },
            "prompt": "x",
            "mode": "read_only",
            "mutation": None,
            "oracle": {
                "adapter": "expected-json",
                "identity": {"id": "o", "version": "1"},
                "configuration": {"expected": {"ok": True}},
            },
            "budgets": {"timeout_seconds": 1},
            "contamination": {
                "allowed_change_globs": [],
                "allowed_generated_globs": [],
            },
        }
        suite = SuiteDefinition(
            root=Path("."),
            experiment=experiment,
            tasks={"task": task},
            subjects={
                "none": {
                    "id": "none",
                    "kind": "control",
                    "adapter": "none",
                    "identity": {"id": "none", "version": "1"},
                    "capabilities": [],
                    "configuration": {},
                }
            },
            agents={
                "agent": {
                    "id": "agent",
                    "adapter": "fake",
                    "identity": {"id": "agent", "version": "1"},
                    "capabilities": [],
                    "configuration": {},
                }
            },
        )
        definition = suite.trial_definitions()[0]["definition_id"]
        receipt = {
            "definition_id": definition,
            "trial_id": "a" * 64,
            "experiment": experiment,
            "task": task,
            "condition": suite.expanded_condition(experiment["conditions"][0]),
            "status": "INVALID",
            "authority": {"subject": {"available": True}},
            "execution": {"trial_index": 0, "seed": 7},
            "measurements": {
                "agent": {
                    "duration_ms": 1200.0,
                    "tool_calls": 4,
                    "command_calls": 3,
                    "mcp_calls": 1,
                    "subject_mcp_calls": 0,
                    "mcp_result_bytes": 32,
                    "input_tokens": 500,
                    "cached_input_tokens": 100,
                    "output_tokens": 20,
                    "source_read_observability": "not-authoritatively-exposed-by-codex-jsonl",
                }
            },
        }
        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=[receipt],
        ):
            report = build_report(
                suite=suite,
                results_root=Path("/unused"),
            )

        condition = report["conditions"]["bare"]
        self.assertEqual(condition["valid_outcomes"], 0)
        self.assertIsNone(condition["task_success_rate"])
        self.assertEqual(
            condition["metrics"]["duration_ms"]["total"],
            1200.0,
        )
        self.assertEqual(
            condition["metrics"]["tool_calls"]["total"],
            4,
        )
        self.assertEqual(
            condition["valid_outcome_metrics"]["duration_ms"]["observations"],
            0,
        )
        self.assertTrue(
            report["authority"]["economics_include_invalid_and_incomplete_trials"]
        )

    def test_report_rejects_multiple_executions_for_one_definition(self) -> None:
        experiment = {
            "id": "report",
            "version": 1,
            "suite": "fake",
            "tasks": ["task"],
            "conditions": [
                {
                    "id": "bare",
                    "subject": "none",
                    "agent": "agent",
                    "trials": 1,
                    "seed": 7,
                }
            ],
            "scoring": {"id": "s", "version": 1, "metrics": ["task_success"]},
        }
        task = {
            "id": "task",
            "version": 1,
            "repository": {
                "url": "https://example.invalid/repo.git",
                "commit": "1" * 40,
                "tree": "2" * 40,
            },
            "prompt": "x",
            "mode": "read_only",
            "mutation": None,
            "oracle": {
                "adapter": "expected-json",
                "identity": {"id": "o", "version": "1"},
                "configuration": {"expected": {"ok": True}},
            },
            "budgets": {"timeout_seconds": 1},
            "contamination": {
                "allowed_change_globs": [],
                "allowed_generated_globs": [],
            },
        }
        suite = SuiteDefinition(
            root=Path("."),
            experiment=experiment,
            tasks={"task": task},
            subjects={
                "none": {
                    "id": "none",
                    "kind": "control",
                    "adapter": "none",
                    "identity": {"id": "none", "version": "1"},
                    "capabilities": [],
                    "configuration": {},
                }
            },
            agents={
                "agent": {
                    "id": "agent",
                    "adapter": "fake",
                    "identity": {"id": "agent", "version": "1"},
                    "capabilities": [],
                    "configuration": {},
                }
            },
        )
        definition = suite.trial_definitions()[0]["definition_id"]
        expanded = suite.expanded_condition(experiment["conditions"][0])
        base = {
            "definition_id": definition,
            "experiment": experiment,
            "task": task,
            "condition": expanded,
            "status": "PASS",
            "authority": {"subject": {"available": True}},
            "execution": {"trial_index": 0, "seed": 7},
            "measurements": {"agent": {}},
        }
        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=[
                {**base, "trial_id": "a" * 64},
                {**base, "trial_id": "b" * 64},
            ],
        ):
            with self.assertRaises(ReportError):
                build_report(
                    suite=suite,
                    results_root=Path("/unused"),
                )

    def test_cycle_score_rejects_duplicate_receipts_before_counting(self) -> None:
        suite = load_suite(CYCLE)
        definition = suite.trial_definitions()[0]["definition_id"]
        cycle_score = runpy.run_path(str(CYCLE / "score.py"))["score"]
        with mock.patch(
            "benchmarks.harness.report._receipts",
            return_value=[
                {"definition_id": definition, "trial_id": "a" * 64},
                {"definition_id": definition, "trial_id": "b" * 64},
            ],
        ):
            with self.assertRaisesRegex(ReportError, "multiple executions"):
                cycle_score(Path("/unused"), require_complete=False)


if __name__ == "__main__":
    unittest.main()
