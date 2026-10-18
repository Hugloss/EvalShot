"""E257/E259: explicit bubblewrap admission and deny-egress launch contract.

Do not use --ro-bind / / or --share-net: both violate the experiment's
native provenance goal. Admit binaries *before work* and generate an exact
mount allowlist for runtime roots, workspace, isolated XDG directories and
one Unix gateway socket directory. OS enforcement must be validated by
a real bubblewrap invocation; this module does not claim an isolation
result merely because an argv array contains --unshare-net.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import Mapping

SCHEMA = "agentscookbook.native-netns-isolation.v1"
RUNTIME_MOUNT_ROOTS = ("/usr", "/lib", "/lib64", "/bin", "/sbin")


def admit_host_only_paths(
    *, workspace: Path, run_root: Path, host_key_file: Path,
    executable: Path, hashmarks_executable: Path,
) -> None:
    """Reject secrets, executable authority or workspaces exposed by mount roots.

    Validate resolved ancestors, not just final symlinks: a symlinked
    intermediate directory can otherwise bypass a lexical path check.
    The same routine runs before creating the private trial directory.
    """
    for candidate in (workspace, run_root, host_key_file,
                      executable, hashmarks_executable):
        if not candidate.is_absolute():
            raise ValueError("native-host-path-not-absolute")
    work, output, key = (x.resolve(strict=False) for x in
                         (workspace, run_root, host_key_file))
    opencode, hashmarks = (x.resolve(strict=False) for x in
                           (executable, hashmarks_executable))
    roots = tuple(Path(root).resolve() for root in RUNTIME_MOUNT_ROOTS)
    if (
        work == output or output.is_relative_to(work)
        or work.is_relative_to(output)
        or any(work.is_relative_to(root) or output.is_relative_to(root)
               or key.is_relative_to(root) for root in roots)
        or key.is_relative_to(work) or key.is_relative_to(output)
        or opencode.is_relative_to(work) or opencode.is_relative_to(output)
        or hashmarks.is_relative_to(work) or hashmarks.is_relative_to(output)
        or host_key_file.is_symlink() or workspace.is_symlink()
        or executable.is_symlink() or hashmarks_executable.is_symlink()
    ):
        raise ValueError("native-host-authority-exposed-to-evaluated-sandbox")




def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1_048_576), b""):
            digest.update(part)
    return digest.hexdigest()


def admit_netns_substrate() -> dict[str, str]:
    if sys.platform != "linux":
        raise ValueError("isolated-native-execution-requires-linux")
    found: dict[str, str] = {}
    for name in ("bwrap",):
        entry = shutil.which(name, path=os.defpath)
        if not entry:
            raise ValueError("missing-native-netns-substrate-" + name)
        binary = Path(entry).resolve()
        if not binary.is_file():
            raise ValueError("unsafe-native-netns-substrate-" + name)
        found[name] = str(binary)
        found[name + "_sha256"] = _sha_file(binary)
    # The host evaluator may itself run with uv's interpreter under
    # ~/.cache/uv or a project venv. Such paths are NOT mounted into the
    # sandbox. The relay needs only stdlib, so pin a real system Python
    # under the existing /usr read-only runtime authority instead.
    candidates = (Path("/usr/bin/python3"), Path(sys.executable))
    interpreter = next((
        path.resolve() for path in candidates
        if path.is_file() and path.resolve().is_relative_to(Path("/usr"))
    ), None)
    if interpreter is None:
        raise ValueError("unsupported-native-netns-python-runtime")
    found["python"] = str(interpreter)
    found["python_sha256"] = _sha_file(interpreter)
    return found


def runtime_mount_argv() -> list[str]:
    """One owned runtime-root whitelist for native launch and kernel probe."""
    out: list[str] = []
    for dirname in RUNTIME_MOUNT_ROOTS:
        entry = Path(dirname)
        if entry.is_symlink():
            target = os.readlink(dirname)
            if target.startswith("/"):
                target = os.path.relpath(target, "/")
            out += ["--symlink", target, dirname]
        elif entry.is_dir():
            out += ["--ro-bind", dirname, dirname]
    return out


def _guest_dirs(path: Path) -> list[str]:
    """Parents for guest mount destinations, excluding OS root."""
    parents: list[str] = []
    current = path.parent
    while current != Path("/"):
        parents.append(str(current))
        current = current.parent
    return list(reversed(parents))


def build_isolated_argv(
    substrate: Mapping[str, str], *, executable: Path,
    workspace: Path, run_root: Path, gateway_dir: Path,
    environment: Mapping[str, str], command: list[str],
    worker_port: int | None,
    hashmarks_executable: Path | None = None,
    use_native_mcp_bridge: bool = False,
) -> list[str]:
    if (not command or not executable.is_absolute()
            or not workspace.is_absolute() or not run_root.is_absolute()
            or not gateway_dir.is_absolute()
            or run_root.is_relative_to(workspace)
            or gateway_dir != run_root / "gateway"
            or any(path.is_symlink() for path in (workspace, run_root, executable))):
        raise ValueError("unsafe-native-sandbox-authority")
    if not isinstance(substrate, Mapping) or not all(
        isinstance(substrate.get(name), str) for name in ("bwrap", "python")
    ):
        raise ValueError("native-substrate-not-admitted")
    if worker_port is not None and (
        type(worker_port) is not int or not 1024 <= worker_port <= 65535
    ):
        raise ValueError("invalid-native-worker-port")
    if hashmarks_executable is not None and (
        not hashmarks_executable.is_absolute()
        or not hashmarks_executable.is_file()
        or hashmarks_executable.is_symlink()
        or hashmarks_executable == executable
    ):
        raise ValueError("unadmitted-native-hashmarks-executable")
    # These runtime roots are system-owned and read-only to the child.
    # Never mount root, arbitrary /etc, user home, or host /var.
    argv = [
        substrate["bwrap"], "--die-with-parent", "--new-session",
        "--unshare-user", "--unshare-pid", "--unshare-net",
        "--clearenv",
    ]
    argv += runtime_mount_argv()
    # Sandbox temporary files are not the host's /tmp. The actual
    # workspace/XDG destinations are mounted after creating these roots.
    argv += ["--tmpfs", "/tmp", "--tmpfs", "/run",
             "--proc", "/proc", "--dev", "/dev"]
    mounts = [
        (workspace, workspace, "rw"),
        (run_root / "home", run_root / "home", "rw"),
        (run_root / "config", run_root / "config", "rw"),
        (run_root / "cache", run_root / "cache", "rw"),
        (run_root / "data", run_root / "data", "rw"),
        (run_root / "state", run_root / "state", "rw"),
        (run_root / "opencode.json", run_root / "opencode.json", "ro"),
        (gateway_dir, Path("/run/gateway"), "rw"),
    ]
    if not executable.is_relative_to(Path("/usr")):
        mounts.append((executable, executable, "ro"))
    if hashmarks_executable is not None and not hashmarks_executable.is_relative_to(Path("/usr")):
        mounts.append((hashmarks_executable, hashmarks_executable, "ro"))
    if use_native_mcp_bridge:
        if hashmarks_executable is None:
            raise ValueError("native-mcp-bridge-requires-hashmarks")
        for source_name, guest_name in (
            ("native_mcp_stdio_bridge.py", "/run/native-mcp-bridge.py"),
            ("native_mcp_bridge_contract.py", "/run/native_mcp_bridge_contract.py"),
        ):
            mounts.append((
                Path(__file__).with_name(source_name), Path(guest_name), "ro",
            ))
        mounts.append((
            run_root / "native-mcp-contract.json",
            Path("/run/native-mcp-contract.json"), "ro",
        ))
    if worker_port is not None:
        worker_file = Path(__file__).with_name("native_netns_worker.py")
        mounts.append((worker_file, Path("/run/native-netns-worker.py"), "ro"))
    made: set[str] = set()
    for src, dst, mode in mounts:
        if not src.exists() or src.is_symlink():
            raise ValueError("native-sandbox-mount-source-missing-or-symlink")
        for parent in _guest_dirs(dst):
            if parent not in made and not parent.startswith(("/usr/", "/lib/", "/bin/", "/sbin/")):
                argv += ["--dir", parent]
                made.add(parent)
        argv += ["--bind" if mode == "rw" else "--ro-bind", str(src), str(dst)]
    argv += ["--chdir", str(workspace)]
    # All env keys originate from the host's fixed allowlist; never inherit
    # the parent env (especially API keys, SSH sockets, HOME, proxy variables).
    allowed_env = frozenset((
        "HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
        "XDG_STATE_HOME", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR",
        "OPENCODE_DISABLE_AUTOUPDATE", "OPENCODE_AUTO_SHARE",
        "BENCHMARK_NATIVE_GATEWAY_TOKEN", "PATH",
    ))
    if set(environment) != allowed_env:
        raise ValueError("unadmitted-native-execution-environment")
    for name, value in sorted(environment.items()):
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("invalid-native-sandbox-environment")
        argv += ["--setenv", name, value]
    if worker_port is not None:
        argv += [
            "--", substrate["python"], "/run/native-netns-worker.py",
            "--port", str(worker_port),
            "--unix", "/run/gateway/upstream.sock",
            "--", *command,
        ]
    else:
        argv += ["--", *command]
    return argv


def verify_substrate_unchanged(substrate: Mapping[str, str]) -> None:
    for key in ("bwrap", "python"):
        path = Path(substrate[key])
        if not path.is_file() or _sha_file(path) != substrate[key + "_sha256"]:
            raise ValueError("native-sandbox-executable-changed-during-trial")
