"""E249-E251: trial-scoped OpenCode V1 compatible provider transport bridge.

A pinned OpenCode executable can use a custom, allowlisted model whose
OpenAI-compatible provider points at this host-owned loopback server. The
server observes actual inbound request bytes and enforces the frozen packet
in the one supported tool-result request before forwarding HTTPS upstream.

This is an OPT-IN separate runner, not an interception of a user's ordinary
OpenCode installation. Loopback bearer authentication is NOT OS process
attestation; native-agent provenance remains unqualified until independent
process-bound network confinement and a native session trace are verified.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import socketserver
import struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .host_input_attestation import canonical
from .trusted_host_capture import TrustedModelRequestCapture
from .trusted_http_provider import (
    MAX_RESPONSE_BYTES, _RejectRedirect, _endpoint,
    dispatch_verified_chat_response, dispatch_verified_chat_stream_response,
    validate_sse_response,
)
from .trusted_treatments import assigned_cell, select_treatment, verify_manifest
from .native_chat_transport_parity import require_chat_tool_content
from .native_chat_request_integrity import require_assistant_tool_ancestry
from .native_provider_tool_provenance import (
    extract_provider_issued_tool, require_issued_tool_continuation,
)

SCHEMA = "agentscookbook.opencode-v1-gateway-launch.v1"
MAX_REQUEST_BYTES = 8_388_608
PROVIDER_ID = "agentscookbook-captured"
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("untrusted-opencode-executable")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for piece in iter(lambda: stream.read(1_048_576), b""):
            digest.update(piece)
    return digest.hexdigest()


def admit_opencode_binary(executable: Path, *, expected_sha256: str,
                          expected_version: str) -> dict[str, str]:
    """Executable and version are both native observed authorities."""
    if (not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or not isinstance(expected_version, str)
            or not 0 < len(expected_version) <= 128):
        raise ValueError("missing-versioned-opencode-native-authority")
    actual = sha256_file(executable)
    if actual != expected_sha256:
        raise ValueError("opencode-executable-drift")
    try:
        result = subprocess.run(
            [str(executable), "--version"], capture_output=True, timeout=5,
            check=False, env={"PATH": os.defpath},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("opencode-version-unavailable") from exc
    if (result.returncode != 0 or result.stderr
            or len(result.stdout) > 1024
            or result.stdout.decode("utf-8", "replace").strip() != expected_version):
        raise ValueError("opencode-version-drift-or-ambiguous-output")
    # Resist time-of-check to time-of-use for in-place executable mutation.
    if sha256_file(executable) != expected_sha256:
        raise ValueError("opencode-executable-drift-after-version-probe")
    return {
        "version": expected_version, "executable_sha256": actual,
        "resolved_path": str(executable.resolve()),
    }


def gateway_config(*, port: int, model_id: str,
                   hashmarks_executable: Path | None = None,
                   workspace: Path | None = None,
                   bridge_python: Path | None = None,
                   bridge_hashmarks_sha256: str | None = None) -> dict[str, Any]:
    """Only one visible provider; no inherited provider fallback in overlay.

    The caller must also isolate the home and XDG config/data directories:
    OpenCode merges managed/global/project settings, so this JSON alone does
    not prove an effective configuration or exclusive network egress.
    """
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid-local-gateway-port")
    if not isinstance(model_id, str) or not MODEL_NAME.fullmatch(model_id):
        raise ValueError("invalid-gateway-model-id")
    if (hashmarks_executable is None) != (workspace is None):
        raise ValueError("incomplete-pinned-hashmarks-mcp-authority")
    if hashmarks_executable is not None and (
        not hashmarks_executable.is_absolute()
        or not workspace.is_absolute()
        or hashmarks_executable.is_symlink()
        or not hashmarks_executable.is_file()
        or not workspace.is_dir()
    ):
        raise ValueError("untrusted-pinned-hashmarks-mcp-authority")
    bridge_enabled = bridge_python is not None or bridge_hashmarks_sha256 is not None
    if bridge_enabled and (
        hashmarks_executable is None or bridge_python is None
        or not bridge_python.is_absolute()
        or not bridge_python.is_file()
        or not isinstance(bridge_hashmarks_sha256, str)
        or len(bridge_hashmarks_sha256) != 64
        or any(c not in "0123456789abcdef" for c in bridge_hashmarks_sha256)
    ):
        raise ValueError("incomplete-native-mcp-bridge-configuration")
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "enabled_providers": [PROVIDER_ID],
        "model": PROVIDER_ID + "/" + model_id,
        "provider": {
            PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Benchmark Captured Provider",
                "options": {
                    "baseURL": f"http://127.0.0.1:{port}/v1",
                    "apiKey": "{env:BENCHMARK_NATIVE_GATEWAY_TOKEN}",
                },
                "models": {
                    model_id: {"name": "Pinned benchmark model"},
                },
            },
        },
    }
    if hashmarks_executable is not None:
        payload["mcp"] = {
            "hashmarks": {
                "type": "local",
                "command": (
                    [
                        str(bridge_python), "/run/native-mcp-bridge.py",
                        "--hashmarks", str(hashmarks_executable),
                        "--sha256", bridge_hashmarks_sha256,
                        "--workspace", str(workspace),
                        "--contract", "/run/native-mcp-contract.json",
                    ] if bridge_enabled else [
                        str(hashmarks_executable), "--workspace",
                        str(workspace), "mcp",
                    ]
                ),
                "enabled": True,
            },
        }
    return payload


def write_gateway_config(path: Path, config: Mapping[str, Any]) -> str:
    if path.is_symlink():
        raise ValueError("symlinked-gateway-config")
    data = canonical(config) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return hashlib.sha256(data).hexdigest()


def _forward_pre_evidence(body: bytes, *, upstream: str,
                          api_key: str, timeout_seconds: int,
                          stream: bool = False) -> bytes:
    """A tool-selection turn may precede Hashmarks; not host delivery proof."""
    opener = build_opener(ProxyHandler({}), _RejectRedirect(),
                          HTTPSHandler())
    request = Request(upstream, data=body, method="POST", headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    })
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            code = response.status
            output = response.read(MAX_RESPONSE_BYTES + 1)
            headers = getattr(response, "headers", None)
            mime = headers.get("Content-Type", "") if headers is not None else ""
        if (type(code) is not int or not 200 <= code < 300
                or not 0 < len(output) <= MAX_RESPONSE_BYTES):
            raise ValueError("unqualified-provider-response")
        if stream:
            if (not isinstance(mime, str)
                    or not mime.lower().startswith("text/event-stream")
                    or not validate_sse_response(output)):
                raise ValueError("unqualified-streaming-provider-response")
        else:
            doc = json.loads(output)
            if (not isinstance(doc, dict) or not isinstance(doc.get("choices"), list)
                    or not doc["choices"]):
                raise ValueError("unqualified-provider-response")
    except Exception:
        raise ValueError("pre-evidence-provider-request-failed") from None
    return output


class NativeOpenCodeGateway:
    """One trial, one Hashmarks result and one post-result provider request."""

    def __init__(
        self, manifest: Mapping[str, Any], *, trial_id: str,
        host_key_file: Path, host_identity: str,
        current: Mapping[str, Any], replaced: Mapping[str, Any] | None,
        gateway_token: str, upstream: str, approved_origin: str,
        upstream_api_key: str, catalog_sha256: str,
        oracle_sha256: str, workspace_sha256: str,
        timeout_seconds: int = 60,
        enforce_chat_tool_wire_contract: bool = False,
    ) -> None:
        self.manifest = verify_manifest(dict(manifest))
        cells = [row for row in self.manifest["assignments"]
                 if row["trial_id"] == trial_id]
        if len(cells) != 1:
            raise ValueError("trial-not-in-frozen-manifest")
        self.cell = cells[0]
        if self.cell["harness"] not in ("opencode", "opencode-native"):
            raise ValueError("not-a-native-opencode-trial")
        assigned_cell(self.manifest, trial_id=trial_id,
                      harness=self.cell["harness"], task=self.cell["task"],
                      replicate=self.cell["replicate"], arm=self.cell["arm"])
        if (not isinstance(gateway_token, str) or not 32 <= len(gateway_token) <= 256
                or any(ch in gateway_token for ch in ("\n", "\r", " "))
                or not isinstance(upstream_api_key, str) or not upstream_api_key
                or any(ch in upstream_api_key for ch in ("\n", "\r"))):
            raise ValueError("untrusted-gateway-authentication")
        self.upstream = _endpoint(upstream, approved_origin=approved_origin)
        self.approved_origin = approved_origin
        self.upstream_api_key = upstream_api_key
        self.token = gateway_token
        self.timeout_seconds = timeout_seconds
        self.enforce_chat_tool_wire_contract = enforce_chat_tool_wire_contract
        self.current = current
        self.replaced = replaced
        self.selected = select_treatment(
            self.manifest, trial_id=trial_id, harness=self.cell["harness"],
            task=self.cell["task"], replicate=self.cell["replicate"],
            arm=self.cell["arm"], current=current, replaced=replaced,
        )
        self.capture = TrustedModelRequestCapture(
            self.manifest, trial_id=trial_id, harness=self.cell["harness"],
            task=self.cell["task"], replicate=self.cell["replicate"],
            arm=self.cell["arm"], host_identity=host_identity,
            host_key_file=host_key_file,
        )
        self.catalog_sha256 = catalog_sha256
        self.oracle_sha256 = oracle_sha256
        self.workspace_sha256 = workspace_sha256
        for field in (catalog_sha256, oracle_sha256, workspace_sha256):
            if not re.fullmatch(r"[0-9a-f]{64}", field):
                raise ValueError("unpinned-native-run-context")
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._unix_server: socketserver.ThreadingUnixStreamServer | None = None
        self._unix_thread: threading.Thread | None = None
        self._unix_path: Path | None = None
        self.peer_credentials_observed: list[tuple[int, int, int]] = []
        self.network_namespace_verified_peers = 0
        self._require_isolated_peer = False
        self._lock = threading.Lock()
        self._state = "PRE_EVIDENCE"
        self.unauthenticated_attempts = 0
        self.pre_evidence_requests = 0
        self.post_evidence_submissions = 0
        self.provider_issued_tool: dict[str, str] | None = None
        self.provider_issued_tool_observed = False
        self.provider_issued_response_sha256: str | None = None

    def _handle_request(self, body: bytes, *, authorization: str) -> bytes:
        if not hmac.compare_digest(authorization, "Bearer " + self.token):
            with self._lock:
                self.unauthenticated_attempts += 1
            raise ValueError("gateway-authorization-rejected")
        if not 0 < len(body) <= MAX_REQUEST_BYTES:
            raise ValueError("gateway-request-size")
        try:
            request = json.loads(body)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("gateway-malformed-provider-request") from exc
        if (not isinstance(request, dict)
                or request.get("model") != self.manifest["design"]["model"]
                or not isinstance(request.get("messages"), list)
                or any(not isinstance(m, dict) for m in request["messages"])):
            raise ValueError("gateway-model-or-request-envelope-drift")
        tool_messages = [x for x in request["messages"] if x.get("role") == "tool"]
        # One request at a time across the trial. The held lock covers the
        # provider response to prohibit a simultaneous second request.
        with self._lock:
            if self._state != "PRE_EVIDENCE":
                raise ValueError("gateway-extra-request-after-treatment")
            if not tool_messages:
                if self.enforce_chat_tool_wire_contract and self.provider_issued_tool is not None:
                    raise ValueError("gateway-tool-issued-but-continuation-absent")
                if self.pre_evidence_requests >= 4:
                    raise ValueError("gateway-pre-evidence-request-budget")
                self.pre_evidence_requests += 1
                try:
                    response = _forward_pre_evidence(
                        body, upstream=self.upstream, api_key=self.upstream_api_key,
                        timeout_seconds=self.timeout_seconds,
                        stream=request.get("stream") is True,
                    )
                    if self.enforce_chat_tool_wire_contract:
                        # The host, not a client-authored assistant turn,
                        # witnesses the exact upstream tool call before
                        # accepting any Hashmarks result on the next turn.
                        issued = extract_provider_issued_tool(
                            response, streamed=request.get("stream") is True,
                        )
                        if issued is not None:
                            self.provider_issued_tool = issued
                            self.provider_issued_tool_observed = True
                            self.provider_issued_response_sha256 = issued[
                                "provider_response_sha256"
                            ]
                    return response
                except ValueError:
                    # No retry or later "recovery" from a response with
                    # unqualified provider-issued tool authority.
                    self._state = "PRE_EVIDENCE_UNQUALIFIED"
                    raise
            if len(tool_messages) != 1:
                raise ValueError("gateway-uncontrolled-foreign-tool-observations")
            call_id = tool_messages[0].get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("gateway-missing-observed-tool-call-id")
            if self.enforce_chat_tool_wire_contract:
                require_issued_tool_continuation(
                    request, call_id=call_id,
                    provider_issued=self.provider_issued_tool,
                )
                require_chat_tool_content(
                    self.selected,
                    actual_tool_content=tool_messages[0].get("content"),
                )
            elif tool_messages[0].get("content") != self.selected["selected_content"]:
                # Legacy controlled host transport still accepts the
                # existing structured JSON fixture. Isolated native
                # Chat Completions must obey the stricter wire contract.
                raise ValueError("gateway-selected-packet-not-in-native-request")
            self._state = "TREATMENT_ATTEMPTED"
            prompt = {
                "request_settings": {k: v for k, v in request.items() if k != "messages"},
                "non_tool_messages": [
                    m for m in request["messages"] if m.get("role") != "tool"
                ],
            }
            prompt_sha256 = hashlib.sha256(canonical(prompt)).hexdigest()
            # One outbound request, one bounded HTTPS response, one relay.
            # Failed / ambiguous transport stays TREATMENT_ATTEMPTED and the
            # run cannot retry or create an attested successful submission.
            submit = (
                dispatch_verified_chat_stream_response if request.get("stream") is True
                else dispatch_verified_chat_response
            )
            result = submit(
                capture=self.capture, serialized_model_request=body,
                endpoint=self.upstream, approved_origin=self.approved_origin,
                api_key=self.upstream_api_key, tool_call_id=call_id,
                returned_packet=self.current["content"],
                request_sequence=1, current=self.current,
                replaced=self.replaced, catalog_sha256=self.catalog_sha256,
                prompt_sha256=prompt_sha256, oracle_sha256=self.oracle_sha256,
                workspace_sha256=self.workspace_sha256,
                timeout_seconds=self.timeout_seconds,
            )
            self._state = "PROVIDER_RESPONSE_RETURNED"
            self.post_evidence_submissions = 1
            return result

    def start(self) -> int:
        if self._server is not None:
            raise ValueError("gateway-already-running")
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                try:
                    length = self.headers.get("Content-Length")
                    if (length is None or not length.isascii() or not length.isdecimal()
                            or int(length) > MAX_REQUEST_BYTES or int(length) <= 0):
                        raise ValueError("unbounded-provider-request")
                    body = self.rfile.read(int(length))
                    output = parent._handle_request(
                        body, authorization=self.headers.get("Authorization", ""),
                    )
                    self.send_response(200)
                    is_stream = json.loads(body).get("stream") is True
                    self.send_header(
                        "Content-Type",
                        "text/event-stream" if is_stream else "application/json",
                    )
                    self.send_header("Content-Length", str(len(output)))
                    self.end_headers()
                    self.wfile.write(output)
                except ValueError:
                    self.send_error(403, "native-gateway-request-unqualified")

            def log_message(self, format, *args):
                # Never log request bodies, tokens or provider response content.
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
            name="agentscookbook-native-gateway",
        )
        self._thread.start()
        return int(self._server.server_address[1])

    def start_unix(self, socket_path: Path, *,
                   require_isolated_peer: bool = False) -> None:
        """Private host Unix gateway for Linux network-isolated OpenCode.

        The sandbox receives a bind mount of the socket directory, *not*
        the upstream provider secret or the host network namespace. UDS
        SO_PEERCRED identifies an OS peer but cannot attest its executable.
        """
        if (self._unix_server is not None or self._server is not None
                or socket_path.exists() or socket_path.is_symlink()
                or not socket_path.parent.is_dir()
                or socket_path.parent.stat().st_mode & 0o077):
            raise ValueError("unsafe-or-duplicate-native-unix-gateway")
        self._require_isolated_peer = require_isolated_peer
        parent = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                conn = self.request
                conn.settimeout(75)
                try:
                    def read_exact(size: int) -> bytes:
                        data = bytearray()
                        while len(data) < size:
                            part = conn.recv(size - len(data))
                            if not part:
                                raise ValueError("unix-gateway-incomplete-frame")
                            data.extend(part)
                        return bytes(data)

                    peer = None
                    if hasattr(socket, "SO_PEERCRED"):
                        raw_peer = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                        peer = struct.unpack("3i", raw_peer)
                        if parent._require_isolated_peer:
                            try:
                                peer_netns = os.stat(f"/proc/{peer[0]}/ns/net").st_ino
                                host_netns = os.stat("/proc/self/ns/net").st_ino
                            except OSError as exc:
                                raise ValueError("netns-peer-proof-unavailable") from exc
                            if peer_netns == host_netns:
                                raise ValueError("unisolated-host-network-peer-denied")
                    elif parent._require_isolated_peer:
                        raise ValueError("peer-credential-kernel-support-required")
                    token_length = struct.unpack("!I", read_exact(4))[0]
                    if not 0 < token_length <= 512:
                        raise ValueError("unix-gateway-auth-size")
                    authorization = read_exact(token_length).decode("utf-8")
                    body_length = struct.unpack("!I", read_exact(4))[0]
                    if not 0 < body_length <= MAX_REQUEST_BYTES:
                        raise ValueError("unix-gateway-body-size")
                    body = read_exact(body_length)
                    result = parent._handle_request(body, authorization=authorization)
                    if not 0 < len(result) <= MAX_RESPONSE_BYTES:
                        raise ValueError("unix-gateway-response-size")
                    # Only completed, authenticated, verified requests can
                    # contribute kernel peer observations. Never let a peer
                    # with a bad token, packet or failed upstream inflate
                    # the number of admitted network-isolated connections.
                    if peer is not None:
                        with parent._lock:
                            parent.peer_credentials_observed.append(peer)
                            if parent._require_isolated_peer:
                                parent.network_namespace_verified_peers += 1
                    conn.sendall(struct.pack("!I", len(result)) + result)
                except (OSError, ValueError, UnicodeError, struct.error):
                    try:
                        conn.sendall(struct.pack("!I", 0))
                    except OSError:
                        pass

        class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        # Parent directory is expected to have mode 0700; the socket itself
        # gets 0600 and is mounted only into the trial's isolated namespace.
        self._unix_server = UnixServer(str(socket_path), Handler)
        self._unix_path = socket_path
        socket_path.chmod(0o600)
        self._unix_thread = threading.Thread(
            target=self._unix_server.serve_forever, daemon=True,
            name="agentscookbook-privileged-unix-provider",
        )
        self._unix_thread.start()

    def close(self) -> None:
        if self._unix_server is not None:
            self._unix_server.shutdown()
            self._unix_server.server_close()
            if self._unix_thread is not None:
                self._unix_thread.join(timeout=5)
            if self._unix_path is not None:
                self._unix_path.unlink(missing_ok=True)
            self._unix_server = None
            self._unix_thread = None
            self._unix_path = None
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._server = None
            self._thread = None

    def result(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "harness": self.cell["harness"],
            "trial_id": self.cell["trial_id"],
            "gateway_state": self._state,
            "pre_evidence_requests": self.pre_evidence_requests,
            "post_evidence_submissions": self.post_evidence_submissions,
            "model_request_attested": False,
            "native_chat_tool_wire_contract_enforced": (
                self.enforce_chat_tool_wire_contract
            ),
            "host_submission_recorded": self.post_evidence_submissions == 1,
            "provider_issued_tool_observed": self.provider_issued_tool_observed,
            "provider_issued_response_sha256": self.provider_issued_response_sha256,
            "provider_issued_tool_continuation_verified": (
                self.enforce_chat_tool_wire_contract
                and self.post_evidence_submissions == 1
                and self.provider_issued_tool_observed
            ),
            "native_process_origin_proven": False,
            "unix_peer_credentials_observed": len(self.peer_credentials_observed),
            "distinct_netns_peer_connections": self.network_namespace_verified_peers,
            "kernel_netns_peer_check_required": self._require_isolated_peer,
            "socket_peer_executable_verified": False,
            "provider_response_relay_qualified": self.post_evidence_submissions == 1,
            "causal_improvement_proven": False,
        }
