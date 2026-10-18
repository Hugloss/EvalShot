"""Concrete Hashmarks repository-intelligence adapter."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from benchmarks.adapters.runtime import observe_executable
from benchmarks.harness.model import (
    McpExposure,
    Observation,
    ParticipantIdentity,
    TrialContext,
)
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


@dataclass(frozen=True)
class HashmarksSubject:
    timeout_seconds: int = 120

    def _source_root(self, context: TrialContext) -> Path | None:
        source = context.environment.get("HASHMARKS_BENCH_SOURCE")
        return Path(source).expanduser().resolve() if source else None

    def _executable(self, context: TrialContext) -> str:
        root = self._source_root(context)
        if root is None:
            raise ValueError(
                "HASHMARKS_BENCH_SOURCE is required; PATH lookup is not benchmark authority"
            )
        return str((root / ".venv" / "bin" / "hashmarks").resolve())

    def source_identity(
        self, context: TrialContext
    ) -> tuple[dict[str, object] | None, str | None]:
        root = self._source_root(context)
        if root is None:
            return None, "HASHMARKS_BENCH_SOURCE is required for scored native runs"
        expected = Path(self._executable(context))
        if not expected.is_file():
            return None, f"Hashmarks benchmark executable does not exist: {expected}"

        def git(*args: str) -> bytes | None:
            result = subprocess.run(
                ("git", "-C", str(root), *args),
                capture_output=True,
                timeout=30,
                check=False,
            )
            return result.stdout if result.returncode == 0 else None

        try:
            start_head_raw = git("rev-parse", "HEAD")
            if start_head_raw is None:
                return None, "cannot establish Hashmarks source identity"
            start_head = start_head_raw.decode().strip()

            tree_raw = git("rev-parse", f"{start_head}^{{tree}}")
            diff = git("diff", "--binary", start_head)
            untracked_raw = git("ls-files", "--others", "--exclude-standard", "-z")
            if tree_raw is None or diff is None or untracked_raw is None:
                return None, "cannot establish Hashmarks source identity"

            untracked = [path for path in untracked_raw.split(b"\0") if path]
            if diff or untracked:
                return None, "Hashmarks benchmark source must be a clean committed checkout"

            end_head_raw = git("rev-parse", "HEAD")
            if end_head_raw is None or end_head_raw.decode().strip() != start_head:
                return None, "Hashmarks benchmark source changed during identity observation"
            final_diff = git("diff", "--binary", start_head)
            final_untracked = git("ls-files", "--others", "--exclude-standard", "-z")
            if (
                final_diff is None
                or final_untracked is None
                or final_diff != diff
                or final_untracked != untracked_raw
            ):
                return None, "Hashmarks benchmark source changed during identity observation"
        except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
            return None, "cannot fingerprint Hashmarks source checkout"

        fingerprint = hashlib.sha256(diff)
        return {
            "root": str(root),
            "commit": start_head,
            "tree": tree_raw.decode().strip(),
            "working_copy_sha256": fingerprint.hexdigest(),
            "working_copy_clean": True,
        }, None

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            "hashmarks",
            "repository_intelligence",
            "runtime-observed",
            {"surface": "cli+mcp", "state": "explicit-isolated"},
        )

    def _state_dir(self, context: TrialContext) -> Path:
        path = context.control_root / "hashmarks-state"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _base(self, context: TrialContext) -> tuple[str, ...]:
        return (
            self._executable(context),
            "--workspace",
            ".",
            "--state-dir",
            str(self._state_dir(context)),
        )

    def _run(self, context: TrialContext, argv: tuple[str, ...]):
        return run_bounded(
            repository_root=context.workspace,
            argv=argv,
            environment=context.environment,
            limits=ProcessLimits(
                timeout_seconds=self.timeout_seconds,
                max_stdout_bytes=10_000_000,
                max_stderr_bytes=2_000_000,
            ),
            inherit_environment=False,
        )

    def prepare(self, context: TrialContext) -> Observation:
        try:
            command = self._executable(context)
        except ValueError as exc:
            return Observation({"available": False, "reason": str(exc)}, "")
        executable = observe_executable(
            context,
            command,
            version_args=("version",),
        )
        if not executable.payload["available"]:
            return executable
        source_identity, source_error = self.source_identity(context)
        if source_error:
            return Observation({"available": False, "reason": source_error}, "")
        sync = self._run(context, (*self._base(context), "map", "sync"))
        available = (
            not sync.executable_missing
            and not sync.timed_out
            and sync.return_code == 0
            and not sync.stdout_truncated
            and not sync.stderr_truncated
        )
        stderr = sync.stderr.decode("utf-8", errors="replace")
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
        reason = (
            None
            if available
            else (
                f"Hashmarks map sync failed (exit {sync.return_code})"
                + (f": {detail[:300]}" if detail else "")
            )
        )
        return Observation(
            {
                "available": available,
                "reason": reason,
                "observed_identity": {
                    "version": executable.payload["version"],
                    "executable_sha256": executable.payload["executable_sha256"],
                    "source": source_identity,
                },
                "sync": sync.metrics(),
                "stderr": stderr,
            },
            sync.stdout.decode("utf-8", errors="replace"),
            {"duration_ms": sync.elapsed_ms},
        )

    def query(self, context: TrialContext, prompt: str) -> Observation:
        result = self._run(context, (*self._base(context), "find", prompt))
        return Observation(
            {
                "available": not result.executable_missing,
                "invoked": True,
                "process": result.metrics(),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
            },
            result.stdout.decode("utf-8", errors="replace"),
            {"duration_ms": result.elapsed_ms, "response_bytes": len(result.stdout)},
        )

    def direct_task_evidence(self, context: TrialContext, task: str) -> Observation:
        """Native structured task evidence for model-free corpus probes."""
        result = self._run(
            context,
            (*self._base(context), "task-evidence", task, "--limit", "20"),
        )
        return Observation(
            {
                "available": not result.executable_missing,
                "invoked": True,
                "query_semantics": "task-evidence",
                "process": result.metrics(),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
            },
            result.stdout.decode("utf-8", errors="replace"),
            {"duration_ms": result.elapsed_ms, "response_bytes": len(result.stdout)},
        )

    def post_change(
        self,
        context: TrialContext,
        changed_paths: tuple[str, ...],
    ) -> Observation:
        result = self._run(context, (*self._base(context), "map", "sync"))
        return Observation(
            {
                "changed_paths": list(changed_paths),
                "process": result.metrics(),
            },
            result.stdout.decode("utf-8", errors="replace"),
            {"duration_ms": result.elapsed_ms},
        )

    def cleanup(self, context: TrialContext) -> Observation:
        return Observation({}, "")

    def mcp_exposure(self, context: TrialContext) -> McpExposure:
        return McpExposure(
            name="hashmarks",
            command=self._executable(context),
            args=(
                "--workspace",
                ".",
                "--state-dir",
                str(self._state_dir(context)),
                "mcp",
            ),
            cwd=context.workspace,
            semantic_identity={
                "name": "hashmarks",
                "transport": "stdio",
                "workspace": "trial-workspace",
                "state": "isolated-explicit-state-dir",
            },
        )

    def generated_globs(self) -> tuple[str, ...]:
        return ()
