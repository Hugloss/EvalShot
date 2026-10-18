"""Native OpenCode benchmark adapter.

OpenCode owns model/provider/auth configuration. The benchmark never writes those
settings. The selected subject adapter owns the exact MCP executable and invocation.
A shared JS runtime overlays only that benchmark subject plus MCP tool gating, then
owns OpenCode run/session/export lifecycle and observation projection.
"""

from __future__ import annotations

import hashlib
import json
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.adapters.runtime import (
    executable_file_metadata,
    observe_executable,
    resolve_native_executable,
)
from benchmarks.harness.identity import canonical_json
from benchmarks.harness.runtime_authority import PROCESS_SUBSTRATE_ENV_KEYS
from benchmarks.harness.tool_results import result_bytes, tool_result_evidence
from benchmarks.harness.model import (
    Observation,
    ParticipantIdentity,
    SubjectAdapter,
    SubjectLifecycleMode,
    TrialContext,
)
from benchmarks.tool_routing import subject_routing_timing
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


_RUNTIME_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "opencode-runtime.js"
)
_SCRATCH_INSTRUCTION = (
    "Benchmark execution note: put temporary files outside the repository only "
    "under $TMPDIR, and remove them before finishing. Other external scratch "
    "paths are unavailable.\n\n"
)

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _observe_opencode_executable(
    context: TrialContext,
    environment: dict[str, str],
) -> Observation:
    """Freshly observe executable/version authority for every admission."""
    return observe_executable(
        context,
        environment["OPENCODE_BIN"],
        environment=environment,
    )


def _subject_source_identity_sha256(
    subject: SubjectAdapter | None,
    context: TrialContext,
) -> tuple[str | None, str | None]:
    observer = getattr(subject, "source_identity", None)
    if observer is None or not callable(observer):
        return None, None
    identity, error = observer(context)
    if error:
        return None, str(error)
    if not isinstance(identity, dict):
        return None, "benchmark subject source identity is unavailable"
    return hashlib.sha256(canonical_json(identity)).hexdigest(), None


def _parse_json_object(raw: str, label: str) -> dict[str, Any]:
    value = json.loads(raw.strip())
    if not isinstance(value, dict):
        raise ValueError(f"{label}: expected JSON object")
    return value


def _bounded_diagnostic(value: Any, *, limit: int = 2_000) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = value.strip()
    if not rendered:
        return None
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + "…"


def _bounded_tail_diagnostic(value: Any, *, limit: int = 2_000) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = value.strip()
    if not rendered:
        return None
    if len(rendered) <= limit:
        return rendered
    return "…" + rendered[-limit:]


def _run_failure_reason(
    run_evidence: dict[str, Any] | None,
    *,
    export_error: str | None,
    final_text: str | None,
    recovered_context_overflow: bool = False,
) -> str | None:
    if run_evidence is None:
        return export_error or "OpenCode runtime emitted no run result"

    run_status = run_evidence.get("status")
    if run_status != 0:
        if recovered_context_overflow and export_error is None and final_text:
            return None
        if run_status is None:
            reason = "OpenCode runtime emitted no run status"
        else:
            reason = f"OpenCode run exited with status {run_status}"
        details: list[str] = []
        stderr = _bounded_diagnostic(run_evidence.get("stderr"))
        if stderr:
            details.append(f"stderr={stderr!r}")
        error = _bounded_diagnostic(run_evidence.get("error"))
        if error:
            details.append(f"error={error!r}")
        if not details:
            stdout_tail = _bounded_tail_diagnostic(run_evidence.get("stdout"))
            if stdout_tail:
                details.append(f"stdout_tail={stdout_tail!r}")
        signal = run_evidence.get("signal")
        if isinstance(signal, str) and signal:
            details.append(f"signal={signal}")
        return "; ".join((reason, *details))

    if export_error is not None:
        return export_error
    if final_text is None:
        return "OpenCode completed without a final assistant message"
    return None


def _run_failure_diagnostic(
    run_evidence: dict[str, Any] | None,
    *,
    export_diagnostic: str | None,
    runtime_stderr: str | None,
    authority_revalidation: dict[str, Any] | None = None,
) -> str | None:
    details: list[str] = []

    if (
        isinstance(authority_revalidation, dict)
        and authority_revalidation.get("status") == "failed"
    ):
        details.append(
            "OpenCode authority revalidation:\n"
            + json.dumps(
                authority_revalidation,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )

    export = _bounded_diagnostic(export_diagnostic)
    if export:
        details.append(f"OpenCode export diagnostic:\n{export}")

    if isinstance(run_evidence, dict):
        stderr = _bounded_diagnostic(run_evidence.get("stderr"))
        if stderr:
            details.append(f"OpenCode run stderr:\n{stderr}")
        error = _bounded_diagnostic(run_evidence.get("error"))
        if error:
            details.append(f"OpenCode run error:\n{error}")
        if run_evidence.get("status") not in (None, 0) and not stderr and not error:
            stdout_tail = _bounded_tail_diagnostic(run_evidence.get("stdout"))
            if stdout_tail:
                details.append(f"OpenCode run stdout tail:\n{stdout_tail}")
        signal = run_evidence.get("signal")
        if isinstance(signal, str) and signal:
            details.append(f"OpenCode run signal: {signal}")

    runtime = _bounded_diagnostic(runtime_stderr)
    if runtime:
        details.append(f"Shared OpenCode runtime stderr:\n{runtime}")

    return "\n\n".join(details) or None


def _authority_changed_fields(
    before: Any,
    after: Any,
    *,
    prefix: str = "",
) -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        changed: list[str] = []
        for key in sorted(set(before) | set(after)):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                changed.append(child)
                continue
            changed.extend(
                _authority_changed_fields(
                    before[key],
                    after[key],
                    prefix=child,
                )
            )
        return changed
    return [] if before == after else [prefix or "<root>"]


def _enrich_authority_revalidation(
    *,
    revalidation: dict[str, Any],
    context: TrialContext,
    environment: dict[str, str],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Add durable forensic context without changing the runtime verdict."""
    reason = str(revalidation.get("reason") or "participant authority changed")
    if "subject" in reason.lower() or "workspace binding" in reason.lower():
        reason_code = "subject-authority-drift"
        authority_kind = "subject-runtime"
    else:
        reason_code = "execution-authority-drift"
        authority_kind = (
            "opencode-executable"
            if "executable" in reason.lower()
            else "opencode-config"
            if "configuration" in reason.lower()
            else "opencode-runtime"
        )

    baseline: dict[str, Any] | None = None
    observed: dict[str, Any] | None = None
    changed_fields: list[str] = []
    if authority_kind == "opencode-executable":
        baseline = {
            "resolved_path": evidence.get("opencode_executable_path"),
            "sha256": evidence.get("opencode_executable_sha256"),
            "version": evidence.get("opencode_version"),
            "file_metadata": evidence.get("opencode_executable_metadata"),
        }
        current = _observe_opencode_executable(context, environment)
        observed = {
            "resolved_path": current.payload.get("resolved_path"),
            "sha256": current.payload.get("executable_sha256"),
            "version": current.payload.get("version"),
            "file_metadata": current.payload.get("file_metadata"),
            "executable_stable": current.payload.get("executable_stable"),
        }
        changed_fields = _authority_changed_fields(baseline, observed)
    elif authority_kind == "opencode-config":
        baseline = {
            "config_sha256": evidence.get("native_config_sha256"),
        }

    return {
        **revalidation,
        "reason_code": reason_code,
        "authority_kind": authority_kind,
        "reason": reason,
        "changed_fields": changed_fields,
        "baseline": baseline,
        "observed": observed,
        "diagnostic": "".join(traceback.format_stack(limit=16)),
    }


def _native_environment(context: TrialContext) -> dict[str, str]:
    required = (
        "BENCHMARK_NATIVE_HOME",
        "BENCHMARK_NATIVE_XDG_CONFIG_HOME",
        "BENCHMARK_OPENCODE_AGENT",
    )
    missing = [name for name in required if not context.environment.get(name)]
    if missing:
        raise ValueError(
            "native OpenCode environment could not be established: "
            + ", ".join(missing)
        )
    resolved = resolve_native_executable(context, "opencode")
    if resolved is None:
        raise ValueError("opencode is not available on the native PATH")
    bwrap = resolve_native_executable(context, "bwrap")
    if bwrap is None:
        raise ValueError("bubblewrap is required for isolated OpenCode trial scratch")
    environment = {
        "OPENCODE_BIN": resolved,
        "BWRAP_BIN": bwrap,
        "HOME": context.environment["BENCHMARK_NATIVE_HOME"],
        "XDG_CONFIG_HOME": context.environment["BENCHMARK_NATIVE_XDG_CONFIG_HOME"],
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_PRUNE": "1",
        "OPENCODE_AUTO_SHARE": "false",
        "TMPDIR": context.environment["TMPDIR"],
        "TMP": context.environment["TMP"],
        "TEMP": context.environment["TEMP"],
        "XDG_CACHE_HOME": context.environment["XDG_CACHE_HOME"],
        "XDG_DATA_HOME": context.environment["XDG_DATA_HOME"],
        "XDG_STATE_HOME": context.environment["XDG_STATE_HOME"],
    }
    for name in PROCESS_SUBSTRATE_ENV_KEYS:
        value = context.environment.get(name)
        if value:
            environment[name] = value
    for name in (
        key.strip()
        for key in context.environment.get(
            "BENCHMARK_PASSTHROUGH_ENV_KEYS",
            "",
        ).split(",")
        if key.strip()
    ):
        value = context.environment.get(name)
        if value:
            environment[name] = value
    return environment


def _assistant_messages(exported: dict[str, Any]) -> list[dict[str, Any]]:
    messages = exported.get("messages")
    if not isinstance(messages, list):
        return []
    return [
        message
        for message in messages
        if isinstance(message, dict)
        and isinstance(message.get("info"), dict)
        and message["info"].get("role") == "assistant"
    ]


def _parts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        result.extend(part for part in parts if isinstance(part, dict))
    return result


def _tool_name(part: dict[str, Any]) -> str | None:
    for key in ("tool", "toolName", "name"):
        value = part.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _observed_model(exported: dict[str, Any]) -> tuple[str | None, str | None]:
    for message in reversed(_assistant_messages(exported)):
        info = message["info"]
        model = info.get("modelID")
        provider = info.get("providerID")
        if isinstance(model, str) and model:
            return (
                f"{provider}/{model}"
                if isinstance(provider, str) and provider
                else model,
                provider if isinstance(provider, str) else None,
            )
        model = info.get("model")
        if isinstance(model, str) and model:
            provider = model.split("/", 1)[0] if "/" in model else None
            return model, provider
    return None, None


def _token_metrics(exported: dict[str, Any]) -> tuple[int, int, int]:
    input_tokens = output_tokens = cached_tokens = 0
    for message in _assistant_messages(exported):
        tokens = message["info"].get("tokens")
        if not isinstance(tokens, dict):
            continue
        input_tokens += int(tokens.get("input", 0) or 0)
        output_tokens += int(tokens.get("output", 0) or 0)
        cache = tokens.get("cache")
        if isinstance(cache, dict):
            cached_tokens += int(cache.get("read", 0) or 0)
        else:
            cached_tokens += int(tokens.get("cached", 0) or 0)
    return input_tokens, output_tokens, cached_tokens


def _subject_tool_name(name: str, selected_server: str | None) -> str:
    if selected_server is None:
        return name
    for prefix in (
        f"tools.{selected_server}.",
        f"{selected_server}.",
        f"{selected_server}_",
    ):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _metrics(
    exported: dict[str, Any],
    *,
    mcp_servers: tuple[str, ...],
    selected_server: str | None,
) -> dict[str, Any]:
    parts = _parts(_assistant_messages(exported))
    tool_parts = [part for part in parts if part.get("type") == "tool"]
    names = [name for part in tool_parts if (name := _tool_name(part))]
    direct_mcp_calls = [
        name
        for name in names
        if any(name.startswith(f"{server}_") for server in mcp_servers)
    ]
    nested_tool_calls: list[str] = []
    nested_tool_rows: list[dict[str, Any]] = []
    nested_mcp_calls: list[str] = []
    observed_tool_sequence: list[str] = []
    nested_observable = True
    for part in tool_parts:
        direct_name = _tool_name(part)
        if direct_name:
            observed_tool_sequence.append(direct_name)
        if direct_name != "execute":
            continue
        state = part.get("state")
        metadata = state.get("metadata") if isinstance(state, dict) else None
        calls = metadata.get("toolCalls") if isinstance(metadata, dict) else None
        if not isinstance(calls, list) or any(
            not isinstance(call, dict) or not isinstance(call.get("tool"), str)
            for call in calls
        ):
            nested_observable = False
            continue
        nested_names = [call["tool"] for call in calls]
        nested_tool_rows.extend(calls)
        nested_tool_calls.extend(nested_names)
        observed_tool_sequence.extend(f"nested:{name}" for name in nested_names)
    nested_mcp_calls = [
        name
        for name in nested_tool_calls
        if any(
            name.startswith(f"{server}.") or name.startswith(f"tools.{server}.")
            for server in mcp_servers
        )
    ]
    mcp_calls = direct_mcp_calls + nested_mcp_calls
    def is_subject_call(name: str) -> bool:
        return bool(
            selected_server
            and (
                name.startswith(f"{selected_server}_")
                or name.startswith(f"{selected_server}.")
                or name.startswith(f"tools.{selected_server}.")
            )
        )

    direct_subject_calls = [name for name in direct_mcp_calls if is_subject_call(name)]
    subject_calls = [name for name in mcp_calls if is_subject_call(name)]
    command_calls = [
        name for name in names if name in {"bash", "shell", "terminal", "run"}
    ]
    file_changes = [
        name for name in names if name in {"edit", "write", "patch", "apply_patch"}
    ]
    mcp_result_bytes = 0
    for part in tool_parts:
        name = _tool_name(part)
        if name not in direct_mcp_calls:
            continue
        state = part.get("state")
        if isinstance(state, dict) and "output" in state:
            measured = result_bytes(state.get("output"))
            if measured is not None:
                mcp_result_bytes += measured

    subject_tool_result_evidence: list[dict[str, Any]] = []
    if selected_server is not None:
        for part in tool_parts:
            name = _tool_name(part)
            if not isinstance(name, str) or not is_subject_call(name):
                continue
            state = part.get("state")
            state_dict = state if isinstance(state, dict) else {}
            subject_tool_result_evidence.append(
                tool_result_evidence(
                    operation=_subject_tool_name(name, selected_server),
                    status=state_dict.get("status"),
                    result_present="output" in state_dict,
                    result=state_dict.get("output"),
                    error=state_dict.get("error"),
                    basis="opencode-direct-export",
                )
            )
        for call in nested_tool_rows:
            name = call.get("tool")
            if not isinstance(name, str) or not is_subject_call(name):
                continue
            if "output" in call:
                result_present = True
                result = call.get("output")
            else:
                result_present = "result" in call
                result = call.get("result")
            subject_tool_result_evidence.append(
                tool_result_evidence(
                    operation=_subject_tool_name(name, selected_server),
                    status=call.get("status"),
                    result_present=result_present,
                    result=result,
                    error=call.get("error"),
                    basis="opencode-execute-metadata",
                )
            )
    input_tokens, output_tokens, cached_tokens = _token_metrics(exported)
    subject_ordinals = [
        index
        for index, name in enumerate(observed_tool_sequence, 1)
        if is_subject_call(name.removeprefix("nested:"))
    ]
    metrics: dict[str, Any] = {
        "event_count": len(parts),
        "command_calls": len(command_calls),
        "file_change_events": len(file_changes),
        "tool_calls": len(tool_parts),
        "subject_tool_configured": selected_server is not None,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "source_read_observability": ("not-authoritatively-exposed-by-opencode-export"),
        "tool_strategy_observability": (
            "opencode-export-direct+execute-metadata"
            if nested_observable
            else "opencode-export-direct-only-partial"
        ),
        "tool_name_counts": dict(
            sorted(Counter(observed_tool_sequence).items())
        ),
        "tool_sequence": observed_tool_sequence,
        "subject_tool_call_ordinals": subject_ordinals,
        "subject_first_tool_call_ordinal": (
            subject_ordinals[0] if subject_ordinals else None
        ),
    }
    if selected_server is not None:
        observed_subject_calls = (
            subject_calls if nested_observable else direct_subject_calls
        )
        metrics.update(
            subject_mcp_calls_observed=len(observed_subject_calls),
            subject_tool_names=sorted(
                {
                    _subject_tool_name(name, selected_server)
                    for name in observed_subject_calls
                }
            ),
            subject_tool_observability=(
                "complete" if nested_observable else "partial"
            ),
            subject_tool_result_evidence=subject_tool_result_evidence,
        )
    if nested_observable:
        metrics.update(
            mcp_calls=len(mcp_calls),
            subject_mcp_calls=len(subject_calls),
            subject_tool_invoked=bool(subject_calls),
        )
        if not nested_mcp_calls:
            metrics["mcp_result_bytes"] = mcp_result_bytes
    elif direct_subject_calls:
        # Direct exported MCP calls are authoritative positive evidence even
        # when nested execute() metadata is incomplete. Absence remains unknown.
        metrics["subject_tool_invoked"] = True

    invoked_value = metrics.get("subject_tool_invoked")
    metrics["subject_routing_timing"] = subject_routing_timing(
        [name.removeprefix("nested:") for name in observed_tool_sequence],
        subject_ordinals,
        configured=selected_server is not None,
        invocation_observed=(
            invoked_value if isinstance(invoked_value, bool) else None
        ),
        order_complete=nested_observable,
    )
    return metrics


def _runtime_call(
    context: TrialContext,
    *,
    args: tuple[str, ...],
    environment: dict[str, str],
    timeout_seconds: float,
    max_stdout_bytes: int,
) -> tuple[dict[str, Any] | None, Any]:
    scratch = context.control_root / "_environment" / "tmp"
    if Path(environment["TMPDIR"]).resolve() != scratch.resolve():
        raise ValueError("OpenCode trial scratch does not match control root")
    argv = (
        environment["BWRAP_BIN"],
        "--die-with-parent",
        "--bind", "/", "/",
        "--bind", str(scratch), "/tmp",
        # Readiness uses a workspace and control root below host /tmp. Preserve
        # those two paths after the private bind; other host /tmp paths stay hidden.
        *(("--bind", str(context.workspace), str(context.workspace))
          if context.workspace.is_relative_to("/tmp") else ()),
        *(("--bind", str(context.control_root), str(context.control_root))
          if context.control_root.is_relative_to("/tmp") else ()),
        "--proc", "/proc",
        "--dev-bind", "/dev", "/dev",
        "--",
        "node", str(_RUNTIME_SCRIPT), *args,
    )
    result = run_bounded(
        repository_root=context.workspace,
        argv=argv,
        environment={**environment, "BENCHMARK_PRIVATE_TMP": "1"},
        limits=ProcessLimits(
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=max_stdout_bytes,
            max_stderr_bytes=2_000_000,
        ),
        inherit_environment=False,
    )
    if (
        result.executable_missing
        or result.timed_out
        or result.return_code != 0
        or result.stdout_truncated
        or result.stderr_truncated
    ):
        return None, result
    try:
        envelope = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, result
    if not isinstance(envelope, dict):
        return None, result
    return envelope, result


@dataclass(frozen=True)
class OpenCodeNativeAgent:
    timeout_seconds: int = 600
    max_output_bytes: int = 50_000_000
    max_tool_calls: int | None = None
    diagnostic_required_tool: str | None = None

    def subject_lifecycle_mode(self) -> SubjectLifecycleMode:
        return SubjectLifecycleMode.AGENT_NATIVE

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            "opencode-native",
            "coding_agent",
            "runtime-observed",
            {
                "configuration": "native-opencode",
                "model_provider": "native-opencode",
                "runtime_overlay": "benchmark-subject-exposure+tool-gating+scratch+compaction",
                "surface": "shared-opencode-runtime-v5",
            },
        )

    def _selected_subject(
        self,
        exposed_subject: SubjectAdapter | None,
    ) -> str | None:
        if exposed_subject is None:
            return None
        identity = exposed_subject.identity()
        if identity.kind == "control" or identity.participant_id == "none":
            return None
        return identity.participant_id

    def _subject_exposure(
        self,
        context: TrialContext,
        exposed_subject: SubjectAdapter | None,
        selected_subject: str | None,
    ) -> tuple[Path | None, dict[str, Any] | None]:
        if selected_subject is None:
            return None, None
        if exposed_subject is None:
            raise ValueError(f"benchmark subject {selected_subject} is unavailable")
        exposure = exposed_subject.mcp_exposure(context)
        if exposure is None or exposure.name != selected_subject:
            raise ValueError(
                f"benchmark subject {selected_subject} has no matching MCP exposure"
            )
        payload = {
            "name": exposure.name,
            "command": [exposure.command, *exposure.args],
            "cwd": str(exposure.cwd),
            "environment": dict(exposure.environment),
            "semantic_identity": dict(exposure.semantic_identity),
        }
        path = context.control_root / "opencode-benchmark-exposure.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json(payload))
        return path, payload

    def _resolve_native(
        self,
        context: TrialContext,
        selected_subject: str | None,
        exposure_path: Path | None,
    ) -> tuple[dict[str, Any], Observation]:
        try:
            environment = _native_environment(context)
        except ValueError as exc:
            return {}, Observation(
                {"available": False, "reason": str(exc)},
                "",
            )
        executable = _observe_opencode_executable(
            context,
            environment,
        )
        args = (
            "inspect-config",
            "--repo",
            str(context.workspace),
            "--benchmark-control-root",
            str(context.control_root),
            "--agent",
            context.environment["BENCHMARK_OPENCODE_AGENT"],
            "--benchmark-subject",
            selected_subject or "none",
        )
        if exposure_path is not None:
            args += ("--benchmark-exposure-file", str(exposure_path))
        envelope, result = _runtime_call(
            context,
            args=args,
            environment=environment,
            timeout_seconds=min(30.0, float(self.timeout_seconds)),
            max_stdout_bytes=2_000_000,
        )
        if (
            envelope is None
            or envelope.get("status") != "completed"
            or not isinstance(envelope.get("inspection"), dict)
            or not isinstance(envelope.get("effective_inspection"), dict)
        ):
            reason = "shared OpenCode runtime could not resolve benchmark config"
            if envelope and (envelope.get("reason") or envelope.get("parse_error")):
                reason = str(envelope.get("reason") or envelope["parse_error"])
            return {}, Observation(
                {
                    **executable.payload,
                    "available": False,
                    "reason": reason,
                    "failure_stage": envelope.get("failure_stage")
                    if envelope
                    else None,
                    "native_config_probe": envelope.get("native_config_probe")
                    if envelope
                    else None,
                    "runtime_process": result.metrics(),
                    "stderr": result.stderr.decode(
                        "utf-8",
                        errors="replace",
                    ),
                },
                result.stdout.decode("utf-8", errors="replace"),
            )
        return dict(envelope), executable

    def prepare(
        self,
        context: TrialContext,
        exposed_subject: SubjectAdapter | None,
    ) -> Observation:
        selected_subject = self._selected_subject(exposed_subject)
        if self.diagnostic_required_tool is not None and not self.diagnostic_required_tool.startswith(
            f"{selected_subject}_"
        ):
            return Observation({
                "available": False,
                "reason": "diagnostic required tool does not match selected subject",
            }, "")
        try:
            exposure_path, subject_exposure = self._subject_exposure(
                context,
                exposed_subject,
                selected_subject,
            )
        except ValueError as exc:
            return Observation(
                {
                    "available": False,
                    "reason": str(exc),
                },
                "",
            )
        subject_source_identity_sha256, source_identity_error = (
            _subject_source_identity_sha256(exposed_subject, context)
        )
        if source_identity_error is not None:
            return Observation(
                {
                    "available": False,
                    "reason": source_identity_error,
                },
                "",
            )
        resolved, executable = self._resolve_native(
            context,
            selected_subject,
            exposure_path,
        )
        if not resolved:
            return executable
        inspection = resolved["inspection"]
        effective = resolved["effective_inspection"]
        model = inspection.get("model")
        provider = inspection.get("provider")
        server_rows = effective.get("mcp_servers")
        if not isinstance(server_rows, list):
            server_rows = []
        servers = {
            str(row.get("name")): bool(row.get("enabled"))
            for row in server_rows
            if isinstance(row, dict) and row.get("name")
        }
        selected = resolved.get("selected_server")
        selected_ready = selected is None or servers.get(selected) is True
        workspace_binding = resolved.get("workspace_binding")
        if not isinstance(workspace_binding, dict):
            workspace_binding = {
                "verified": selected is None,
                "reason": "shared runtime emitted no workspace-binding evidence",
            }
        binding_verified = selected is None or workspace_binding.get("verified") is True
        executable_verified = True
        if selected:
            expected = (
                subject_exposure["command"][0]
                if isinstance(subject_exposure, dict)
                and isinstance(subject_exposure.get("command"), list)
                and subject_exposure["command"]
                else None
            )
            native_identity = resolved.get("native_subject_identity")
            observed_path = (
                native_identity.get("executable_path")
                if isinstance(native_identity, dict)
                else None
            )
            executable_verified = bool(
                isinstance(expected, str)
                and isinstance(observed_path, str)
                and Path(expected).resolve() == Path(observed_path).resolve()
            )
        overlay_identity = dict(resolved.get("overlay_identity", {}))
        native_subject_identity = resolved.get("native_subject_identity")
        if not isinstance(native_subject_identity, dict):
            native_subject_identity = None
        native_subject_identity_sha256 = (
            hashlib.sha256(canonical_json(native_subject_identity)).hexdigest()
            if native_subject_identity is not None
            else None
        )
        native_identity_verified = selected is None or (
            isinstance(native_subject_identity, dict)
            and native_subject_identity.get("verified") is True
            and isinstance(
                native_subject_identity.get("executable_sha256"),
                str,
            )
        )
        workspace_binding_identity = {
            "verified": workspace_binding.get("verified") is True,
            "subject": workspace_binding.get("subject"),
            "method": workspace_binding.get("method"),
            "reason_code": workspace_binding.get("reason_code"),
        }
        bwrap_path = Path(_native_environment(context)["BWRAP_BIN"])
        evidence = {
            "runtime_contract": "agents-cookbook-opencode-runtime/v7",
            "opencode_executable_path": executable.payload.get("resolved_path"),
            "opencode_executable_sha256": executable.payload.get("executable_sha256"),
            "opencode_version": executable.payload.get("version"),
            "opencode_executable_metadata": executable.payload.get("file_metadata"),
            "bubblewrap_executable_path": str(bwrap_path),
            "bubblewrap_executable_sha256": _sha256_file(bwrap_path),
            "native_config_sha256": inspection.get("config_sha256"),
            "model": model,
            "provider": provider,
            "agent": context.environment["BENCHMARK_OPENCODE_AGENT"],
            "native_mcp_servers": sorted(
                row["name"] for row in inspection.get("mcp_servers", [])
            ),
            "mcp_shape": inspection.get("mcp_shape"),
            "selected_server": selected,
            "workspace_binding": workspace_binding_identity,
            "native_subject_identity": native_subject_identity,
            "native_subject_identity_sha256": native_subject_identity_sha256,
            "subject_source_identity_sha256": subject_source_identity_sha256,
            "subject_exposure_sha256": overlay_identity.get("subject_exposure_sha256"),
            "native_server_shadowed": overlay_identity.get("native_server_shadowed"),
            "native_server_conflict": overlay_identity.get("native_server_conflict"),
            "overlay_sha256": hashlib.sha256(
                canonical_json(overlay_identity)
            ).hexdigest(),
            "prompt_policy_sha256": hashlib.sha256(
                _SCRATCH_INSTRUCTION.encode("utf-8")
            ).hexdigest(),
        }
        (context.control_root / "opencode-native-evidence.json").write_bytes(
            canonical_json(evidence)
        )
        available = (
            bool(executable.payload.get("available"))
            and isinstance(model, str)
            and bool(model)
            and selected_ready
            and binding_verified
            and native_identity_verified
            and executable_verified
        )
        reason = None
        if not isinstance(model, str) or not model:
            reason = "native OpenCode config does not resolve an explicit build model"
        elif not selected_ready:
            reason = f"benchmark MCP server {selected} is not enabled"
        elif not binding_verified:
            reason = str(
                workspace_binding.get("reason")
                or "benchmark MCP workspace binding is unverified"
            )
        elif not executable_verified:
            reason = (
                f"benchmark MCP {selected} does not use the selected subject executable"
            )
        elif not native_identity_verified:
            reason = "benchmark MCP executable identity is unverified"
        return Observation(
            {
                **executable.payload,
                "available": available,
                "reason": reason,
                "observed_identity": {
                    "version": executable.payload.get("version"),
                    "executable_sha256": executable.payload.get("executable_sha256"),
                    **evidence,
                },
                "model": model,
                "provider": provider,
                "native_config_sha256": evidence["native_config_sha256"],
                "native_mcp_servers": evidence["native_mcp_servers"],
                "base_config_source": "fresh",
                "workspace_binding": workspace_binding,
                "native_subject_identity": native_subject_identity,
                "mcp_exposure": (
                    {
                        "name": selected,
                        "source": "benchmark-subject-exposure",
                        **overlay_identity,
                    }
                    if selected
                    else None
                ),
                "auth_mode": "native-opencode",
            },
            executable.raw,
            {
                **executable.measurements,
                "base_config_reused": False,
            },
        )

    def _load_prepared(
        self,
        context: TrialContext,
    ) -> dict[str, Any]:
        evidence = json.loads(
            (context.control_root / "opencode-native-evidence.json").read_text(
                encoding="utf-8"
            )
        )
        return evidence

    def run(
        self,
        context: TrialContext,
        prompt: str,
        exposed_subject: SubjectAdapter | None,
    ) -> Observation:
        evidence = self._load_prepared(context)
        environment = _native_environment(context)
        admitted_source_identity_sha256 = evidence.get(
            "subject_source_identity_sha256"
        )
        current_source_identity_sha256, source_identity_error = (
            _subject_source_identity_sha256(exposed_subject, context)
        )
        if source_identity_error is not None:
            raise ValueError(source_identity_error)
        if current_source_identity_sha256 != admitted_source_identity_sha256:
            raise ValueError(
                "benchmark subject source authority changed after admission"
            )
        current_opencode_path = str(Path(environment["OPENCODE_BIN"]).resolve())
        if evidence.get("runtime_contract") in {
            "agents-cookbook-opencode-runtime/v6",
            "agents-cookbook-opencode-runtime/v7",
        }:
            current_bwrap_path = str(Path(environment["BWRAP_BIN"]).resolve())
            if (
                current_bwrap_path != evidence.get("bubblewrap_executable_path")
                or _sha256_file(Path(current_bwrap_path))
                != evidence.get("bubblewrap_executable_sha256")
            ):
                raise ValueError("bubblewrap executable authority changed after admission")
        admitted_opencode_path = evidence.get("opencode_executable_path")
        admitted_opencode_sha256 = evidence.get("opencode_executable_sha256")
        try:
            current_opencode_sha256 = _sha256_file(Path(current_opencode_path))
            current_opencode_metadata = executable_file_metadata(
                Path(current_opencode_path)
            )
        except OSError as exc:
            raise ValueError(
                "OpenCode executable authority cannot be revalidated before inference"
            ) from exc
        if (
            current_opencode_path != admitted_opencode_path
            or current_opencode_sha256 != admitted_opencode_sha256
        ):
            baseline = {
                "resolved_path": admitted_opencode_path,
                "sha256": admitted_opencode_sha256,
                "version": evidence.get("opencode_version"),
                "file_metadata": evidence.get("opencode_executable_metadata"),
            }
            observed = {
                "resolved_path": current_opencode_path,
                "sha256": current_opencode_sha256,
                "file_metadata": current_opencode_metadata,
            }
            raise ValueError(
                "OpenCode executable authority changed after admission: "
                + json.dumps(
                    {
                        "changed_fields": _authority_changed_fields(
                            baseline,
                            observed,
                        ),
                        "baseline": baseline,
                        "observed": observed,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )
        title = "agents-cookbook-benchmark:" + context.control_root.parent.name
        prompt_path = context.control_root / "opencode-prompt.txt"
        if self.diagnostic_required_tool is not None:
            prompt = (
                f"Diagnostic tool requirement: call {self.diagnostic_required_tool} "
                "for this repository task before using file search. Inspect source "
                "evidence before answering. The tool call is required even if you "
                "believe you already know the answer.\n\n" + prompt
            )
        prompt_path.write_text(_SCRATCH_INSTRUCTION + prompt, encoding="utf-8")
        run_args = (
            "run-export",
            "--repo",
            str(context.workspace),
            "--benchmark-control-root",
            str(context.control_root),
            "--agent",
            context.environment["BENCHMARK_OPENCODE_AGENT"],
            "--title",
            title,
            "--prompt-file",
            str(prompt_path),
            "--benchmark-subject",
            (
                str(evidence["selected_server"])
                if evidence.get("selected_server")
                else "none"
            ),
        )
        if evidence.get("selected_server"):
            subject_runtime_sha256 = evidence.get(
                "native_subject_identity_sha256"
            )
            if not isinstance(subject_runtime_sha256, str):
                raise ValueError(
                    "benchmark subject runtime authority is unavailable"
                )
            run_args += (
                "--benchmark-exposure-file",
                str(context.control_root / "opencode-benchmark-exposure.json"),
                "--subject-exposure-sha256",
                str(evidence["subject_exposure_sha256"]),
                "--subject-runtime-sha256",
                subject_runtime_sha256,
            )
        run_args += (
            "--native-config-sha256",
            evidence["native_config_sha256"],
            "--opencode-executable-sha256",
            str(evidence["opencode_executable_sha256"]),
        )
        envelope, result = _runtime_call(
            context,
            args=run_args,
            environment=environment,
            timeout_seconds=self.timeout_seconds + 120,
            max_stdout_bytes=self.max_output_bytes,
        )
        post_source_identity_sha256, post_source_identity_error = (
            _subject_source_identity_sha256(exposed_subject, context)
        )
        source_authority_error = (
            post_source_identity_error
            or (
                "benchmark subject source authority changed during inference"
                if post_source_identity_sha256 != admitted_source_identity_sha256
                else None
            )
        )

        exported: dict[str, Any] = {}
        export_raw = ""
        export_error: str | None = None
        export_diagnostic: str | None = None
        session_id: str | None = None
        operation_id: str | None = None
        authority_revalidation: dict[str, Any] | None = None
        run_evidence: dict[str, Any] | None = None
        if envelope is None:
            export_error = "shared OpenCode runtime failed"
        else:
            session = envelope.get("session_id")
            session_id = session if isinstance(session, str) else None
            operation = envelope.get("operation_id")
            operation_id = operation if isinstance(operation, str) else None
            revalidation = envelope.get("authority_revalidation")
            authority_revalidation = (
                revalidation if isinstance(revalidation, dict) else None
            )
            if (
                isinstance(authority_revalidation, dict)
                and authority_revalidation.get("status") == "failed"
            ):
                authority_revalidation = _enrich_authority_revalidation(
                    revalidation=authority_revalidation,
                    context=context,
                    environment=environment,
                    evidence=evidence,
                )
            run_value = envelope.get("run")
            run_evidence = run_value if isinstance(run_value, dict) else None
            runtime_error = envelope.get("error")
            if isinstance(runtime_error, str) and runtime_error:
                export_error = runtime_error
            export_value = envelope.get("export")
            if isinstance(export_value, dict):
                raw = export_value.get("stdout")
                if isinstance(raw, str):
                    export_raw = raw
                if export_value.get("status") == 0:
                    try:
                        exported = _parse_json_object(
                            export_raw,
                            "opencode export",
                        )
                    except (ValueError, json.JSONDecodeError) as exc:
                        export_error = str(exc)
                else:
                    export_error = "opencode export failed"
            elif export_error is None:
                export_error = "unable to locate OpenCode session"
            parse_error = envelope.get("export_parse_error")
            if isinstance(parse_error, str) and parse_error:
                export_error = parse_error
            diagnostic = envelope.get("export_diagnostic")
            if isinstance(diagnostic, str) and diagnostic:
                export_diagnostic = diagnostic
        if source_authority_error is not None:
            export_error = source_authority_error

        selected = evidence.get("selected_server")
        server_names = tuple(evidence.get("native_mcp_servers", []))
        metric_server_names = server_names + (
            (selected,) if isinstance(selected, str) else ()
        )
        metrics = (
            _metrics(
                exported,
                mcp_servers=metric_server_names,
                selected_server=(selected if isinstance(selected, str) else None),
            )
            if exported
            else {
                "subject_tool_configured": isinstance(selected, str),
                "source_read_observability": (
                    "not-authoritatively-exposed-by-opencode-export"
                ),
            }
        )
        export_attempts = envelope.get("export_attempts") if isinstance(envelope, dict) else None
        if type(export_attempts) is int and export_attempts >= 0:
            metrics["session_export_attempts"] = export_attempts
        recovered_context_overflow = (
            isinstance(envelope, dict)
            and envelope.get("recovered_context_overflow") is True
        )
        metrics["recovered_context_overflow"] = recovered_context_overflow
        metrics["duration_ms"] = result.elapsed_ms
        metrics["stdout_bytes"] = len(result.stdout)
        metrics["stderr_bytes"] = len(result.stderr)

        observed_model, observed_provider = (
            _observed_model(exported) if exported else (None, None)
        )
        final_text = envelope.get("final_text") if isinstance(envelope, dict) else None
        if not isinstance(final_text, str) or not final_text:
            final_text = None
        if source_authority_error is not None:
            final_text = None

        model_mismatch = observed_model is not None and observed_model != evidence.get(
            "model"
        )
        if observed_model is None and export_error is None:
            export_error = "OpenCode export did not identify the executed model"
        elif model_mismatch:
            export_error = (
                "OpenCode executed model differs from admitted native config: "
                f"{observed_model} != {evidence.get('model')}"
            )

        failure_reason = _run_failure_reason(
            run_evidence,
            export_error=export_error,
            final_text=final_text,
            recovered_context_overflow=recovered_context_overflow,
        )
        runtime_stderr = result.stderr.decode(
            "utf-8",
            errors="replace",
        )
        failure_diagnostic = (
            _run_failure_diagnostic(
                run_evidence,
                export_diagnostic=export_diagnostic,
                runtime_stderr=runtime_stderr,
                authority_revalidation=authority_revalidation,
            )
            if failure_reason is not None
            else None
        )
        complete = envelope is not None and failure_reason is None
        terminal = (
            {"type": "turn.completed"}
            if complete
            else {
                "type": "turn.failed",
                "reason": failure_reason or "OpenCode run failed",
            }
        )
        tool_calls = int(metrics.get("tool_calls", 0))
        return Observation(
            {
                "available": not result.executable_missing,
                "terminal_event": terminal,
                "terminal_complete": complete,
                "final_message": final_text,
                "jsonl_parse_errors": [],
                "subject_server": selected,
                "tool_available": isinstance(selected, str),
                "model": observed_model or evidence.get("model"),
                "provider": observed_provider or evidence.get("provider"),
                "native_config_sha256": evidence.get("native_config_sha256"),
                "native_mcp_servers": list(server_names),
                "session_id": session_id,
                "operation_id": operation_id,
                "authority_revalidation": authority_revalidation,
                "runtime_contract": evidence.get("runtime_contract"),
                "budget_violation": (
                    (
                        f"tool calls {tool_calls} exceed max_tool_calls "
                        f"{self.max_tool_calls}"
                    )
                    if self.max_tool_calls is not None
                    and tool_calls > self.max_tool_calls
                    else (
                        "agent output exceeded max_output_bytes "
                        f"{self.max_output_bytes}"
                        if result.stdout_truncated
                        else None
                    )
                ),
                "process": result.metrics(),
                "runtime_run": run_evidence,
                "failure_diagnostic": failure_diagnostic,
                "stderr": runtime_stderr,
            },
            export_raw,
            metrics,
        )
