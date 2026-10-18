"""External OpenAI Responses routing probe for Hashmarks versus native grep/read."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - native Windows import boundary
    _fcntl = None

from benchmarks.tool_routing import routing_artifact_sha256
from benchmarks.tool_routing_trace import (
    CATALOG_CAPTURE_SCHEMA,
    TRACE_SCHEMA,
    score_trace,
)

_HOST = "openai-responses-api"
_HANDOFF_SCHEMA = "hashmarks.chatgpt-secure-mcp-tunnel-handoff.v1"
_RECEIPT_SCHEMA = "agents-cookbook-openai-responses-routing-probe.v1"
_RESPONSES_URL = "https://api.openai.com/v1/responses"
_MAX_RESPONSES = 8
_MAX_TRACKED_PATHS = 100_000
_MAX_SEARCH_FILE_BYTES = 1_000_000
_MAX_SEARCH_TOTAL_BYTES = 64_000_000
_MAX_SEARCH_RESULTS = 20
_MAX_READ_LINES = 200
_MAX_READ_BYTES = 100_000
_HASHMARKS_ALLOWED_TOOLS = ("task_evidence", "find")


class OpenAIRoutingProbeError(RuntimeError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpenAIRoutingProbeError(
            f"command failed to execute: {argv[0]}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise OpenAIRoutingProbeError(
            f"command failed ({result.returncode}): {argv[0]}: {detail[:2000]}"
        )
    return result


def _git(workspace: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpenAIRoutingProbeError(
            f"cannot establish workspace git authority: {exc}"
        ) from exc
    if result.returncode != 0:
        raise OpenAIRoutingProbeError(
            "cannot establish workspace git authority: "
            + result.stderr.decode(errors="replace")[:2000]
        )
    return result.stdout


def _workspace_identity(workspace: Path) -> dict[str, object]:
    commit = _git(workspace, "rev-parse", "HEAD").decode().strip()
    tree = _git(workspace, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    status = _git(
        workspace,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status:
        raise OpenAIRoutingProbeError(
            "OpenAI routing probe requires a clean tracked workspace"
        )
    tracked_raw = _git(workspace, "ls-files", "-z")
    tracked = [
        os.fsdecode(value)
        for value in tracked_raw.split(b"\0")
        if value
    ]
    if len(tracked) > _MAX_TRACKED_PATHS:
        raise OpenAIRoutingProbeError(
            f"workspace exceeds {_MAX_TRACKED_PATHS} tracked paths"
        )
    digest = hashlib.sha256()
    for relative in tracked:
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
    return {
        "commit": commit,
        "tree": tree,
        "tracked_paths_sha256": digest.hexdigest(),
        "tracked_path_count": len(tracked),
        "tracked_paths": tracked,
    }


def _stable_workspace(
    workspace: Path,
    expected: dict[str, object],
) -> None:
    current = _workspace_identity(workspace)
    for key in ("commit", "tree", "tracked_paths_sha256", "tracked_path_count"):
        if current[key] != expected[key]:
            raise OpenAIRoutingProbeError(
                "workspace authority changed during OpenAI routing probe"
            )


def _load_handoff(path: Path, workspace: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenAIRoutingProbeError(
            f"Hashmarks tunnel handoff is unreadable: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise OpenAIRoutingProbeError("Hashmarks tunnel handoff must be an object")
    if payload.get("schema") != _HANDOFF_SCHEMA or payload.get("status") != "READY":
        raise OpenAIRoutingProbeError(
            "Hashmarks tunnel handoff is not a READY v1 receipt"
        )
    try:
        bound_workspace = Path(str(payload["workspace"])).resolve()
    except (KeyError, OSError) as exc:
        raise OpenAIRoutingProbeError(
            "Hashmarks tunnel handoff workspace is unavailable"
        ) from exc
    if bound_workspace != workspace.resolve():
        raise OpenAIRoutingProbeError(
            "Hashmarks tunnel handoff is bound to a different workspace"
        )
    command = payload.get("mcp_command")
    if not isinstance(command, str) or not command.strip():
        raise OpenAIRoutingProbeError(
            "Hashmarks tunnel handoff has no qualified stdio command"
        )
    tools = payload.get("mcp", {}).get("tools")
    names = {
        row.get("name")
        for row in tools
        if isinstance(row, dict)
    } if isinstance(tools, list) else set()
    missing = [
        name
        for name in _HASHMARKS_ALLOWED_TOOLS
        if name not in names
    ]
    if missing:
        raise OpenAIRoutingProbeError(
            "Hashmarks tunnel handoff does not expose required routing tools: "
            + ", ".join(missing)
        )
    payload["_receipt_sha256"] = _sha256_bytes(raw)
    return payload


def _hashmarks_runtime_identity(handoff: dict[str, Any]) -> dict[str, object]:
    implementation = handoff.get("hashmarks")
    if not isinstance(implementation, dict):
        raise OpenAIRoutingProbeError(
            "Hashmarks tunnel handoff implementation identity is unavailable"
        )
    executable_value = implementation.get("executable")
    expected_sha256 = implementation.get("executable_sha256")
    expected_version = implementation.get("version")
    if (
        not isinstance(executable_value, str)
        or not isinstance(expected_sha256, str)
        or not isinstance(expected_version, str)
    ):
        raise OpenAIRoutingProbeError(
            "Hashmarks tunnel handoff implementation identity is incomplete"
        )
    executable = Path(executable_value).resolve()
    if not executable.is_file():
        raise OpenAIRoutingProbeError(
            f"Hashmarks handoff executable no longer exists: {executable}"
        )
    observed_sha256 = _sha256_file(executable)
    if observed_sha256 != expected_sha256:
        raise OpenAIRoutingProbeError(
            "Hashmarks executable changed after tunnel handoff qualification"
        )
    observed_version = _run([str(executable), "--version"]).stdout.strip()
    if observed_version != expected_version:
        raise OpenAIRoutingProbeError(
            "Hashmarks version changed after tunnel handoff qualification"
        )

    source = handoff.get("source")
    source_identity: str | None = None
    if source is not None:
        if not isinstance(source, dict):
            raise OpenAIRoutingProbeError(
                "Hashmarks handoff source identity is malformed"
            )
        root_value = source.get("root")
        expected_source = source.get("repository_content_identity")
        if not isinstance(root_value, str) or not isinstance(expected_source, str):
            raise OpenAIRoutingProbeError(
                "Hashmarks handoff source identity is incomplete"
            )
        source_root = Path(root_value).resolve()
        python = executable.parent / (
            "python.exe" if os.name == "nt" else "python"
        )
        if not python.is_file():
            raise OpenAIRoutingProbeError(
                "source-bound Hashmarks handoff has no adjacent Python interpreter"
            )
        code = (
            "from pathlib import Path; "
            "from hashmarks.test_shards import repository_content_identity; "
            "import sys; root=Path(sys.argv[1]).resolve(); "
            "print(repository_content_identity("
            "root, excluded_paths=(root / 'dist',)))"
        )
        source_identity = _run(
            [str(python), "-I", "-c", code, str(source_root)],
            cwd=source_root,
            timeout=120.0,
        ).stdout.strip()
        if source_identity != expected_source:
            raise OpenAIRoutingProbeError(
                "Hashmarks source changed after tunnel handoff qualification"
            )
    argv = handoff.get("mcp_command_argv")
    if not isinstance(argv, list) or not argv or str(argv[0]) != str(executable):
        raise OpenAIRoutingProbeError(
            "Hashmarks handoff command no longer matches its executable authority"
        )
    return {
        "executable": str(executable),
        "executable_sha256": observed_sha256,
        "version": observed_version,
        "source_identity": source_identity,
    }


def _native_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "grep",
            "description": (
                "Search tracked repository text for a literal query when you need "
                "native repository discovery."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Literal text to search for.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "read",
            "description": (
                "Read a bounded line range from one exact tracked repository path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Tracked repository-relative path.",
                    },
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "end_line": {
                        "type": "integer",
                        "minimum": 1,
                    },
                },
                "required": ["path", "start_line", "end_line"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]


def _tools(tunnel_id: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "mcp",
            "server_label": "hashmarks",
            "tunnel_id": tunnel_id,
            "allowed_tools": list(_HASHMARKS_ALLOWED_TOOLS),
            "require_approval": "never",
        },
        *_native_tools(),
    ]


class _NativeRepository:
    def __init__(self, workspace: Path, tracked: list[str]) -> None:
        self.workspace = workspace.resolve()
        self.tracked = frozenset(tracked)
        self.files: dict[str, bytes] = {}
        total = 0
        for relative in sorted(self.tracked):
            path = (self.workspace / relative).resolve()
            try:
                path.relative_to(self.workspace)
            except ValueError as exc:
                raise OpenAIRoutingProbeError(
                    f"tracked path escapes workspace: {relative}"
                ) from exc
            if not path.is_file():
                raise OpenAIRoutingProbeError(
                    f"tracked path is not a file: {relative}"
                )
            payload = path.read_bytes()
            total += len(payload)
            if total > _MAX_SEARCH_TOTAL_BYTES:
                raise OpenAIRoutingProbeError(
                    "tracked repository bytes exceed the native probe bound"
                )
            self.files[relative] = payload

    def _payload(self, relative: str) -> bytes:
        if relative not in self.tracked:
            raise OpenAIRoutingProbeError(
                f"native read path is not tracked: {relative}"
            )
        return self.files[relative]

    def grep(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise OpenAIRoutingProbeError("native grep query must not be empty")
        needle = query.casefold()
        matches: list[dict[str, object]] = []
        scanned_bytes = 0
        truncated = False
        for relative, payload in sorted(self.files.items()):
            size = len(payload)
            if size > _MAX_SEARCH_FILE_BYTES:
                continue
            scanned_bytes += size
            if b"\0" in payload:
                continue
            text = payload.decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                if needle not in line.casefold():
                    continue
                matches.append(
                    {
                        "path": relative,
                        "line": number,
                        "text": line[:500],
                    }
                )
                if len(matches) >= _MAX_SEARCH_RESULTS:
                    truncated = True
                    break
            if truncated:
                break
        return {
            "schema": "agents-cookbook-native-grep.v1",
            "query": query,
            "matches": matches,
            "truncated": truncated,
            "scanned_bytes": scanned_bytes,
        }

    def read(
        self,
        relative: str,
        start_line: int,
        end_line: int,
    ) -> dict[str, Any]:
        if start_line < 1 or end_line < start_line:
            raise OpenAIRoutingProbeError("native read line range is invalid")
        if end_line - start_line + 1 > _MAX_READ_LINES:
            raise OpenAIRoutingProbeError(
                f"native read exceeds {_MAX_READ_LINES} lines"
            )
        payload = self._payload(relative)
        if b"\0" in payload:
            raise OpenAIRoutingProbeError("native read refuses binary files")
        text = payload.decode("utf-8", errors="replace")
        selected = text.splitlines()[start_line - 1 : end_line]
        rendered = "\n".join(selected)
        if len(rendered.encode("utf-8")) > _MAX_READ_BYTES:
            raise OpenAIRoutingProbeError(
                f"native read exceeds {_MAX_READ_BYTES} bytes"
            )
        return {
            "schema": "agents-cookbook-native-read.v1",
            "path": relative,
            "start_line": start_line,
            "end_line": start_line + len(selected) - 1,
            "text": rendered,
        }

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "grep":
            query = arguments.get("query")
            if not isinstance(query, str):
                raise OpenAIRoutingProbeError("native grep query must be a string")
            return self.grep(query)
        if name == "read":
            relative = arguments.get("path")
            start = arguments.get("start_line")
            end = arguments.get("end_line")
            if (
                not isinstance(relative, str)
                or not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
            ):
                raise OpenAIRoutingProbeError(
                    "native read arguments have invalid types"
                )
            return self.read(relative, start, end)
        raise OpenAIRoutingProbeError(f"unexpected native tool: {name}")

def _responses_create(
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: float = 120.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        _RESPONSES_URL,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise OpenAIRoutingProbeError(
            f"OpenAI Responses API rejected the probe ({exc.code}): {detail}"
        ) from exc
    except (OSError, urllib.error.URLError) as exc:
        raise OpenAIRoutingProbeError(
            f"OpenAI Responses API unavailable: {exc}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenAIRoutingProbeError(
            "OpenAI Responses API returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise OpenAIRoutingProbeError(
            "OpenAI Responses API returned a non-object response"
        )
    return payload


def _parse_arguments(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise OpenAIRoutingProbeError("tool call arguments are unavailable")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise OpenAIRoutingProbeError("tool call arguments are invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise OpenAIRoutingProbeError("tool call arguments must be an object")
    return parsed


def _message_text(response: dict[str, Any]) -> str | None:
    chunks: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
            elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                chunks.append(part["refusal"])
    return "\n".join(chunks) if chunks else None


def _catalog_from_list_tools(
    item: dict[str, Any],
) -> list[str]:
    if item.get("server_label") not in {None, "hashmarks"}:
        return []
    raw_tools = item.get("tools")
    if not isinstance(raw_tools, list):
        return []
    names = []
    for tool in raw_tools:
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            names.append(f"mcp__hashmarks__{tool['name']}")
    return names


def _response_items(
    response: dict[str, Any],
    *,
    repository: _NativeRepository,
    trace_calls: list[dict[str, object]],
) -> tuple[list[dict[str, Any]], list[str]]:
    outputs = response.get("output")
    if not isinstance(outputs, list):
        raise OpenAIRoutingProbeError("OpenAI response output is unavailable")
    function_outputs: list[dict[str, Any]] = []
    imported_tools: list[str] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "mcp_list_tools":
            imported_tools.extend(_catalog_from_list_tools(item))
            continue
        if kind == "mcp_approval_request":
            raise OpenAIRoutingProbeError(
                "unexpected MCP approval request despite require_approval=never"
            )
        if kind == "mcp_call":
            name = item.get("name")
            if not isinstance(name, str):
                raise OpenAIRoutingProbeError("MCP call has no tool name")
            if name not in _HASHMARKS_ALLOWED_TOOLS:
                raise OpenAIRoutingProbeError(
                    f"Responses invoked an MCP tool outside the admitted contract: {name}"
                )
            arguments = _parse_arguments(item.get("arguments", "{}"))
            error = item.get("error")
            output = item.get("output")
            trace_calls.append(
                {
                    "tool": f"mcp__hashmarks__{name}",
                    "status": "error" if error else "completed",
                    "input": arguments,
                    **(
                        {"output": output}
                        if output is not None
                        else {"result": {"error": error}}
                    ),
                }
            )
            continue
        if kind != "function_call":
            continue
        name = item.get("name")
        call_id = item.get("call_id")
        if not isinstance(name, str) or not isinstance(call_id, str):
            raise OpenAIRoutingProbeError(
                "Responses function call lacks name or call_id"
            )
        arguments = _parse_arguments(item.get("arguments", "{}"))
        try:
            result = repository.call(name, arguments)
            status = "completed"
            tool_output = json.dumps(result, sort_keys=True)
        except OpenAIRoutingProbeError as exc:
            status = "error"
            tool_output = json.dumps(
                {"error": str(exc)},
                sort_keys=True,
            )
        trace_calls.append(
            {
                "tool": name,
                "status": status,
                "input": arguments,
                "output": tool_output,
            }
        )
        function_outputs.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": tool_output,
            }
        )
    return function_outputs, imported_tools


@contextlib.contextmanager
def _tunnel_lock(tunnel_id: str) -> Iterator[None]:
    token = hashlib.sha256(tunnel_id.encode("utf-8")).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / f"agentscookbook-tunnel-{token}.lock"
    if _fcntl is None:
        raise OpenAIRoutingProbeError(
            "OpenAI routing probe tunnel locking currently requires a POSIX host "
            "(use WSL/Linux rather than native Windows)"
        )
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OpenAIRoutingProbeError(
                "another local OpenAI routing probe already owns this tunnel ID"
            ) from exc
        yield
    finally:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        os.close(fd)


def _wait_tunnel(
    process: subprocess.Popen[bytes],
    health_file: Path,
    *,
    timeout: float = 20.0,
) -> str:
    deadline = time.monotonic() + timeout
    last_error = "health URL not published"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise OpenAIRoutingProbeError(
                f"tunnel-client exited before readiness ({process.returncode})"
            )
        if health_file.is_file():
            try:
                base = health_file.read_text(encoding="utf-8").strip().rstrip("/")
                if base:
                    with urllib.request.urlopen(
                        base + "/readyz",
                        timeout=1.0,
                    ) as response:
                        if response.status == 200:
                            return base
            except (OSError, urllib.error.URLError) as exc:
                last_error = str(exc)
        time.sleep(0.05)
    raise OpenAIRoutingProbeError(
        f"tunnel-client did not become ready: {last_error}"
    )


def _tunnel_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "CA_BUNDLE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key in allowed and value
    }


@contextlib.contextmanager
def _running_tunnel(
    *,
    tunnel_client: Path,
    tunnel_id: str,
    mcp_command: str,
    control_plane_api_key: str,
) -> Iterator[dict[str, str]]:
    if not tunnel_client.is_file():
        raise OpenAIRoutingProbeError(
            f"tunnel-client executable does not exist: {tunnel_client}"
        )
    version = _run([str(tunnel_client), "--version"]).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="agentscookbook-openai-tunnel-") as tmp:
        root = Path(tmp)
        health_file = root / "health.url"
        log_path = root / "tunnel.log"
        secret_path = root / "control-plane-api-key"
        secret_path.write_text(control_plane_api_key, encoding="utf-8")
        secret_path.chmod(0o600)
        environment = _tunnel_environment()
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    str(tunnel_client),
                    "run",
                    "--control-plane.tunnel-id",
                    tunnel_id,
                    "--control-plane.api-key",
                    f"file:{secret_path}",
                    "--mcp.command",
                    mcp_command,
                    "--health.listen-addr",
                    "127.0.0.1:0",
                    "--health.url-file",
                    str(health_file),
                    "--log.level",
                    "warn",
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                health_url = _wait_tunnel(process, health_file)
                yield {
                    "version": version,
                    "executable_sha256": _sha256_file(tunnel_client),
                    "health_url": health_url,
                }
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _request_payload(
    *,
    model: str,
    tools: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "model": model,
        "store": False,
        "parallel_tool_calls": False,
        "instructions": (
            "Answer from repository evidence using the available tools as needed. "
            "Do not invent file paths, symbols, or implementation details."
        ),
        "input": items,
        "tools": tools,
        "tool_choice": "auto",
        "max_output_tokens": 1200,
    }


def _user_item(prompt: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [{"type": "input_text", "text": prompt}],
    }


def _run_responses_loop(
    *,
    model: str,
    tunnel_id: str,
    prompt: str,
    api_key: str,
    repository: _NativeRepository,
    workspace: Path,
    workspace_identity: dict[str, object],
    requester: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    tools = _tools(tunnel_id)
    items: list[dict[str, Any]] = [_user_item(prompt)]
    trace_calls: list[dict[str, object]] = []
    imported: list[str] = []
    response_ids: list[str] = []
    usage: list[dict[str, Any]] = []
    capture_id: str | None = None
    final_text: str | None = None

    for _ in range(_MAX_RESPONSES):
        response = requester(
            _request_payload(model=model, tools=tools, items=items),
            api_key=api_key,
        )
        response_id = response.get("id")
        if not isinstance(response_id, str) or not response_id:
            raise OpenAIRoutingProbeError("OpenAI response has no response ID")
        if capture_id is None:
            capture_id = response_id
        response_ids.append(response_id)
        if isinstance(response.get("usage"), dict):
            usage.append(response["usage"])
        if response.get("status") not in {"completed", None}:
            raise OpenAIRoutingProbeError(
                f"OpenAI response stopped with status {response.get('status')!r}"
            )

        function_outputs, discovered = _response_items(
            response,
            repository=repository,
            trace_calls=trace_calls,
        )
        imported.extend(discovered)
        _stable_workspace(workspace, workspace_identity)

        output = response.get("output")
        if not isinstance(output, list):
            raise OpenAIRoutingProbeError("OpenAI response output is unavailable")
        items.extend(
            item for item in output if isinstance(item, dict)
        )
        if function_outputs:
            items.extend(function_outputs)
            continue

        final_text = _message_text(response)
        if final_text is not None:
            break
    else:
        raise OpenAIRoutingProbeError(
            f"OpenAI routing probe exceeded {_MAX_RESPONSES} response turns"
        )

    if capture_id is None:
        raise OpenAIRoutingProbeError("OpenAI routing probe produced no response")
    return {
        "capture_id": capture_id,
        "trace_calls": trace_calls,
        "catalog_names": [
            *sorted(set(imported)),
            "grep",
            "read",
        ],
        "response_ids": response_ids,
        "usage": usage,
        "final_text": final_text,
    }


def preflight_probe(
    *,
    workspace: Path,
    handoff_path: Path,
    tunnel_client: Path,
    tunnel_id: str,
    model: str,
    openai_api_key: str,
    control_plane_api_key: str,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise OpenAIRoutingProbeError(
            f"workspace does not exist: {workspace}"
        )
    if re.fullmatch(r"tunnel_[0-9a-f]{32}", tunnel_id) is None:
        raise OpenAIRoutingProbeError(
            "tunnel ID must be tunnel_ followed by 32 lowercase hex digits"
        )
    if not model.strip():
        raise OpenAIRoutingProbeError("OpenAI model must not be empty")
    if not openai_api_key or not control_plane_api_key:
        raise OpenAIRoutingProbeError(
            "OPENAI_API_KEY and CONTROL_PLANE_API_KEY are required"
        )
    tunnel_client = tunnel_client.resolve()
    if not tunnel_client.is_file():
        raise OpenAIRoutingProbeError(
            f"tunnel-client executable does not exist: {tunnel_client}"
        )

    handoff = _load_handoff(handoff_path, workspace)
    hashmarks = _hashmarks_runtime_identity(handoff)
    workspace_identity = _workspace_identity(workspace)
    tracked = workspace_identity["tracked_paths"]
    assert isinstance(tracked, list)
    _NativeRepository(workspace, tracked)
    _stable_workspace(workspace, workspace_identity)

    tunnel_version = _run([str(tunnel_client), "--version"]).stdout.strip()
    tunnel_sha256 = _sha256_file(tunnel_client)

    with _tunnel_lock(tunnel_id):
        pass

    return {
        "schema": "agents-cookbook-openai-responses-routing-preflight.v1",
        "status": "READY",
        "authority": {
            "model_free": True,
            "credentials_observed": {
                "OPENAI_API_KEY": True,
                "CONTROL_PLANE_API_KEY": True,
            },
            "credentials_persisted": False,
            "tunnel_lock": "local-nonblocking",
            "cross_host_tunnel_exclusivity": "external",
        },
        "workspace": {
            key: value
            for key, value in workspace_identity.items()
            if key != "tracked_paths"
        },
        "hashmarks_handoff_sha256": handoff["_receipt_sha256"],
        "hashmarks_runtime": hashmarks,
        "tunnel": {
            "id_sha256": _sha256_bytes(tunnel_id.encode("utf-8")),
            "client_version": tunnel_version,
            "client_executable_sha256": tunnel_sha256,
        },
        "openai": {
            "model": model,
        },
    }


def run_probe(
    *,
    workspace: Path,
    handoff_path: Path,
    tunnel_client: Path,
    tunnel_id: str,
    model: str,
    prompt: str,
    openai_api_key: str,
    control_plane_api_key: str,
    requester: Callable[..., dict[str, Any]] = _responses_create,
) -> dict[str, Any]:
    if not prompt.strip():
        raise OpenAIRoutingProbeError("probe prompt must not be empty")
    preflight = preflight_probe(
        workspace=workspace,
        handoff_path=handoff_path,
        tunnel_client=tunnel_client,
        tunnel_id=tunnel_id,
        model=model,
        openai_api_key=openai_api_key,
        control_plane_api_key=control_plane_api_key,
    )
    workspace = workspace.resolve()
    handoff = _load_handoff(handoff_path, workspace)
    hashmarks_before = dict(preflight["hashmarks_runtime"])
    before = _workspace_identity(workspace)
    tracked = before["tracked_paths"]
    assert isinstance(tracked, list)
    repository = _NativeRepository(workspace, tracked)
    _stable_workspace(workspace, before)

    with _tunnel_lock(tunnel_id):
        with _running_tunnel(
            tunnel_client=tunnel_client.resolve(),
            tunnel_id=tunnel_id,
            mcp_command=str(handoff["mcp_command"]),
            control_plane_api_key=control_plane_api_key,
        ) as tunnel:
            result = _run_responses_loop(
                model=model,
                tunnel_id=tunnel_id,
                prompt=prompt,
                api_key=openai_api_key,
                repository=repository,
                workspace=workspace,
                workspace_identity=before,
                requester=requester,
            )

    _stable_workspace(workspace, before)
    hashmarks_after = _hashmarks_runtime_identity(handoff)
    if hashmarks_after != hashmarks_before:
        raise OpenAIRoutingProbeError(
            "Hashmarks implementation changed during OpenAI routing probe"
        )
    capture_id = str(result["capture_id"])
    catalog = {
        "schema": CATALOG_CAPTURE_SCHEMA,
        "host": _HOST,
        "capture_id": capture_id,
        "tools": [
            {"name": name}
            for name in result["catalog_names"]
        ],
    }
    trace = {
        "schema": TRACE_SCHEMA,
        "host": _HOST,
        "capture_id": capture_id,
        "catalog_sha256": routing_artifact_sha256(catalog),
        "calls": result["trace_calls"],
    }
    score = score_trace(
        catalog_payload=catalog,
        trace_payload=trace,
        subject="hashmarks",
        required_tool="hashmarks_task_evidence",
    )
    receipt = {
        "schema": _RECEIPT_SCHEMA,
        "status": "COMPLETE",
        "preflight": preflight,
        "authority": {
            "diagnostic_only": True,
            "heldout_comparable": False,
            "openai_response_store": False,
            "parallel_tool_calls": False,
            "credentials_persisted": False,
            "hashmarks_receives_openai_credentials": False,
            "hashmarks_allowed_tools": list(_HASHMARKS_ALLOWED_TOOLS),
            "native_tools": ["grep", "read"],
            "tunnel_exclusivity": (
                "local-nonblocking-lock; cross-host exclusivity remains external"
            ),
        },
        "workspace": {
            key: value
            for key, value in before.items()
            if key != "tracked_paths"
        },
        "hashmarks_handoff_sha256": handoff["_receipt_sha256"],
        "hashmarks_runtime": hashmarks_before,
        "tunnel": {
            "id_sha256": _sha256_bytes(tunnel_id.encode("utf-8")),
            "client": tunnel,
        },
        "openai": {
            "model": model,
            "response_ids": result["response_ids"],
            "usage": result["usage"],
        },
        "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "final_text": result["final_text"],
        "catalog": catalog,
        "trace": trace,
        "score": score,
    }
    return receipt
