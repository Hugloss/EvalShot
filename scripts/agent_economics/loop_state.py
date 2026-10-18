from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


class LoopStateError(ValueError):
    pass


@dataclass(frozen=True)
class LoopBudget:
    max_iterations: int = 8
    max_commands: int = 24
    max_wall_seconds: float = 1800.0
    max_stdout_bytes: int = 8_000_000
    max_stderr_bytes: int = 8_000_000
    max_evidence_files: int = 256
    max_evidence_lines: int = 20_000
    max_context_tokens: int = 100_000

    def __post_init__(self) -> None:
        values = (
            self.max_iterations,
            self.max_commands,
            self.max_stdout_bytes,
            self.max_stderr_bytes,
            self.max_evidence_files,
            self.max_evidence_lines,
            self.max_context_tokens,
        )
        if min(values) < 1 or self.max_wall_seconds <= 0:
            raise LoopStateError("loop budgets must be positive")


def semantic_identity(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
    )


class LoopSession:
    def __init__(
        self,
        path: Path,
        *,
        budget: LoopBudget = LoopBudget(),
        repository_root: Path | None = None,
    ) -> None:
        self.path = path.resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.budget = budget
        self._lock_fd: int | None = None
        if repository_root is not None:
            root = repository_root.resolve()
            try:
                self.path.relative_to(root)
            except ValueError as exc:
                raise LoopStateError(
                    "loop state must stay inside repository root"
                ) from exc

    def __enter__(self) -> "LoopSession":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fd = os.open(
                self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError as exc:
            raise LoopStateError(
                "loop state is already owned by another writer"
            ) from exc
        os.write(self._lock_fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *_args: object) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass

    def load(self) -> dict[str, object]:
        if not self.path.exists():
            return {
                "version": 2,
                "iterations": [],
                "cumulative_commands": 0,
                "cumulative_stdout_bytes": 0,
                "cumulative_stderr_bytes": 0,
                "cumulative_elapsed_ms": 0.0,
                "cumulative_evidence_files": 0,
                "cumulative_evidence_lines": 0,
                "cumulative_context_tokens": 0,
                "previous_stage": None,
            }
        if self.path.stat().st_size > 2_000_000:
            raise LoopStateError("loop state exceeds 2,000,000 byte bound")
        data = json.loads(self.path.read_bytes())
        if not isinstance(data, dict) or data.get("version") != 2:
            raise LoopStateError("invalid loop state")
        return data

    def _atomic_write(self, state: dict[str, object]) -> None:
        payload = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
        fd, name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def record(
        self,
        *,
        source_identity: str,
        command_identity: str,
        failure_identity: str,
        evidence_identity: str,
        stdout_bytes: int,
        stderr_bytes: int,
        elapsed_ms: float = 0.0,
        stage: str | None = None,
        evidence_files: int = 0,
        evidence_lines: int = 0,
        context_tokens: int = 0,
    ) -> dict[str, object]:
        state = self.load()
        iterations = state.setdefault("iterations", [])
        assert isinstance(iterations, list)
        progress_signature = semantic_identity(
            {"source_identity": source_identity, "failure_identity": failure_identity}
        )
        evidence_signature = semantic_identity(
            {"source_identity": source_identity, "evidence_identity": evidence_identity}
        )
        prior_progress = [
            x.get("progress_signature") for x in iterations if isinstance(x, dict)
        ]
        prior_evidence = [
            x.get("evidence_signature") for x in iterations if isinstance(x, dict)
        ]
        stop_reason: str | None = None
        if prior_progress and prior_progress[-1] == progress_signature:
            stop_reason = "NO_PROGRESS"
        elif prior_evidence and prior_evidence[-1] == evidence_signature:
            stop_reason = "NO_NEW_EVIDENCE"
        elif len(prior_progress) >= 2 and prior_progress[-2] == progress_signature:
            stop_reason = "OSCILLATION"
        iterations.append(
            {
                "iteration": len(iterations) + 1,
                "source_identity": source_identity,
                "command_identity": command_identity,
                "failure_identity": failure_identity,
                "evidence_identity": evidence_identity,
                "progress_signature": progress_signature,
                "evidence_signature": evidence_signature,
                "stage": stage,
            }
        )
        increments = {
            "cumulative_commands": 1,
            "cumulative_stdout_bytes": stdout_bytes,
            "cumulative_stderr_bytes": stderr_bytes,
            "cumulative_elapsed_ms": elapsed_ms,
            "cumulative_evidence_files": evidence_files,
            "cumulative_evidence_lines": evidence_lines,
            "cumulative_context_tokens": context_tokens,
        }
        for key, value in increments.items():
            state[key] = state.get(key, 0) + value
        state["previous_stage"] = stage
        limits = [
            (
                "cumulative_commands",
                self.budget.max_commands,
                "COMMAND_BUDGET_EXHAUSTED",
            ),
            (
                "cumulative_stdout_bytes",
                self.budget.max_stdout_bytes,
                "STDOUT_BUDGET_EXHAUSTED",
            ),
            (
                "cumulative_stderr_bytes",
                self.budget.max_stderr_bytes,
                "STDERR_BUDGET_EXHAUSTED",
            ),
            (
                "cumulative_elapsed_ms",
                self.budget.max_wall_seconds * 1000,
                "WALL_TIME_BUDGET_EXHAUSTED",
            ),
            (
                "cumulative_evidence_files",
                self.budget.max_evidence_files,
                "EVIDENCE_FILE_BUDGET_EXHAUSTED",
            ),
            (
                "cumulative_evidence_lines",
                self.budget.max_evidence_lines,
                "EVIDENCE_LINE_BUDGET_EXHAUSTED",
            ),
            (
                "cumulative_context_tokens",
                self.budget.max_context_tokens,
                "CONTEXT_TOKEN_BUDGET_EXHAUSTED",
            ),
        ]
        if len(iterations) >= self.budget.max_iterations and stop_reason is None:
            stop_reason = "ITERATION_BUDGET_EXHAUSTED"
        for key, limit, reason in limits:
            if state[key] >= limit and stop_reason is None:
                stop_reason = reason
        state["stop_reason"] = stop_reason
        state["state_identity"] = semantic_identity(
            {
                "iterations": iterations,
                **{key: state[key] for key, _, _ in limits},
                "stop_reason": stop_reason,
                "previous_stage": stage,
            }
        )
        self._atomic_write(state)
        return state
