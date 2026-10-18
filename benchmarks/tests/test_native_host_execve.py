"""Model-free host execve boundary and independent MAC verification attacks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.native_host_execve import (
    admit_execve_tracer,
    run_host_execve_observed,
    summarize_execve_trace,
    verify_host_execve_receipt,
)

KEY = b"external-host-owned-secret-that-the-agent-cannot-read"
BUILD = "e" * 64


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary(text: str) -> dict:
    return summarize_execve_trace(text.encode("utf-8"))


class NativeExecveBoundaryTests(unittest.TestCase):
    def test_only_successful_kernel_style_executable_launches_count(self) -> None:
        row = summary(
            '123 execve("/usr/bin/rg", ["rg", "--hidden"], 0x123) = 0\n'
            '123 execve("/usr/bin/grep", ["grep", "secret"], 0x123) = 0\n'
            '[pid 456] execve("/bin/sh", ["sh"], 0x123) = 0\n'
            '123 execve("/usr/bin/false", ["false"], 0x123) = -1 ENOENT (Not found)\n'
            '123 execve("/usr/bin/git", ["git", "grep"], 0x123) = 0\n'
        )
        self.assertEqual(row["successful_execve_observations"], 4)
        self.assertEqual(row["recognized_search_process_launches"], 2)
        self.assertFalse(row["no_hidden_searches_proven"])
        self.assertFalse(row["complete_nested_tool_call_coverage_proven"])
        self.assertFalse(row["causal_search_savings_proven"])
        self.assertNotIn("secret", json.dumps(row))
        # git grep is not promoted into a known search from executable name.
        self.assertNotIn("git", row["recognized_search_executables"])

    def test_partial_fork_lines_never_become_zero_search(self) -> None:
        bad = (
            b"",
            b"garbage",
            b'123 execve("/bin/sh", ["sh"] <unfinished ...>\n',
            b'[pid 123] <... execve resumed> ) = 0\n',
            b'123 execveat(AT_FDCWD, "/usr/bin/rg", ...) = 0\n',
            b'123 execve("relative/rg", ["rg"], 0x123) = 0\n',
            b'123 execve("/usr/bin/rg", ["rg"], 0x123) = ?\n',
            b'123 execve("/usr/bin/rg", ["rg"], 0x123) = 0\n'
            b'123 unknown("x") = 0\n',
        )
        for trace in bad:
            with self.subTest(trace=trace), self.assertRaises(ValueError):
                summarize_execve_trace(trace)

    def _tracer_fixture(self, root: Path) -> Path:
        """Simulation of the transport adapter, NOT an actual syscall tracer."""
        tracer = root / "host-tracer-test-fixture"
        tracer.write_text(
            "#!" + sys.executable + "\n"
            "import pathlib,subprocess,sys\n"
            "args=sys.argv[1:]\n"
            "out=pathlib.Path(args[args.index('-o')+1])\n"
            "cmd=args[args.index('--')+1:]\n"
            "r=subprocess.run(cmd,check=False)\n"
            "out.write_text('execve(\"'+cmd[0]+'\", [\"elided\"], 0x0) = 0\\n')\n"
            "sys.exit(r.returncode)\n",
            encoding="utf-8",
        )
        tracer.chmod(0o700)
        return tracer

    def test_host_launch_actual_child_and_signs_bounded_redacted_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / "host-results"
            work = root / "workspace"
            work.mkdir()
            run.mkdir(mode=0o700)
            tracer = self._tracer_fixture(root)
            digest = sha(tracer)
            code, evidence = run_host_execve_observed(
                [sys.executable, "-c", "print('native child executed')"],
                tracer=tracer, tracer_sha256=digest,
                workspace=work, run_root=run,
                environment=dict(os.environ), timeout_seconds=10,
                campaign_id="campaign", trial_id="trial",
                host_identity="pinned-host", host_build_sha256=BUILD,
                key=KEY,
            )
            self.assertEqual(code, 0)
            self.assertEqual(evidence["state"], "HOST_EXECVE_OBSERVED")
            self.assertFalse(evidence["nested_call_completeness_proven"])
            self.assertFalse((run / "host-execve.trace").exists())
            receipt = run / "host-execve.attestation.json"
            self.assertTrue(receipt.exists())
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
            row = verify_host_execve_receipt(
                receipt, key=KEY, campaign_id="campaign",
                trial_id="trial", host_identity="pinned-host",
                host_build_sha256=BUILD,
            )
            self.assertEqual(row["observation"]["successful_execve_observations"], 1)
            self.assertFalse(
                row["observation"]["complete_nested_tool_call_coverage_proven"]
            )
            self.assertNotIn("native child executed", receipt.read_text())
            with self.assertRaisesRegex(ValueError, "exists"):
                run_host_execve_observed(
                    [sys.executable, "-c", "pass"],
                    tracer=tracer, tracer_sha256=digest,
                    workspace=work, run_root=run,
                    environment=dict(os.environ), timeout_seconds=10,
                    campaign_id="campaign", trial_id="trial",
                    host_identity="pinned-host", host_build_sha256=BUILD,
                    key=KEY,
                )

    def test_tracer_digest_and_signed_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / "private"
            work = root / "workspace"
            run.mkdir(mode=0o700)
            work.mkdir()
            tracer = self._tracer_fixture(root)
            with self.assertRaisesRegex(ValueError, "drift"):
                admit_execve_tracer(tracer, sha256="a" * 64)
            code, _ = run_host_execve_observed(
                [sys.executable, "-c", "pass"],
                tracer=tracer, tracer_sha256=sha(tracer),
                workspace=work, run_root=run, environment=dict(os.environ),
                timeout_seconds=10, campaign_id="campaign", trial_id="trial",
                host_identity="host", host_build_sha256=BUILD, key=KEY,
            )
            self.assertEqual(code, 0)
            receipt = run / "host-execve.attestation.json"
            for kwargs in (
                {"trial_id": "other"},
                {"host_identity": "other"},
                {"host_build_sha256": "a" * 64},
                {"key": b"other-key-but-still-32-bytes-000000000"},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    verify_host_execve_receipt(receipt, **{
                        "key": KEY, "campaign_id": "campaign",
                        "trial_id": "trial", "host_identity": "host",
                        "host_build_sha256": BUILD, **kwargs,
                    })
            saved = receipt.read_bytes()
            forged = json.loads(saved)
            forged["observation"]["no_hidden_searches_proven"] = True
            receipt.write_text(json.dumps(forged), encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_host_execve_receipt(
                    receipt, key=KEY, campaign_id="campaign",
                    trial_id="trial", host_identity="host",
                    host_build_sha256=BUILD,
                )
            receipt.write_bytes(saved)
            self.assertEqual(
                verify_host_execve_receipt(
                    receipt, key=KEY, campaign_id="campaign",
                    trial_id="trial", host_identity="host",
                    host_build_sha256=BUILD,
                )["observation"]["recognized_search_process_launches"],
                0,
            )

    @unittest.skipUnless(
        sys.platform == "linux" and shutil.which("strace") is not None,
        "real kernel strace is unavailable on this runner",
    )
    def test_real_linux_execve_capture_when_kernel_supports_ptrace(self) -> None:
        tracer = Path(shutil.which("strace") or "")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / "run"
            work = root / "work"
            run.mkdir(mode=0o700)
            work.mkdir()
            executable = shutil.which("grep")
            if executable is None:
                self.skipTest("grep executable unavailable")
            _, result = run_host_execve_observed(
                [executable, "--version"],
                tracer=tracer, tracer_sha256=sha(tracer),
                workspace=work, run_root=run,
                environment=dict(os.environ),
                timeout_seconds=10, campaign_id="campaign", trial_id="trial",
                host_identity="host", host_build_sha256=BUILD,
                key=KEY,
            )
            if result["state"] != "HOST_EXECVE_OBSERVED":
                self.skipTest("ptrace unavailable in this host/kernel environment")
            self.assertEqual(result["observed_search_launches"], 1)
