"""E265–E268 model-free attacks on real-worker mount admission.

Subprocesses and kernel observations are mocked here. Only an actual
target-host bwrap invocation can establish the live mount facts.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.harness.native_bwrap_execution import (
    admit_host_only_paths,
)
from benchmarks.harness.native_mount_probe import (
    GUEST_PROBE, inspect_mount_probe, probe_native_mounts,
    verify_worker_source_unchanged,
)
from benchmarks.tests.test_opencode_native_gateway import H


class HostSecretBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.host_key = self.root / "host.key"
        self.host_key.write_bytes(b"private-host-key")
        self.native = self.root / "opencode"
        self.native.write_bytes(b"bin")
        self.hashmarks = self.root / "hashmarks"
        self.hashmarks.write_bytes(b"bin")
        self.kwargs = dict(
            workspace=self.workspace, run_root=self.run_root,
            host_key_file=self.host_key, executable=self.native,
            hashmarks_executable=self.hashmarks,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_separate_host_key_and_executables_admitted(self):
        admit_host_only_paths(**self.kwargs)

    def test_key_in_workspace_is_rejected(self):
        key = self.workspace / "key"
        key.write_bytes(b"secret")
        with self.assertRaisesRegex(ValueError, "host-authority-exposed"):
            admit_host_only_paths(**{**self.kwargs, "host_key_file": key})

    def test_key_inside_runtime_readonly_root_is_still_exposed(self):
        with self.assertRaisesRegex(ValueError, "host-authority-exposed"):
            admit_host_only_paths(**{**self.kwargs, "host_key_file": Path("/usr/share/host.key")})

    def test_symlinked_parent_cannot_bypass_path_admission(self):
        redirect = self.root / "redirect"
        redirect.symlink_to(self.workspace, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "host-authority-exposed"):
            admit_host_only_paths(**{
                **self.kwargs, "host_key_file": redirect / "nested-key",
            })

    def test_agent_can_mutate_executable_inside_workspace_so_reject(self):
        executable = self.workspace / "untrusted-opencode"
        executable.write_bytes(b"bin")
        with self.assertRaisesRegex(ValueError, "host-authority-exposed"):
            admit_host_only_paths(**{**self.kwargs, "executable": executable})

    def test_workspace_cannot_alias_readonly_system_mount(self):
        with self.assertRaisesRegex(ValueError, "host-authority-exposed"):
            admit_host_only_paths(**{**self.kwargs, "workspace": Path("/usr/experiment")})

    def test_run_root_inside_workspace_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "host-authority-exposed"):
            admit_host_only_paths(**{**self.kwargs, "run_root": self.workspace / "runs"})


class RuntimeMountProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.run_root = self.root / "trial"
        self.run_root.mkdir()
        for name in ("home", "config", "cache", "data", "state", "gateway"):
            (self.run_root / name).mkdir()
        (self.run_root / "opencode.json").write_text("{}")
        self.opencode = self.root / "opencode"
        self.opencode.write_bytes(b"opencode executable")
        self.hashmarks = self.root / "hashmarks"
        self.hashmarks.write_bytes(b"hashmarks executable")
        self.key = self.root / "host.key"
        self.key.write_bytes(b"no child access")
        self.substrate = {
            "bwrap": "/usr/bin/bwrap", "bwrap_sha256": H("bwrap"),
            "python": "/usr/bin/python3", "python_sha256": H("python"),
        }
        self.env = {
            "HOME": str(self.run_root / "home"),
            "XDG_CONFIG_HOME": str(self.run_root / "config"),
            "XDG_CACHE_HOME": str(self.run_root / "cache"),
            "XDG_DATA_HOME": str(self.run_root / "data"),
            "XDG_STATE_HOME": str(self.run_root / "state"),
            "OPENCODE_CONFIG": str(self.run_root / "opencode.json"),
            "OPENCODE_CONFIG_DIR": str(self.run_root / "config"),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_AUTO_SHARE": "false",
            "BENCHMARK_NATIVE_GATEWAY_TOKEN": "token-that-is-local-only",
            "PATH": os.defpath,
        }

    def tearDown(self):
        self.temp.cleanup()

    def run_probe(self, result=0):
        with patch(
            "benchmarks.harness.native_mount_probe.verify_substrate_unchanged",
        ) as source, patch(
            "benchmarks.harness.native_mount_probe.subprocess.run",
            return_value=SimpleNamespace(returncode=result),
        ) as invoked:
            if result != 0:
                with self.assertRaisesRegex(ValueError, "mount-preflight-failed"):
                    probe_native_mounts(
                        self.substrate, executable=self.opencode,
                        hashmarks=self.hashmarks, workspace=self.workspace,
                        run_root=self.run_root, host_key_file=self.key,
                        environment=self.env, port=42000, config_sha256=H("config"),
                    )
                return invoked
            proof = probe_native_mounts(
                self.substrate, executable=self.opencode,
                hashmarks=self.hashmarks, workspace=self.workspace,
                run_root=self.run_root, host_key_file=self.key,
                environment=self.env, port=42000, config_sha256=H("config"),
            )
            self.assertEqual(source.call_count, 2)
            return proof, invoked

    def test_probe_uses_real_worker_sandbox_argv_and_no_provider(self):
        proof, invoked = self.run_probe()
        self.assertTrue(inspect_mount_probe(
            proof, expected_bwrap_sha256=H("bwrap"),
            expected_config_sha256=H("config"),
        ))
        self.assertFalse(proof["provider_model_execution_observed"])
        self.assertFalse(proof["independent_process_origin_proven"])
        args = invoked.call_args
        argv = args.args[0]
        self.assertEqual(argv[0], "/usr/bin/bwrap")
        self.assertIn("--unshare-net", argv)
        self.assertIn("/run/native-netns-worker.py", argv)
        self.assertIn(GUEST_PROBE, argv)
        self.assertIn(str(self.key), argv)
        self.assertNotIn("BENCHMARK_UPSTREAM_API_KEY", argv)
        self.assertNotIn("provider-secret", argv)
        self.assertEqual(args.kwargs["timeout"], 20)
        self.assertEqual(args.kwargs["env"], self.env)

    def test_failed_guest_worker_blocks_model_launch(self):
        self.run_probe(result=41)

    def test_readonly_config_and_secret_claims_are_strict(self):
        proof, _ = self.run_probe()
        bad = (
            ("readonly_config_observed", False),
            ("gateway_socket_observed", False),
            ("host_key_not_mounted_observed", False),
            ("provider_model_execution_observed", True),
            ("independent_process_origin_proven", True),
            ("config_sha256", H("different")),
            ("worker_sha256", "bad"),
            ("probe_program_sha256", H("different")),
        )
        for name, value in bad:
            with self.subTest(name=name):
                altered = {**proof, name: value}
                self.assertFalse(inspect_mount_probe(
                    altered, expected_bwrap_sha256=H("bwrap"),
                    expected_config_sha256=H("config"),
                ))

    def test_worker_source_rechecked_with_exact_pinned_digest(self):
        proof, _ = self.run_probe()
        verify_worker_source_unchanged(proof)
        changed = {**proof, "worker_sha256": H("older-worker")}
        with self.assertRaisesRegex(ValueError, "worker-source-drift"):
            verify_worker_source_unchanged(changed)
        with patch(
            "benchmarks.harness.native_mount_probe._sha",
            side_effect=[H("first"), H("replaced")],
        ), patch(
            "benchmarks.harness.native_mount_probe.verify_substrate_unchanged",
        ), patch(
            "benchmarks.harness.native_mount_probe.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ):
            with self.assertRaisesRegex(ValueError, "worker-source-changed"):
                probe_native_mounts(
                    self.substrate, executable=self.opencode,
                    hashmarks=self.hashmarks, workspace=self.workspace,
                    run_root=self.run_root, host_key_file=self.key,
                    environment=self.env, port=42000, config_sha256=H("config"),
                )


if __name__ == "__main__":
    unittest.main()
