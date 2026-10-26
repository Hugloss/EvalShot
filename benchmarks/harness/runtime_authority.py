"""Explicit local runtime authority for benchmark participants."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from pathlib import Path
import re
from typing import Any

from benchmarks.harness.suite import SuiteDefinition


PROCESS_SUBSTRATE_ENV_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TERM",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
)


RUNTIME_AUTHORITY_ENV_KEYS = (
    "BENCHMARK_OH_GOON_NATIVE_HOME",
    "BENCHMARK_OH_GOON_TEMP_PARENT",
    "BENCHMARK_OH_GOON_BASELINE_PYTHON",
    "BENCHMARK_OH_GOON_BASELINE_CONFIG",
    "BENCHMARK_OH_GOON_BASELINE_RECEIPT",
    "BENCHMARK_OH_GOON_CANDIDATE_PYTHON",
    "BENCHMARK_OH_GOON_CANDIDATE_CONFIG",
    "BENCHMARK_OH_GOON_CANDIDATE_RECEIPT",
    "HASHMARKS_BENCH_SOURCE",
    "BENCHMARK_OPENCODE_AGENT",
    "BENCHMARK_PASSTHROUGH_ENV_KEYS",
)


# These locations are allocated by the trial, rather than inherited as provider
# inputs. Native CLIs may write them during even a model-free startup.
PRIVATE_STATE_ENV_KEYS = frozenset({
    "HOME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
    "CODEX_HOME",
    "CODEX_SQLITE_HOME",
})


NATIVE_CODEX_INPUT_ENV_KEYS = (
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "CODEX_MCP_NODE_PATH",
    "CODEX_CLI_PATH",
)


def transport_native_codex_environment(
    source: Mapping[str, str], destination: MutableMapping[str, str],
) -> None:
    """Preserve declared native client/runtime inputs, excluding session state."""
    for name in NATIVE_CODEX_INPUT_ENV_KEYS:
        if name in source:
            destination[name] = source[name]


def native_host_paths(source: Mapping[str, str]) -> dict[str, str]:
    home = Path(source.get("HOME") or Path.home()).expanduser().resolve()
    xdg_config = (
        Path(source.get("XDG_CONFIG_HOME") or home / ".config").expanduser().resolve()
    )
    codex_home = (
        Path(source.get("CODEX_HOME") or home / ".codex").expanduser().resolve()
    )
    return {
        "home": str(home),
        "xdg_config_home": str(xdg_config),
        "codex_home": str(codex_home),
    }


def required_runtime_authority(
    suite: SuiteDefinition,
    rows: list[dict[str, Any]],
) -> tuple[str, ...]:
    selected_condition_ids = {
        str(row["condition_id"])
        for row in rows
        if isinstance(row, dict) and "condition_id" in row
    }
    conditions = [
        condition
        for condition in suite.experiment["conditions"]
        if condition.get("id") in selected_condition_ids
    ]
    subjects = {str(condition.get("subject")) for condition in conditions}
    agents = {str(condition.get("agent")) for condition in conditions}

    required: set[str] = set()
    if "hashmarks" in subjects:
        required.add("HASHMARKS_BENCH_SOURCE")
    if any(
        suite.agents[agent]["adapter"] == "opencode-native"
        for agent in agents
        if agent in suite.agents
    ):
        required.add("BENCHMARK_OPENCODE_AGENT")
    for agent in agents:
        definition = suite.agents.get(agent, {})
        if definition.get("adapter") == "oh-goon":
            runtime = str(
                definition.get("configuration", {}).get("runtime", "")
            ).upper()
            required.update(
                ("BENCHMARK_OH_GOON_NATIVE_HOME", "BENCHMARK_OH_GOON_TEMP_PARENT")
            )
            required.update(
                "BENCHMARK_OH_GOON_" + runtime + "_" + name
                for name in ("PYTHON", "CONFIG", "RECEIPT")
            )
    return tuple(sorted(required))


def oh_goon_temporary_parent(source: Mapping[str, str]) -> Path:
    """Validate the caller-selected allocation boundary without reading stores."""
    raw = source.get("BENCHMARK_OH_GOON_TEMP_PARENT")
    native_home = source.get("BENCHMARK_OH_GOON_NATIVE_HOME")
    if not raw or not native_home:
        raise ValueError(
            "explicit Oh-Goon temporary parent and native home are required"
        )
    parent = Path(raw)
    home = Path(native_home).expanduser().resolve(strict=True)
    if (
        not parent.is_absolute()
        or parent.resolve(strict=True) != parent
        or not parent.is_dir()
        or not home.is_dir()
        or parent.is_relative_to(home)
    ):
        raise ValueError(
            "Oh-Goon temporary parent must be a canonical public directory outside native home"
        )
    return parent


def transport_runtime_authority(
    source: Mapping[str, str],
    destination: MutableMapping[str, str],
) -> None:
    raw_passthrough = source.get("BENCHMARK_PASSTHROUGH_ENV_KEYS", "")
    passthrough = tuple(
        name.strip() for name in raw_passthrough.split(",") if name.strip()
    )
    invalid = [
        name
        for name in passthrough
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
    ]
    if invalid:
        raise ValueError(
            "invalid BENCHMARK_PASSTHROUGH_ENV_KEYS name(s): "
            + ", ".join(sorted(set(invalid)))
        )
    reserved = PRIVATE_STATE_ENV_KEYS.intersection(passthrough)
    if reserved:
        raise ValueError(
            "benchmark passthrough cannot replace trial state roots: "
            + ", ".join(sorted(reserved))
        )
    missing = [name for name in passthrough if not source.get(name)]
    if missing:
        raise ValueError(
            "declared benchmark passthrough variable(s) are unset: "
            + ", ".join(sorted(set(missing)))
        )
    for name in RUNTIME_AUTHORITY_ENV_KEYS:
        value = source.get(name)
        if value:
            destination[name] = value
    for name in passthrough:
        destination[name] = source[name]
