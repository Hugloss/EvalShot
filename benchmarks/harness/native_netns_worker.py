"""E258: tiny stdlib HTTP-to-Unix relay inside a bwrap network namespace.

Only loopback HTTP is exposed inside the namespace. The other end is a
host-owned Unix domain socket bind-mounted explicitly into the sandbox.
No upstream HTTPS credential, host socket directory parent, or provider
API URL enters this child. A private socket is not kernel process proof.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socket
import socketserver
import struct
import subprocess
import sys
import threading

MAX_BODY = 8_388_608
MAX_RESPONSE = 2_097_152
MAX_AUTH = 512


def _exact(stream: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        part = stream.recv(length - len(data))
        if not part:
            raise ValueError("relay-incomplete-frame")
        data.extend(part)
    return bytes(data)


def _relay(unix_path: str, auth: str, payload: bytes) -> bytes:
    if not 0 < len(payload) <= MAX_BODY or not 0 < len(auth) <= MAX_AUTH:
        raise ValueError("relay-invalid-input")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(70)
            sock.connect(unix_path)
            token = auth.encode("utf-8")
            sock.sendall(struct.pack("!I", len(token)) + token)
            sock.sendall(struct.pack("!I", len(payload)) + payload)
            length = struct.unpack("!I", _exact(sock, 4))[0]
            if not 0 < length <= MAX_RESPONSE:
                raise ValueError("relay-host-rejected-or-oversized")
            return _exact(sock, length)
    # A rejecting privileged gateway may close immediately after the peer
    # namespace check, before either framed write completes. The kernel can
    # report EPIPE or ECONNRESET depending on scheduling. Both are terminal
    # denials, never a retry or evidence of a successful provider submission.
    except (BrokenPipeError, ConnectionResetError) as exc:
        raise ValueError("relay-host-closed-before-response") from exc


def serve_child(port: int, socket_path: str, argv: list[str]) -> int:
    if not argv or not socket_path.startswith("/run/gateway/"):
        raise ValueError("invalid-native-worker-input")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            try:
                size = self.headers.get("Content-Length", "")
                if not size.isdecimal() or not 0 < int(size) <= MAX_BODY:
                    raise ValueError("invalid-relay-content-length")
                body = self.rfile.read(int(size))
                if len(body) != int(size):
                    raise ValueError("truncated-relay-request")
                doc = json.loads(body)
                if not isinstance(doc, dict):
                    raise ValueError("invalid-relay-json")
                result = _relay(
                    socket_path, self.headers.get("Authorization", ""), body,
                )
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/event-stream" if doc.get("stream") is True
                    else "application/json",
                )
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                self.wfile.write(result)
            except (OSError, ValueError, UnicodeError):
                self.send_error(403, "provider-relay-unqualified")

        def log_message(self, *args):
            return

    class LocalHTTP(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = False

    server = LocalHTTP(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(argv, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False)
        return int(result.returncode)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--unix", required=True)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not args.argv:
        return 2
    command = args.argv[1:] if args.argv[0] == "--" else args.argv
    # Bubblewrap itself brings up lo as part of --unshare-net and fails
    # sandbox setup if that cannot be done. Asking ip(8) again after
    # capabilities drop is both redundant and potentially unprivileged.
    # A failed loopback bind in serve_child is terminal: no host-net fallback.
    try:
        return serve_child(args.port, args.unix, command)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
