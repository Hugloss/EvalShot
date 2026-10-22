"""Native CLI fixture exercises config transport without any model calls."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.adapters.codex import CodexAgent
from benchmarks.adapters.codex_native import observe_native_config
from benchmarks.adapters.registry import AdapterConfigurationError, build_agent
from benchmarks.harness.model import McpExposure, TrialContext
from benchmarks.harness.workspace import isolated_environment


CLI_FIXTURE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
if '--sandbox' in args and '--approve-for-me' in args:
    raise SystemExit('native CLI rejects mutually exclusive sandbox and automatic review')
dispatch = [argument for argument in args if argument != '--no-daemon']
home = pathlib.Path(os.environ['CODEX_HOME'])
with (home / 'native-startups.jsonl').open('a') as startup:
    startup.write(json.dumps(args) + '\n')
if '--version' in args:
    print('codex-cli 0.163.0')
elif '--help' in args:
    print('--no-daemon --ephemeral --approve-for-me --sandbox --json')
elif dispatch[:2] == ['doctor', '--json']:
    unsupported = (home / 'unsupported-doctor').exists()
    if unsupported:
        print('unsupported', file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({'schemaVersion': 1, 'overallStatus': 'fail', 'checks': {
        'config.load': {'status': 'ok', 'details': {
            'model': 'cli-resolved-model', 'model provider': 'cli-resolved-provider',
            'configuration scope': 'invocation config, including native policy'}},
        'auth.credentials': {'status': 'ok', 'details': {'secret': 'not-retained'}},
        'state.paths': {'status': 'fail'}}}))
    raise SystemExit(1)
elif dispatch[:3] == ['mcp', 'list', '--json']:
    print(json.dumps([{'name': 'ambient'}]))
elif '--no-daemon' in args and 'exec' in args:
    (home / 'runtime-state').write_text('private')
    (home / 'observed-argv.json').write_text(json.dumps(args))
    (home / 'observed-transport-keys.json').write_text(json.dumps([key for key in os.environ if key.startswith('BENCHMARK_NATIVE_')]))
    if args[-1] == 'mutate-config':
        (home / 'config.toml').write_text('model="changed"\n')
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}}))
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 0, 'cached_input_tokens': False, 'output_tokens': 0}}))
else:
    raise SystemExit(2)
'''


class NativeCodexQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.native = self.root / "native"
        self.native.mkdir()
        self.config = (
            'model = "root-file-is-not-resolved-authority"\n'
            'model_reasoning_effort = "custom-effort"\n'
            'model_auto_compact_token_limit = 0\n'
            '[features]\nweb_search = false\n'
            '[model_providers.custom]\nname = "custom"\n'
        )
        (self.native / "config.toml").write_text(self.config)
        (self.native / "custom.config.toml").write_text('model_context_window = 0\n[features]\nfoo = false\n')
        (self.native / "auth.json").write_text('{"opaque-secret":"must-stay-private"}')
        (self.native / "AGENTS.md").write_text("Native guidance\n")
        skill = self.native / "skills" / "example" / "run.sh"
        skill.parent.mkdir(parents=True)
        skill.write_text("#!/bin/sh\nprintf 'native helper'\n")
        skill.chmod(0o755)
        (self.native / "state.sqlite").write_text("host-state")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.command = self.bin / "codex"
        self.command.write_text(CLI_FIXTURE)
        self.command.chmod(0o755)

    def context(self, name):
        control = self.root / name
        environment = isolated_environment(control, source={"PATH": str(self.bin) + os.pathsep + os.environ["PATH"]})
        environment["BENCHMARK_NATIVE_CODEX_HOME"] = str(self.native)
        return TrialContext(self.workspace, control, environment)

    def test_native_cli_resolution_and_exact_false_zero_inputs_are_preserved(self):
        context = self.context("trial")
        agent = CodexAgent(native_host=True)
        observation = agent.prepare(context, None)
        self.assertTrue(observation.payload["available"], observation.payload.get("reason"))
        self.assertEqual(observation.payload["model"], "cli-resolved-model")
        self.assertEqual(observation.payload["provider"], "cli-resolved-provider")
        self.assertIsNone(observation.payload["reasoning_effort"])
        private = Path(context.environment["CODEX_HOME"])
        self.assertEqual((private / "config.toml").read_bytes(), (self.native / "config.toml").read_bytes())
        self.assertEqual((private / "custom.config.toml").read_bytes(), (self.native / "custom.config.toml").read_bytes())
        self.assertEqual((private / "AGENTS.md").read_text(), "Native guidance\n")
        self.assertEqual((private / "auth.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((private / "skills/example/run.sh").stat().st_mode & 0o777, 0o700)
        self.assertFalse((private / "state.sqlite").exists())
        self.assertNotIn("must-stay-private", json.dumps(observation.payload))
        self.assertNotIn("not-retained", json.dumps(observation.payload))
        self.assertFalse((self.native / "native-startups.jsonl").exists())
        result = agent.run(context, "Locate the owner", None)
        self.assertTrue(result.payload["terminal_complete"])
        self.assertEqual(result.payload["final_message"], "done")
        self.assertEqual(result.measurements["input_tokens"], 0)
        self.assertIsNone(result.measurements["cached_input_tokens"])
        self.assertFalse((self.native / "runtime-state").exists())
        self.assertEqual((self.native / "state.sqlite").read_text(), "host-state")
        args = json.loads((private / "observed-argv.json").read_text())
        self.assertIn("--no-daemon", args)
        self.assertIn("--ephemeral", args)
        self.assertIn("--approve-for-me", args)
        self.assertNotIn("--sandbox", args)
        self.assertNotIn("--model", args)
        self.assertFalse(any("model_reasoning_effort=" in argument for argument in args))
        self.assertEqual(json.loads((private / "observed-transport-keys.json").read_text()), [])

    def test_trials_do_not_share_session_or_database_state(self):
        first, second = self.context("one"), self.context("two")
        agent = CodexAgent(native_host=True)
        self.assertTrue(agent.prepare(first, None).payload["available"])
        self.assertTrue(agent.prepare(second, None).payload["available"])
        agent.run(first, "Locate owner", None)
        self.assertFalse((Path(second.environment["CODEX_HOME"]) / "runtime-state").exists())
        self.assertEqual((self.native / "auth.json").read_text(), '{"opaque-secret":"must-stay-private"}')

    def test_absent_root_config_and_auth_preserve_native_environment_selection(self):
        (self.native / "config.toml").unlink()
        (self.native / "auth.json").unlink()
        (self.native / "custom.config.toml").unlink()
        context = self.context("defaults")
        context.environment["EXPLICIT_PROVIDER_CREDENTIAL"] = "not-retained-credential"
        agent = CodexAgent(native_host=True)
        observation = agent.prepare(context, None)
        self.assertTrue(observation.payload["available"], observation.payload.get("reason"))
        self.assertEqual(observation.payload["model"], "cli-resolved-model")
        private = Path(context.environment["CODEX_HOME"])
        self.assertFalse((private / "config.toml").exists())
        self.assertFalse((private / "auth.json").exists())
        self.assertNotIn("not-retained-credential", json.dumps(observation.payload))
        self.assertTrue(agent.run(context, "Locate owner", None).payload["terminal_complete"])
        (self.native / "config.toml").write_text("")
        empty = agent.prepare(self.context("empty-config"), None)
        self.assertTrue(empty.payload["available"])
        self.assertNotEqual(observation.payload["native_config_sha256"], empty.payload["native_config_sha256"])

    def test_unknown_doctor_is_not_replaced_by_root_config_or_default(self):
        context = self.context("unknown")
        (Path(context.environment["CODEX_HOME"]) / "unsupported-doctor").touch()
        # Unknown diagnostics are exercised directly; no config precedence is inferred.
        result = observe_native_config(context, str(self.command), 10)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["model"])
        self.assertIsNone(result["provider"])

    def test_shared_source_state_is_rejected_before_any_cli_startup(self):
        context = self.context("shared-state")
        context.environment["CODEX_HOME"] = str(self.native)
        with patch("benchmarks.adapters.codex.observe_executable") as probe:
            result = CodexAgent(native_host=True).prepare(context, None)
        probe.assert_not_called()
        self.assertFalse(result.payload["available"])
        self.assertIn("independent trial", result.payload["reason"])
        self.assertFalse((self.native / "native-startups.jsonl").exists())

    def test_source_trial_overlap_is_rejected_before_native_startup(self):
        for kind in ("equal", "trial-inside-source", "source-inside-trial"):
            with self.subTest(kind=kind):
                context = self.context("overlap-" + kind)
                if kind == "equal":
                    context = TrialContext(self.workspace, self.native, context.environment)
                elif kind == "trial-inside-source":
                    context = TrialContext(self.workspace, self.native / "trial", context.environment)
                else:
                    context.environment["BENCHMARK_NATIVE_CODEX_HOME"] = str(context.control_root / "native")
                with patch("benchmarks.adapters.codex.observe_executable") as probe:
                    result = CodexAgent(native_host=True).prepare(context, None)
                probe.assert_not_called()
                self.assertFalse(result.payload["available"])
                self.assertIn("overlap", result.payload["reason"])

    def test_personal_marketplace_does_not_disappear_with_private_home(self):
        source_home = self.root / "source-home"
        catalog = source_home / ".agents/plugins/marketplace.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text('{"name":"fixture-personal","secret":"must-stay-private"}')
        context = self.context("personal-marketplace")
        context.environment["BENCHMARK_NATIVE_HOME"] = str(source_home)
        result = CodexAgent(native_host=True).prepare(context, None)
        self.assertFalse(result.payload["available"])
        self.assertIn("personal-marketplace-transport-unsupported", result.payload["reason"])
        self.assertNotIn("must-stay-private", json.dumps(result.payload))
        self.assertFalse((Path(context.environment["CODEX_HOME"]) / "auth.json").exists())

    def test_unsupported_dependencies_block_without_copying_credentials(self):
        for config, expected in (('[plugins.example]\nenabled = true\n', 'native-plugin-id-format-unsupported'), ('model_instructions_file = "custom.md"\n', 'unsupported native Codex input dependencies'), ('sqlite_home = "/shared/state"\n', 'unsupported native Codex input dependencies')):
            with self.subTest(config=config):
                (self.native / "config.toml").write_text(config)
                context = self.context("blocked-" + str(len(list(self.root.iterdir()))))
                result = CodexAgent(native_host=True).prepare(context, None)
                self.assertFalse(result.payload["available"])
                self.assertIn(expected, result.payload["reason"])
                self.assertFalse((Path(context.environment["CODEX_HOME"]) / "auth.json").exists())

    def test_changed_private_config_is_rejected_before_execution(self):
        context = self.context("changed")
        agent = CodexAgent(native_host=True)
        self.assertTrue(agent.prepare(context, None).payload["available"])
        private = Path(context.environment["CODEX_HOME"])
        (private / "custom.config.toml").write_text("changed=true\n")
        result = agent.run(context, "Locate owner", None)
        self.assertIn("config changed", result.payload["reason"])
        self.assertFalse((private / "runtime-state").exists())

    def test_changed_native_client_environment_is_rejected_before_execution(self):
        for name in ("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "CODEX_MCP_NODE_PATH", "BENCHMARK_NATIVE_CODEX_HOME"):
            with self.subTest(name=name):
                context = self.context("environment-drift-" + name)
                agent = CodexAgent(native_host=True)
                self.assertTrue(agent.prepare(context, None).payload["available"])
                context.environment[name] = "/changed/input-must-not-appear"
                result = agent.run(context, "Locate owner", None)
                self.assertIn("environment changed", result.payload["reason"])
                self.assertNotIn("input-must-not-appear", json.dumps(result.payload))
                self.assertFalse((Path(context.environment["CODEX_HOME"]) / "runtime-state").exists())

    def test_guidance_executable_mode_drift_is_rejected(self):
        context = self.context("mode-drift")
        agent = CodexAgent(native_host=True)
        self.assertTrue(agent.prepare(context, None).payload["available"])
        private = Path(context.environment["CODEX_HOME"])
        (private / "skills/example/run.sh").chmod(0o600)
        result = agent.run(context, "Locate owner", None)
        self.assertIn("config changed", result.payload["reason"])
        self.assertFalse((private / "runtime-state").exists())

    def test_config_drift_during_execution_makes_result_incomplete(self):
        context = self.context("changed-during")
        agent = CodexAgent(native_host=True)
        self.assertTrue(agent.prepare(context, None).payload["available"])
        result = agent.run(context, "mutate-config", None)
        self.assertFalse(result.payload["terminal_complete"])
        self.assertIsNone(result.payload["final_message"])
        self.assertIn("config changed", result.payload["reason"])
        self.assertEqual(
            result.payload["native_input_integrity_reason"], "input-content-drift"
        )
        from benchmarks.harness.runner import _agent_failure

        self.assertEqual(
            _agent_failure(result)[0], "native-input-integrity-rejection"
        )
        self.assertEqual((self.native / "config.toml").read_text(), self.config)

    def test_new_native_input_membership_is_rejected_before_execution(self):
        for name in ("AGENTS.override.md", "new.config.toml", "rules/new.rules"):
            with self.subTest(name=name):
                context = self.context("added-" + str(len(list(self.root.iterdir()))))
                agent = CodexAgent(native_host=True)
                self.assertTrue(agent.prepare(context, None).payload["available"])
                private = Path(context.environment["CODEX_HOME"])
                path = private / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("new=true\n")
                result = agent.run(context, "Locate owner", None)
                self.assertIn("config changed", result.payload["reason"])
                self.assertEqual(
                    result.payload["native_input_integrity_reason"],
                    "input-membership-drift",
                )
                self.assertFalse((private / "runtime-state").exists())

    def test_private_input_drift_has_bounded_failure_classifications(self):
        from benchmarks.adapters.codex_native import (
            native_input_integrity_reason,
            verify_native_inputs,
        )

        for change, expected in (
            ("content", "input-content-drift"),
            ("mode", "input-mode-drift"),
            ("membership", "input-membership-drift"),
        ):
            with self.subTest(change=change):
                context = self.context("classified-" + change)
                agent = CodexAgent(native_host=True)
                self.assertTrue(agent.prepare(context, None).payload["available"])
                private = Path(context.environment["CODEX_HOME"])
                manifest = json.loads(
                    (context.control_root / "codex-native-servers.json").read_text()
                )["inputs"]
                self.assertIsNone(native_input_integrity_reason(context, manifest))
                self.assertTrue(verify_native_inputs(context, manifest))
                if change == "content":
                    (private / "config.toml").write_text("model = 'different'\n")
                elif change == "mode":
                    (private / "AGENTS.md").chmod(0o700)
                else:
                    (private / "added.config.toml").write_text("feature = true\n")
                self.assertEqual(
                    native_input_integrity_reason(context, manifest), expected
                )
                self.assertFalse(verify_native_inputs(context, manifest))
                result = agent.run(context, "Locate owner", None)
                self.assertEqual(
                    result.payload["native_input_integrity_reason"], expected
                )
                from benchmarks.harness.runner import _agent_failure

                self.assertEqual(
                    _agent_failure(result)[0], "native-input-integrity-rejection"
                )
                self.assertIsNone(result.payload["terminal_event"])
                self.assertNotIn("different", json.dumps(result.payload))
                self.assertFalse((private / "runtime-state").exists())

    def test_changed_executable_is_rejected_before_execution(self):
        context = self.context("changed-executable")
        agent = CodexAgent(native_host=True)
        self.assertTrue(agent.prepare(context, None).payload["available"])
        self.command.write_text(CLI_FIXTURE + "\n# changed executable\n")
        result = agent.run(context, "Locate owner", None)
        self.assertIn("executable changed", result.payload["reason"])
        self.assertFalse((Path(context.environment["CODEX_HOME"]) / "runtime-state").exists())

    def test_required_use_is_explicit_and_subject_bound(self):
        context = self.context("required")
        exposure = McpExposure("hashmarks", str(self.command), (), self.workspace, {"transport": "stdio"})
        subject = type("Subject", (), {"mcp_exposure": lambda self, context: exposure})()
        agent = CodexAgent(native_host=True, diagnostic_required_tool="hashmarks_task_evidence")
        self.assertFalse(agent.prepare(context, None).payload["available"])
        self.assertTrue(agent.prepare(context, subject).payload["available"])
        result = agent.run(context, "Locate owner", subject)
        self.assertTrue(result.payload["terminal_complete"])
        args = json.loads((Path(context.environment["CODEX_HOME"]) / "observed-argv.json").read_text())
        self.assertIn("Diagnostic tool requirement: call hashmarks_task_evidence", args[-1])
        self.assertNotIn("mcp_servers.ambient.enabled=false", args)

    def test_doctor_false_zero_and_wrong_schema_remain_unknown(self):
        context = self.context("malformed")
        from types import SimpleNamespace
        for model, provider, schema in ((False, 0, 1), ("observed", "provider", True), ("default", "provider", 1)):
            report = {"schemaVersion": schema, "checks": {"config.load": {"status": "ok", "details": {"model": model, "model provider": provider}}}}
            result = SimpleNamespace(executable_missing=False, timed_out=False, stdout_truncated=False, stderr_truncated=False, stdout=json.dumps(report).encode())
            with self.subTest(model=model, schema=schema), patch("benchmarks.adapters.codex_native.run_bounded", return_value=result):
                observed = observe_native_config(context, str(self.command), 10)
                self.assertEqual(observed["state"], "unknown")
                self.assertIsNone(observed["model"])

    def test_missing_launch_flag_capability_blocks_before_auth_copy(self):
        context = self.context("unsupported-flags")
        self.command.write_text(CLI_FIXTURE.replace("print('--no-daemon --ephemeral --approve-for-me --sandbox --json')", "print('--json')"))
        result = CodexAgent(native_host=True).prepare(context, None)
        self.assertFalse(result.payload["available"])
        self.assertIn("launch flags", result.payload["reason"])
        self.assertFalse((Path(context.environment["CODEX_HOME"]) / "auth.json").exists())

    def test_native_controlled_transport_accepts_only_observed_local_environment_marker(self):
        from benchmarks.adapters.codex_native_plugins import _subject_conformance
        exposure = McpExposure("hashmarks", str(self.command), ("mcp",), self.workspace, {"NATIVE_FALSE": "false"})
        row = {"command": exposure.command, "args": list(exposure.args), "cwd": str(self.workspace.resolve()), "enabled": True, "env": dict(exposure.environment)}
        for marker in (None, "local"):
            with self.subTest(marker=marker):
                _subject_conformance({"mcp_servers": {"hashmarks": {**row, "environment_id": marker}}}, "hashmarks", exposure)
        for extra in ({"environment_id": "remote"}, {"environment_id": "default"}, {"environment_id": False}, {"url": "https://unadmitted.invalid"}, {"unknown": 0}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "transport-not-exact"):
                _subject_conformance({"mcp_servers": {"hashmarks": {**row, **extra}}}, "hashmarks", exposure)

    def test_diagnostic_configuration_is_not_a_model_override(self):
        definition = {"adapter": "codex", "configuration": {"native_host": True, "diagnostic_required_tool": "hashmarks_task_evidence"}}
        agent = build_agent(definition, budgets={"timeout_seconds": 10})
        self.assertEqual(agent.diagnostic_required_tool, "hashmarks_task_evidence")
        definition["configuration"]["model"] = "forced"
        with self.assertRaises(AdapterConfigurationError):
            build_agent(definition, budgets={"timeout_seconds": 10})
