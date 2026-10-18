"""E261-E264: signed point-in-time kernel probe, never fabricated egress proof.

All process responses are synthetic. This CI suite verifies fail-before-work
contracts; it cannot certify that any user's Linux kernel supports bubblewrap.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.harness.native_netns_probe import (
    PROBE_CODE, SCHEMA, kernel_probe_receipt_valid,
    probe_netns_kernel, validate_kernel_probe,
)
from benchmarks.harness.opencode_native_trial import launch_native_trial
from benchmarks.tests.test_opencode_native_gateway import (
    H, KEY, UPSTREAM, manifest, source,
)


_HOST_STAT = os.stat


def _stat_for_probe(path, *args, **kwargs):
    # Mock only the /proc witness: pathlib itself needs real stat results.
    if str(path) == "/proc/self/ns/net":
        return SimpleNamespace(st_ino=1001)
    return _HOST_STAT(path, *args, **kwargs)


def observed(*, guest: int = 1002, outbound_errno: int = errno.ENETUNREACH) -> dict:
    return {
        "guest_netns_inode": guest, "interfaces": ["lo"],
        "loopback_bind_ok": True, "outbound_errno": outbound_errno,
    }


def admitted() -> dict:
    return {
        "bwrap": "/usr/bin/bwrap", "bwrap_sha256": H("pinned-bwrap"),
        "python": "/usr/bin/python3", "python_sha256": H("pinned-python"),
    }


class NativeKernelProbeAttacks(unittest.TestCase):
    def test_one_observed_distinct_kernel_namespace_with_denied_tcp(self) -> None:
        proof = validate_kernel_probe(
            observed(), host_netns_inode=1001,
            bwrap_sha256=H("pinned-bwrap"), python_sha256=H("pinned-python"),
        )
        self.assertEqual(proof["schema"], SCHEMA)
        self.assertTrue(proof["kernel_probe_qualified"])
        self.assertTrue(kernel_probe_receipt_valid(
            proof, expected_bwrap_sha256=H("pinned-bwrap"),
        ))
        self.assertFalse(proof["exclusive_egress_independently_attested"])
        self.assertFalse(proof["native_child_process_origin_proven"])

    def test_spoofed_or_weakened_kernel_observation_fails_before_model(self) -> None:
        bad = [
            observed(guest=1001),  # same network namespace
            observed(outbound_errno=0),  # outside TCP succeeded
            observed(outbound_errno=errno.ETIMEDOUT),  # timeout != isolation
            {**observed(), "interfaces": ["lo", "eth0"]},
            {**observed(), "loopback_bind_ok": False},
            {**observed(), "guest_netns_inode": True},
            {**observed(), "self_declared_proof": True},
        ]
        for evidence in bad:
            with self.subTest(observed=evidence), self.assertRaises(ValueError):
                validate_kernel_probe(
                    evidence, host_netns_inode=1001,
                    bwrap_sha256=H("pinned-bwrap"),
                    python_sha256=H("pinned-python"),
                )

    def test_validator_rejects_overstated_or_edited_signed_claim(self) -> None:
        proof = validate_kernel_probe(
            observed(), host_netns_inode=1001,
            bwrap_sha256=H("pinned-bwrap"), python_sha256=H("pinned-python"),
        )
        for key, mutation in (
            ("host_netns_inode", 1002),
            ("only_loopback_interface_observed", False),
            ("representative_external_tcp_denied", False),
            ("probe_program_sha256", H("different-program")),
            ("exclusive_egress_independently_attested", True),
            ("native_child_process_origin_proven", True),
            ("bwrap_sha256", H("foreign-bwrap")),
        ):
            altered = {**proof, key: mutation}
            with self.subTest(key=key):
                self.assertFalse(kernel_probe_receipt_valid(
                    altered, expected_bwrap_sha256=H("pinned-bwrap"),
                ))

    def test_real_bwrap_invocation_is_required_but_model_free_and_bounded(self) -> None:
        substrate = admitted()
        fake_child = SimpleNamespace(
            returncode=0, stdout=json.dumps(observed()).encode("utf-8"),
        )
        with patch(
            "benchmarks.harness.native_netns_probe.verify_substrate_unchanged",
        ) as rehash, patch(
            "benchmarks.harness.native_netns_probe.os.stat",
            side_effect=_stat_for_probe,
        ), patch(
            "benchmarks.harness.native_netns_probe.subprocess.run",
            return_value=fake_child,
        ) as subprocess_call:
            result = probe_netns_kernel(substrate)
        self.assertEqual(rehash.call_count, 2)
        self.assertTrue(result["kernel_probe_qualified"])
        self.assertEqual(subprocess_call.call_count, 1)
        args = subprocess_call.call_args
        command = args.args[0]
        self.assertEqual(command[0], "/usr/bin/bwrap")
        self.assertIn("--unshare-net", command)
        self.assertIn("--unshare-user", command)
        self.assertIn("--clearenv", command)
        self.assertIn(PROBE_CODE, command)
        self.assertEqual(args.kwargs["timeout"], 10)
        self.assertEqual(args.kwargs["cwd"], "/")
        self.assertEqual(args.kwargs["env"], {"PATH": os.defpath})
        self.assertNotIn("--share-net", command)
        self.assertNotIn("BENCHMARK_UPSTREAM_API_KEY", command)

    def test_unavailable_or_host_network_probe_fails_no_fallback(self) -> None:
        substrate = admitted()
        for payload in (
            SimpleNamespace(returncode=1, stdout=b""),
            SimpleNamespace(returncode=0, stdout=b'{"bad":true}'),
            SimpleNamespace(returncode=0, stdout=b" " * 4097),
        ):
            with self.subTest(payload=payload), patch(
                "benchmarks.harness.native_netns_probe.verify_substrate_unchanged",
            ), patch(
                "benchmarks.harness.native_netns_probe.os.stat",
                side_effect=_stat_for_probe,
            ), patch(
                "benchmarks.harness.native_netns_probe.subprocess.run",
                return_value=payload,
            ):
                with self.assertRaises(ValueError):
                    probe_netns_kernel(substrate)

    def test_failed_kernel_probe_never_creates_native_run_or_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            (workspace / ".git").mkdir(parents=True)
            exe = root / "opencode"
            exe.write_text("#!/bin/sh\necho pinned-version\n")
            exe.chmod(0o700)
            hashmarks = root / "hashmarks"
            hashmarks.write_text("#!/bin/sh\nexit 0\n")
            hashmarks.chmod(0o700)
            key = root / "host.key"
            key.write_bytes(KEY)
            key.chmod(0o600)
            plan = manifest()
            # The historical structured control is deliberately rejected by
            # pure Chat tool-wire admission *before* the kernel probe. Use
            # its wire-compatible text variant to exercise the independent
            # kernel-denial path without weakening fail-before-work.
            trial = next(
                row["trial_id"] for row in plan["assignments"]
                if row["arm"] == "variant"
            )
            kwargs = {
                "manifest": plan, "trial_id": trial,
                "opencode_executable": exe,
                "expected_binary_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
                "expected_version": "pinned-version",
                "run_root": root / "run", "workspace": workspace,
                "prompt": "locate source owner", "host_key_file": key,
                "host_identity": "independent-host", "current": source(),
                "replaced": None, "upstream": UPSTREAM,
                "approved_origin": "https://api.example.invalid",
                "upstream_api_key": "host-only-provider-key",
                "catalog_sha256": H("catalog"),
                "oracle_sha256": H("oracle"),
                "workspace_sha256": H("workspace"),
                "use_netns_isolation": True,
                # Test kernel denial with explicit diagnostic admission:
                # the historical structured control cannot form a native pair.
                "allow_unpaired_native_diagnostic": True,
                "hashmarks_executable": hashmarks,
                "expected_hashmarks_sha256": hashlib.sha256(
                    hashmarks.read_bytes()).hexdigest(),
            }
            with patch(
                "benchmarks.harness.opencode_native_trial.admit_netns_substrate",
                return_value=admitted(),
            ), patch(
                "benchmarks.harness.opencode_native_trial.native_export_preflight",
                return_value="/usr/bin/prlimit",
            ), patch(
                "benchmarks.harness.opencode_native_trial.probe_netns_kernel",
                side_effect=ValueError("native-netns-effective-isolation-probe-failed"),
            ) as probed, patch(
                "benchmarks.harness.opencode_native_trial.NativeOpenCodeGateway",
            ) as gateway:
                with self.assertRaisesRegex(ValueError, "effective-isolation-probe-failed"):
                    launch_native_trial(**kwargs)
            probed.assert_called_once()
            gateway.assert_not_called()
            self.assertFalse((root / "run").exists())


if __name__ == "__main__":
    unittest.main()
