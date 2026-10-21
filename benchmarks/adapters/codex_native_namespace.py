"""Keep native absolute plugin paths while giving their processes private state.

This namespace relocates filesystem paths, not native configuration semantics.
Networking and read access to the host remain available; it is not an oracle
secrecy boundary or a claim that an external desktop service has private state.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from benchmarks.harness.model import TrialContext
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


SCHEMA = "evalshot.native-codex-path-namespace.v1"


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def _native_runtime(context: TrialContext, command: tuple[str, ...]) -> list[dict[str, Any]]:
    selected = context.environment.get("BENCHMARK_NATIVE_CODEX_EXECUTABLE")
    if selected is None and command and Path(command[0]).name == "codex":
        selected = command[0]
    if selected is None:
        return []
    executable = Path(selected)
    if not executable.is_absolute() or executable.is_symlink() or not executable.is_file():
        raise ValueError("native Codex namespace runtime is unsupported")
    paths = [executable]
    sibling = executable.with_name("codex-code-mode-host")
    if sibling.exists() or sibling.is_symlink():
        paths.append(sibling)
    result = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("native Codex namespace runtime is unsupported")
        result.append({"path": str(path), "sha256": _sha_file(path), "mode": path.stat().st_mode & 0o777})
    for name in ("CODEX_CLI_PATH", "CODEX_MCP_NODE_PATH"):
        if name not in context.environment:
            continue
        selected_path = Path(context.environment[name])
        if not selected_path.is_absolute() or not selected_path.is_file():
            raise ValueError("native Codex namespace runtime input is unavailable")
        path = selected_path.resolve()
        result.append({
            "path": str(path), "source_path": str(selected_path),
            "source_environment_key": name,
            "sha256": _sha_file(path), "mode": path.stat().st_mode & 0o777,
        })
    return result


def verify_native_namespace(observation: dict[str, Any]) -> bool:
    """Keep the admitted wrapper executable bound around native execution."""
    if not isinstance(observation, dict):
        return False
    try:
        runtime = observation.get("native_runtime", [])
        return (
            observation.get("schema") == SCHEMA
            and observation.get("state") == "observed"
            and all(observation.get(name) is True for name in (
                "source_home_alias_private", "descendant_alias_private", "host_readonly_observed",
            ))
            and observation.get("bwrap_sha256") == hashlib.sha256(_bwrap().read_bytes()).hexdigest()
            and isinstance(runtime, list)
            and all(
                isinstance(row, dict) and Path(row["path"]).is_absolute()
                and not Path(row["path"]).is_symlink()
                and _sha_file(Path(row["path"])) == row["sha256"]
                and Path(row["path"]).stat().st_mode & 0o777 == row["mode"]
                and ("source_path" not in row or Path(row["source_path"]).resolve() == Path(row["path"]))
                for row in runtime
            )
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _bwrap() -> Path:
    selected = shutil.which("bwrap", path=os.defpath)
    if selected is None:
        raise ValueError("native Codex namespace requires bubblewrap")
    executable = Path(selected).resolve()
    if not executable.is_file():
        raise ValueError("native Codex namespace executable is unavailable")
    return executable


def build_native_argv(
    context: TrialContext, command: tuple[str, ...], source_home: Path,
) -> tuple[str, ...]:
    """Wrap the entire native process tree, including its MCP descendants."""
    if not command or any(not isinstance(value, str) or "\0" in value for value in command):
        raise ValueError("native Codex namespace command is invalid")
    if (
        not source_home.is_absolute() or source_home.is_symlink()
        or source_home != source_home.resolve()
    ):
        raise ValueError("native Codex namespace source home is invalid")
    source = source_home.resolve()
    control = context.control_root.resolve()
    workspace = context.workspace.resolve()
    private_value = context.environment.get("CODEX_HOME")
    if not private_value:
        raise ValueError("native Codex namespace private home is missing")
    private = Path(private_value).resolve()
    if (
        source == Path("/") or not source.is_dir()
        or not private.is_relative_to(control) or not private.is_dir()
        or not control.is_dir() or not workspace.is_dir()
        or context.control_root.is_symlink() or context.workspace.is_symlink()
        or Path(private_value).is_symlink()
        or any(source.is_relative_to(path) or path.is_relative_to(source)
               for path in (workspace, control))
    ):
        raise ValueError("native Codex namespace source and trial paths overlap or are unsupported")
    argv = (
        str(_bwrap()), "--die-with-parent", "--new-session", "--unshare-pid",
        "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev",
        "--bind", str(control), str(control),
        "--bind", str(workspace), str(workspace),
        "--bind", str(private), str(source),
    )
    bound_runtime = set()
    for runtime in _native_runtime(context, command):
        path = Path(runtime["path"])
        if path.is_relative_to(source) and path not in bound_runtime:
            # Installed Codex itself may live under CODEX_HOME. Bind only the
            # admitted executable and native code-mode sibling, after the alias.
            argv += ("--ro-bind", str(path), str(path))
            bound_runtime.add(path)
    return (
        *argv, "--setenv", "CODEX_HOME", str(source),
        "--chdir", str(workspace), "--", *command,
    )


_PROBE = r'''
import json, os, pathlib, subprocess, sys
alias, private, marker, token = map(pathlib.Path, sys.argv[1:])
same = os.path.samefile(alias, private) and os.path.samefile(os.environ['CODEX_HOME'], private)
alias_write = alias / token.name
alias_write.write_text('parent')
child = subprocess.run([sys.executable, '-c',
    'import pathlib, sys; pathlib.Path(sys.argv[1]).write_text("child")',
    str(alias_write)], check=False)
descendant = child.returncode == 0 and (private / token.name).read_text() == 'child'
readonly = False
try:
    marker.write_text('unexpected-write')
except OSError:
    readonly = True
alias_write.unlink()
print(json.dumps({'source_home_alias_private': same,
    'descendant_alias_private': descendant, 'host_readonly_observed': readonly}))
'''


def probe_native_namespace(context: TrialContext, source_home: Path) -> dict[str, Any]:
    """Observe the actual kernel mapping before any native plugin can launch."""
    build_native_argv(context, ("/usr/bin/python3",), source_home)
    # Create this witness outside both writable mounts. Never modify the user's
    # source home to manufacture a probe fixture.
    witness_parent = Path("/tmp").resolve()
    if any(witness_parent.is_relative_to(path.resolve()) for path in (
        source_home, context.control_root, context.workspace,
    )):
        raise ValueError("native Codex namespace probe witness location is unsupported")
    with tempfile.TemporaryDirectory(prefix="evalshot-codex-namespace-", dir=witness_parent) as temporary:
        witness = Path(temporary) / "readonly"
        witness.write_text("host")
        token = Path(temporary).name
        argv = build_native_argv(context, (
            "/usr/bin/python3", "-c", _PROBE, str(source_home.resolve()),
            context.environment["CODEX_HOME"], str(witness), token,
        ), source_home)
        result = run_bounded(
            repository_root=context.workspace, argv=argv,
            environment=context.environment, inherit_environment=False,
            limits=ProcessLimits(timeout_seconds=20, max_stdout_bytes=4096, max_stderr_bytes=4096),
        )
        try:
            observed = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            observed = None
        if (
            result.return_code != 0 or result.timed_out or result.executable_missing
            or result.stdout_truncated or result.stderr_truncated
            or not isinstance(observed, dict)
            or set(observed) != {"source_home_alias_private", "descendant_alias_private", "host_readonly_observed"}
            or any(value is not True for value in observed.values())
            or witness.read_text() != "host"
        ):
            raise ValueError("native Codex namespace probe failed")
    return {
        "schema": SCHEMA, "state": "observed", **observed,
        "bwrap_sha256": hashlib.sha256(Path(argv[0]).read_bytes()).hexdigest(),
        "native_runtime": _native_runtime(context, ("/usr/bin/python3",)),
    }
