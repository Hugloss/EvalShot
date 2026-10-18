"""E265-E268: exercise the actual native sandbox's worker and mount authority.

Runs a fixed stdlib probe under the *same* bubblewrap wrapper as OpenCode,
including the HTTP/Unix relay worker, before any model calls. It tests
in-sandbox visibility and inability to write the read-only gateway config.
It does not establish independent process lineage, full egress auditing, or
MCP/provider packet parity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .native_bwrap_execution import build_isolated_argv, verify_substrate_unchanged

SCHEMA = "agentscookbook.native-runtime-mount-preflight.v1"
MAX_SECONDS = 20
HEX = re.compile(r"[0-9a-f]{64}\Z")

# The sole executable probe body: its arguments are paths/identities, not code.
# The guest cannot see the host's private key or upstream credential.
GUEST_PROBE = r"""
import json
import os
import socket
import stat
import sys
import uuid

workspace, config, hashmarks, opencode, host_key, bridge_mode, hashmarks_sha = sys.argv[1:]
if bridge_mode not in ('direct', 'verified-bridge'):
    sys.exit(40)
if not os.path.isdir(workspace) or not os.path.lexists(os.path.join(workspace, '.git')):
    sys.exit(41)
if not os.path.isfile(config) or not os.path.isfile(hashmarks) or not os.path.isfile(opencode):
    sys.exit(42)
if os.path.lexists(host_key):
    sys.exit(43)
if any(name.endswith('_API_KEY') or name in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','AWS_SECRET_ACCESS_KEY')
       for name in os.environ):
    sys.exit(44)
with open(config, encoding='utf-8') as stream:
    cfg = json.load(stream)
if cfg.get('enabled_providers') != ['agentscookbook-captured']:
    sys.exit(45)
mcp = cfg.get('mcp', {}).get('hashmarks', {}).get('command')
if bridge_mode == 'verified-bridge':
    expected = [
        sys.executable, '/run/native-mcp-bridge.py',
        '--hashmarks', hashmarks, '--sha256',
        hashmarks_sha,
        '--workspace', workspace,
        '--contract', '/run/native-mcp-contract.json',
    ]
    if not all(os.path.isfile(f) for f in (
        '/run/native-mcp-bridge.py', '/run/native_mcp_bridge_contract.py',
        '/run/native-mcp-contract.json',
    )):
        sys.exit(46)
    if mcp != expected:
        sys.exit(46)
else:
    if mcp != [hashmarks, '--workspace', workspace, 'mcp']:
        sys.exit(46)
try:
    fd = os.open(config, os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0))
except OSError:
    pass
else:
    os.close(fd)
    sys.exit(47)
for name in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
    folder = os.environ.get(name)
    if not folder or not os.path.isdir(folder):
        sys.exit(48)
    test = os.path.join(folder, '.native-probe-' + uuid.uuid4().hex)
    try:
        fd = os.open(test, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.unlink(test)
    except OSError:
        sys.exit(49)
sock = '/run/gateway/upstream.sock'
try:
    kind = os.stat(sock).st_mode
except OSError:
    sys.exit(50)
if not stat.S_ISSOCK(kind):
    sys.exit(51)
if os.stat('/proc/self/ns/net').st_ino == 0:
    sys.exit(52)
""".strip()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def inspect_mount_probe(proof: object, *, expected_bwrap_sha256: str,
                        expected_config_sha256: str) -> bool:
    """Host MAC is independently checked by the paired-pilot auditor."""
    if not isinstance(proof, dict) or set(proof) != {
        "schema", "runtime_mount_probe_qualified", "bwrap_sha256",
        "python_sha256", "worker_sha256", "probe_program_sha256",
        "config_sha256", "workspace_binding_observed",
        "readonly_config_observed", "isolated_xdg_write_observed",
        "gateway_socket_observed", "host_key_not_mounted_observed",
        "provider_model_execution_observed", "independent_process_origin_proven",
    }:
        return False
    return (
        proof["schema"] == SCHEMA
        and proof["runtime_mount_probe_qualified"] is True
        and all(isinstance(proof[name], str) and HEX.fullmatch(proof[name])
                for name in ("bwrap_sha256", "python_sha256", "worker_sha256",
                             "probe_program_sha256", "config_sha256"))
        and proof["bwrap_sha256"] == expected_bwrap_sha256
        and proof["config_sha256"] == expected_config_sha256
        and proof["probe_program_sha256"] == _sha(GUEST_PROBE.encode("utf-8"))
        and proof["workspace_binding_observed"] is True
        and proof["readonly_config_observed"] is True
        and proof["isolated_xdg_write_observed"] is True
        and proof["gateway_socket_observed"] is True
        and proof["host_key_not_mounted_observed"] is True
        and proof["provider_model_execution_observed"] is False
        and proof["independent_process_origin_proven"] is False
    )


def verify_worker_source_unchanged(proof: Mapping[str, Any]) -> None:
    """Verify the exact worker source pinned by the successful guest probe."""
    worker = Path(__file__).with_name("native_netns_worker.py")
    if (not isinstance(proof, Mapping)
            or not isinstance(proof.get("worker_sha256"), str)
            or not HEX.fullmatch(proof["worker_sha256"])
            or worker.is_symlink() or not worker.is_file()
            or _sha(worker.read_bytes()) != proof["worker_sha256"]):
        raise ValueError("native-runtime-worker-source-drift")


def probe_native_mounts(
    substrate: Mapping[str, str], *, executable: Path, hashmarks: Path,
    workspace: Path, run_root: Path, host_key_file: Path,
    environment: Mapping[str, str], port: int, config_sha256: str,
    use_native_mcp_bridge: bool = False,
) -> dict[str, Any]:
    """No provider or native agent is launched; failure blocks its launch."""
    verify_substrate_unchanged(substrate)
    if (not isinstance(config_sha256, str)
            or not HEX.fullmatch(config_sha256)
            or not isinstance(port, int) or not 1024 <= port <= 65535):
        raise ValueError("unadmitted-native-mount-probe-identity")
    worker = Path(__file__).with_name("native_netns_worker.py")
    if worker.is_symlink() or not worker.is_file():
        raise ValueError("missing-native-worker-source-authority")
    original_worker_sha = _sha(worker.read_bytes())
    command = [substrate["python"], "-c", GUEST_PROBE,
               str(workspace), str(run_root / "opencode.json"),
               str(hashmarks), str(executable), str(host_key_file),
               "verified-bridge" if use_native_mcp_bridge else "direct",
               hashlib.sha256(hashmarks.read_bytes()).hexdigest()]
    argv = build_isolated_argv(
        substrate, executable=executable, workspace=workspace,
        run_root=run_root, gateway_dir=run_root / "gateway",
        environment=environment, command=command, worker_port=port,
        hashmarks_executable=hashmarks,
        use_native_mcp_bridge=use_native_mcp_bridge,
    )
    try:
        response = subprocess.run(
            argv, cwd=workspace, env=dict(environment),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=MAX_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("native-runtime-mount-preflight-unavailable") from exc
    verify_substrate_unchanged(substrate)
    if _sha(worker.read_bytes()) != original_worker_sha:
        raise ValueError("native-runtime-worker-source-changed")
    if response.returncode != 0:
        raise ValueError("native-runtime-mount-preflight-failed")
    proof = {
        "schema": SCHEMA,
        "runtime_mount_probe_qualified": True,
        "bwrap_sha256": substrate["bwrap_sha256"],
        "python_sha256": substrate["python_sha256"],
        "worker_sha256": original_worker_sha,
        "probe_program_sha256": _sha(GUEST_PROBE.encode("utf-8")),
        "config_sha256": config_sha256,
        "workspace_binding_observed": True,
        "readonly_config_observed": True,
        "isolated_xdg_write_observed": True,
        "gateway_socket_observed": True,
        "host_key_not_mounted_observed": True,
        "provider_model_execution_observed": False,
        "independent_process_origin_proven": False,
    }
    if not inspect_mount_probe(
        proof, expected_bwrap_sha256=substrate["bwrap_sha256"],
        expected_config_sha256=config_sha256,
    ):
        raise ValueError("native-runtime-mount-preflight-not-verified")
    return proof
