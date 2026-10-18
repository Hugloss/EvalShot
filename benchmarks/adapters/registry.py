"""Adapter registry for frozen benchmark definitions."""

from __future__ import annotations

import sys
from typing import Any

from benchmarks.adapters.codex import CodexAgent
from benchmarks.adapters.enola import EnolaSubject
from benchmarks.adapters.hashmarks import HashmarksSubject
from benchmarks.adapters.opencode_native import OpenCodeNativeAgent
from benchmarks.adapters.oracles import (
    CommandOracle,
    ExpectedJsonOracle,
    RepositoryLocationOracle,
)
from benchmarks.adapters.subjects import NoneSubject


class AdapterConfigurationError(ValueError):
    pass


def build_subject(definition: dict[str, Any]):
    adapter = definition["adapter"]
    config = definition.get("configuration", {})
    if adapter == "none":
        return NoneSubject()
    if adapter == "hashmarks":
        return HashmarksSubject(
            timeout_seconds=int(config.get("timeout_seconds", 120)),
        )
    if adapter == "enola":
        return EnolaSubject(timeout_seconds=int(config.get("timeout_seconds", 180)))
    raise AdapterConfigurationError(f"unknown subject adapter: {adapter}")


def build_agent(definition: dict[str, Any], *, budgets: dict[str, Any]):
    adapter = definition["adapter"]
    config = definition.get("configuration", {})
    timeout_seconds = min(
        int(config.get("timeout_seconds", budgets["timeout_seconds"])),
        int(budgets["timeout_seconds"]),
    )
    max_output_bytes = int(budgets.get("max_output_bytes", 50_000_000))
    max_tool_calls = (
        int(budgets["max_tool_calls"]) if "max_tool_calls" in budgets else None
    )

    if adapter == "codex":
        model = config.get("model")
        native_host = config.get("native_host", False) is True
        if native_host and (
            model is not None or config.get("reasoning_effort") is not None
        ):
            raise AdapterConfigurationError(
                "native Codex model settings belong to the host config"
            )
        if model is not None and not isinstance(model, str):
            raise AdapterConfigurationError("codex model must be a string or null")
        reasoning_effort = config.get("reasoning_effort")
        if reasoning_effort is not None and not isinstance(
            reasoning_effort,
            str,
        ):
            raise AdapterConfigurationError(
                "codex reasoning_effort must be a string or null"
            )
        unexpected = {
            key
            for key in (
                "local_provider",
                "local_base_url",
                "ollama_host",
            )
            if config.get(key) is not None
        }
        if unexpected:
            raise AdapterConfigurationError(
                "Codex benchmark adapter does not run local models: "
                + ", ".join(sorted(unexpected))
            )
        return CodexAgent(
            model=model,
            reasoning_effort=reasoning_effort,
            native_host=native_host,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_tool_calls=max_tool_calls,
        )

    if adapter == "opencode-native":
        forbidden = {
            key
            for key in (
                "model",
                "provider",
                "local_provider",
                "base_url",
                "api_key",
            )
            if key in config
        }
        if forbidden:
            raise AdapterConfigurationError(
                "native OpenCode model/provider/auth belong to the "
                "installed OpenCode config, not the benchmark: "
                + ", ".join(sorted(forbidden))
            )
        diagnostic_required_tool = config.get("diagnostic_required_tool")
        if diagnostic_required_tool is not None and (
            not isinstance(diagnostic_required_tool, str)
            or not diagnostic_required_tool.strip()
            or diagnostic_required_tool != diagnostic_required_tool.strip()
        ):
            raise AdapterConfigurationError(
                "diagnostic required tool must be a nonempty canonical tool name"
            )
        return OpenCodeNativeAgent(
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_tool_calls=max_tool_calls,
            diagnostic_required_tool=diagnostic_required_tool,
        )

    raise AdapterConfigurationError(f"unknown agent adapter: {adapter}")


def build_oracle(definition: dict[str, Any], *, timeout_seconds: int, suite_root=None):
    adapter = definition["adapter"]
    identity = definition["identity"]
    config = definition["configuration"]
    if adapter == "expected-json":
        expected = config.get("expected")
        if not isinstance(expected, dict):
            raise AdapterConfigurationError(
                "expected-json oracle requires object configuration.expected"
            )
        return ExpectedJsonOracle(
            str(identity["id"]),
            str(identity["version"]),
            expected,
        )
    if adapter == "repository-location-json":
        expected = config.get("expected")
        if not isinstance(expected, dict):
            raise AdapterConfigurationError(
                "repository-location-json oracle requires object configuration.expected"
            )
        return RepositoryLocationOracle(
            str(identity["id"]),
            str(identity["version"]),
            expected,
        )
    if adapter == "command":
        health = config.get("health_argv")
        grade = config.get("grade_argv")
        if not (
            isinstance(health, list)
            and health
            and all(isinstance(value, str) and value for value in health)
            and isinstance(grade, list)
            and grade
            and all(isinstance(value, str) and value for value in grade)
        ):
            raise AdapterConfigurationError(
                "command oracle requires non-empty health_argv and grade_argv"
            )

        def expand(values):
            return tuple(
                sys.executable
                if value == "{python}"
                else value.replace("{suite}", str(suite_root))
                if suite_root is not None
                else value
                for value in values
            )

        valid_exit_codes = config.get("valid_exit_codes")
        result_format = config.get("result_format", "text")
        if result_format not in {"text", "lexigram-v1"}:
            raise AdapterConfigurationError("unknown command oracle result_format")
        if valid_exit_codes is not None and not (
            isinstance(valid_exit_codes, list)
            and valid_exit_codes
            and all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in valid_exit_codes
            )
        ):
            raise AdapterConfigurationError(
                "command oracle valid_exit_codes must be nonempty integers"
            )
        return CommandOracle(
            str(identity["id"]),
            str(identity["version"]),
            expand(health),
            expand(grade),
            timeout_seconds=min(
                int(config.get("timeout_seconds", timeout_seconds)),
                timeout_seconds,
            ),
            valid_exit_codes=tuple(valid_exit_codes)
            if valid_exit_codes is not None
            else None,
            result_format=result_format,
        )
    raise AdapterConfigurationError(f"unknown oracle adapter: {adapter}")
