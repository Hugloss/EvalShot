"""Concrete Enola architecture-intelligence adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from benchmarks.adapters.runtime import observe_executable, resolve_native_executable
from benchmarks.harness.model import (
    McpExposure,
    Observation,
    ParticipantIdentity,
    TrialContext,
)
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


@dataclass(frozen=True)
class EnolaSubject:
    timeout_seconds: int = 180

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            "enola",
            "repository_intelligence",
            "runtime-observed",
            {"surface": "cli+mcp", "output": "explicit-repository-derived"},
        )

    def _executable(self, context: TrialContext) -> str:
        resolved = resolve_native_executable(context, "enola")
        if resolved is None:
            raise ValueError("enola is not available on the native PATH")
        return resolved

    def _config(self, context: TrialContext) -> Path:
        path = context.control_root / "enola-benchmark.yaml"
        if not path.exists():
            payload = {
                "repo": str(context.workspace),
                "output": {"dir": ".benchmark-enola"},
            }
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def _run(self, context: TrialContext, argv: tuple[str, ...]):
        return run_bounded(
            repository_root=context.workspace,
            argv=argv,
            environment=context.environment,
            limits=ProcessLimits(
                timeout_seconds=self.timeout_seconds,
                max_stdout_bytes=20_000_000,
                max_stderr_bytes=5_000_000,
            ),
            inherit_environment=False,
        )

    def prepare(self, context: TrialContext) -> Observation:
        try:
            command = self._executable(context)
        except ValueError as exc:
            return Observation({"available": False, "reason": str(exc)}, "")
        executable = observe_executable(context, command)
        if not executable.payload["available"]:
            return executable
        result = self._run(
            context,
            (command, "--generate", str(self._config(context))),
        )
        available = (
            not result.executable_missing
            and not result.timed_out
            and result.return_code == 0
            and not result.stdout_truncated
            and not result.stderr_truncated
        )
        return Observation(
            {
                "available": available,
                "observed_identity": {
                    "version": executable.payload["version"],
                    "executable_sha256": executable.payload["executable_sha256"],
                },
                "generate": result.metrics(),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
            },
            result.stdout.decode("utf-8", errors="replace"),
            {"duration_ms": result.elapsed_ms},
        )

    def query(self, context: TrialContext, prompt: str) -> Observation:
        result = self._run(
            context,
            (self._executable(context), "--explain", str(context.workspace)),
        )
        return Observation(
            {
                "available": not result.executable_missing,
                "invoked": True,
                "query_semantics": "architecture-explain",
                "prompt": prompt,
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
        result = self._run(
            context,
            (self._executable(context), "--generate", str(self._config(context))),
        )
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
        command = self._executable(context)
        return McpExposure(
            name="enola",
            command=command,
            args=(str(self._config(context)),),
            cwd=context.workspace,
            semantic_identity={
                "name": "enola",
                "transport": "stdio",
                "config": "explicit-trial-repo",
                "output": ".benchmark-enola",
            },
        )

    def generated_globs(self) -> tuple[str, ...]:
        return (".benchmark-enola/**",)
