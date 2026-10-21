"""Differential native plugin readback without model calls or host state.

RPC fixtures use the observed app-server response families. They are synthetic
native-format records, not live host attestations or an alternative resolver.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmarks.adapters import codex_native_plugins as plugins
from benchmarks.harness.model import McpExposure, TrialContext
from benchmarks.harness.runtime_authority import PRIVATE_STATE_ENV_KEYS
from benchmarks.harness.workspace import isolated_environment


SECRET = "fixture-credential-must-not-be-retained"
MARKET = "fixture-market"
HELPER = "helper@fixture-market"
DISABLED = "disabled@fixture-market"


DYNAMIC_RPC_CLI = r'''#!/usr/bin/python3
import json, os, pathlib, secrets, select, sys

private = pathlib.Path(os.environ['CODEX_HOME'])
log = private / 'dynamic-rpc-methods.jsonl'
active = private / 'dynamic-rpc-active.json'
thread = None
closed = False
initialized = False
status_calls = 0

def record(value):
    with log.open('a') as stream:
        stream.write(json.dumps(value) + '\n')

def reply(request, result):
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)

record({'event': 'startup', 'argv': sys.argv[1:], 'cwd': os.getcwd()})
for raw in sys.stdin:
    request = json.loads(raw)
    method, params = request['method'], request.get('params', {})
    record({'event': 'request', 'method': method, 'params': params})
    if method.startswith('turn/'):
        raise SystemExit('fixture refuses every model turn request')
    if method == 'initialize':
        reply(request, {'serverInfo': {'name': 'native-format-fixture'}})
    elif method == 'initialized':
        initialized = True
    elif method == 'plugin/installed':
        reply(request, {'marketplaces': [], 'marketplaceLoadErrors': []})
    elif method == 'config/read':
        reply(request, {'config': {'mcp_servers': {'fixture-runtime': {'enabled': True}}}})
    elif method == 'thread/start':
        if not initialized or params != {'cwd': os.getcwd(), 'ephemeral': True}:
            raise SystemExit('thread must be ephemeral and bound to the actual cwd')
        thread = 'native-fixture-' + secrets.token_hex(24)
        active.write_text(json.dumps({'threadId': thread}))
        mode = os.environ.get('FIXTURE_THREAD_RESPONSE', 'valid')
        if mode == 'missing':
            reply(request, {'thread': {}})
        elif mode == 'empty':
            reply(request, {'thread': {'id': ''}})
        elif mode == 'non-string':
            reply(request, {'thread': {'id': 42}})
        else:
            reply(request, {'thread': {'id': thread}})
    elif method == 'mcpServerStatus/list':
        if thread is None or params.get('threadId') != thread or params.get('detail') != 'full':
            raise SystemExit('status is not bound to the actual native thread')
        status_calls += 1
        startup = os.environ.get('FIXTURE_RUNTIME_STARTUP')
        if startup and status_calls == 1:
            reply(request, {'data': [{'name': 'fixture-runtime', 'runtimeStatus': 'starting',
                'tools': {}, 'toolsError': None}], 'nextCursor': None})
            print(json.dumps({'method': 'mcpServer/startupStatus/updated', 'params': {
                'threadId': 'another-thread', 'name': 'fixture-runtime', 'status': 'ready'}}), flush=True)
            if select.select([sys.stdin], [], [], 0.05)[0]:
                raise SystemExit('client advanced before this thread completed startup')
            print(json.dumps({'method': 'mcpServer/startupStatus/updated', 'params': {
                'threadId': thread, 'name': 'fixture-runtime', 'status': 'failed' if startup == 'failed' else 'ready'}}), flush=True)
            continue
        state = 'failed' if startup == 'failed' else 'connected'
        reply(request, {'data': [{'name': 'fixture-runtime', 'runtimeStatus': state,
            'tools': {'fixture_read': {}}, 'toolsError': None, 'threadId': thread}], 'nextCursor': None})
    elif method == 'thread/unsubscribe':
        if thread is None or params != {'threadId': thread}:
            raise SystemExit('unsubscribe is not bound to the actual native thread')
        active.unlink()
        closed = True
        reply(request, {})
    else:
        raise SystemExit('unexpected RPC method: ' + method)
record({'event': 'eof', 'thread_unsubscribed': closed, 'active_thread_file': active.exists()})
'''


class NativePluginFixtures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evalshot-plugin-fidelity-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "native-source"
        self.source.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.market = self.source / "plugins/marketplaces" / MARKET
        catalog = self.market / ".agents/plugins/marketplace.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps({"name": MARKET, "plugins": [
            {"name": name, "source": {"source": "local", "path": f"packages/{name}"}}
            for name in ("helper", "disabled")
        ]}), encoding="utf-8")
        self.write_package(self.market / "packages/helper", "helper", "source-only", "source_mcp")
        self.write_package(self.market / "packages/disabled", "disabled", "source-only", "disabled_source")
        self.old = self.cache(self.source, "helper", "1.0.0")
        self.new = self.cache(self.source, "helper", "2.0.0")
        self.write_package(self.old, "helper", "1.0.0", "cached_v1")
        self.write_package(self.new, "helper", "2.0.0", "cached_v2")
        self.write_package(self.cache(self.source, "disabled", "1.0.0"), "disabled", "1.0.0", "disabled_cached")
        self.config = (
            f'[plugins."{HELPER}"]\nenabled = true\n'
            f'[plugins."{DISABLED}"]\nenabled = false\n'
            f'[marketplaces."{MARKET}"]\nsource_type = "local"\nsource = {json.dumps(str(self.market))}\n'
        )
        (self.source / "config.toml").write_text(self.config, encoding="utf-8")
        (self.source / "auth.json").write_text(SECRET, encoding="utf-8")
        (self.source / "state.sqlite").write_text("host-runtime-state", encoding="utf-8")
        self.context = self.make_context("trial")

    @staticmethod
    def cache(home, name, version):
        return home / "plugins/cache" / MARKET / name / version

    def write_package(self, root, name, version, server):
        (root / ".codex-plugin").mkdir(parents=True)
        (root / ".codex-plugin/plugin.json").write_text(json.dumps({
            "name": name, "version": version, "skills": "./skills", "mcpServers": "./.mcp.json",
        }), encoding="utf-8")
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": {server: {
            "command": "python3", "args": ["native-resource.py"], "env": {"API_KEY": SECRET},
        }}}), encoding="utf-8")
        skill = root / "skills/inspect/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: inspect\ndescription: fixture\n---\nUse resources/info.txt.\n", encoding="utf-8")
        resource = root / "resources/info.txt"
        resource.parent.mkdir()
        resource.write_text(f"immutable resource for {version}\n", encoding="utf-8")
        executable = root / "resources/helper.sh"
        executable.write_text("#!/bin/sh\nprintf fixture\n", encoding="utf-8")
        executable.chmod(0o755)

    def make_context(self, name):
        control = self.root / name
        environment = isolated_environment(control, source={"PATH": os.defpath})
        private = Path(environment["CODEX_HOME"])
        (private / "config.toml").write_text(self.config, encoding="utf-8")
        return TrialContext(self.workspace, control, environment)

    def responses(self):
        selected = [{"id": HELPER, "name": "helper", "installed": True, "enabled": True, "localVersion": "1.0.0"},
                    {"id": DISABLED, "name": "disabled", "installed": True, "enabled": False, "localVersion": "1.0.0"}]
        initial = {
            1: {"marketplaces": [{"name": MARKET, "path": str(self.market), "plugins": selected}], "marketplaceLoadErrors": []},
            2: {"config": {"plugins": {HELPER: {"enabled": True}, DISABLED: {"enabled": False}},
                           "mcp_servers": {"ambient": {"enabled": True}, "cached_v1": {"enabled": True}, "hashmarks": {"enabled": False}}, "private_credentials": SECRET}},
            3: {"data": [
                {"name": "ambient", "pluginId": None, "tools": {"ambient_read": {}}, "toolsError": None, "runtimeStatus": "connected"},
                {"name": "cached_v1", "pluginId": HELPER, "tools": {"cached_lookup": {}}, "toolsError": None,
                 "runtimeStatus": "connected", "serverInfo": {"name": "fixture-helper", "version": "1.0.0"}},
            ], "nextCursor": None},
        }
        details = {}
        for index, plugin in enumerate(selected):
            root = self.cache(self.source, plugin["name"], "1.0.0")
            details[100 + index] = {"plugin": {
                "summary": {"id": plugin["id"], "name": plugin["name"], "enabled": plugin["enabled"], "installed": True,
                            "version": "1.0.0", "localVersion": "1.0.0"},
                "skills": [{"name": "inspect", "enabled": plugin["enabled"], "path": str(root / "skills/inspect/SKILL.md")}],
                "mcpServers": ["cached_v1"] if plugin["enabled"] else ["disabled_cached"],
                "hooks": [{"eventName": "SessionStart", "key": "fixture-hook"}],
                "apps": [], "appTemplates": [], "scheduledTasks": [], "private_value": SECRET,
            }}
        return initial, details

    def observe(self, initial=None, details=None, exposure=None):
        defaults = self.responses()
        values = [initial if initial is not None else defaults[0], details if details is not None else defaults[1]]
        with patch.object(plugins, "_rpc", side_effect=values) as rpc:
            result = plugins.observe_native_plugins(
                self.context, "native-codex", self.source,
                overrides=("-c", "mcp_servers.hashmarks.enabled=" + ("true" if exposure else "false")), controlled_subject="hashmarks",
                exposure=exposure,
            )
        return result, rpc


class NativePluginTransportTests(NativePluginFixtures):
    def test_both_cache_candidates_and_distinct_source_payload_are_transported_opaquely(self):
        manifest = plugins.prepare_native_plugin_inputs(self.context, self.source)
        private = Path(self.context.environment["CODEX_HOME"])
        self.assertEqual(manifest["candidates"][HELPER], ["1.0.0", "2.0.0"])
        self.assertEqual(self.cache(private, "helper", "1.0.0").joinpath(".mcp.json").read_bytes(), (self.old / ".mcp.json").read_bytes())
        self.assertNotEqual((self.old / ".mcp.json").read_bytes(), (self.market / "packages/helper/.mcp.json").read_bytes())
        copied_resource = self.cache(private, "helper", "1.0.0") / "resources/helper.sh"
        self.assertEqual(copied_resource.stat().st_mode & 0o777, 0o755)
        self.assertFalse((private / "auth.json").exists())
        self.assertFalse((private / "state.sqlite").exists())
        self.assertNotIn(SECRET, json.dumps(manifest))
        self.assertTrue(plugins.verify_native_plugin_inputs(self.context, manifest))

    def test_equal_immutable_plugin_inputs_do_not_clone_or_share_runtime_data(self):
        source_data = self.source / "plugins/data/helper/runtime.json"
        source_data.parent.mkdir(parents=True)
        source_data.write_text("host-history-must-not-seed-trial", encoding="utf-8")
        second = self.make_context("second-trial")
        first_manifest = plugins.prepare_native_plugin_inputs(self.context, self.source)
        second_manifest = plugins.prepare_native_plugin_inputs(second, self.source)
        self.assertEqual(first_manifest["identity"], second_manifest["identity"])
        first_data = Path(self.context.environment["CODEX_HOME"]) / "plugins/data/helper/runtime.json"
        second_data = Path(second.environment["CODEX_HOME"]) / "plugins/data/helper/runtime.json"
        self.assertFalse(first_data.exists())
        self.assertFalse(second_data.exists())
        first_data.parent.mkdir(parents=True)
        first_data.write_text("first-trial-only", encoding="utf-8")
        self.assertFalse(second_data.exists())
        self.assertEqual(source_data.read_text(), "host-history-must-not-seed-trial")
        self.assertTrue(plugins.verify_native_plugin_inputs(self.context, first_manifest))
        self.assertTrue(plugins.verify_native_plugin_inputs(second, second_manifest))

    def test_resource_bytes_mode_and_membership_drift_are_not_reused(self):
        for change in ("bytes", "mode", "file-add", "file-remove", "empty-directory-add", "version-add"):
            with self.subTest(change=change):
                context = self.make_context("drift-" + change)
                manifest = plugins.prepare_native_plugin_inputs(context, self.source)
                private = Path(context.environment["CODEX_HOME"])
                package = self.cache(private, "helper", "1.0.0")
                if change == "bytes":
                    (package / "resources/info.txt").write_text("changed", encoding="utf-8")
                elif change == "mode":
                    (package / "resources/helper.sh").chmod(0o600)
                elif change == "file-add":
                    (package / "resources/new.txt").write_text("new", encoding="utf-8")
                elif change == "file-remove":
                    (package / "resources/info.txt").unlink()
                elif change == "empty-directory-add":
                    (package / "resources/new-empty").mkdir()
                else:
                    self.write_package(self.cache(private, "helper", "3.0.0"), "helper", "3.0.0", "new-version")
                self.assertFalse(plugins.verify_native_plugin_inputs(context, manifest))


class NativePluginReadbackTests(NativePluginFixtures):
    def setUp(self):
        super().setUp()
        self.manifest = plugins.prepare_native_plugin_inputs(self.context, self.source)

    def test_native_selected_older_version_and_disabled_state_survive_with_unrelated_MCP(self):
        result, rpc = self.observe()
        observed = {row["id"]: row for row in result["plugins"]}
        self.assertEqual(observed[HELPER]["local_version"], "1.0.0")
        self.assertEqual(observed[HELPER]["mcp_servers"], ["cached_v1"])
        self.assertIs(observed[DISABLED]["enabled"], False)
        self.assertEqual(observed[DISABLED]["skills"], [{"name": "inspect", "enabled": False}])
        self.assertEqual({row["name"] for row in result["non_subject_servers"]}, {"ambient", "cached_v1"})
        self.assertEqual(rpc.call_count, 2)
        self.assertTrue(all(call.args[-1] == ("-c", "mcp_servers.hashmarks.enabled=false") for call in rpc.call_args_list))
        self.assertEqual(result["source_equivalence"], "unknown")
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn("private_credentials", json.dumps(result))
        self.assertNotIn("private_value", json.dumps(result))

    def test_enabled_skill_path_is_checked_against_private_alias_not_original_host_file(self):
        (self.old / "skills/inspect/SKILL.md").unlink()
        result, _ = self.observe()
        self.assertEqual(result["state"], "observed")
        private = Path(self.context.environment["CODEX_HOME"])
        self.cache(private, "helper", "1.0.0").joinpath("skills/inspect/SKILL.md").unlink()
        with self.assertRaisesRegex(ValueError, "native-plugin"):
            self.observe()

    def test_uncontrolled_alias_of_Hashmarks_is_not_a_clean_baseline(self):
        initial, details = self.responses()
        initial[3]["data"].append({"name": "innocent-alias", "tools": {"repository_lookup": {}}, "toolsError": None,
                                    "runtimeStatus": "connected", "serverInfo": {"name": "Hashmarks", "version": "fixture"}})
        with self.assertRaisesRegex(ValueError, "native-mcp-uncontrolled-subject-route"):
            self.observe(initial, details)

    def test_cached_tools_do_not_make_a_failed_native_connection_ready(self):
        initial, details = self.responses()
        initial[3]["data"][0]["runtimeStatus"] = "failed"
        with self.assertRaisesRegex(ValueError, "native-mcp-enabled-runtime-unavailable"):
            self.observe(initial, details)

    def test_controlled_baseline_cannot_keep_an_active_canonical_route(self):
        initial, details = self.responses()
        initial[3]["data"].append({"name": "hashmarks", "tools": {"task_evidence": {}},
                                    "toolsError": None, "runtimeStatus": "connected"})
        with self.assertRaisesRegex(ValueError, "native-mcp-controlled-disabled-route-active"):
            self.observe(initial, details)

    def test_subject_transport_readback_must_match_the_exact_admitted_exposure(self):
        initial, details = self.responses()
        exposure = McpExposure("hashmarks", "/admitted/hashmarks", ("--workspace", str(self.workspace), "mcp"),
                               self.workspace, {"transport": "stdio"}, {"OPTION": "preserved"})
        row = {"command": exposure.command, "args": list(exposure.args), "cwd": str(exposure.cwd),
               "enabled": True, "env": dict(exposure.environment)}
        initial[2]["config"]["mcp_servers"]["hashmarks"] = row
        initial[3]["data"].append({"name": "hashmarks", "tools": {"task_evidence": {}},
                                    "toolsError": None, "runtimeStatus": "connected"})
        result, _ = self.observe(initial, details, exposure)
        self.assertEqual({row["name"] for row in result["non_subject_servers"]}, {"ambient", "cached_v1"})
        row["command"] = "/other/hashmarks"
        with self.assertRaisesRegex(ValueError, "native-mcp-controlled-transport-not-exact"):
            self.observe(initial, details, exposure)

    def test_missing_native_inventory_scope_or_pagination_is_not_empty_observation(self):
        initial, details = self.responses()
        variants = []
        missing_plugins = copy.deepcopy(initial)
        missing_plugins[1] = {}
        variants.append(missing_plugins)
        paginated = copy.deepcopy(initial)
        paginated[3]["nextCursor"] = "more"
        variants.append(paginated)
        missing_servers = copy.deepcopy(initial)
        missing_servers[3] = {}
        variants.append(missing_servers)
        missing_enabled_server = copy.deepcopy(initial)
        missing_enabled_server[3]["data"] = [row for row in missing_enabled_server[3]["data"] if row["name"] != "ambient"]
        variants.append(missing_enabled_server)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.observe(value, details)

    def test_native_detail_identity_and_capability_scope_must_match_selected_plugin(self):
        initial, details = self.responses()
        variants = []
        wrong_identity = copy.deepcopy(details)
        wrong_identity[100]["plugin"]["summary"]["id"] = "different@fixture-market"
        variants.append(wrong_identity)
        missing_skills = copy.deepcopy(details)
        del missing_skills[100]["plugin"]["skills"]
        variants.append(missing_skills)
        missing_version = copy.deepcopy(details)
        del missing_version[100]["plugin"]["summary"]["localVersion"]
        variants.append(missing_version)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.observe(initial, value)


class NativePluginProcessBoundaryTests(NativePluginFixtures):
    def test_app_server_and_descendants_use_outer_namespace_with_final_overrides(self):
        raw = b'\n'.join(json.dumps({"id": i, "result": value}).encode() for i, value in ((0, {}), (1, {"marketplaces": []}))) + b'\n'
        process = SimpleNamespace(return_code=0, timed_out=False, executable_missing=False,
                                  stdout_truncated=False, stderr_truncated=False, stdout=raw)
        wrapped = ("/system/bwrap", "--", "native-codex", "app-server")
        with patch.object(plugins, "build_native_argv", return_value=wrapped) as build, \
             patch.object(plugins, "run_bounded", return_value=process) as run:
            plugins._rpc(self.context, "native-codex", self.source,
                         [(1, "plugin/installed", {})], ("-c", "mcp_servers.hashmarks.enabled=false"))
        self.assertEqual(build.call_args.args[0], self.context)
        self.assertEqual(build.call_args.args[2], self.source)
        self.assertEqual(build.call_args.args[1], ("native-codex", "--no-daemon", "-c", "mcp_servers.hashmarks.enabled=false", "app-server", "--listen", "stdio://"))
        self.assertEqual(run.call_args.kwargs["argv"], wrapped)
        self.assertFalse(run.call_args.kwargs["inherit_environment"])

    def test_namespace_rejection_precedes_every_native_plugin_process(self):
        with patch.object(plugins, "build_native_argv", side_effect=ValueError("native Codex namespace invalid")), \
             patch.object(plugins, "run_bounded") as run, \
             self.assertRaisesRegex(ValueError, "native Codex namespace"):
            plugins._rpc(self.context, "native-codex", self.source, [(1, "plugin/installed", {})], ())
        run.assert_not_called()

    def test_direct_plugin_context_cannot_bypass_private_root_guard(self):
        original = dict(self.context.environment)
        for name in sorted(PRIVATE_STATE_ENV_KEYS):
            with self.subTest(name=name), \
                 patch.object(plugins, "build_native_argv") as build, \
                 patch.object(plugins, "run_bounded") as run, \
                 self.assertRaisesRegex(ValueError, "native Codex state"):
                self.context.environment.clear()
                self.context.environment.update(original)
                self.context.environment[name] = "/outside-private-trial/" + name
                plugins._rpc(self.context, "native-codex", self.source, [(1, "plugin/installed", {})], ())
            run.assert_not_called()
            build.assert_not_called()


class NativeDynamicRpcTests(NativePluginFixtures):
    def setUp(self):
        super().setUp()
        self.command = self.root / "native-dynamic-rpc-fixture"
        self.command.write_text(DYNAMIC_RPC_CLI, encoding="utf-8")
        self.command.chmod(0o700)

    def invoke(self, context):
        # Only filesystem wrapping is replaced: the real bounded JSONL process,
        # readiness predicates and stdout-dependent payload factories execute.
        with patch.object(plugins, "build_native_argv", side_effect=lambda context, command, source: command) as build:
            result = plugins._rpc(
                context, str(self.command), self.source,
                [(1, "plugin/installed", {"cwds": [str(context.workspace)]}),
                 (2, "config/read", {"cwd": str(context.workspace), "includeLayers": False}),
                 (3, "mcpServerStatus/list", {"detail": "full"})],
                ("-c", "mcp_servers.hashmarks.enabled=false"),
            )
        build.assert_called_once()
        return result

    def read_log(self, context):
        path = Path(context.environment["CODEX_HOME"]) / "dynamic-rpc-methods.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_native_response_thread_id_drives_full_status_and_unsubscribe_without_model_turn(self):
        source_before = plugins._records(self.source)
        first = self.invoke(self.context)
        second = self.invoke(self.context)
        first_id, second_id = first[3]["data"][0]["threadId"], second[3]["data"][0]["threadId"]
        self.assertNotEqual(first_id, second_id)
        self.assertTrue(first_id.startswith("native-fixture-"))
        self.assertEqual(first[3]["data"][0]["runtimeStatus"], "connected")
        self.assertEqual(first[3]["data"][0]["tools"], {"fixture_read": {}})
        log = self.read_log(self.context)
        requests = [row for row in log if row["event"] == "request"]
        expected = ["initialize", "initialized", "plugin/installed", "config/read",
                    "thread/start", "mcpServerStatus/list", "mcpServerStatus/list", "thread/unsubscribe"]
        self.assertEqual([row["method"] for row in requests], expected * 2)
        for offset, thread_id in ((0, first_id), (8, second_id)):
            self.assertEqual(requests[offset + 4]["params"], {"cwd": str(self.workspace), "ephemeral": True})
            self.assertEqual(requests[offset + 5]["params"], {"detail": "full", "threadId": thread_id})
            self.assertEqual(requests[offset + 6]["params"], {"detail": "full", "threadId": thread_id})
            self.assertEqual(requests[offset + 7]["params"], {"threadId": thread_id})
        endings = [row for row in log if row["event"] == "eof"]
        self.assertEqual(len(endings), 2)
        self.assertTrue(all(row["thread_unsubscribed"] is True and row["active_thread_file"] is False for row in endings))
        self.assertFalse(any(row["method"].startswith("turn/") for row in requests))
        startup = next(row for row in log if row["event"] == "startup")
        self.assertEqual(startup["cwd"], str(self.workspace))
        self.assertEqual(startup["argv"], ["--no-daemon", "-c", "mcp_servers.hashmarks.enabled=false", "app-server", "--listen", "stdio://"])
        self.assertFalse((Path(self.context.environment["CODEX_HOME"]) / "dynamic-rpc-active.json").exists())
        self.assertEqual(plugins._records(self.source), source_before)

    def test_starting_runtime_waits_for_its_own_native_event_then_rereads_status(self):
        for terminal, expected in (("ready", "connected"), ("failed", "failed")):
            with self.subTest(terminal=terminal):
                context = self.make_context("startup-" + terminal)
                context.environment["FIXTURE_RUNTIME_STARTUP"] = terminal
                result = self.invoke(context)
                self.assertEqual(result[3]["data"][0]["runtimeStatus"], expected)
                requests = [row for row in self.read_log(context) if row["event"] == "request"]
                self.assertEqual(sum(row["method"] == "mcpServerStatus/list" for row in requests), 2)
                self.assertFalse(any(row["method"].startswith("turn/") for row in requests))

    def test_missing_empty_or_wrong_type_native_thread_id_stops_before_status_or_model_request(self):
        source_before = plugins._records(self.source)
        for mode in ("missing", "empty", "non-string"):
            context = self.make_context("bad-thread-" + mode)
            context.environment["FIXTURE_THREAD_RESPONSE"] = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "native-mcp-ephemeral-runtime-unavailable"):
                self.invoke(context)
            requests = [row["method"] for row in self.read_log(context) if row["event"] == "request"]
            self.assertIn("thread/start", requests)
            self.assertNotIn("mcpServerStatus/list", requests)
            self.assertFalse(any(method.startswith("turn/") for method in requests))
        self.assertEqual(plugins._records(self.source), source_before)


class NativePluginSelectionFailureTests(NativePluginFixtures):
    def test_selection_failures_name_scope_and_cause_without_retaining_native_output(self):
        self.context.environment["BENCHMARK_NATIVE_HOME"] = str(self.root)
        cases = (
            ({"timed_out": True}, "timeout"),
            ({"return_code": 7}, "nonzero-exit"),
            ({"executable_missing": True}, "missing-executable"),
            ({"stdout_truncated": True}, "truncated"),
        )
        for reference in (False, True):
            for changed, cause in cases:
                with self.subTest(reference=reference, cause=cause):
                    result = SimpleNamespace(**{
                        "return_code": 0, "timed_out": False,
                        "executable_missing": False, "stdout_truncated": False,
                        "stderr_truncated": False, "stdout": SECRET.encode(),
                        "stderr": SECRET.encode(), **changed,
                    })
                    with patch.object(plugins, "build_native_argv", return_value=("/system/bwrap", "--", "native-codex")), \
                         patch.object(plugins, "run_bounded", return_value=result), \
                         self.assertRaises(ValueError) as failure:
                        plugins.observe_native_plugin_selection(self.context, "native-codex", self.source, reference=reference)
                    message = str(failure.exception)
                    self.assertIn("source-readonly" if reference else "trial", message)
                    self.assertIn(cause, message)
                    self.assertNotIn(SECRET, message)


if __name__ == "__main__":
    unittest.main()
