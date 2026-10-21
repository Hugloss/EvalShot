"""Versioned, stdlib-only reader for captured Hashmarks task evidence.

The initial-v2 wire codec follows Hashmarks' ``task_evidence_initial`` contract
(producer 9fcc7f1). Decoding preserves producer facts and identities; it does
not establish repository freshness, ownership, completeness or model attention.
Readable MCP presentation text is not a lossless packet.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import lzma
import math
import re
from typing import Any
import zlib

TASK_EVIDENCE_SCHEMAS = frozenset(
    {
        "hashmarks.task-evidence.v2",
        "hashmarks.task-evidence.v3",
        "hashmarks.task-evidence.v5",
        "hashmarks.task-evidence-initial.v2",
    }
)

INITIAL_SCHEMA = "hashmarks.task-evidence-initial.v2"
_HEADERS = {"schema", "source_evidence_identity", "evidence_packet_identity"}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_COMPRESSED_BYTES = 1_048_576
_MAX_JSON_BYTES = 8 * _MAX_COMPRESSED_BYTES
_MAX_NODES = 100_000


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(items)
    if len(result) != len(items):
        raise ValueError("duplicate task-evidence object key")
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite task-evidence number: {value}")


def _json(value: str | bytes) -> Any:
    if len(value) > _MAX_JSON_BYTES:
        raise ValueError("task-evidence JSON exceeds consumer observation bound")
    try:
        return json.loads(
            value, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid task-evidence JSON") from exc


def _check_json(value: Any) -> None:
    """Bound nested input and string-table expansion before copying/hashing it."""
    nodes = 0
    size = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes, size
        nodes += 1
        if depth > 64 or nodes > _MAX_NODES:
            raise ValueError("task-evidence nesting or collection bound exceeded")
        if isinstance(item, str):
            size += len(item.encode("utf-8"))
        elif isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("task-evidence object key is not text")
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif item is not None and type(item) not in (bool, int, float):
            raise ValueError("invalid task-evidence JSON type")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite task-evidence number")
        if size > _MAX_JSON_BYTES:
            raise ValueError("task-evidence decoded strings exceed consumer bound")

    visit(value, 0)


def _unpack(encoding: dict[str, Any], data: Any) -> Any:
    size = encoding.get("decoded_bytes")
    if (
        set(encoding) != {"kind", "decoded_bytes"}
        or type(size) is not int
        or not 0 < size <= _MAX_COMPRESSED_BYTES
        or not isinstance(data, str)
        or len(data) > 2 * _MAX_COMPRESSED_BYTES
    ):
        raise ValueError("invalid compressed initial packet bounds")
    try:
        if encoding["kind"] == "json+lzma-base85":
            raw = base64.b85decode(data)
            decoder = lzma.LZMADecompressor(memlimit=16_777_216)
            payload = decoder.decompress(raw, max_length=size + 1)
            incomplete = not decoder.eof or bool(decoder.unused_data)
        else:
            raw = base64.b64decode(data, validate=True)
            decoder = zlib.decompressobj()
            payload = decoder.decompress(raw, size + 1)
            incomplete = (
                not decoder.eof or bool(decoder.unused_data)
                or bool(decoder.unconsumed_tail)
            )
        if len(payload) != size or incomplete:
            raise ValueError("compressed initial packet size/scope differs")
        return _json(payload)
    except (binascii.Error, lzma.LZMAError, zlib.error) as exc:
        raise ValueError("invalid compressed initial packet") from exc


def _string_table(encoding: Any, data: Any) -> dict[str, Any]:
    if (
        not isinstance(encoding, dict)
        or set(encoding) != {"kind", "strings"}
        or encoding["kind"] != "json-string-table.v1"
        or not isinstance(encoding["strings"], list)
    ):
        raise ValueError("invalid initial string table")
    strings: list[str] = []
    previous = ""
    table_bytes = 0
    for row in encoding["strings"]:
        if (
            not isinstance(row, list) or len(row) != 2
            or type(row[0]) is not int or not isinstance(row[1], str)
            or not 0 <= row[0] <= len(previous)
        ):
            raise ValueError("invalid initial string table row")
        text = previous[:row[0]] + row[1]
        table_bytes += len(text.encode("utf-8"))
        if table_bytes > _MAX_JSON_BYTES or (strings and text <= previous):
            raise ValueError("initial string table order/expansion bound exceeded")
        strings.append(text)
        previous = text

    def string(index: str) -> str:
        if (
            not index.isascii() or not index.isdecimal()
            or (len(index) > 1 and index[0] == "0")
            or len(index) > 6 or int(index) >= len(strings)
        ):
            raise ValueError("invalid initial string reference")
        return strings[int(index)]

    def decode(value: Any) -> Any:
        if isinstance(value, str):
            return string(value)
        if isinstance(value, dict):
            return _unique_object([(string(k), decode(v)) for k, v in value.items()])
        if isinstance(value, list):
            return [decode(child) for child in value]
        return value

    result = decode(data)
    if not isinstance(result, dict):
        raise ValueError("initial semantic data must be an object")
    return result


def _initial(packet: dict[str, Any]) -> dict[str, Any]:
    if "encoding" not in packet:
        result = packet
    else:
        if set(packet) != _HEADERS | {"encoding", "data"}:
            raise ValueError("invalid initial envelope fields")
        encoding, data = packet["encoding"], packet["data"]
        if not isinstance(encoding, dict):
            raise ValueError("invalid initial encoding")
        kind = encoding.get("kind")
        if not isinstance(kind, str):
            raise ValueError("invalid initial encoding kind")
        if kind in {"json+zlib", "json+lzma-base85", "json-string-table.v1+zlib"}:
            data = _unpack(encoding, data)
            if kind == "json-string-table.v1+zlib":
                if not isinstance(data, dict) or set(data) != {"strings", "data"}:
                    raise ValueError("invalid compressed initial string table")
                encoding = {"kind": "json-string-table.v1", "strings": data["strings"]}
                data = _string_table(encoding, data["data"])
        else:
            data = _string_table(encoding, data)
        if not isinstance(data, dict) or set(data) & _HEADERS:
            raise ValueError("invalid initial semantic data/header shadowing")
        result = {**{key: packet[key] for key in _HEADERS}, **data}
    _check_json(result)
    if any(not isinstance(result.get(key), str) for key in _HEADERS):
        raise ValueError("invalid initial packet headers")
    if not _SHA256.fullmatch(result["source_evidence_identity"]):
        raise ValueError("invalid initial source identity")
    if result["evidence_packet_identity"] != _packet_identity(result):
        raise ValueError("initial semantic identity differs from decoded packet")
    return result


def _packet_identity(packet: dict[str, Any]) -> str:
    semantic = {key: value for key, value in packet.items() if key != "evidence_packet_identity"}
    encoded = json.dumps(
        semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(packet["schema"].encode() + b"\0" + encoded).hexdigest()


def decode_initial_collection(rows: Any, encoding: Any) -> list[dict[str, Any]]:
    """Read a declared initial table without guessing omitted fields."""
    if not isinstance(encoding, Mapping):
        raise ValueError("invalid initial collection encoding")
    columns, kind = encoding.get("columns"), encoding.get("kind")
    if (
        not isinstance(kind, str)
        or kind not in {"columns-rows.v1", "columns-rows-with-extras.v1"}
        or not isinstance(columns, list) or not all(isinstance(c, str) for c in columns)
        or len(set(columns)) != len(columns) or not isinstance(rows, list)
    ):
        raise ValueError("invalid initial collection columns")
    records = []
    extras = kind == "columns-rows-with-extras.v1"
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns) + int(extras):
            raise ValueError("initial collection row differs from columns")
        record = dict(zip(columns, row[:len(columns)], strict=True))
        if extras:
            if not isinstance(row[-1], dict) or set(row[-1]) & set(columns):
                raise ValueError("initial collection extras overwrite columns")
            record.update(row[-1])
        records.append(record)
    return records


def _current_shape(packet: dict[str, Any]) -> None:
    # Validate fields used by evaluation; leave authority qualification to the
    # producer and preserve unknown extension fields and independent dimensions.
    for key in ("task", "consumer_action", "evidence_packet_identity"):
        if not isinstance(packet.get(key), str):
            raise ValueError(f"invalid current task-evidence field: {key}")
    if not _SHA256.fullmatch(packet["evidence_packet_identity"]):
        raise ValueError("invalid current packet identity")
    if packet["schema"] == "hashmarks.task-evidence.v5" and packet["evidence_packet_identity"] != _packet_identity(packet):
        raise ValueError("full task-evidence identity differs from packet")
    for key in ("retrieval", "ownership", "verification", "freshness", "provenance", "evidence_receipt"):
        if not isinstance(packet.get(key), dict):
            raise ValueError(f"invalid current task-evidence section: {key}")
    retrieval = packet["retrieval"]
    if not isinstance(retrieval.get("results"), list) or any(
        not isinstance(row, dict) for row in retrieval["results"]
    ):
        raise ValueError("invalid current retrieval results")
    ownership = packet["ownership"]
    if (
        not isinstance(ownership.get("status"), str) or "owner" not in ownership
        or (ownership["owner"] is not None and not isinstance(ownership["owner"], dict))
        or not isinstance(ownership.get("ambiguity"), dict)
        or type(ownership["ambiguity"].get("ambiguous")) is not bool
        or not isinstance(packet["freshness"].get("state"), str)
    ):
        raise ValueError("invalid current ownership/freshness shape")
    if isinstance(ownership["owner"], dict) and not isinstance(ownership["owner"].get("path"), str):
        raise ValueError("invalid current owner locator")
    if packet["schema"] == INITIAL_SCHEMA:
        projection = packet.get("initial_projection")
        if not isinstance(projection, dict) or not isinstance(projection.get("omissions"), dict):
            raise ValueError("missing initial omission accounting")
        relevance = packet["verification"].get("relevance")
        if isinstance(relevance, dict) and "candidates_encoding" in relevance:
            decode_initial_collection(relevance.get("candidates"), relevance["candidates_encoding"])


def decode_task_evidence(value: object) -> dict[str, Any]:
    """Read an exact packet or captured MCP result; reject unsupported formats.

    No resource is fetched, readable text is not parsed heuristically, and this
    operation does not attest that a native host delivered structured content.
    Legacy full v2/v3 retain their historical reader; current packets are decoded
    and shape-checked. Initial disclosure stays a projection, never a full packet.
    """
    if isinstance(value, str):
        value = _json(value)
    if not isinstance(value, Mapping):
        raise ValueError("task-evidence packet is unavailable")
    packet = dict(value)
    _check_json(packet)
    if "schema" not in packet:
        if any(key in packet and type(packet[key]) is not bool for key in ("isError", "is_error")):
            raise ValueError("invalid MCP task-evidence error flag")
        if packet.get("isError") is True or packet.get("is_error") is True:
            raise ValueError("MCP task-evidence result is an error")
        structured = [packet[key] for key in ("structuredContent", "structured_content") if packet.get(key) is not None]
        candidates = [decode_task_evidence(item) for item in structured]
        content = packet.get("content", [])
        if not isinstance(content, list):
            raise ValueError("invalid MCP task-evidence content")
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            try:
                text = _json(block.get("text", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(text, dict) and str(text.get("schema", "")).startswith("hashmarks.task-evidence"):
                candidates.append(decode_task_evidence(text))
        if not candidates:
            raise ValueError("lossless task-evidence packet is unavailable")
        if any(candidate != candidates[0] for candidate in candidates[1:]):
            raise ValueError("conflicting MCP task-evidence packets")
        return candidates[0]
    if not isinstance(packet.get("schema"), str) or packet["schema"] not in TASK_EVIDENCE_SCHEMAS:
        raise ValueError("unsupported task-evidence schema")
    if packet["schema"] == INITIAL_SCHEMA:
        packet = _initial(packet)
    if packet["schema"] in {INITIAL_SCHEMA, "hashmarks.task-evidence.v5"}:
        _current_shape(packet)
    return deepcopy(packet)


def is_task_evidence_packet(value: object) -> bool:
    try:
        decode_task_evidence(value)
        return True
    except (ValueError, TypeError, RecursionError):
        return False


def retrieval_candidate_symbol(candidate: object) -> str | None:
    if not isinstance(candidate, Mapping):
        return None
    value = (
        candidate.get("symbol")
        or candidate.get("qualname")
        or candidate.get("name")
    )
    if not value:
        return None
    return str(value).rsplit(".", 1)[-1]


def retrieval_candidate_matches(
    candidate: object,
    expected: Mapping[str, str],
) -> bool:
    if not isinstance(candidate, Mapping):
        return False
    return (
        candidate.get("path") == expected.get("path")
        and retrieval_candidate_symbol(candidate) == expected.get("symbol")
    )


def task_evidence_schema(value: Any) -> str | None:
    try:
        return str(decode_task_evidence(value)["schema"])
    except (ValueError, TypeError, RecursionError):
        return None
