"""Opaque native Codex inputs and redacted, model-free configuration observation.

Only declared configuration and recognized guidance are copied. Conversation,
memory, database, cache and session state never seed an independent trial.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tomllib
from typing import Any

from benchmarks.harness.model import TrialContext
from benchmarks.harness.runtime_authority import PRIVATE_STATE_ENV_KEYS
from benchmarks.adapters.codex_native_namespace import build_native_argv
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_private_state(context: TrialContext) -> None:
    """Reject a shared source home before even native CLI startup can write it."""
    required = {"CODEX_HOME", "HOME", "TMPDIR", "TMP", "TEMP", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"}
    source_value = context.environment.get("BENCHMARK_NATIVE_CODEX_HOME")
    if source_value:
        source, control = Path(source_value).resolve(), context.control_root.resolve()
        if source.is_relative_to(control) or control.is_relative_to(source):
            raise ValueError("native Codex source and trial state overlap")
    for name in PRIVATE_STATE_ENV_KEYS:
        selected = context.environment.get(name)
        if selected is None and name not in required:
            continue
        if not selected or not Path(selected).is_absolute() or not Path(selected).resolve().is_relative_to(context.control_root.resolve()):
            raise ValueError("native Codex state must use independent trial state roots")


def native_cli_argv(context: TrialContext, command: tuple[str, ...]) -> tuple[str, ...]:
    validate_private_state(context)
    if context.environment.get("BENCHMARK_NATIVE_NAMESPACE") == "1":
        source = context.environment.get("BENCHMARK_NATIVE_CODEX_HOME")
        if not source:
            raise ValueError("native Codex namespace source is unavailable")
        return build_native_argv(context, command, Path(source))
    return command


def _unsupported_inputs(config: dict[str, Any], prefix: str = "") -> list[str]:
    """Reject input dependencies and shared state roots this transport cannot bind."""
    unsupported = []
    for key, value in config.items():
        path = f"{prefix}.{key}" if prefix else key
        if key == "mcp_servers":
            # Native MCP transports are retained byte-for-byte. Their active
            # capabilities are qualified by native inventory in the private
            # process namespace after the controlled-subject overlay.
            continue
        if key in {"hooks", "notify"} and value:
            unsupported.append(path)
        elif key in {"sqlite_home", "log_dir"}:
            unsupported.append(path)
        elif (key in {"file", "path", "directory"} or key.endswith(("_file", "_path", "_dir", "_home"))) and isinstance(value, str) and value:
            unsupported.append(path)
        elif isinstance(value, dict):
            unsupported.extend(_unsupported_inputs(value, path))
    return unsupported


def _input_paths(source: Path) -> list[Path]:
    """Name exactly the supported input scope; runtime/auth files are separate."""
    root_config = source / "config.toml"
    files = [root_config] if root_config.exists() or root_config.is_symlink() else []
    for path in source.glob("*.config.toml"):
        files.append(path)
        if len(files) > 512:
            raise ValueError("native Codex input count exceeds observation bound")
    for name in ("managed_config.toml", "requirements.toml"):
        if (source / name).exists() or (source / name).is_symlink():
            files.append(source / name)
    for name in ("AGENTS.md", "AGENTS.override.md"):
        if (source / name).exists() or (source / name).is_symlink():
            files.append(source / name)
    for name in ("rules", "skills"):
        directory = source / name
        if directory.is_symlink():
            raise ValueError(f"unsupported native Codex input symlink: {name}")
        if directory.is_dir():
            visited = 0
            for path in directory.rglob("*"):
                visited += 1
                if visited > 4096:
                    raise ValueError("native Codex guidance scope exceeds observation bound")
                if path.is_symlink():
                    raise ValueError(f"unsupported native Codex input symlink in {name}")
                if path.is_dir() and path.name in {"node_modules", ".venv", "site-packages"}:
                    raise ValueError(f"unsupported native Codex guidance dependency in {name}")
                if path.is_file():
                    files.append(path)
                    if len(files) > 512:
                        raise ValueError("native Codex input count exceeds observation bound")
    return files


def copy_native_auth(context: TrialContext, source: Path) -> bool:
    """Transport opaque selected native credentials after static admission."""
    validate_private_state(context)
    target = Path(context.environment["CODEX_HOME"])
    auth = source / "auth.json"
    copied = auth.is_file()
    if auth.is_symlink() or (auth.exists() and (not copied or auth.stat().st_size > 1_048_576)):
        raise ValueError("unsupported native Codex auth input")
    if copied:
        shutil.copyfile(auth, target / "auth.json")
        (target / "auth.json").chmod(0o600)
    return copied


def snapshot_native_inputs(context: TrialContext, source: Path, *, copy_auth: bool = True) -> dict[str, Any]:
    """Copy bounded native input bytes without serializing credentials in receipts."""
    source = source.expanduser().resolve()
    validate_private_state(context)
    target = Path(context.environment["CODEX_HOME"]).resolve()
    if source == target or not target.is_relative_to(context.control_root.resolve()):
        raise ValueError("native Codex state must use an independent trial CODEX_HOME")
    if source.exists() and not source.is_dir():
        raise ValueError("native Codex input home is not a directory")
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o700)
    files = _input_paths(source)
    manifest = {}
    total = 0
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError("native Codex config input is missing or unsupported")
        size = path.stat().st_size
        total += size
        if size > 1_048_576 or total > 8_388_608:
            raise ValueError("native Codex input bytes exceed observation bound")
        raw = path.read_bytes()
        if len(raw) != size:
            raise ValueError("native Codex input changed during snapshot")
        relative = path.relative_to(source).as_posix()
        configuration = relative.endswith(".toml") and "/" not in relative
        if configuration:
            config = tomllib.loads(raw.decode("utf-8"))
            unsupported = _unsupported_inputs(config)
            if unsupported:
                raise ValueError("unsupported native Codex input dependencies: " + ", ".join(sorted(unsupported)))
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        executable = not configuration and os.access(path, os.X_OK)
        private_mode = 0o700 if executable else 0o600
        destination.chmod(private_mode)
        identity = hashlib.sha256(raw).hexdigest()
        if hashlib.sha256(path.read_bytes()).hexdigest() != identity:
            raise ValueError("native Codex input changed during snapshot")
        if not configuration and os.access(path, os.X_OK) != executable:
            raise ValueError("native Codex guidance mode changed during snapshot")
        manifest[relative] = {"sha256": identity, "mode": private_mode}
    auth_copied = copy_native_auth(context, source) if copy_auth else False
    return {
        "schema": "evalshot.native-codex-inputs.v1",
        "input_scope": "native-config-and-recognized-guidance; no conversation or state clone",
        "files": manifest,
        "identity": _digest(manifest),
        "auth_file_copied": auth_copied,
        "auth_mode": "native-selected-storage",
        "private_state": True,
    }


def native_input_integrity_reason(
    context: TrialContext, manifest: dict[str, Any]
) -> str | None:
    """Classify a failed private-input seal without exposing paths or config bytes.

    The same observation governs both qualification and the public boolean
    verifier; a diagnostic must never excuse a changed admitted input.
    """
    root = Path(context.environment["CODEX_HOME"])
    if not isinstance(manifest, dict):
        return "manifest-invalid"
    files = manifest.get("files")
    if (
        manifest.get("schema") != "evalshot.native-codex-inputs.v1"
        or not isinstance(files, dict)
        or any(
            not isinstance(name, str)
            or not isinstance(evidence, dict)
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            for name, evidence in files.items()
        )
        or manifest.get("identity") != _digest(files)
    ):
        return "manifest-invalid"
    try:
        observed = {
            path.relative_to(root).as_posix() for path in _input_paths(root)
        }
        if observed != set(files):
            return "input-membership-drift"
        for name, evidence in files.items():
            path = root / name
            if path.is_symlink() or not path.is_file():
                return "input-type-drift"
            if stat.S_IMODE(path.stat().st_mode) != evidence.get("mode"):
                return "input-mode-drift"
            if hashlib.sha256(path.read_bytes()).hexdigest() != evidence.get(
                "sha256"
            ):
                return "input-content-drift"
    except (OSError, ValueError, KeyError, TypeError):
        return "input-observation-unavailable"
    return None


def verify_native_inputs(context: TrialContext, manifest: dict[str, Any]) -> bool:
    """A classified mismatch always rejects admission or the terminal result."""
    return native_input_integrity_reason(context, manifest) is None

def validate_native_launch(context: TrialContext, command: str, timeout_seconds: float) -> None:
    """Prove support for the native flags this adapter itself emits."""
    validate_private_state(context)
    for args, required in (
        (("--help",), ("--no-daemon",)),
        (("exec", "--help"), ("--ephemeral", "--approve-for-me", "--json")),
    ):
        result = run_bounded(
            repository_root=context.workspace, argv=native_cli_argv(context, (command, *args)),
            environment=context.environment,
            limits=ProcessLimits(timeout_seconds=min(30.0, timeout_seconds), max_stdout_bytes=200_000, max_stderr_bytes=200_000),
            inherit_environment=False,
        )
        help_text = result.stdout.decode("utf-8", errors="replace")
        if result.executable_missing or result.timed_out or result.return_code != 0 or result.stdout_truncated or result.stderr_truncated or any(flag not in help_text for flag in required):
            raise ValueError("native Codex launch flags are not supported by observed CLI help")


def observe_native_config(context: TrialContext, command: str, timeout_seconds: float) -> dict[str, Any]:
    """Use the CLI's resolved observation, without deriving config precedence.

    Unsupported diagnostic versions remain unknown. Doctor's unrelated network
    or database health failures do not replace its typed configuration check.
    Raw doctor output and authentication details are never retained.
    """
    unknown = {
        "state": "unknown", "model": None, "provider": None, "scope": None,
        "reason": "native-doctor-observation-unavailable",
    }
    try:
        validate_private_state(context)
    except ValueError:
        return {**unknown, "reason": "native-private-state-not-established"}
    result = run_bounded(
        repository_root=context.workspace,
        argv=native_cli_argv(context, (command, "doctor", "--json")),
        environment=context.environment,
        limits=ProcessLimits(timeout_seconds=min(30.0, timeout_seconds), max_stdout_bytes=1_000_000, max_stderr_bytes=200_000),
        inherit_environment=False,
    )
    if result.executable_missing or result.timed_out or result.stdout_truncated or result.stderr_truncated:
        return unknown
    try:
        report = json.loads(result.stdout)
        check = report["checks"]["config.load"]
        details = check["details"]
        if type(report.get("schemaVersion")) is not int or report["schemaVersion"] != 1 or check.get("status") != "ok" or not isinstance(details, dict):
            return unknown
    except (ValueError, KeyError, TypeError):
        return unknown
    fields = {
        key: value if isinstance(value, str) and value.strip()
        and value.strip().strip("<>()").lower() not in {"unknown", "none", "default", "not set", "unset", "null"}
        else None
        for key, value in (
            ("model", details.get("model")),
            ("provider", details.get("model provider")),
            ("scope", details.get("configuration scope")),
        )
    }
    auth = report["checks"].get("auth.credentials")
    auth_state = auth.get("status") if isinstance(auth, dict) else None
    if not isinstance(auth_state, str) or auth_state not in {"ok", "warn", "fail"}:
        auth_state = None
    return {
        "state": "observed" if fields["model"] is not None and fields["provider"] is not None else "unknown",
        **fields,
        "reason": None if fields["model"] is not None and fields["provider"] is not None else "native-doctor-selection-not-reported",
        "auth_state": auth_state,
    }
