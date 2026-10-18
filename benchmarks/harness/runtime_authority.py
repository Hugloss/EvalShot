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
    "HASHMARKS_BENCH_SOURCE",
    "BENCHMARK_OPENCODE_AGENT",
    "BENCHMARK_PASSTHROUGH_ENV_KEYS",
)


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
    return tuple(sorted(required))


def transport_runtime_authority(
    source: Mapping[str, str],
    destination: MutableMapping[str, str],
) -> None:
    for name in RUNTIME_AUTHORITY_ENV_KEYS:
        value = source.get(name)
        if value:
            destination[name] = value

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
    missing = [name for name in passthrough if not source.get(name)]
    if missing:
        raise ValueError(
            "declared benchmark passthrough variable(s) are unset: "
            + ", ".join(sorted(set(missing)))
        )
    for name in passthrough:
        destination[name] = source[name]
