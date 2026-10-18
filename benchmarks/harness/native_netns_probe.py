"""E261: execute an actual network-namespace probe before any model trial work.

The existing bwrap executable presence/configuration is not evidence of
effective kernel support. Require a bounded child in its own namespace,
inspect /proc/self/ns/net and interfaces inside that child, and reject if
a representative non-loopback outbound TCP connection is routable.

This is a *point-in-time host probe*, not independent egress attestation
for the later OpenCode process. Its host observation can be MAC-bound by
the existing E260 host receipt, but no causal, process-origin or
host-wide-exclusivity upgrade follows from it.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from .native_bwrap_execution import (
    runtime_mount_argv, verify_substrate_unchanged,
)

SCHEMA = "agentscookbook.native-netns-kernel-probe.v1"

# Fixed program: no trial prompt, tool output or agent-controlled input.
# Child stdout consists only of bounded JSON diagnostics.
PROBE_CODE = r"""
import errno
import json
import os
import socket

names = sorted(name for _number, name in socket.if_nameindex())
inode = os.stat('/proc/self/ns/net').st_ino
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(('127.0.0.1', 0))
    loopback_bind_ok = listener.getsockname()[0] == '127.0.0.1'
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as outgoing:
    outgoing.settimeout(0.25)
    outbound_errno = outgoing.connect_ex(('198.51.100.1', 443))
print(json.dumps({
    'guest_netns_inode': inode,
    'interfaces': names,
    'loopback_bind_ok': loopback_bind_ok,
    'outbound_errno': outbound_errno,
}, sort_keys=True, separators=(',', ':')))
""".strip()
DENIED_ERRNOS = frozenset((errno.ENETUNREACH, errno.EHOSTUNREACH))
MAX_STDOUT = 4096
MAX_PROBE_SECONDS = 10


def _hex(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_kernel_probe(
    observed: object, *, host_netns_inode: int, bwrap_sha256: str,
    python_sha256: str,
) -> dict[str, Any]:
    """Validate raw child facts, not its self-reported proof/confidence."""
    if (
        not isinstance(observed, dict)
        or set(observed) != {
            "guest_netns_inode", "interfaces",
            "loopback_bind_ok", "outbound_errno",
        }
        or type(host_netns_inode) is not int or host_netns_inode < 1
        or not _hex(bwrap_sha256) or not _hex(python_sha256)
    ):
        raise ValueError("invalid-native-kernel-probe-authority")
    guest = observed["guest_netns_inode"]
    if (
        type(guest) is not int or guest < 1 or guest == host_netns_inode
        or observed["interfaces"] != ["lo"]
        or observed["loopback_bind_ok"] is not True
        or type(observed["outbound_errno"]) is not int
        or observed["outbound_errno"] not in DENIED_ERRNOS
    ):
        raise ValueError("native-netns-effective-isolation-probe-failed")
    return {
        "schema": SCHEMA,
        "kernel_probe_qualified": True,
        "host_netns_inode": host_netns_inode,
        "guest_netns_inode": guest,
        "only_loopback_interface_observed": True,
        "loopback_socket_bind_observed": True,
        "representative_external_tcp_denied": True,
        "bwrap_sha256": bwrap_sha256,
        "python_sha256": python_sha256,
        "probe_program_sha256": hashlib.sha256(PROBE_CODE.encode()).hexdigest(),
        "exclusive_egress_independently_attested": False,
        "native_child_process_origin_proven": False,
    }



def kernel_probe_receipt_valid(value: object, *, expected_bwrap_sha256: str) -> bool:
    """Strict model-free validator for an already signed host observation.

    Signature/custody is checked by native_netns_pilot_readiness. This
    predicate alone does not attest that a kernel probe was executed.
    """
    if not isinstance(value, dict) or set(value) != {
        "schema", "kernel_probe_qualified", "host_netns_inode",
        "guest_netns_inode", "only_loopback_interface_observed",
        "loopback_socket_bind_observed", "representative_external_tcp_denied",
        "bwrap_sha256", "python_sha256", "probe_program_sha256",
        "exclusive_egress_independently_attested",
        "native_child_process_origin_proven",
    }:
        return False
    return (
        value["schema"] == SCHEMA
        and value["kernel_probe_qualified"] is True
        and type(value["host_netns_inode"]) is int
        and value["host_netns_inode"] > 0
        and type(value["guest_netns_inode"]) is int
        and value["guest_netns_inode"] > 0
        and value["host_netns_inode"] != value["guest_netns_inode"]
        and value["only_loopback_interface_observed"] is True
        and value["loopback_socket_bind_observed"] is True
        and value["representative_external_tcp_denied"] is True
        and _hex(value["bwrap_sha256"])
        and value["bwrap_sha256"] == expected_bwrap_sha256
        and _hex(value["python_sha256"])
        and value["probe_program_sha256"] == hashlib.sha256(PROBE_CODE.encode()).hexdigest()
        and value["exclusive_egress_independently_attested"] is False
        and value["native_child_process_origin_proven"] is False
    )


def probe_netns_kernel(substrate: Mapping[str, str]) -> dict[str, Any]:
    """Single bounded probe; failure occurs before run-root/gateway creation."""
    if sys.platform != "linux":
        raise ValueError("native-netns-probe-requires-linux")
    if not isinstance(substrate, Mapping) or not all(
        isinstance(substrate.get(name), str)
        for name in ("bwrap", "python", "bwrap_sha256", "python_sha256")
    ):
        raise ValueError("native-netns-probe-unadmitted-substrate")
    verify_substrate_unchanged(substrate)
    try:
        host_inode = os.stat("/proc/self/ns/net").st_ino
    except OSError as exc:
        raise ValueError("native-host-network-namespace-unavailable") from exc
    command = [
        substrate["bwrap"], "--die-with-parent", "--new-session",
        "--unshare-user", "--unshare-pid", "--unshare-net", "--clearenv",
        *runtime_mount_argv(), "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--chdir", "/", "--setenv", "PATH", os.defpath,
        "--", substrate["python"], "-c", PROBE_CODE,
    ]
    try:
        result = subprocess.run(
            command, cwd="/", env={"PATH": os.defpath},
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=MAX_PROBE_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("native-netns-kernel-probe-unavailable") from exc
    verify_substrate_unchanged(substrate)
    if (
        result.returncode != 0 or not isinstance(result.stdout, bytes)
        or not 0 < len(result.stdout) <= MAX_STDOUT
    ):
        raise ValueError("native-netns-kernel-probe-not-verified")
    try:
        observed = json.loads(result.stdout)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("native-netns-kernel-probe-json-invalid") from exc
    return validate_kernel_probe(
        observed, host_netns_inode=host_inode,
        bwrap_sha256=substrate["bwrap_sha256"],
        python_sha256=substrate["python_sha256"],
    )

def main() -> int:
    from .native_bwrap_execution import admit_netns_substrate
    try:
        report = probe_netns_kernel(admit_netns_substrate())
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({
            "schema": SCHEMA, "kernel_probe_qualified": False,
            "reason": str(exc),
            "exclusive_egress_independently_attested": False,
            "native_child_process_origin_proven": False,
        }, indent=2, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
