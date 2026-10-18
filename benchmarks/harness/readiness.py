"""Fast benchmark runtime readiness checks.

This module answers only whether the configured native hosts and benchmark subjects
can be wired on this machine. It never materializes a benchmark task, applies a
mutation, runs an oracle, inspects campaign receipts, or invokes a model.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.adapters.hashmarks import HashmarksSubject
from benchmarks.adapters.opencode_native import _native_environment
from benchmarks.adapters.registry import build_agent, build_subject
from benchmarks.adapters.runtime import observe_executable
from benchmarks.harness.mcp_catalog import probe_mcp_tool_catalog
from benchmarks.harness.model import McpExposure, TrialContext
from benchmarks.harness.subject_exposure import exposure_probe_required_tool
from benchmarks.harness.runtime_authority import (
    native_host_paths,
    transport_runtime_authority,
)
from benchmarks.harness.suite import SuiteDefinition
from benchmarks.harness.workspace import isolated_environment
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


READINESS_TIMEOUT_SECONDS = 15
READINESS_MAX_OUTPUT_BYTES = 1_000_000


@dataclass(frozen=True)
class ReadinessCheck:
    label: str
    state: str
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.state in {"READY", "CONNECTED"}

    def line(self) -> str:
        detail = f": {self.reason}" if self.reason else ""
        return f"{self.label}: {self.state}{detail}"


@dataclass(frozen=True)
class ReadinessReport:
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)


def _unique(values):
    return tuple(dict.fromkeys(values))


def _context(root: Path, label: str, source: Mapping[str, str]) -> TrialContext:
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    marker = workspace / "README.md"
    if not marker.exists():
        marker.write_text("# benchmark readiness workspace\n", encoding="utf-8")

    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)
    control_root = root / "control" / safe
    environment = isolated_environment(control_root, source=source)
    transport_runtime_authority(source, environment)

    native = native_host_paths(source)
    environment["CODEX_HOME"] = native["codex_home"]
    environment["BENCHMARK_NATIVE_HOME"] = native["home"]
    environment["BENCHMARK_NATIVE_XDG_CONFIG_HOME"] = native["xdg_config_home"]
    return TrialContext(
        workspace=workspace,
        control_root=control_root,
        environment=environment,
    )


def _reason(payload: dict[str, Any], fallback: str) -> str:
    value = payload.get("reason")
    if isinstance(value, str) and value:
        return value
    stderr = payload.get("stderr")
    if isinstance(stderr, str) and stderr.strip():
        return stderr.strip()[-1200:]
    return fallback


def _subject_runtime_check(
    suite: SuiteDefinition,
    subject_id: str,
    root: Path,
    source: Mapping[str, str],
) -> ReadinessCheck:
    context = _context(root, f"subject-{subject_id}", source)
    try:
        subject = build_subject(suite.subjects[subject_id])
        exposure = subject.mcp_exposure(context)
        if exposure is None:
            return ReadinessCheck(
                f"{subject_id} runtime",
                "FAILED",
                "subject has no MCP exposure",
            )
        version_args = ("version",) if subject_id == "hashmarks" else ("--version",)
        observed = observe_executable(
            context,
            exposure.command,
            version_args=version_args,
            timeout_seconds=READINESS_TIMEOUT_SECONDS,
        )
        if not observed.payload.get("available"):
            return ReadinessCheck(
                f"{subject_id} runtime",
                "FAILED",
                _reason(observed.payload, "executable probe failed"),
            )
        if isinstance(subject, HashmarksSubject):
            identity, error = subject.source_identity(context)
            if error:
                return ReadinessCheck(
                    "hashmarks runtime",
                    "FAILED",
                    error,
                )
            if not identity or identity.get("working_copy_clean") is not True:
                return ReadinessCheck(
                    "hashmarks runtime",
                    "FAILED",
                    "Hashmarks benchmark source authority is not clean",
                )
        contract = suite.experiment.get("subject_exposure_contract")
        if isinstance(contract, dict) and contract.get("require_probe_contract") is True:
            required_tool = exposure_probe_required_tool(suite, subject_id)
            probe_mcp_tool_catalog(
                context=context,
                exposure=exposure,
                subject_id=subject_id,
                required_tool=required_tool,
            )
        return ReadinessCheck(f"{subject_id} runtime", "READY")
    except (OSError, ValueError) as exc:
        return ReadinessCheck(f"{subject_id} runtime", "FAILED", str(exc))


def _agent(
    suite: SuiteDefinition,
    agent_id: str,
):
    return build_agent(
        suite.agents[agent_id],
        budgets={
            "timeout_seconds": READINESS_TIMEOUT_SECONDS,
            "max_output_bytes": READINESS_MAX_OUTPUT_BYTES,
        },
    )


def _agent_native_check(
    suite: SuiteDefinition,
    agent_id: str,
    root: Path,
    source: Mapping[str, str],
) -> ReadinessCheck:
    context = _context(root, f"agent-{agent_id}", source)
    try:
        observation = _agent(suite, agent_id).prepare(context, None)
    except (OSError, ValueError) as exc:
        return ReadinessCheck(f"{agent_id} native config", "FAILED", str(exc))
    if observation.payload.get("available") is not True:
        return ReadinessCheck(
            f"{agent_id} native config",
            "FAILED",
            _reason(observation.payload, "native host preparation failed"),
        )
    return ReadinessCheck(f"{agent_id} native config", "READY")


def _mcp_startup_diagnostic(
    context: TrialContext,
    exposure: McpExposure,
) -> str | None:
    try:
        result = run_bounded(
            repository_root=context.workspace,
            argv=(exposure.command, *exposure.args),
            cwd=exposure.cwd,
            environment={**_native_environment(context), **exposure.environment},
            limits=ProcessLimits(
                timeout_seconds=5,
                max_stdout_bytes=200_000,
                max_stderr_bytes=200_000,
            ),
            inherit_environment=False,
            close_stdin=True,
        )
    except (OSError, ValueError) as exc:
        return f"direct-startup-probe-error={exc}"
    if result.executable_missing:
        return "direct-startup-executable-missing=true"
    if (
        result.return_code == 0
        and not result.timed_out
        and not result.stdout_truncated
        and not result.stderr_truncated
    ):
        return None
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    details: list[str] = [f"direct-startup-exit={result.return_code}"]
    if result.timed_out:
        details.append("direct-startup-timeout=true")
    if result.stdout_truncated:
        details.append("direct-startup-stdout-truncated=true")
    if result.stderr_truncated:
        details.append("direct-startup-stderr-truncated=true")
    if stderr:
        details.append(f"direct-startup-stderr={stderr[-2000:]!r}")
    elif stdout:
        details.append(f"direct-startup-stdout={stdout[-1000:]!r}")
    return "; ".join(details)


def _pair_check(
    suite: SuiteDefinition,
    agent_id: str,
    subject_id: str,
    root: Path,
    source: Mapping[str, str] | None = None,
) -> ReadinessCheck:
    source = os.environ if source is None else source
    context = _context(root, f"pair-{agent_id}-{subject_id}", source)
    definition = suite.agents[agent_id]
    adapter = str(definition["adapter"])
    label = (
        f"{agent_id} -> {subject_id} MCP"
        if adapter == "opencode-native"
        else f"{agent_id} -> {subject_id} exposure"
    )
    try:
        subject = build_subject(suite.subjects[subject_id])
        exposure = subject.mcp_exposure(context)
        observation = _agent(suite, agent_id).prepare(context, subject)
    except (OSError, ValueError) as exc:
        return ReadinessCheck(label, "FAILED", str(exc))
    if observation.payload.get("available") is not True:
        reason = _reason(observation.payload, "host/subject readiness failed")
        if (
            adapter == "opencode-native"
            and exposure is not None
            and observation.payload.get("failure_stage") == "mcp-connection"
        ):
            direct = _mcp_startup_diagnostic(context, exposure)
            if direct:
                reason = f"{reason}; {direct}"
        return ReadinessCheck(
            label,
            "FAILED",
            reason,
        )
    return ReadinessCheck(
        label,
        "CONNECTED" if adapter == "opencode-native" else "READY",
    )


def check_runtime_readiness(
    suite: SuiteDefinition,
    *,
    agents: tuple[str, ...] = (),
    source: Mapping[str, str] | None = None,
) -> ReadinessReport:
    source = os.environ if source is None else source
    unknown = sorted(set(agents) - set(suite.agents))
    if unknown:
        raise ValueError("unknown benchmark agent(s): " + ", ".join(unknown))
    selected_agents = set(agents)
    conditions = tuple(
        condition
        for condition in suite.experiment["conditions"]
        if not agents or str(condition["agent"]) in selected_agents
    )
    if not conditions:
        raise ValueError("benchmark readiness has no suite conditions")
    missing_agents = sorted(
        selected_agents - {str(condition["agent"]) for condition in conditions}
    )
    if missing_agents:
        raise ValueError(
            "no suite conditions for selected agent(s): " + ", ".join(missing_agents)
        )
    subject_ids = _unique(
        str(condition["subject"])
        for condition in conditions
        if str(condition["subject"]) != "none"
    )
    pairs = _unique(
        (str(condition["agent"]), str(condition["subject"])) for condition in conditions
    )

    checks: list[ReadinessCheck] = []
    with tempfile.TemporaryDirectory(prefix="agents-cookbook-benchmark-check-") as tmp:
        root = Path(tmp)
        subject_checks = {
            subject_id: _subject_runtime_check(suite, subject_id, root, source)
            for subject_id in subject_ids
        }
        for agent_id, subject_id in pairs:
            if subject_id == "none":
                result = _agent_native_check(suite, agent_id, root, source)
            elif not subject_checks[subject_id].ok:
                prerequisite = subject_checks[subject_id]
                result = ReadinessCheck(
                    "",
                    "FAILED",
                    f"{subject_id} runtime: {prerequisite.reason}",
                )
            else:
                result = _pair_check(suite, agent_id, subject_id, root, source)
            checks.append(
                ReadinessCheck(
                    f"{agent_id} + {'bare' if subject_id == 'none' else subject_id}",
                    result.state,
                    result.reason,
                )
            )
    return ReadinessReport(tuple(checks))
