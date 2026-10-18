"""One explicit snapshot of benchmark file choices and host environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from benchmarks.harness.runtime_authority import RUNTIME_AUTHORITY_ENV_KEYS
from benchmarks.harness.selection import SelectionError, parse_agent_arguments


FILE_KEYS = frozenset(
    (
        *RUNTIME_AUTHORITY_ENV_KEYS,
        "BENCHMARK_SUITE_PATH",
        "BENCHMARK_AGENT",
        "BENCHMARK_CAMPAIGN_ROOT",
        "BENCHMARK_HARNESS_REPO_ROOT",
        "BENCHMARK_SCORE_SCRIPT_PATH",
        "BENCHMARK_SCORE_OUTPUT_PATH",
        "BENCHMARK_HARBOR_MODEL",
        "BENCHMARK_HARBOR_EXECUTABLE",
        "BENCHMARK_HARBOR_PASSTHROUGH_ENV_KEYS",
    )
)

COMMAND_REQUIRED_KEYS = {
    "check": ("BENCHMARK_SUITE_PATH",),
    "doctor": ("BENCHMARK_SUITE_PATH",),
    "preflight": (
        "BENCHMARK_SUITE_PATH",
        "BENCHMARK_CAMPAIGN_ROOT",
        "BENCHMARK_HARNESS_REPO_ROOT",
        "BENCHMARK_AGENT",
    ),
    "campaign-audit": (
        "BENCHMARK_SUITE_PATH",
        "BENCHMARK_CAMPAIGN_ROOT",
        "BENCHMARK_HARNESS_REPO_ROOT",
        "BENCHMARK_AGENT",
    ),
    "prepare": (
        "BENCHMARK_SUITE_PATH",
        "BENCHMARK_CAMPAIGN_ROOT",
        "BENCHMARK_HARNESS_REPO_ROOT",
        "BENCHMARK_AGENT",
    ),
    "run": (
        "BENCHMARK_SUITE_PATH",
        "BENCHMARK_CAMPAIGN_ROOT",
        "BENCHMARK_HARNESS_REPO_ROOT",
        "BENCHMARK_AGENT",
    ),
    "report": ("BENCHMARK_SUITE_PATH", "BENCHMARK_CAMPAIGN_ROOT", "BENCHMARK_AGENT"),
    "reports": (
        "BENCHMARK_SUITE_PATH",
        "BENCHMARK_CAMPAIGN_ROOT",
        "BENCHMARK_AGENT",
        "BENCHMARK_SCORE_SCRIPT_PATH",
        "BENCHMARK_SCORE_OUTPUT_PATH",
    ),
    "score": (
        "BENCHMARK_SUITE_PATH",
        "BENCHMARK_CAMPAIGN_ROOT",
        "BENCHMARK_AGENT",
        "BENCHMARK_SCORE_SCRIPT_PATH",
        "BENCHMARK_SCORE_OUTPUT_PATH",
    ),
    "status": ("BENCHMARK_SUITE_PATH", "BENCHMARK_CAMPAIGN_ROOT"),
    "runs": ("BENCHMARK_CAMPAIGN_ROOT",),
}


class BenchmarkConfigError(ValueError):
    pass


def load_env_values(
    file: Path,
    *,
    allowed_keys: frozenset[str],
) -> dict[str, str]:
    """Parse one dotenv-style file for an explicit allowlist of configuration keys."""
    if not file.is_file():
        raise BenchmarkConfigError(f"benchmark env file does not exist: {file}")
    try:
        lines = file.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise BenchmarkConfigError(
            f"cannot read benchmark env file {file}: {exc}"
        ) from exc

    values: dict[str, str] = {}
    for line_no, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            if line in allowed_keys:
                raise BenchmarkConfigError(
                    f"{file}:{line_no}: benchmark entry needs KEY=VALUE"
                )
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in allowed_keys:
            continue
        if key in values:
            raise BenchmarkConfigError(f"{file}:{line_no}: duplicate {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class BenchmarkConfig:
    file: Path | None
    values: Mapping[str, str] = field(repr=False)
    host: Mapping[str, str] = field(repr=False)

    @classmethod
    def load(
        cls,
        file: Path | None,
        *,
        host: Mapping[str, str] | None = None,
    ) -> BenchmarkConfig:
        snapshot = dict(os.environ if host is None else host)
        values = (
            load_env_values(file, allowed_keys=FILE_KEYS)
            if file is not None
            else {}
        )
        return cls(file, MappingProxyType(values), MappingProxyType(snapshot))

    def require(self, *keys: str) -> None:
        missing = sorted(key for key in keys if not self.values.get(key))
        if missing:
            place = str(self.file) if self.file is not None else "the selected .env"
            raise BenchmarkConfigError(
                f"missing explicit benchmark setting(s) in {place}: {', '.join(missing)}"
            )

    def require_for(self, command: str) -> None:
        if self.file is not None:
            self.require(*COMMAND_REQUIRED_KEYS.get(command, ()))

    def path(self, key: str) -> Path | None:
        value = self.values.get(key)
        return Path(value) if value else None

    def agents(self) -> tuple[str, ...]:
        value = self.values.get("BENCHMARK_AGENT")
        if not value:
            return ()
        try:
            return parse_agent_arguments((value,))
        except SelectionError as exc:
            raise BenchmarkConfigError(str(exc)) from exc

    def runtime_environment(self) -> Mapping[str, str]:
        """Host substrate and secrets, with file-declared benchmark choices winning."""
        result = dict(self.host)
        for key in FILE_KEYS:
            if key in self.values:
                result[key] = self.values[key]
            else:
                result.pop(key, None)
        return MappingProxyType(result)
