"""E257-E259 Linux netns + host Unix gateway attack ring (model-free).

These prove exact launch construction and an actual Unix-socket relay.
No fake subprocess or local socket is described as a completed model trial.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.harness.native_bwrap_execution import (
    admit_netns_substrate, build_isolated_argv, verify_substrate_unchanged,
)
from benchmarks.harness.native_netns_worker import _relay, serve_child
from benchmarks.harness.opencode_native_gateway import (
    gateway_config, NativeOpenCodeGateway,
)
from benchmarks.harness.opencode_native_trial import launch_native_trial
from benchmarks.harness.opencode_native_export import native_export_preflight
from benchmarks.tests.test_opencode_native_gateway import (
    KEY, H, manifest, source, request, UPSTREAM,
)

RESPONSE = b'{"choices":[{"index":0,"message":{"role":"assistant","content":"owner"}}]}'


class IsolationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / ".git").mkdir()
        self.run_root = self.root / "host-only"
        self.run_root.mkdir(mode=0o700)
        for name in ("home", "config", "cache", "data", "state", "gateway"):
            (self.run_root / name).mkdir(mode=0o700)
        (self.run_root / "opencode.json").write_text("{}")
        self.exe = self.root / "opencode"
        self.exe.write_text("#!/bin/sh\nexit 0\n")
        self.exe.chmod(0o700)
        self.hashmarks = self.root / "hashmarks"
        self.hashmarks.write_text("#!/bin/sh\nexit 0\n")
        self.hashmarks.chmod(0o700)
        self.key = self.root / "host.key"
        self.key.write_bytes(KEY)
        self.key.chmod(0o600)
        self.substrate = {
            "bwrap": "/usr/bin/bwrap", "bwrap_sha256": H("bwrap"),
            "ip": "/usr/sbin/ip", "ip_sha256": H("ip"),
            "python": "/usr/bin/python3", "python_sha256": H("python"),
        }
        self.environment = {
            "HOME": str(self.run_root / "home"),
            "XDG_CONFIG_HOME": str(self.run_root / "config"),
            "XDG_CACHE_HOME": str(self.run_root / "cache"),
            "XDG_DATA_HOME": str(self.run_root / "data"),
            "XDG_STATE_HOME": str(self.run_root / "state"),
            "OPENCODE_CONFIG": str(self.run_root / "opencode.json"),
            "OPENCODE_CONFIG_DIR": str(self.run_root / "config"),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_AUTO_SHARE": "false",
            "BENCHMARK_NATIVE_GATEWAY_TOKEN": "a" * 40,
            "PATH": os.defpath,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        return build_isolated_argv(
            self.substrate, executable=self.exe,
            workspace=self.workspace, run_root=self.run_root,
            gateway_dir=self.run_root / "gateway",
            environment=self.environment,
            command=[str(self.exe), "run", "evaluate"],
            worker_port=42424,
            hashmarks_executable=self.hashmarks,
        )

    def test_bwrap_denies_network_and_never_exposes_host_home_or_provider_key(self):
        argv = self.plan()
        self.assertEqual(argv[0], "/usr/bin/bwrap")
        for required in ("--unshare-user", "--unshare-pid", "--unshare-net",
                         "--clearenv", "--die-with-parent"):
            self.assertIn(required, argv)
        self.assertNotIn("--share-net", argv)
        self.assertNotIn(("ro-bind", "/", "/"), list(zip(argv, argv[1:], argv[2:])))
        self.assertNotIn("BENCHMARK_UPSTREAM_API_KEY", argv)
        self.assertNotIn("provider-secret", argv)
        self.assertIn(str(self.hashmarks), argv)
        self.assertIn("--ro-bind", argv)
        self.assertIn("/run/gateway", argv)
        self.assertIn("/run/native-netns-worker.py", argv)
        self.assertEqual(argv[-3:], [str(self.exe), "run", "evaluate"])

    def test_sandbox_env_must_match_explicit_allowlist_and_mounts(self):
        bad_env = {**self.environment, "OPENAI_API_KEY": "leaked"}
        with self.assertRaisesRegex(ValueError, "unadmitted-native-execution"):
            build_isolated_argv(
                self.substrate, executable=self.exe,
                workspace=self.workspace, run_root=self.run_root,
                gateway_dir=self.run_root / "gateway",
                environment=bad_env, command=[str(self.exe)],
                worker_port=42424, hashmarks_executable=self.hashmarks,
            )
        with self.assertRaises(ValueError):
            build_isolated_argv(
                self.substrate, executable=self.exe,
                workspace=self.workspace, run_root=self.workspace / "inside",
                gateway_dir=self.workspace / "inside" / "gateway",
                environment=self.environment, command=[str(self.exe)],
                worker_port=42424,
            )
        self.assertNotIn(str(self.key), self.plan())

    def test_missing_netns_and_hashmarks_authorities_fail_before_model_work(self):
        m = manifest()
        cell = next(row for row in m["assignments"] if row["arm"] == "control")
        kwargs = dict(
            manifest=m, trial_id=cell["trial_id"],
            opencode_executable=self.exe,
            expected_binary_sha256=hashlib.sha256(self.exe.read_bytes()).hexdigest(),
            expected_version="fixture", run_root=self.root / "not-created",
            workspace=self.workspace, prompt="locate owner",
            host_key_file=self.key, host_identity="host",
            current=source(), replaced=None,
            upstream=UPSTREAM, approved_origin="https://api.example.invalid",
            upstream_api_key="provider-secret",
            catalog_sha256=H("catalog"), oracle_sha256=H("oracle"),
            workspace_sha256=H("workspace"),
            use_netns_isolation=True,
        )
        with patch(
            "benchmarks.harness.opencode_native_trial.admit_netns_substrate",
            side_effect=ValueError("missing-native-netns-substrate-bwrap"),
        ):
            with self.assertRaisesRegex(ValueError, "missing-native-netns-substrate"):
                launch_native_trial(**kwargs)
        self.assertFalse(kwargs["run_root"].exists())
        with patch(
            "benchmarks.harness.opencode_native_trial.admit_netns_substrate",
            return_value=self.substrate,
        ):
            with self.assertRaisesRegex(ValueError, "missing-or-stale-frozen-hashmarks"):
                launch_native_trial(**kwargs)
        self.assertFalse(kwargs["run_root"].exists())

    @unittest.skipUnless(Path("/usr/bin/python3").is_file(), "system Python unavailable")
    def test_uv_host_python_cannot_escape_native_runtime_mounts(self):
        # The test host often uses uv under ~/.cache/uv: that executable
        # is intentionally absent from the sandbox root.
        with patch(
            "benchmarks.harness.native_bwrap_execution.sys.executable",
            "/home/runner/.cache/uv/python/bin/python",
        ), patch(
            "benchmarks.harness.native_bwrap_execution.shutil.which",
            return_value="/usr/bin/bash",
        ):
            substrate = admit_netns_substrate()
        self.assertTrue(Path(substrate["python"]).is_relative_to("/usr"))
        self.assertNotIn(".cache", substrate["python"])
        self.assertEqual(
            hashlib.sha256(Path(substrate["python"]).read_bytes()).hexdigest(),
            substrate["python_sha256"],
        )

    def test_pinned_hashmarks_config_is_the_only_local_mcp_source(self):
        config = gateway_config(
            port=42424, model_id="gpt-fixture",
            hashmarks_executable=self.hashmarks,
            workspace=self.workspace,
        )
        self.assertEqual(list(config["mcp"]), ["hashmarks"])
        self.assertEqual(config["mcp"]["hashmarks"]["command"], [
            str(self.hashmarks), "--workspace", str(self.workspace), "mcp",
        ])
        self.assertEqual(config["enabled_providers"], ["agentscookbook-captured"])
        with self.assertRaises(ValueError):
            gateway_config(port=42424, model_id="gpt-fixture",
                           hashmarks_executable=self.hashmarks)


class UnixSocketNativeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        socket_dir = self.root / "private"
        socket_dir.mkdir(mode=0o700)
        self.sock = socket_dir / "upstream.sock"
        self.key = self.root / "host.key"
        self.key.write_bytes(KEY)
        self.key.chmod(0o600)
        m = manifest()
        cell = next(row for row in m["assignments"] if row["arm"] == "control")
        self.gateway = NativeOpenCodeGateway(
            m, trial_id=cell["trial_id"], host_key_file=self.key,
            host_identity="trusted-provider-host", current=source(),
            replaced=None,
            gateway_token="some-privileged-local-secret-12345678",
            upstream=UPSTREAM, approved_origin="https://api.example.invalid",
            upstream_api_key="only-in-host-process",
            catalog_sha256=H("catalog"), oracle_sha256=H("oracle"),
            workspace_sha256=H("workspace"),
        )
        self.gateway.start_unix(self.sock)

    def tearDown(self):
        self.gateway.close()
        self.tmp.cleanup()

    def test_actual_unix_bridge_delivers_exact_frozen_tool_input_once(self):
        payload = request(
            selected=self.gateway.selected["selected_content"], tool=True,
        )
        with patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
            return_value=RESPONSE,
        ) as upstream:
            observed = _relay(str(self.sock),
                              "Bearer " + self.gateway.token, payload)
            self.assertEqual(observed, RESPONSE)
            self.assertEqual(upstream.call_count, 1)
            self.assertEqual(
                upstream.call_args.kwargs["serialized_model_request"], payload,
            )
            with self.assertRaises(ValueError):
                _relay(str(self.sock), "Bearer " + self.gateway.token, payload)
            self.assertEqual(upstream.call_count, 1)
        self.assertEqual(self.gateway.result()["post_evidence_submissions"], 1)
        self.assertGreaterEqual(self.gateway.result()["unix_peer_credentials_observed"], 1)
        self.assertFalse(self.gateway.result()["socket_peer_executable_verified"])
        self.assertFalse(self.gateway.result()["native_process_origin_proven"])

    def test_host_namespace_peer_cannot_impersonate_isolated_worker(self):
        self.gateway.close()
        self.gateway.start_unix(self.sock, require_isolated_peer=True)
        with patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
            return_value=RESPONSE,
        ) as upstream:
            with self.assertRaises(ValueError):
                _relay(str(self.sock), "Bearer " + self.gateway.token, request(
                    selected=self.gateway.selected["selected_content"], tool=True,
                ))
            upstream.assert_not_called()
        self.assertTrue(self.gateway.result()["kernel_netns_peer_check_required"])
        self.assertEqual(self.gateway.result()["distinct_netns_peer_connections"], 0)
        self.assertFalse(self.gateway.result()["native_process_origin_proven"])

    def test_early_gateway_disconnect_is_a_terminal_relay_denial(self):
        # Peer-namespace rejection can race with either sendall() or recv().
        # Both kernel outcomes must be a stable fail-closed ValueError rather
        # than an uncaught transport exception or a retry.
        for stage, error in (
            ("write", BrokenPipeError("gateway-refused-peer")),
            ("read", ConnectionResetError("gateway-refused-peer")),
        ):
            with self.subTest(stage=stage):
                with patch("benchmarks.harness.native_netns_worker.socket.socket") as factory:
                    client = factory.return_value.__enter__.return_value
                    if stage == "write":
                        client.sendall.side_effect = error
                    else:
                        client.recv.side_effect = error
                    with self.assertRaisesRegex(
                        ValueError, "relay-host-closed-before-response"
                    ):
                        _relay(str(self.sock), "Bearer " + self.gateway.token, b"{}")
                    factory.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.assertEqual(client.connect.call_count, 1)
                self.assertEqual(self.gateway.result()["post_evidence_submissions"], 0)
                self.assertEqual(
                    self.gateway.result()["distinct_netns_peer_connections"], 0
                )

    def test_bad_token_or_foreign_packet_refuses_host_submission(self):
        with patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
            return_value=RESPONSE,
        ) as upstream:
            with self.assertRaises(ValueError):
                _relay(str(self.sock), "Bearer wrong", request(
                    selected=self.gateway.selected["selected_content"], tool=True,
                ))
            with self.assertRaises(ValueError):
                _relay(str(self.sock), "Bearer " + self.gateway.token, request(
                    selected={"owner": "forged.py"}, tool=True,
                ))
            upstream.assert_not_called()
        self.assertEqual(self.gateway.result()["post_evidence_submissions"], 0)
        self.assertEqual(self.gateway.result()["unix_peer_credentials_observed"], 0)
        self.assertEqual(self.gateway.result()["distinct_netns_peer_connections"], 0)

    def test_guest_loopback_relay_forwards_only_to_unix_socket(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            port = port_probe.getsockname()[1]
        chosen = request(
            selected=self.gateway.selected["selected_content"], tool=True,
        )
        result = []
        def fake_native(argv, **kwargs):
            from urllib.request import Request, urlopen
            request_obj = Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=chosen,
                headers={"Authorization": "Bearer " + self.gateway.token},
                method="POST",
            )
            with urlopen(request_obj, timeout=3) as response:
                result.append(response.read())
            return SimpleNamespace(returncode=0)
        with patch(
            "benchmarks.harness.opencode_native_gateway.dispatch_verified_chat_response",
            return_value=RESPONSE,
        ), patch(
            "benchmarks.harness.native_netns_worker._relay",
            side_effect=lambda path, token, body: _relay(str(self.sock), token, body),
        ) as forwarded, patch(
            "benchmarks.harness.native_netns_worker.subprocess.run",
            side_effect=fake_native,
        ):
            status = serve_child(port, "/run/gateway/upstream.sock", ["/bin/echo"])
        # Model-free host test substitutes the bind mount destination while
        # exercising real TCP and Unix socket I/O.
        self.assertEqual(status, 0)
        self.assertEqual(result, [RESPONSE])
        self.assertEqual(forwarded.call_count, 1)
        self.assertEqual(forwarded.call_args.args[0],
                         "/run/gateway/upstream.sock")


if __name__ == "__main__":
    unittest.main()
