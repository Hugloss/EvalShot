from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


_retained_lock_fd: ContextVar[int | None] = ContextVar("retained_benchmark_lock_fd", default=None)


@contextmanager
def retain_lock_in_subprocesses(fd: int):
    """Keep an execution lock held if the parent dies before its child exits."""
    token = _retained_lock_fd.set(fd)
    try:
        yield
    finally:
        _retained_lock_fd.reset(token)


class BoundedProcessError(ValueError):
    pass


class StopStdinExchange(Exception):
    """Close stdin without sending later stages; the caller interprets stdout."""


@dataclass(frozen=True)
class ProcessLimits:
    timeout_seconds: float = 30.0
    max_stdout_bytes: int = 1_000_000
    max_stderr_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise BoundedProcessError("timeout_seconds must be > 0")
        if self.max_stdout_bytes < 1 or self.max_stderr_bytes < 1:
            raise BoundedProcessError("stdout/stderr byte bounds must be >= 1")


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    cwd: str
    command_identity: str
    environment_identity: str
    environment_variables: tuple[str, ...]
    return_code: int | None
    signal: int | None
    timed_out: bool
    executable_missing: bool
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    elapsed_ms: float
    process_tree_termination: str

    def metrics(self) -> dict[str, object]:
        return {
            "command_identity": self.command_identity,
            "environment_identity": self.environment_identity,
            "environment_variables": list(self.environment_variables),
            "return_code": self.return_code,
            "signal": self.signal,
            "timed_out": self.timed_out,
            "executable_missing": self.executable_missing,
            "stdout_bytes": len(self.stdout),
            "stderr_bytes": len(self.stderr),
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "elapsed_ms": self.elapsed_ms,
            "process_tree_termination": self.process_tree_termination,
        }


def process_tree_capability() -> dict[str, object]:
    if os.name != "nt":
        return {"available": True, "mechanism": "posix-process-group"}
    taskkill = shutil.which("taskkill")
    return {
        "available": taskkill is not None,
        "mechanism": "windows-taskkill-tree" if taskkill else None,
        "reason": None if taskkill else "taskkill executable unavailable",
    }


def _environment_evidence(
    environment: Mapping[str, str],
) -> tuple[str, tuple[str, ...]]:
    semantic = {
        key: "sha256:" + hashlib.sha256(value.encode()).hexdigest()
        for key, value in sorted(environment.items())
    }
    raw = json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest(), tuple(sorted(semantic))


def _identity(
    argv: Sequence[str],
    cwd_relative: str,
    environment_identity: str,
) -> str:
    raw = json.dumps(
        {
            "argv": list(argv),
            "cwd": cwd_relative,
            "environment_identity": environment_identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def safe_cwd(repository_root: Path, cwd: Path | str = ".") -> tuple[Path, str]:
    root = repository_root.resolve()
    candidate = Path(cwd)
    resolved = (
        candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    )
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise BoundedProcessError(
            f"command cwd escapes repository root: {cwd}"
        ) from exc
    if not resolved.is_dir():
        raise BoundedProcessError(f"command cwd does not exist: {relative.as_posix()}")
    return resolved, relative.as_posix() if relative != Path(".") else "."


def _terminate_tree(process: subprocess.Popen[bytes]) -> str:
    if process.poll() is not None:
        return "already-exited"
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill is None:
            process.kill()
            return "parent-only-fallback"
        try:
            subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            return "windows-taskkill-tree"
        except (OSError, subprocess.SubprocessError):
            process.kill()
            return "parent-only-fallback"
    try:
        os.killpg(process.pid, signal.SIGKILL)
        return "posix-process-group"
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
        return "parent-only-fallback"


def run_bounded(
    *,
    repository_root: Path,
    argv: Sequence[str],
    cwd: Path | str = ".",
    limits: ProcessLimits = ProcessLimits(),
    environment: Mapping[str, str] | None = None,
    inherit_environment: bool = True,
    close_stdin: bool = False,
    stdin_bytes: bytes | None = None,
    stdin_close_when: Callable[[bytes], bool] | None = None,
    stdin_stages: Sequence[tuple[bytes, Callable[[bytes], bool]]] | None = None,
) -> ProcessResult:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise BoundedProcessError("argv must contain non-empty strings")
    if close_stdin and stdin_bytes is not None:
        raise BoundedProcessError("close_stdin and stdin_bytes are mutually exclusive")
    if stdin_bytes is not None and not isinstance(stdin_bytes, bytes):
        raise BoundedProcessError("stdin_bytes must be bytes")
    if stdin_close_when is not None and stdin_bytes is None:
        raise BoundedProcessError("stdin_close_when requires stdin_bytes")
    if stdin_stages is not None:
        if close_stdin or stdin_bytes is not None or stdin_close_when is not None:
            raise BoundedProcessError(
                "stdin_stages cannot be combined with other stdin options"
            )
        if not stdin_stages or any(
            not isinstance(payload, bytes) or not callable(ready)
            for payload, ready in stdin_stages
        ):
            raise BoundedProcessError(
                "stdin_stages requires bytes and readiness predicates"
            )
    resolved_cwd, cwd_relative = safe_cwd(repository_root, cwd)
    runtime_environment = dict(environment or {})
    if any(
        not isinstance(key, str)
        or not key
        or "=" in key
        or "\x00" in key
        or not isinstance(value, str)
        or "\x00" in value
        for key, value in runtime_environment.items()
    ):
        raise BoundedProcessError(
            "environment overrides must be valid non-NUL string pairs"
        )
    environment_identity, environment_variables = _environment_evidence(
        runtime_environment
    )
    command_identity = _identity(argv, cwd_relative, environment_identity)
    started = time.perf_counter()
    child_environment = os.environ.copy() if inherit_environment else {}
    child_environment.update(runtime_environment)
    popen_kwargs: dict[str, object] = {
        "cwd": resolved_cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
        "env": child_environment,
    }
    if close_stdin:
        popen_kwargs["stdin"] = subprocess.DEVNULL
    elif stdin_bytes is not None or stdin_stages is not None:
        popen_kwargs["stdin"] = subprocess.PIPE
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
        lock_fd = _retained_lock_fd.get()
        if lock_fd is not None:
            popen_kwargs["pass_fds"] = (lock_fd,)
    try:
        process = subprocess.Popen(list(argv), **popen_kwargs)
    except FileNotFoundError:
        return ProcessResult(
            argv=tuple(argv),
            cwd=cwd_relative,
            command_identity=command_identity,
            environment_identity=environment_identity,
            environment_variables=environment_variables,
            return_code=None,
            signal=None,
            timed_out=False,
            executable_missing=True,
            stdout=b"",
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            process_tree_termination="not-needed",
        )
    except OSError as exc:
        raise BoundedProcessError(
            f"command failed to start: {type(exc).__name__}"
        ) from exc

    stdout, stderr = bytearray(), bytearray()
    stdout_truncated, stderr_truncated = threading.Event(), threading.Event()
    exchange_ready = threading.Event()
    stage_satisfied = threading.Event()
    stage_aborted = threading.Event()
    stage_lock = threading.Lock()
    active_predicate: list[Callable[[bytes], bool] | None] = [None]
    exchange_errors: list[Exception] = []
    termination = {"value": "not-needed"}
    termination_lock = threading.Lock()

    def terminate() -> None:
        with termination_lock:
            if process.poll() is None:
                termination["value"] = _terminate_tree(process)

    def drain(
        stream: object, sink: bytearray, maximum: int, truncated: threading.Event
    ) -> None:
        watching_stdout = stream is process.stdout and (
            stdin_close_when is not None or stdin_stages is not None
        )
        reader = (
            getattr(stream, "read1", stream.read)  # type: ignore[attr-defined]
            if watching_stdout
            else stream.read  # type: ignore[attr-defined]
        )
        try:
            while True:
                chunk = reader(65536)
                if not chunk:
                    return
                with stage_lock:
                    remaining = maximum - len(sink)
                    if remaining > 0:
                        sink.extend(chunk[:remaining])
                    over_limit = len(chunk) > max(remaining, 0)
                    if watching_stdout and not over_limit:
                        try:
                            predicate = active_predicate[0]
                            if predicate is not None and predicate(bytes(sink)):
                                stage_satisfied.set()
                                exchange_ready.set()
                        except StopStdinExchange:
                            active_predicate[0] = None
                            stage_aborted.set()
                            exchange_ready.set()
                        except Exception as exc:
                            exchange_errors.append(exc)
                            exchange_ready.set()
                if over_limit:
                    truncated.set()
                    terminate()
                    return
                if exchange_errors:
                    terminate()
                    return
        finally:
            if watching_stdout:
                exchange_ready.set()

    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(
            target=drain,
            args=(process.stdout, stdout, limits.max_stdout_bytes, stdout_truncated),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=(process.stderr, stderr, limits.max_stderr_bytes, stderr_truncated),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    timed_out = threading.Event()

    def timeout_kill() -> None:
        if process.poll() is None:
            timed_out.set()
            terminate()
            exchange_ready.set()

    timer = threading.Timer(limits.timeout_seconds, timeout_kill)
    timer.daemon = True
    timer.start()
    try:
        try:
            if stdin_bytes is not None or stdin_stages is not None:
                assert process.stdin is not None
                try:
                    stages = (
                        tuple(stdin_stages)
                        if stdin_stages is not None
                        else ((stdin_bytes, stdin_close_when),)
                    )
                    for payload, predicate in stages:
                        with stage_lock:
                            active_predicate[0] = predicate
                            stage_satisfied.clear()
                            stage_aborted.clear()
                            exchange_ready.clear()
                            if predicate is not None:
                                try:
                                    if predicate(bytes(stdout)):
                                        stage_satisfied.set()
                                        exchange_ready.set()
                                except StopStdinExchange:
                                    active_predicate[0] = None
                                    stage_aborted.set()
                                    exchange_ready.set()
                                except Exception as exc:
                                    exchange_errors.append(exc)
                                    exchange_ready.set()
                        if stage_aborted.is_set() or exchange_errors:
                            break
                        process.stdin.write(payload)
                        process.stdin.flush()
                        if predicate is not None:
                            exchange_ready.wait()
                            if (
                                stage_aborted.is_set()
                                or not stage_satisfied.is_set()
                                or exchange_errors
                            ):
                                break
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
            return_code = process.wait()
        except BaseException:
            terminate()
            try:
                process.wait(timeout=5.0)
            except (OSError, subprocess.SubprocessError):
                pass
            raise
    finally:
        timer.cancel()
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        for thread in threads:
            thread.join(timeout=1.0)
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
    if exchange_errors:
        raise BoundedProcessError("stdout completion predicate failed") from exchange_errors[0]
    sig = -return_code if return_code < 0 else None
    return ProcessResult(
        argv=tuple(argv),
        cwd=cwd_relative,
        command_identity=command_identity,
        environment_identity=environment_identity,
        environment_variables=environment_variables,
        return_code=return_code,
        signal=sig,
        timed_out=timed_out.is_set(),
        executable_missing=False,
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        stdout_truncated=stdout_truncated.is_set(),
        stderr_truncated=stderr_truncated.is_set(),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        process_tree_termination=termination["value"],
    )
