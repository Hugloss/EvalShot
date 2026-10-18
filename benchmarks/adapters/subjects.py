"""Generic bounded command-backed subjects plus the bare control."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from benchmarks.harness.model import (
    McpExposure,
    Observation,
    ParticipantIdentity,
    TrialContext,
)
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


def _text(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")


def _observe(result) -> tuple[dict[str, object], dict[str, object]]:
    process = result.metrics()
    payload = {
        "exit_code": result.return_code,
        "timed_out": result.timed_out,
        "executable_missing": result.executable_missing,
        "stderr": _text(result.stderr),
        "process": process,
    }
    measurements = {
        "duration_ms": result.elapsed_ms,
        "stdout_bytes": len(result.stdout),
        "stderr_bytes": len(result.stderr),
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
    }
    return payload, measurements


@dataclass(frozen=True)
class NoneSubject:
    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity("none", "control", "1")

    def prepare(self, context: TrialContext) -> Observation:
        return Observation({"available": True, "observed": True}, "")

    def query(self, context: TrialContext, prompt: str) -> Observation:
        return Observation({"available": False, "invoked": False}, "")

    def post_change(
        self,
        context: TrialContext,
        changed_paths: tuple[str, ...],
    ) -> Observation:
        return Observation({"available": True}, "")

    def cleanup(self, context: TrialContext) -> Observation:
        return Observation({}, "")

    def mcp_exposure(self, context: TrialContext) -> McpExposure | None:
        return None

    def generated_globs(self) -> tuple[str, ...]:
        return ()


@dataclass(frozen=True)
class CommandSubject:
    participant_id: str
    version: str
    identity_argv: tuple[str, ...]
    query_argv: tuple[str, ...]
    timeout_seconds: int = 60

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            self.participant_id,
            "repository_intelligence",
            self.version,
            {"identity_argv": self.identity_argv, "query_argv": self.query_argv},
        )

    def _run(self, context: TrialContext, argv: tuple[str, ...]):
        return run_bounded(
            repository_root=context.workspace,
            argv=argv,
            environment=context.environment,
            limits=ProcessLimits(
                timeout_seconds=self.timeout_seconds,
                max_stdout_bytes=5_000_000,
                max_stderr_bytes=2_000_000,
            ),
        )

    def prepare(self, context: TrialContext) -> Observation:
        result = self._run(context, self.identity_argv)
        payload, measurements = _observe(result)
        payload.update(
            {
                "available": (
                    not result.executable_missing
                    and not result.timed_out
                    and result.return_code == 0
                    and not result.stdout_truncated
                    and not result.stderr_truncated
                ),
                "observed": True,
                "observed_identity": _text(result.stdout).strip(),
            }
        )
        return Observation(payload, _text(result.stdout), measurements)

    def query(self, context: TrialContext, prompt: str) -> Observation:
        argv = tuple(part.replace("{prompt}", prompt) for part in self.query_argv)
        result = self._run(context, argv)
        payload, measurements = _observe(result)
        payload.update({"available": not result.executable_missing, "invoked": True})
        return Observation(payload, _text(result.stdout), measurements)

    def post_change(
        self,
        context: TrialContext,
        changed_paths: tuple[str, ...],
    ) -> Observation:
        return Observation({"changed_paths": list(changed_paths)}, "")

    def cleanup(self, context: TrialContext) -> Observation:
        return Observation({}, "")

    def mcp_exposure(self, context: TrialContext) -> McpExposure | None:
        return None

    def generated_globs(self) -> tuple[str, ...]:
        return ()


def command_from_string(
    participant_id: str,
    version: str,
    *,
    identity_command: str,
    query_command: str,
    timeout_seconds: int = 60,
) -> CommandSubject:
    return CommandSubject(
        participant_id,
        version,
        tuple(shlex.split(identity_command)),
        tuple(shlex.split(query_command)),
        timeout_seconds,
    )
