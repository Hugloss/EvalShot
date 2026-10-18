"""E273/E274: fail-closed ownership of one Hashmarks MCP stdio packet.

The proxy must observe an actual native Hashmarks tools/call reply. It
compares the original structured packet against a previously frozen
semantic source *before* presenting the selected text to OpenCode. The
proxy neither manufactures a successful MCP reply nor upgrades a local
process observation into provider attention or causal evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

def canonical(value: object) -> bytes:
    # Exact shared canonical JSON transport contract; intentionally self-
    # contained so the bwrap guest needs no host evaluator imports.
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")

SCHEMA = "agentscookbook.native-hashmarks-mcp-bridge-contract.v1"
MAX_FRAME = 1_048_576
MAX_REQUEST_IDS = 2048
MAX_INFLIGHT_REQUESTS = 128
SUPPORTED_TOOLS = frozenset(("task_evidence", "find", "hashmarks_task_evidence", "hashmarks_find"))


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def freeze_bridge_contract(
    selected: Mapping[str, Any], *, original_packet: object,
    hashmarks_sha256: str,
) -> dict[str, Any]:
    if not isinstance(selected, Mapping):
        raise ValueError("native-mcp-selected-treatment-invalid")
    text = selected.get("selected_content")
    if (type(text) is not str
            or not 0 < len(text.encode("utf-8")) <= MAX_FRAME
            or selected.get("presentation") not in (
                "text", "compact-json-text", "indented-json-text"
            )
            or type(hashmarks_sha256) is not str
            or len(hashmarks_sha256) != 64
            or any(c not in "0123456789abcdef" for c in hashmarks_sha256)):
        raise ValueError("native-mcp-bridge-requires-pinned-text-authority")
    try:
        parsed = json.loads(text)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("native-mcp-treatment-not-json") from exc
    if canonical(parsed) != canonical(original_packet):
        raise ValueError("native-mcp-selected-content-not-source-equivalent")
    original_digest = _sha(canonical(original_packet))
    if original_digest != selected.get("semantic_sha256"):
        raise ValueError("native-mcp-original-semantic-source-stale")
    required = ("trial_id", "manifest_sha256", "generation_sha256",
                "semantic_sha256", "surface_sha256", "presentation", "arm")
    if not all(isinstance(selected.get(name), str)
               and selected[name] for name in required):
        raise ValueError("native-mcp-missing-selected-source-identity")
    if _sha(canonical(text)) != selected["surface_sha256"]:
        raise ValueError("native-mcp-selected-text-surface-stale")
    body = {
        "schema": SCHEMA,
        "trial_id": selected["trial_id"],
        "manifest_sha256": selected["manifest_sha256"],
        "generation_sha256": selected["generation_sha256"],
        "semantic_sha256": selected["semantic_sha256"],
        "surface_sha256": selected["surface_sha256"],
        "presentation": selected["presentation"],
        "arm": selected["arm"],
        "hashmarks_sha256": hashmarks_sha256,
        "original_packet": original_packet,
        "selected_text": text,
    }
    return {**body, "contract_sha256": _sha(canonical(body))}


def validate_bridge_contract(document: object, *, hashmarks_sha256: str) -> dict[str, Any]:
    if (not isinstance(document, dict)
            or set(document) != {
                "schema", "trial_id", "manifest_sha256", "generation_sha256",
                "semantic_sha256", "surface_sha256", "presentation", "arm",
                "hashmarks_sha256", "original_packet", "selected_text",
                "contract_sha256",
            } or document.get("schema") != SCHEMA
            or document.get("hashmarks_sha256") != hashmarks_sha256):
        raise ValueError("native-mcp-bridge-untrusted-config")
    source = {
        "trial_id": document["trial_id"],
        "manifest_sha256": document["manifest_sha256"],
        "generation_sha256": document["generation_sha256"],
        "semantic_sha256": document["semantic_sha256"],
        "surface_sha256": document["surface_sha256"],
        "presentation": document["presentation"], "arm": document["arm"],
        "selected_content": document["selected_text"],
    }
    try:
        expected = freeze_bridge_contract(
            source, original_packet=document["original_packet"],
            hashmarks_sha256=hashmarks_sha256,
        )
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        raise ValueError("native-mcp-bridge-invalid-contract") from exc
    if canonical(document) != canonical(expected):
        raise ValueError("native-mcp-bridge-contract-mismatch")
    return expected


def write_bridge_contract(path: Path, document: Mapping[str, Any]) -> str:
    if path.is_symlink() or path.exists() or not path.parent.is_dir():
        raise ValueError("native-mcp-bridge-create-only-target")
    data = canonical(document) + b"\n"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
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
    return _sha(data)


def _valid_tool_schema(tool: object) -> bool:
    """Admit observable MCP input shape, never claim full JSON-Schema proof."""
    if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
        return False
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return False
    props, required = schema.get("properties"), schema.get("required", [])
    if (not isinstance(props, dict) or not isinstance(required, list)
            or len(props) > 128 or len(required) > 128
            or any(not isinstance(k, str) or not isinstance(v, dict)
                   for k, v in props.items())
            or any(type(k) is not str or k not in props for k in required)
            or len(set(required)) != len(required)):
        return False
    return True


def _validate_call_input(tool: Mapping[str, Any], args: Mapping[str, Any]) -> None:
    schema = tool["inputSchema"]
    properties = schema["properties"]
    for key in schema.get("required", []):
        if key not in args:
            raise ValueError("native-mcp-catalog-required-input-missing")
    if schema.get("additionalProperties") is False and any(
        key not in properties for key in args
    ):
        raise ValueError("native-mcp-catalog-foreign-input")
    types = {
        "string": lambda x: type(x) is str,
        "integer": lambda x: type(x) is int,
        "number": lambda x: type(x) in (int, float),
        "boolean": lambda x: type(x) is bool,
        "array": lambda x: type(x) is list,
        "object": lambda x: type(x) is dict,
        "null": lambda x: x is None,
    }
    for key, value in args.items():
        if key not in properties:
            continue
        definition = properties[key]
        expected = definition.get("type")
        if isinstance(expected, str) and expected in types:
            if not types[expected](value):
                raise ValueError("native-mcp-catalog-input-type-drift")
        elif isinstance(expected, list):
            if not any(isinstance(kind, str) and kind in types
                       and types[kind](value) for kind in expected):
                raise ValueError("native-mcp-catalog-input-type-drift")
        # The full schema may have refinements (e.g. bounds, oneOf) that
        # only Hashmarks itself validates. Do not certify full conformance.


def _id(message: Mapping[str, Any]) -> object:
    value = message.get("id")
    if type(value) is str and 0 < len(value) <= 256:
        return value
    if type(value) is int and 0 <= value < 2**53:
        return value
    raise ValueError("native-mcp-unqualified-request-id")


class BridgeState:
    def __init__(self, contract: Mapping[str, Any]):
        self.contract = validate_bridge_contract(
            dict(contract), hashmarks_sha256=contract["hashmarks_sha256"],
        )
        self.pending: object | None = None
        self.subject_calls = 0
        self.subject_responses = 0
        self.subject_tool: str | None = None
        self.completed_subject_id: object | None = None
        # One bounded, type-exact JSON-RPC authority for all forwarded calls,
        # not just the selected subject. Unsolicited MCP replies must never
        # become model-visible observations through the transparent bridge.
        self.seen_ids: set[tuple[type, str | int]] = set()
        self.inflight: dict[tuple[type, str | int], str] = {}
        self.initialize_request_id: object | None = None
        self.catalog_request_id: object | None = None
        self.initialized_server_version: str | None = None
        self.catalog_tools: dict[str, dict[str, Any]] | None = None

    def receive_request(self, frame: bytes) -> bytes:
        if not isinstance(frame, bytes) or not 0 < len(frame) <= MAX_FRAME:
            raise ValueError("native-mcp-unbounded-request")
        try:
            message = json.loads(frame)
        except (UnicodeError, ValueError) as exc:
            raise ValueError("native-mcp-invalid-request-json") from exc
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("native-mcp-non-jsonrpc-request")
        method = message.get("method")
        if (type(method) is not str or not method
                or "result" in message or "error" in message):
            raise ValueError("native-mcp-invalid-client-request-envelope")
        token = None
        if "id" in message:
            request_id = _id(message)
            token = (type(request_id), request_id)
            if (token in self.seen_ids
                    or len(self.seen_ids) >= MAX_REQUEST_IDS
                    or len(self.inflight) >= MAX_INFLIGHT_REQUESTS):
                raise ValueError("native-mcp-duplicate-or-unbounded-request-id")
        elif method == "tools/call":
            raise ValueError("native-mcp-subject-request-id-required")
        if method == "initialize":
            if token is None or self.initialize_request_id is not None:
                raise ValueError("native-mcp-repeated-or-unqualified-initialization")
            self.initialize_request_id = request_id
        elif method == "tools/list":
            if (token is None or self.initialize_request_id is None
                    or self.catalog_request_id is not None):
                raise ValueError("native-mcp-catalog-request-outside-initialization")
            self.catalog_request_id = request_id
        if method == "tools/call":
            # E285: both real server and real catalog must already be
            # *validated*. Issued but unfinished JSON-RPC requests are
            # never authorization to perform native repository work.
            if (self.initialized_server_version is None
                    or self.catalog_tools is None):
                raise ValueError("native-mcp-subject-before-verified-catalog")
            if self.subject_calls != 0 or self.pending is not None:
                raise ValueError("native-mcp-extra-subject-call-denied")
            params = message.get("params")
            if (not isinstance(params, dict)
                    or params.get("name") not in SUPPORTED_TOOLS
                    or not isinstance(params.get("arguments"), dict)
                    or len(canonical(params["arguments"])) > 16_384):
                raise ValueError("native-mcp-foreign-or-unbounded-tool-call")
            tool = self.catalog_tools.get(params["name"])
            if tool is None:
                raise ValueError("native-mcp-original-subject-not-listed")
            # E286: the schema shape decision belongs to pre-dispatch
            # admission, never after Hashmarks has already executed.
            # Full JSON Schema semantics remain Hashmarks' authority.
            _validate_call_input(tool, params["arguments"])
            self.pending = _id(message)
            self.subject_calls = 1
            self.subject_tool = params["name"]
        if token is not None:
            self.seen_ids.add(token)
            self.inflight[token] = method
        return frame

    def receive_response(self, frame: bytes) -> bytes:
        if not isinstance(frame, bytes) or not 0 < len(frame) <= MAX_FRAME:
            raise ValueError("native-mcp-unbounded-response")
        try:
            response = json.loads(frame)
        except (UnicodeError, ValueError) as exc:
            raise ValueError("native-mcp-invalid-response-json") from exc
        if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
            raise ValueError("native-mcp-non-jsonrpc-response")
        if "id" not in response:
            # MCP notifications have no correlation id and no result body.
            # Server-initiated requests and unsolicited success are not
            # admitted: no response is fabricated to satisfy them.
            method = response.get("method")
            if (type(method) is str and method.startswith("notifications/")
                    and "result" not in response and "error" not in response):
                return frame
            raise ValueError("native-mcp-unrequested-server-message")
        received_id = response["id"]
        if (self.completed_subject_id is not None
                and type(received_id) is type(self.completed_subject_id)
                and received_id == self.completed_subject_id):
            raise ValueError("native-mcp-replayed-subject-response")
        if (self.pending is not None and received_id == self.pending
                and type(received_id) is not type(self.pending)):
            raise ValueError("native-mcp-subject-id-type-confusion")
        response_id = _id(response)
        token = (type(response_id), response_id)
        if token not in self.inflight:
            raise ValueError("native-mcp-orphan-or-replayed-response")
        if (("result" in response) == ("error" in response)
                or "method" in response):
            raise ValueError("native-mcp-ambiguous-response-envelope")
        if self.pending is None or token != (type(self.pending), self.pending):
            # Every response, including server identity and the real catalog,
            # must be owned by an exact outstanding client request.
            method = self.inflight[token]
            if method == "initialize":
                result = response.get("result")
                info = result.get("serverInfo") if isinstance(result, dict) else None
                if (self.initialized_server_version is not None
                        or not isinstance(info, dict)
                        or info.get("name") != "Hashmarks"
                        or type(info.get("version")) is not str
                        or not 0 < len(info["version"]) <= 128):
                    raise ValueError("native-mcp-server-identity-unverified")
                self.initialized_server_version = info["version"]
            elif method == "tools/list":
                result = response.get("result")
                catalog = result.get("tools") if isinstance(result, dict) else None
                if (self.initialized_server_version is None
                        or self.catalog_tools is not None
                        or not isinstance(catalog, list) or not catalog
                        or len(catalog) > 128
                        or any(not _valid_tool_schema(row) for row in catalog)):
                    raise ValueError("native-mcp-catalog-unqualified")
                names = [row["name"] for row in catalog]
                if len(set(names)) != len(names):
                    raise ValueError("native-mcp-catalog-duplicate-tool")
                self.catalog_tools = {row["name"]: row for row in catalog}
            del self.inflight[token]
            return frame
        if (self.catalog_tools is None
                or self.subject_tool not in self.catalog_tools
                or self.initialized_server_version is None):
            raise ValueError("native-mcp-original-subject-not-listed")
        # The original native response still needs exact source proof,
        # but the argument admission occurred once before subprocess work.
        if "error" in response or not isinstance(response.get("result"), dict):
            raise ValueError("native-mcp-original-hashmarks-call-failed")
        result = response["result"]
        if set(result) - {"content", "structuredContent", "isError"}:
            raise ValueError("native-mcp-unrecognized-result-fields")
        if result.get("isError") is True:
            raise ValueError("native-mcp-hashmarks-tool-error")
        content = result.get("content")
        if (not isinstance(content, list) or len(content) != 1
                or not isinstance(content[0], dict)
                or set(content[0]) != {"type", "text"}
                or content[0]["type"] != "text"
                or not isinstance(content[0]["text"], str)):
            raise ValueError("native-mcp-ambiguous-hashmarks-output")
        try:
            original = json.loads(content[0]["text"])
        except (ValueError, UnicodeError) as exc:
            raise ValueError("native-mcp-original-result-not-json") from exc
        expected = self.contract["original_packet"]
        if canonical(original) != canonical(expected):
            raise ValueError("native-mcp-hashmarks-original-packet-drift")
        if "structuredContent" in result and canonical(
            result["structuredContent"]
        ) != canonical(expected):
            raise ValueError("native-mcp-hashmarks-structured-content-drift")
        # The only permitted change is presentation of the *same* packet
        # using a frozen text format. No synthetic Hashmarks success.
        updated = dict(response)
        updated["result"] = {
            "content": [{"type": "text", "text": self.contract["selected_text"]}],
        }
        self.completed_subject_id = self.pending
        self.inflight.pop(token)
        self.pending = None
        self.subject_responses += 1
        return canonical(updated)
