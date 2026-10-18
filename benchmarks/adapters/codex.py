"""Codex CLI benchmark agent with structured JSONL evidence."""

from __future__ import annotations

import hashlib
from collections import Counter
import json
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.adapters.runtime import observe_executable, resolve_native_executable
from benchmarks.harness.model import (
    McpExposure,
    Observation,
    ParticipantIdentity,
    SubjectAdapter,
    SubjectLifecycleMode,
    TrialContext,
)
from benchmarks.harness.tool_results import result_bytes, tool_result_evidence
from benchmarks.tool_routing import subject_routing_timing
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_config(
    model: str | None,
    exposure: McpExposure | None,
    *,
    reasoning_effort: str | None = None,
) -> str:
    lines: list[str] = []
    if model:
        lines.append(f"model = {_toml_string(model)}")
    if reasoning_effort:
        lines.append(f"model_reasoning_effort = {_toml_string(reasoning_effort)}")
    if exposure is not None:
        lines.extend(
            [
                "",
                f"[mcp_servers.{exposure.name}]",
                f"command = {_toml_string(exposure.command)}",
                "args = ["
                + ", ".join(_toml_string(value) for value in exposure.args)
                + "]",
                f"cwd = {_toml_string(str(exposure.cwd))}",
                "enabled = true",
            ]
        )
        if exposure.environment:
            lines.append(f"[mcp_servers.{exposure.name}.env]")
            for key, value in sorted(exposure.environment.items()):
                lines.append(f"{key} = {_toml_string(value)}")
    return "\n".join(lines).strip() + "\n"


def parse_codex_jsonl(raw: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_no}: {exc.msg}")
            continue
        if not isinstance(value, dict):
            errors.append(f"line {line_no}: event is not an object")
            continue
        events.append(value)
    return events, errors


def _completed_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event.get("item", {})
        for event in events
        if event.get("type") == "item.completed" and isinstance(event.get("item"), dict)
    ]


def _metrics(
    events: list[dict[str, Any]],
    *,
    subject_server: str | None,
) -> dict[str, Any]:
    items = _completed_items(events)
    commands = [item for item in items if item.get("type") == "command_execution"]
    changes = [item for item in items if item.get("type") == "file_change"]
    mcp_calls = [item for item in items if item.get("type") == "mcp_tool_call"]
    subject_calls = [
        item
        for item in mcp_calls
        if subject_server and item.get("server") == subject_server
    ]
    tool_sequence: list[str] = []
    subject_ordinals: list[int] = []
    for item in items:
        item_type = item.get("type")
        if item_type == "command_execution":
            name = "command_execution"
        elif item_type == "file_change":
            name = "file_change"
        elif item_type == "mcp_tool_call":
            server = item.get("server")
            tool = item.get("tool")
            name = (
                f"mcp:{server}/{tool}"
                if isinstance(server, str)
                and server
                and isinstance(tool, str)
                and tool
                else "mcp_tool_call"
            )
        else:
            continue
        tool_sequence.append(name)
        if (
            item_type == "mcp_tool_call"
            and subject_server
            and item.get("server") == subject_server
        ):
            subject_ordinals.append(len(tool_sequence))
    subject_tool_names = sorted(
        {
            str(item.get("tool"))
            for item in subject_calls
            if isinstance(item.get("tool"), str) and item.get("tool")
        }
    )
    subject_tool_result_evidence = [
        tool_result_evidence(
            operation=str(item["tool"]),
            status=item.get("status"),
            result_present="result" in item,
            result=item.get("result"),
            error=item.get("error"),
            basis="codex-item-completed",
        )
        for item in subject_calls
        if isinstance(item.get("tool"), str) and item.get("tool")
    ]
    completed = [event for event in events if event.get("type") == "turn.completed"]
    usage = completed[-1].get("usage", {}) if completed else {}
    if not isinstance(usage, dict):
        usage = {}
    mcp_result_bytes = 0
    for item in mcp_calls:
        measured = result_bytes(item.get("result")) if "result" in item else None
        if measured is not None:
            mcp_result_bytes += measured
    subject_invoked = bool(subject_calls)
    return {
        "event_count": len(events),
        "command_calls": len(commands),
        "file_change_events": len(changes),
        "tool_calls": len(commands) + len(changes) + len(mcp_calls),
        "mcp_calls": len(mcp_calls),
        "subject_mcp_calls": len(subject_calls),
        "subject_tool_configured": subject_server is not None,
        "subject_tool_invoked": subject_invoked,
        "subject_tool_names": subject_tool_names,
        "subject_tool_observability": "complete",
        "subject_tool_result_evidence": subject_tool_result_evidence,
        "mcp_result_bytes": mcp_result_bytes,
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "cached_input_tokens": int(usage.get("cached_input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "source_read_observability": ("not-authoritatively-exposed-by-codex-jsonl"),
        "tool_strategy_observability": "codex-item-completed",
        "tool_name_counts": dict(sorted(Counter(tool_sequence).items())),
        "tool_sequence": tool_sequence,
        "subject_tool_call_ordinals": subject_ordinals,
        "subject_first_tool_call_ordinal": (
            subject_ordinals[0] if subject_ordinals else None
        ),
        "subject_routing_timing": subject_routing_timing(
            tool_sequence,
            subject_ordinals,
            configured=subject_server is not None,
            invocation_observed=subject_invoked,
            order_complete=True,
        ),
    }


_REMOTE_AUTH_VARIABLES = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "CODEX_ACCESS_TOKEN",
)


def seed_codex_auth(
    context: TrialContext,
    source: Path | None,
) -> str:
    codex_home = Path(context.environment["CODEX_HOME"])
    codex_home.mkdir(parents=True, exist_ok=True)
    target = codex_home / "auth.json"
    if source is not None:
        source = source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Codex auth seed does not exist: {source}")
        shutil.copyfile(source, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        for name in _REMOTE_AUTH_VARIABLES:
            context.environment[name] = ""
        mode = "seeded-auth-file"
    elif any(context.environment.get(name) for name in _REMOTE_AUTH_VARIABLES):
        mode = "explicit-auth-environment"
    else:
        mode = "none"
    context.environment["BENCHMARK_CODEX_AUTH_MODE"] = mode
    return mode


def _final_message(events: list[dict[str, Any]]) -> str | None:
    terminals = [
        index
        for index, event in enumerate(events)
        if event.get("type") in {"turn.completed", "turn.failed", "error"}
    ]
    if not terminals or events[terminals[-1]].get("type") != "turn.completed":
        return None
    if any(
        event.get("type") == "item.completed" for event in events[terminals[-1] + 1 :]
    ):
        return None
    for event in reversed(events[: terminals[-1]]):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            return None
        if item.get("type") != "agent_message":
            return None
        value = item.get("text")
        return value if isinstance(value, str) and value.strip() else None
    return None


def _exposure_payload(exposure: McpExposure | None) -> dict[str, Any] | None:
    if exposure is None:
        return None
    return {
        "name": exposure.name,
        "command": exposure.command,
        "args": list(exposure.args),
        "cwd": str(exposure.cwd.resolve()),
        "environment": dict(sorted(exposure.environment.items())),
        "semantic_identity": dict(exposure.semantic_identity),
    }


def _exposure_sha256(exposure: McpExposure | None) -> str | None:
    payload = _exposure_payload(exposure)
    if payload is None:
        return None
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _validate_native_exposure(
    context: TrialContext,
    exposure: McpExposure | None,
) -> None:
    if exposure is None:
        return
    executable = Path(exposure.command).expanduser().resolve()
    if not executable.is_file():
        raise ValueError(f"benchmark MCP executable does not exist: {executable}")
    if exposure.cwd.resolve() != context.workspace.resolve():
        raise ValueError(f"benchmark MCP {exposure.name} runs outside trial workspace")


@dataclass(frozen=True)
class CodexAgent:
    model: str | None = None
    reasoning_effort: str | None = None
    native_host: bool = False
    timeout_seconds: int = 600
    max_output_bytes: int = 50_000_000
    max_tool_calls: int | None = None

    def subject_lifecycle_mode(self) -> SubjectLifecycleMode:
        return SubjectLifecycleMode.ADAPTER

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            "codex",
            "coding_agent",
            "runtime-observed",
            {
                "model": self.model or "host-default",
                "reasoning_effort": self.reasoning_effort,
                "surface": "codex-exec-json",
                "native_host": self.native_host,
            },
        )

    def _exposure(
        self,
        context: TrialContext,
        subject: SubjectAdapter | None,
    ) -> McpExposure | None:
        if subject is None:
            return None
        return subject.mcp_exposure(context)

    def _executable(self, context: TrialContext) -> str:
        resolved = resolve_native_executable(context, "codex")
        if resolved is None:
            raise ValueError("codex is not available on the native PATH")
        return resolved

    def _config_path(self, context: TrialContext) -> Path:
        return Path(context.environment["CODEX_HOME"]) / "config.toml"

    def prepare(
        self,
        context: TrialContext,
        exposed_subject: SubjectAdapter | None,
    ) -> Observation:
        exposure = self._exposure(context, exposed_subject)
        if self.native_host:
            return self._prepare_native(context, exposure)
        config = _render_config(
            self.model,
            exposure,
            reasoning_effort=self.reasoning_effort,
        )
        config_path = self._config_path(context)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config, encoding="utf-8")

        try:
            command = self._executable(context)
        except ValueError as exc:
            return Observation({"available": False, "reason": str(exc)}, "")
        observed = observe_executable(context, command)
        payload = dict(observed.payload)
        payload.update(
            {
                "config_sha256": hashlib.sha256(config.encode()).hexdigest(),
                "mcp_exposure": (exposure.semantic_identity if exposure else None),
                "auth_mode": context.environment.get(
                    "BENCHMARK_CODEX_AUTH_MODE",
                    "none",
                ),
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
            }
        )
        return Observation(
            payload,
            observed.raw,
            observed.measurements,
        )

    def _prepare_native(
        self, context: TrialContext, exposure: McpExposure | None
    ) -> Observation:
        try:
            command = self._executable(context)
        except ValueError as exc:
            return Observation({"available": False, "reason": str(exc)}, "")
        observed = observe_executable(context, command)
        config_path = self._config_path(context)
        try:
            raw = config_path.read_bytes()
            config = tomllib.loads(raw.decode("utf-8"))
            model = config.get("model")
            effort = config.get("model_reasoning_effort")
            if not isinstance(model, str) or not model:
                raise ValueError(
                    "native Codex config needs an explicit model for comparison"
                )
            result = run_bounded(
                repository_root=context.workspace,
                argv=(command, "mcp", "list", "--json"),
                environment=context.environment,
                limits=ProcessLimits(
                    timeout_seconds=min(30.0, float(self.timeout_seconds)),
                    max_stdout_bytes=1_000_000,
                ),
                inherit_environment=False,
            )
            if result.return_code != 0 or result.timed_out or result.stdout_truncated:
                raise ValueError("native Codex MCP list did not complete")
            servers = json.loads(result.stdout)
            if not isinstance(servers, list):
                raise ValueError("native Codex MCP list was not an array")
            if any(not isinstance(row, dict) for row in servers):
                raise ValueError("native Codex MCP list contains a malformed server")
            names = [row.get("name") for row in servers]
            if any(
                not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name)
                for name in names
            ) or len(names) != len(set(names)):
                raise ValueError("native Codex MCP names are invalid or duplicated")
            selected = exposure.name if exposure else None
            _validate_native_exposure(context, exposure)
            exposure_sha256 = _exposure_sha256(exposure)
            (context.control_root / "codex-native-servers.json").write_text(
                json.dumps(
                    {
                        "names": names,
                        "config_sha256": hashlib.sha256(raw).hexdigest(),
                        "selected_subject": selected,
                        "subject_exposure_sha256": exposure_sha256,
                    },
                    sort_keys=True,
                )
            )
            reason = None
            available = bool(observed.payload.get("available"))
        except (
            OSError,
            UnicodeError,
            tomllib.TOMLDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            model = effort = None
            names = []
            reason = str(exc)
            available = False
            raw = b""
        payload = dict(observed.payload)
        payload.update(
            {
                "available": available,
                "reason": reason,
                "auth_mode": "native-host",
                "model": model,
                "reasoning_effort": effort,
                "native_config_sha256": hashlib.sha256(raw).hexdigest()
                if raw
                else None,
                "native_mcp_servers": sorted(names),
                "mcp_exposure": (
                    {
                        "source": "benchmark-subject-exposure",
                        "subject": exposure.name,
                        "sha256": _exposure_sha256(exposure),
                        "semantic_identity": exposure.semantic_identity,
                    }
                    if exposure
                    else None
                ),
                "observed_identity": {
                    "version": observed.payload.get("version"),
                    "executable_sha256": observed.payload.get("executable_sha256"),
                    "model": model,
                    "reasoning_effort": effort,
                    "native_config_sha256": hashlib.sha256(raw).hexdigest()
                    if raw
                    else None,
                    "subject_exposure_sha256": _exposure_sha256(exposure),
                },
            }
        )
        return Observation(payload, observed.raw, observed.measurements)

    def _exec_argv(
        self,
        context: TrialContext,
        prompt: str,
        names: tuple[str, ...] = (),
        exposure: McpExposure | None = None,
    ) -> tuple[str, ...]:
        argv = [self._executable(context), "exec", "--json"]
        if self.native_host:
            argv.extend(
                ("--sandbox", "workspace-write", "--approve-for-me", "--ephemeral")
            )
            for name in names:
                argv.extend(("-c", f"mcp_servers.{name}.enabled=false"))
            if exposure is not None:
                prefix = f"mcp_servers.{exposure.name}"
                argv.extend(
                    (
                        "-c",
                        f"{prefix}.command={_toml_string(exposure.command)}",
                        "-c",
                        f"{prefix}.args="
                        + json.dumps(list(exposure.args), ensure_ascii=False),
                        "-c",
                        f"{prefix}.cwd={_toml_string(str(exposure.cwd.resolve()))}",
                        "-c",
                        f"{prefix}.enabled=true",
                    )
                )
                for key, value in sorted(exposure.environment.items()):
                    argv.extend(
                        (
                            "-c",
                            f"{prefix}.env.{key}={_toml_string(value)}",
                        )
                    )
        else:
            argv.append("--full-auto")
        if self.model:
            argv.extend(("--model", self.model))
        argv.append(prompt)
        return tuple(argv)

    def run(
        self,
        context: TrialContext,
        prompt: str,
        exposed_subject: SubjectAdapter | None,
    ) -> Observation:
        exposure = self._exposure(context, exposed_subject)
        names: tuple[str, ...] = ()
        if self.native_host:
            prepared = json.loads(
                (context.control_root / "codex-native-servers.json").read_text()
            )
            if (
                hashlib.sha256(self._config_path(context).read_bytes()).hexdigest()
                != prepared["config_sha256"]
            ):
                return Observation(
                    {
                        "terminal_event": None,
                        "reason": "native Codex config changed during trial",
                    },
                    "",
                )
            if _exposure_sha256(exposure) != prepared.get("subject_exposure_sha256"):
                return Observation(
                    {
                        "terminal_event": None,
                        "reason": "benchmark subject exposure changed during trial",
                    },
                    "",
                )
            names = tuple(prepared["names"])
        result = run_bounded(
            repository_root=context.workspace,
            argv=self._exec_argv(context, prompt, names, exposure),
            environment=context.environment,
            limits=ProcessLimits(
                timeout_seconds=self.timeout_seconds,
                max_stdout_bytes=self.max_output_bytes,
                max_stderr_bytes=5_000_000,
            ),
            inherit_environment=False,
        )
        events, parse_errors = parse_codex_jsonl(result.stdout)
        terminal = next(
            (
                event
                for event in reversed(events)
                if event.get("type") in {"turn.completed", "turn.failed", "error"}
            ),
            None,
        )
        subject_server = exposure.name if exposure else None
        metrics = _metrics(events, subject_server=subject_server)
        metrics["duration_ms"] = result.elapsed_ms
        metrics["stdout_bytes"] = len(result.stdout)
        metrics["stderr_bytes"] = len(result.stderr)
        tool_calls = int(metrics["tool_calls"])
        payload = {
            "available": not result.executable_missing,
            "terminal_event": terminal,
            "terminal_complete": bool(
                terminal and terminal.get("type") in {"turn.completed", "turn.failed"}
            ),
            "final_message": _final_message(events),
            "jsonl_parse_errors": parse_errors,
            "subject_server": subject_server,
            "tool_available": exposure is not None,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "budget_violation": (
                (f"tool calls {tool_calls} exceed max_tool_calls {self.max_tool_calls}")
                if self.max_tool_calls is not None and tool_calls > self.max_tool_calls
                else (
                    f"agent output exceeded max_output_bytes {self.max_output_bytes}"
                    if result.stdout_truncated
                    else None
                )
            ),
            "process": result.metrics(),
            "stderr": result.stderr.decode(
                "utf-8",
                errors="replace",
            ),
        }
        return Observation(
            payload,
            result.stdout.decode("utf-8", errors="replace"),
            metrics,
        )
