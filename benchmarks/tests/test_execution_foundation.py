from __future__ import annotations

import gc
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from benchmarks.adapters.oracles import CommandOracle
from benchmarks.harness.campaign import TrialSpec, pending
from benchmarks.harness.events import EventStreamError, append_event, seal_events
from benchmarks.harness.model import Observation, TrialContext
from benchmarks.harness.receipt import write_receipt
from benchmarks.harness.workspace import (
    diff_snapshots,
    isolated_environment,
    materialize_git,
    snapshot,
)
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


def _context(root: Path) -> TrialContext:
    workspace = root / "workspace"
    workspace.mkdir()
    control_root = root / "isolation"
    environment = isolated_environment(control_root)
    return TrialContext(
        workspace=workspace,
        control_root=control_root,
        environment=environment,
    )


def _spec() -> TrialSpec:
    return TrialSpec(
        experiment={"id": "e", "version": 1},
        task={"id": "t", "version": 1},
        condition={"id": "c"},
        trial=0,
        seed=1,
        subject_identity={"id": "none", "version": "1"},
        agent_identity={"id": "agent", "version": "1"},
        oracle_identity={"id": "oracle", "version": "1"},
        harness_identity={"commit": "abc"},
        environment_identity={"id": "env"},
    )


class ExecutionFoundationTests(unittest.TestCase):
    def test_snapshot_detects_ignored_path_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ignored").mkdir()
            before = snapshot(root)
            (root / ".ignored" / "side-effect").write_text("x")
            self.assertEqual(
                diff_snapshots(before, snapshot(root))["added"],
                [".ignored/side-effect"],
            )

    def test_snapshot_hashes_symlink_identity_not_external_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root.parent / (root.name + "-external")
            external.write_text("secret")
            try:
                os.symlink(external, root / "link")
                evidence = snapshot(root)["link"]
                self.assertEqual(evidence["kind"], "symlink")
                self.assertEqual(evidence["size"], len(str(external).encode()))
            finally:
                external.unlink(missing_ok=True)

    def test_snapshot_does_not_follow_symlinked_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root.parent / (root.name + "-external-dir")
            external.mkdir()
            (external / "secret").write_text("secret")
            try:
                os.symlink(external, root / "dirlink", target_is_directory=True)
                evidence = snapshot(root)
                self.assertEqual(evidence["dirlink"]["kind"], "symlink")
                self.assertNotIn("dirlink/secret", evidence)
            finally:
                shutil.rmtree(external, ignore_errors=True)

    def test_bounded_process_closes_capture_streams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always", ResourceWarning)
                for _ in range(3):
                    result = run_bounded(
                        repository_root=root,
                        argv=(sys.executable, "-c", "print('ok')"),
                        limits=ProcessLimits(timeout_seconds=10),
                    )
                    self.assertEqual(result.return_code, 0)
                gc.collect()
            resource_warnings = [
                warning
                for warning in seen
                if issubclass(warning.category, ResourceWarning)
            ]
            self.assertEqual(resource_warnings, [])

    def test_bounded_process_can_close_child_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_bounded(
                repository_root=root,
                argv=(
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "data=sys.stdin.buffer.read(); "
                        "print('closed' if data == b'' else 'unexpected')"
                    ),
                ),
                limits=ProcessLimits(timeout_seconds=10),
                close_stdin=True,
            )
            self.assertEqual(result.return_code, 0)
            self.assertEqual(result.stdout.decode().strip(), "closed")

    def test_bounded_exchange_times_out_and_terminates_waiting_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_bounded(
                repository_root=Path(tmp),
                argv=(
                    sys.executable,
                    "-c",
                    "import sys; sys.stdin.buffer.read()",
                ),
                limits=ProcessLimits(timeout_seconds=0.3),
                stdin_bytes=b"request\n",
                stdin_close_when=lambda _: False,
            )
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.return_code, 0)
        if os.name != "nt":
            self.assertEqual(result.process_tree_termination, "posix-process-group")

    def test_staged_exchange_times_out_waiting_for_second_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_bounded(
                repository_root=Path(tmp),
                argv=(
                    sys.executable,
                    "-u",
                    "-c",
                    (
                        "import sys; "
                        "sys.stdin.buffer.readline(); "
                        "print('first', flush=True); "
                        "sys.stdin.buffer.readline(); "
                        "sys.stdin.buffer.read()"
                    ),
                ),
                limits=ProcessLimits(timeout_seconds=0.3),
                stdin_stages=(
                    (b"request-1\n", lambda raw: b"first\n" in raw),
                    (b"request-2\n", lambda raw: b"second\n" in raw),
                ),
            )
        self.assertTrue(result.timed_out)
        self.assertIn(b"first\n", result.stdout)
        self.assertNotEqual(result.return_code, 0)
        if os.name != "nt":
            self.assertEqual(result.process_tree_termination, "posix-process-group")

    def test_bounded_process_interrupt_terminates_process_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            process = mock.Mock(pid=4321)
            process.stdout = io.BytesIO()
            process.stderr = io.BytesIO()
            process.poll.return_value = None
            process.wait.side_effect = [KeyboardInterrupt(), 0]

            with (
                mock.patch(
                    "scripts.agent_economics.bounded_process.subprocess.Popen",
                    return_value=process,
                ),
                mock.patch(
                    "scripts.agent_economics.bounded_process._terminate_tree",
                    return_value="posix-process-group",
                ) as terminate_tree,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_bounded(
                        repository_root=root,
                        argv=("opencode", "run"),
                        limits=ProcessLimits(timeout_seconds=10),
                    )

            terminate_tree.assert_called_once_with(process)
            self.assertEqual(
                process.wait.call_args_list,
                [mock.call(), mock.call(timeout=5.0)],
            )

    def test_oracle_requires_positive_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(Path(tmp))
            oracle = CommandOracle(
                "o",
                "1",
                (sys.executable, "-c", "raise SystemExit(7)"),
                (sys.executable, "-c", "raise SystemExit(0)"),
            )
            self.assertFalse(oracle.healthcheck(context).payload["healthy"])

    def test_oracle_receives_canonical_observation_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(Path(tmp))
            script = (
                "import json,os; "
                "d=json.load(open(os.environ['BENCHMARK_OBSERVATION_PATH'])); "
                "raise SystemExit(0 if d['raw']=='answer' else 9)"
            )
            oracle = CommandOracle(
                "o",
                "1",
                (sys.executable, "-c", "raise SystemExit(0)"),
                (sys.executable, "-c", script),
            )
            graded = oracle.grade(context, Observation({}, "answer"))
            self.assertTrue(graded.payload["passed"])

    def test_materialization_binds_exact_commit_and_tree(self) -> None:
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
            (source / "a").write_text("a")
            subprocess.run(("git", "add", "a"), cwd=source, check=True)
            subprocess.run(
                ("git", "commit", "-m", "a"),
                cwd=source,
                check=True,
                capture_output=True,
            )
            sha = subprocess.run(
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
            self.assertEqual(
                materialize_git(
                    source=source,
                    commit=sha,
                    expected_tree=tree,
                    destination=root / "trial",
                ),
                sha,
            )

    def test_partial_receipt_remains_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = _spec()
            root = Path(tmp)
            trial = root / spec.id
            trial.mkdir()
            (trial / "result.json").write_text("{}\n")
            self.assertEqual(pending([spec], root), [spec])

    def test_verified_receipt_is_not_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = _spec()
            root = Path(tmp)
            write_receipt(root / spec.id, {"status": "PASS"})
            self.assertEqual(pending([spec], root), [])

    def test_event_stream_seals_identity_and_rejects_late_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            append_event(path, trial_id="trial", sequence=0, kind="started", payload={})
            evidence = seal_events(path, trial_id="trial")
            self.assertEqual(evidence["event_count"], 1)
            with self.assertRaises(EventStreamError):
                append_event(
                    path,
                    trial_id="trial",
                    sequence=1,
                    kind="late",
                    payload={},
                )


if __name__ == "__main__":
    unittest.main()
