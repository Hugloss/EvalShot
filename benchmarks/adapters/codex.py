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

from benchmarks.adapters.codex_native import (
    copy_native_auth,
    native_cli_argv,
    observe_native_config,
    snapshot_native_inputs,
    validate_native_launch,
    validate_private_state,
    verify_native_inputs,
)
from benchmarks.adapters.codex_native_namespace import probe_native_namespace, verify_native_namespace
from benchmarks.adapters.codex_native_plugins import (
    discover_native_plugin_ids,
    observe_native_plugins,
    observe_native_plugin_selection,
    prepare_native_plugin_inputs,
    verify_native_plugin_inputs,
)
from benchmarks.adapters.runtime import _sha256_file, observe_executable, resolve_native_executable
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
        **{
            key: value if type(value) is int and value >= 0 else None
            for key in ("input_tokens", "cached_input_tokens", "output_tokens")
            for value in (usage.get(key),)
        },
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


def _native_environment_sha256(context: TrialContext) -> str:
    return hashlib.sha256(json.dumps(
        context.environment, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _validate_native_exposure(
    context: TrialContext,
    exposure: McpExposure | None,
) -> None:
    if exposure is None:
        return
    if not re.fullmatch(r"[A-Za-z0-9_-]+", exposure.name):
        raise ValueError("benchmark MCP server name is not canonical")
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
    diagnostic_required_tool: str | None = None
    controlled_subject: str | None = None

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
                "diagnostic_required_tool": self.diagnostic_required_tool,
                "controlled_subject": self.controlled_subject,
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
        if self.controlled_subject is not None and (
            not self.native_host or not isinstance(self.controlled_subject, str)
            or re.fullmatch(r"[A-Za-z0-9_-]+", self.controlled_subject) is None
            or exposure is not None and exposure.name != self.controlled_subject
        ):
            return Observation({"available": False, "reason": "native controlled subject must match the canonical admitted MCP server"}, "")
        if self.diagnostic_required_tool is not None and (
            not self.native_host or exposure is None
            or not self.diagnostic_required_tool.startswith(exposure.name + "_")
            or len(self.diagnostic_required_tool) <= len(exposure.name) + 1
        ):
            return Observation({"available": False, "reason": "diagnostic required tool must belong to the selected native subject"}, "")
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
            validate_private_state(context)
            command = self._executable(context)
        except ValueError as exc:
            return Observation({"available": False, "reason": str(exc)}, "")
        observed = observe_executable(context, command)
        selection = {"state": "unknown", "model": None, "provider": None, "scope": None}
        inputs = None
        plugin_inputs = None
        plugins = None
        namespace = None
        selection_observation = None
        source_selection_observation = None
        config_identity = None
        try:
            source_home = context.environment.get("BENCHMARK_NATIVE_CODEX_HOME")
            if not source_home:
                raise ValueError("native Codex input source is not selected")
            validate_native_launch(context, command, self.timeout_seconds)
            inputs = snapshot_native_inputs(context, Path(source_home), copy_auth=False)
            plugin_inputs = prepare_native_plugin_inputs(context, Path(source_home))
            inputs["auth_file_copied"] = copy_native_auth(context, Path(source_home))
            if plugin_inputs["configured_ids"] or self.controlled_subject is not None:
                context.environment["BENCHMARK_NATIVE_CODEX_EXECUTABLE"] = command
                namespace = probe_native_namespace(context, Path(source_home))
                context.environment["BENCHMARK_NATIVE_NAMESPACE"] = "1"
                discovered = discover_native_plugin_ids(context, command, Path(source_home))
                plugin_inputs = prepare_native_plugin_inputs(context, Path(source_home), discovered_ids=discovered)
                source_selection_observation = observe_native_plugin_selection(context, command, Path(source_home), reference=True)
                selection_observation = observe_native_plugin_selection(context, command, Path(source_home))
            config_identity = hashlib.sha256(json.dumps({
                "configuration": inputs["identity"], "plugins": plugin_inputs["identity"],
            }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            selection = observe_native_config(context, command, self.timeout_seconds)
            selection["source_comparison"] = "unknown"
            model = selection.get("model")
            # Doctor does not expose resolved effort. Keep the native bytes,
            # without promoting a root-file value over profiles/policy.
            effort = None
            result = run_bounded(
                repository_root=context.workspace,
                argv=native_cli_argv(context, (command, "--no-daemon", "mcp", "list", "--json")),
                environment=context.environment,
                limits=ProcessLimits(
                    timeout_seconds=min(30.0, float(self.timeout_seconds)),
                    max_stdout_bytes=1_000_000,
                ),
                inherit_environment=False,
            )
            if result.return_code != 0 or result.timed_out or result.executable_missing or result.stdout_truncated or result.stderr_truncated:
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
            if self.controlled_subject is not None and exposure is not None and exposure.name != self.controlled_subject:
                raise ValueError("benchmark exposure differs from the controlled native subject")
            controlled = self.controlled_subject or selected
            disabled_names = (controlled,) if controlled in names else ()
            overrides = self._native_mcp_overrides(disabled_names, exposure)
            if namespace is not None:
                plugins = observe_native_plugins(
                    context, command, Path(source_home), overrides,
                    controlled_subject=controlled, exposure=exposure,
                    selection_observation=selection_observation,
                    source_selection_observation=source_selection_observation,
                )
                if not verify_native_namespace(namespace) or not verify_native_plugin_inputs(context, plugin_inputs):
                    raise ValueError("native Codex plugin inputs changed during admission")
            exposure_sha256 = _exposure_sha256(exposure)
            (context.control_root / "codex-native-servers.json").write_text(
                json.dumps(
                    {
                        "names": disabled_names,
                        "config_sha256": config_identity,
                        "inputs": inputs,
                        "plugin_inputs": plugin_inputs,
                        "namespace": namespace,
                        "environment_sha256": _native_environment_sha256(context),
                        "executable_path": observed.payload.get("resolved_path"),
                        "executable_sha256": observed.payload.get("executable_sha256"),
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
            model, effort = selection.get("model"), None
            names = []
            reason = str(exc)
            available = False
        payload = dict(observed.payload)
        payload.update(
            {
                "available": available,
                "reason": reason,
                "auth_mode": "native-host",
                "model": model,
                "provider": selection.get("provider"),
                "reasoning_effort": effort,
                "native_config_sha256": config_identity,
                "native_config_observation": selection,
                "native_state_isolation": "private-input-snapshot" if inputs else "unavailable",
                "native_plugins_observation": plugins,
                "native_namespace_observation": namespace,
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
                    "provider": selection.get("provider"),
                    "reasoning_effort": effort,
                    "native_config_sha256": config_identity,
                    "native_config_observation": selection,
                    "native_state_isolation": "private-input-snapshot" if inputs else "unavailable",
                    "native_plugins_observation": plugins,
                    "native_namespace_observation": namespace,
                    "subject_exposure_sha256": _exposure_sha256(exposure),
                },
            }
        )
        return Observation(payload, observed.raw, observed.measurements)

    def _native_mcp_overrides(
        self, names: tuple[str, ...], exposure: McpExposure | None,
    ) -> tuple[str, ...]:
        argv: list[str] = []
        controlled = self.controlled_subject or (exposure.name if exposure else None)
        for name in names:
            if name == controlled and exposure is None:
                argv.extend(("-c", f"mcp_servers.{name}.enabled=false"))
        if exposure is not None:
            fields = [
                f"command={_toml_string(exposure.command)}",
                "args=" + json.dumps(list(exposure.args), ensure_ascii=False),
                f"cwd={_toml_string(str(exposure.cwd.resolve()))}",
                "enabled=true",
                "env={" + ",".join(
                    f"{_toml_string(key)}={_toml_string(value)}"
                    for key, value in sorted(exposure.environment.items())
                ) + "}",
            ]
            argv.extend(("-c", f"mcp_servers.{exposure.name}={{" + ",".join(fields) + "}"))
        return tuple(argv)

    def _exec_argv(
        self,
        context: TrialContext,
        prompt: str,
        names: tuple[str, ...] = (),
        exposure: McpExposure | None = None,
    ) -> tuple[str, ...]:
        argv = [self._executable(context)]
        if self.native_host:
            argv.append("--no-daemon")
        argv.extend(("exec", "--json"))
        if self.native_host:
            argv.extend(
                ("--sandbox", "workspace-write", "--approve-for-me", "--ephemeral")
            )
            argv.extend(self._native_mcp_overrides(names, exposure))
        else:
            argv.append("--full-auto")
        if self.model:
            argv.extend(("--model", self.model))
        argv.append(prompt)
        return native_cli_argv(context, tuple(argv)) if self.native_host else tuple(argv)

    def run(
        self,
        context: TrialContext,
        prompt: str,
        exposed_subject: SubjectAdapter | None,
    ) -> Observation:
        exposure = self._exposure(context, exposed_subject)
        if self.diagnostic_required_tool is not None:
            if exposure is None or not self.diagnostic_required_tool.startswith(exposure.name + "_") or len(self.diagnostic_required_tool) <= len(exposure.name) + 1:
                raise ValueError("diagnostic required tool must belong to the selected subject")
            prompt = (
                f"Diagnostic tool requirement: call {self.diagnostic_required_tool} "
                "for this repository task before using file search. Inspect source "
                "evidence before answering. The tool call is required even if you "
                "believe you already know the answer.\n\n" + prompt
            )
        names: tuple[str, ...] = ()
        if self.native_host:
            try:
                validate_private_state(context)
            except ValueError:
                return Observation({"terminal_event": None, "reason": "native Codex state roots changed during trial"}, "")
            prepared = json.loads(
                (context.control_root / "codex-native-servers.json").read_text()
            )
            if _native_environment_sha256(context) != prepared.get("environment_sha256"):
                return Observation({"terminal_event": None, "reason": "native Codex environment changed during trial"}, "")
            if not verify_native_inputs(context, prepared.get("inputs", {})):
                return Observation(
                    {
                        "terminal_event": None,
                        "reason": "native Codex config changed during trial",
                    },
                    "",
                )
            if not verify_native_plugin_inputs(context, prepared.get("plugin_inputs", {})):
                return Observation({"terminal_event": None, "reason": "native Codex plugin inputs changed during trial"}, "")
            if prepared.get("namespace") is not None and not verify_native_namespace(prepared["namespace"]):
                return Observation({"terminal_event": None, "reason": "native Codex namespace changed during trial"}, "")
            try:
                executable = self._executable(context)
                executable_matches = executable == prepared.get("executable_path") and _sha256_file(Path(executable)) == prepared.get("executable_sha256")
            except (OSError, ValueError):
                executable_matches = False
            if not executable_matches:
                return Observation({"terminal_event": None, "reason": "native Codex executable changed during trial"}, "")
            if _exposure_sha256(exposure) != prepared.get("subject_exposure_sha256"):
                return Observation(
                    {
                        "terminal_event": None,
                        "reason": "benchmark subject exposure changed during trial",
                    },
                    "",
                )
            names = tuple(prepared["names"])
        execution_environment = {
            key: value for key, value in context.environment.items()
            if not key.startswith("BENCHMARK_NATIVE_")
        } if self.native_host else context.environment
        result = run_bounded(
            repository_root=context.workspace,
            argv=self._exec_argv(context, prompt, names, exposure),
            environment=execution_environment,
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
        authority_error = None
        if self.native_host:
            if not verify_native_inputs(context, prepared.get("inputs", {})):
                authority_error = "native Codex config changed during trial"
            if not verify_native_plugin_inputs(context, prepared.get("plugin_inputs", {})):
                authority_error = "native Codex plugin inputs changed during trial"
            if prepared.get("namespace") is not None and not verify_native_namespace(prepared["namespace"]):
                authority_error = "native Codex namespace changed during trial"
            try:
                executable = self._executable(context)
                if executable != prepared.get("executable_path") or _sha256_file(Path(executable)) != prepared.get("executable_sha256"):
                    authority_error = "native Codex executable changed during trial"
            except (OSError, ValueError):
                authority_error = "native Codex executable became unavailable during trial"
            if authority_error:
                terminal = None
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
            "final_message": None if authority_error else _final_message(events),
            "reason": authority_error,
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
