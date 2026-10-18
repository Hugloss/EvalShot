from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from benchmarks.adapters.hashmarks import HashmarksSubject
from benchmarks.harness.model import McpExposure, Observation
from benchmarks.harness.readiness import (
    _mcp_startup_diagnostic,
    _pair_check,
    _subject_runtime_check,
    check_runtime_readiness,
)
from benchmarks.harness.suite import (
    SuiteDefinition,
    SuiteError,
    load_runtime_suite,
    load_suite,
)
from scripts.agent_economics.bounded_process import BoundedProcessError


class FakeSubject:
    def __init__(self, name: str) -> None:
        self.name = name

    def identity(self):
        return mock.Mock(participant_id=self.name, kind="repository_intelligence")

    def mcp_exposure(self, context):
        return McpExposure(
            name=self.name,
            command=f"/tools/{self.name}",
            args=("mcp",),
            cwd=context.workspace,
            semantic_identity={"name": self.name, "transport": "stdio"},
        )


class FakeAgent:
    def __init__(self, name: str, calls: list[tuple[str, str | None]]) -> None:
        self.name = name
        self.calls = calls

    def prepare(self, context, subject):
        subject_name = None
        if subject is not None:
            subject_name = subject.identity().participant_id
            subject.mcp_exposure(context)
        self.calls.append((self.name, subject_name))
        if self.name == "opencode-native" and subject_name == "hashmarks":
            return Observation(
                {
                    "available": False,
                    "reason": "hashmarks MCP connection closed",
                },
                "",
            )
        return Observation({"available": True}, "")


def fake_suite() -> SuiteDefinition:
    return SuiteDefinition(
        root=Path("."),
        experiment={
            "conditions": [
                {
                    "id": "none-codex-native",
                    "subject": "none",
                    "agent": "codex-native",
                },
                {
                    "id": "hashmarks-codex-native",
                    "subject": "hashmarks",
                    "agent": "codex-native",
                },
                {
                    "id": "enola-codex-native",
                    "subject": "enola",
                    "agent": "codex-native",
                },
                {
                    "id": "none-opencode-native",
                    "subject": "none",
                    "agent": "opencode-native",
                },
                {
                    "id": "hashmarks-opencode-native",
                    "subject": "hashmarks",
                    "agent": "opencode-native",
                },
                {
                    "id": "enola-opencode-native",
                    "subject": "enola",
                    "agent": "opencode-native",
                },
                {
                    "id": "duplicate-hashmarks-opencode-native",
                    "subject": "hashmarks",
                    "agent": "opencode-native",
                },
            ]
        },
        tasks={},
        subjects={
            "none": {"id": "none", "adapter": "none"},
            "hashmarks": {"id": "hashmarks", "adapter": "hashmarks"},
            "enola": {"id": "enola", "adapter": "enola"},
        },
        agents={
            "codex-native": {
                "id": "codex-native",
                "adapter": "codex",
                "configuration": {"native_host": True},
            },
            "opencode-native": {
                "id": "opencode-native",
                "adapter": "opencode-native",
                "configuration": {},
            },
        },
    )


ROOT = Path(__file__).resolve().parents[2]
HELDOUT = ROOT / "benchmarks" / "suites" / "repository-intelligence" / "heldout-v1"


class RuntimeReadinessTests(unittest.TestCase):
    def test_runtime_suite_does_not_load_task_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "heldout"
            shutil.copytree(HELDOUT, copied)
            shutil.rmtree(copied / "tasks")

            runtime = load_runtime_suite(copied)
            self.assertEqual(runtime.tasks, {})
            self.assertIn("hashmarks", runtime.subjects)
            self.assertIn("codex-native", runtime.agents)

            with self.assertRaises(SuiteError):
                load_suite(copied)

    def test_readiness_has_no_trial_admission_dependencies(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "harness" / "readiness.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "preflight_trial",
            "materialize_repository",
            "apply_mutation",
            "build_oracle",
            "campaign_status",
            "run_trial",
        ):
            self.assertNotIn(forbidden, source)

    def test_subject_runtime_requires_declared_mcp_operation_when_suite_opts_in(
        self,
    ) -> None:
        suite = fake_suite()
        suite.experiment["subject_exposure_contract"] = {
            "require_probe_contract": True
        }
        suite.subjects["enola"]["kind"] = "repository_intelligence"
        suite.subjects["enola"]["exposure_probe"] = {
            "required_tool": "enola_explore"
        }
        observed = Observation(
            {
                "available": True,
                "version": "test",
                "executable_sha256": "a" * 64,
            },
            "test",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "benchmarks.harness.readiness.build_subject",
                return_value=FakeSubject("enola"),
            ),
            mock.patch(
                "benchmarks.harness.readiness.observe_executable",
                return_value=observed,
            ),
            mock.patch(
                "benchmarks.harness.readiness.probe_mcp_tool_catalog",
                side_effect=ValueError(
                    "MCP required operation is not exposed: explore"
                ),
            ) as catalog,
        ):
            result = _subject_runtime_check(
                suite,
                "enola",
                Path(tmp),
                {"PATH": os.environ.get("PATH", "")},
            )

        self.assertEqual(result.state, "FAILED")
        self.assertIn(
            "MCP required operation is not exposed: explore",
            result.reason or "",
        )
        self.assertEqual(
            catalog.call_args.kwargs["required_tool"],
            "enola_explore",
        )

    def test_failed_mcp_pair_surfaces_direct_child_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            control = root / "control"
            control.mkdir()
            context = mock.Mock(
                workspace=workspace,
                control_root=control,
                environment={"PATH": os.environ.get("PATH", "")},
            )
            exposure = McpExposure(
                name="hashmarks",
                command="/work/Hashmarks/.venv/bin/hashmarks",
                args=("--workspace", ".", "mcp"),
                cwd=workspace,
                semantic_identity={"name": "hashmarks", "transport": "stdio"},
                environment={"MCP_SETTING": "selected"},
            )
            process = SimpleNamespace(
                return_code=1,
                timed_out=False,
                executable_missing=False,
                stdout_truncated=False,
                stderr_truncated=False,
                stderr=(
                    b"Hashmarks MCP support requires the optional extra: "
                    b'pip install "hashmarks[mcp]"\n'
                ),
                stdout=b"",
            )
            with (
                mock.patch(
                    "benchmarks.harness.readiness._native_environment",
                    return_value={"PATH": "/native/bin", "HOME": "/native/home"},
                ),
                mock.patch(
                    "benchmarks.harness.readiness.run_bounded",
                    return_value=process,
                ) as bounded,
            ):
                detail = _mcp_startup_diagnostic(context, exposure)

            self.assertIn("direct-startup-exit=1", detail)
            self.assertIn("Hashmarks MCP support requires the optional extra", detail)
            self.assertTrue(bounded.call_args.kwargs["close_stdin"])
            self.assertEqual(
                bounded.call_args.kwargs["limits"].timeout_seconds,
                5,
            )
            self.assertEqual(bounded.call_args.kwargs["cwd"], workspace)
            self.assertEqual(
                bounded.call_args.kwargs["environment"],
                {
                    "PATH": "/native/bin",
                    "HOME": "/native/home",
                    "MCP_SETTING": "selected",
                },
            )

    def test_direct_probe_does_not_turn_clean_eof_into_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = mock.Mock(workspace=workspace, environment={})
            exposure = FakeSubject("hashmarks").mcp_exposure(context)
            process = SimpleNamespace(
                return_code=0,
                timed_out=False,
                executable_missing=False,
                stdout_truncated=False,
                stderr_truncated=False,
                stderr=b"MCP server started\n",
                stdout=b"",
            )
            with (
                mock.patch(
                    "benchmarks.harness.readiness._native_environment",
                    return_value={},
                ),
                mock.patch(
                    "benchmarks.harness.readiness.run_bounded",
                    return_value=process,
                ),
            ):
                self.assertIsNone(_mcp_startup_diagnostic(context, exposure))

    def test_direct_probe_failures_remain_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = mock.Mock(workspace=workspace, environment={})
            exposure = FakeSubject("hashmarks").mcp_exposure(context)
            with (
                mock.patch(
                    "benchmarks.harness.readiness._native_environment",
                    return_value={},
                ),
                mock.patch(
                    "benchmarks.harness.readiness.run_bounded",
                    side_effect=BoundedProcessError("command failed to start"),
                ),
            ):
                self.assertIn(
                    "direct-startup-probe-error=command failed to start",
                    _mcp_startup_diagnostic(context, exposure),
                )

            process = SimpleNamespace(
                return_code=-9,
                timed_out=False,
                executable_missing=False,
                stdout_truncated=False,
                stderr_truncated=True,
                stderr=b"startup output",
                stdout=b"",
            )
            with (
                mock.patch(
                    "benchmarks.harness.readiness._native_environment",
                    return_value={},
                ),
                mock.patch(
                    "benchmarks.harness.readiness.run_bounded",
                    return_value=process,
                ),
            ):
                detail = _mcp_startup_diagnostic(context, exposure)
            self.assertIn("direct-startup-stderr-truncated=true", detail)

    def test_only_mcp_connection_failures_launch_direct_probe(self) -> None:
        suite = fake_suite()
        agent = mock.Mock()
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "benchmarks.harness.readiness.build_subject",
                return_value=FakeSubject("hashmarks"),
            ),
            mock.patch("benchmarks.harness.readiness._agent", return_value=agent),
            mock.patch(
                "benchmarks.harness.readiness._mcp_startup_diagnostic",
                return_value="direct-startup-exit=1",
            ) as direct,
        ):
            config_root = Path(tmp) / "config"
            config_root.mkdir()
            agent.prepare.return_value = Observation(
                {"available": False, "reason": "native config failed"}, ""
            )
            config_check = _pair_check(
                suite, "opencode-native", "hashmarks", config_root
            )
            self.assertEqual(config_check.reason, "native config failed")
            direct.assert_not_called()

            mcp_root = Path(tmp) / "mcp"
            mcp_root.mkdir()
            agent.prepare.return_value = Observation(
                {
                    "available": False,
                    "reason": "MCP connection closed",
                    "failure_stage": "mcp-connection",
                },
                "",
            )
            mcp_check = _pair_check(suite, "opencode-native", "hashmarks", mcp_root)
            self.assertEqual(
                mcp_check.reason,
                "MCP connection closed; direct-startup-exit=1",
            )
            direct.assert_called_once()

    def test_readiness_checks_only_explicitly_selected_agent(self) -> None:
        suite = fake_suite()
        calls: list[tuple[str, str | None]] = []

        def agent(definition, *, budgets):
            return FakeAgent(str(definition["id"]), calls)

        observed = Observation(
            {
                "available": True,
                "version": "test",
                "executable_sha256": "a" * 64,
            },
            "test",
        )
        clean_source = (
            {
                "root": "/work/Hashmarks",
                "commit": "b" * 40,
                "tree": "c" * 40,
                "working_copy_sha256": "d" * 64,
                "working_copy_clean": True,
            },
            None,
        )
        environment = {
            "HASHMARKS_BENCH_SOURCE": "/work/Hashmarks",
            "BENCHMARK_OPENCODE_AGENT": "build",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=False),
            mock.patch(
                "benchmarks.harness.readiness.build_subject",
                side_effect=lambda definition: (
                    HashmarksSubject(timeout_seconds=15)
                    if definition["id"] == "hashmarks"
                    else FakeSubject(str(definition["id"]))
                ),
            ),
            mock.patch(
                "benchmarks.harness.readiness.build_agent",
                side_effect=agent,
            ),
            mock.patch(
                "benchmarks.harness.readiness.observe_executable",
                return_value=observed,
            ),
            mock.patch.object(
                HashmarksSubject,
                "source_identity",
                return_value=clean_source,
            ),
            mock.patch(
                "benchmarks.harness.readiness._mcp_startup_diagnostic",
                return_value="direct-startup-exit=1",
            ),
        ):
            report = check_runtime_readiness(
                suite,
                agents=("opencode-native",),
            )

        labels = {check.label for check in report.checks}
        self.assertEqual(
            labels,
            {
                "opencode-native + bare",
                "opencode-native + hashmarks",
                "opencode-native + enola",
            },
        )
        self.assertFalse(any("codex-native" in label for label in labels))
        self.assertFalse(any(call[0] == "codex-native" for call in calls))

    def test_readiness_rejects_selected_agent_without_conditions(self) -> None:
        suite = fake_suite()
        suite.agents["unused"] = {"id": "unused", "adapter": "codex"}
        with self.assertRaisesRegex(ValueError, "unused"):
            check_runtime_readiness(suite, agents=("codex-native", "unused"))

    def test_each_unique_runtime_pair_is_checked_once_without_retry(self) -> None:
        suite = fake_suite()
        calls: list[tuple[str, str | None]] = []

        def subject(definition):
            if definition["id"] == "hashmarks":
                return HashmarksSubject(timeout_seconds=15)
            return FakeSubject(str(definition["id"]))

        def agent(definition, *, budgets):
            self.assertEqual(budgets["timeout_seconds"], 15)
            return FakeAgent(str(definition["id"]), calls)

        observed = Observation(
            {
                "available": True,
                "version": "test",
                "executable_sha256": "a" * 64,
            },
            "test",
        )
        clean_source = (
            {
                "root": "/work/Hashmarks",
                "commit": "b" * 40,
                "tree": "c" * 40,
                "working_copy_sha256": "d" * 64,
                "working_copy_clean": True,
            },
            None,
        )

        environment = {
            "HASHMARKS_BENCH_SOURCE": "/work/Hashmarks",
            "BENCHMARK_OPENCODE_AGENT": "build",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=False),
            mock.patch(
                "benchmarks.harness.readiness.build_subject",
                side_effect=subject,
            ),
            mock.patch(
                "benchmarks.harness.readiness.build_agent",
                side_effect=agent,
            ),
            mock.patch(
                "benchmarks.harness.readiness.observe_executable",
                return_value=observed,
            ) as executable_probe,
            mock.patch.object(
                HashmarksSubject,
                "source_identity",
                return_value=clean_source,
            ),
            mock.patch.object(
                SuiteDefinition,
                "trial_definitions",
                side_effect=AssertionError("readiness expanded frozen trials"),
            ),
        ):
            report = check_runtime_readiness(suite)

        states = {check.label: check.state for check in report.checks}
        self.assertEqual(len(states), 6)
        self.assertEqual(executable_probe.call_count, 2)
        self.assertEqual(states["codex-native + bare"], "READY")
        self.assertEqual(states["opencode-native + bare"], "READY")
        self.assertEqual(
            states["codex-native + hashmarks"],
            "READY",
        )
        self.assertEqual(
            states["codex-native + enola"],
            "READY",
        )
        self.assertEqual(
            states["opencode-native + hashmarks"],
            "FAILED",
        )
        self.assertEqual(
            states["opencode-native + enola"],
            "CONNECTED",
        )
        self.assertFalse(report.ready)

        self.assertEqual(
            calls.count(("opencode-native", "hashmarks")),
            1,
        )
        self.assertEqual(
            calls.count(("opencode-native", "enola")),
            1,
        )
        self.assertEqual(
            calls.count(("codex-native", "hashmarks")),
            1,
        )
        self.assertEqual(
            calls.count(("codex-native", "enola")),
            1,
        )
        self.assertEqual(calls.count(("codex-native", None)), 1)
        self.assertEqual(calls.count(("opencode-native", None)), 1)


if __name__ == "__main__":
    unittest.main()
