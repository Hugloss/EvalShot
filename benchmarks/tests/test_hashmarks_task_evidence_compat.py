from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import lzma
from pathlib import Path
import unittest
import zlib

from benchmarks.evidence import grade_direct_claims
from benchmarks.hashmarks_task_evidence import (
    TASK_EVIDENCE_SCHEMAS,
    INITIAL_SCHEMA,
    decode_initial_collection,
    decode_task_evidence,
    is_task_evidence_packet,
    retrieval_candidate_matches,
    retrieval_candidate_symbol,
)
from benchmarks.harness.trace_diagnostics import (
    _hashmarks_evidence,
    _repository_candidate_evidence,
)

FIXTURE = Path(__file__).parent / "fixtures/hashmarks-task-evidence/current-producer.json"


def packet_identity(packet):
    data = {key: value for key, value in packet.items() if key != "evidence_packet_identity"}
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(packet["schema"].encode() + b"\0" + raw).hexdigest()


def wire_packet(packet, kind):
    """Encode fixed producer semantic data into each documented wire format."""
    headers = {key: packet[key] for key in ("schema", "source_evidence_identity", "evidence_packet_identity")}
    data = {key: value for key, value in packet.items() if key not in headers}
    if kind in {"json-string-table.v1", "json-string-table.v1+zlib"}:
        strings = set()

        def collect(value):
            if isinstance(value, str):
                strings.add(value)
            elif isinstance(value, dict):
                for key, child in value.items():
                    strings.add(key)
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(data)
        ordered = sorted(strings)
        indices = {text: str(index) for index, text in enumerate(ordered)}

        def refs(value):
            if isinstance(value, str):
                return indices[value]
            if isinstance(value, dict):
                return {indices[key]: refs(child) for key, child in value.items()}
            if isinstance(value, list):
                return [refs(child) for child in value]
            return value

        table = [[0, text] for text in ordered]
        data = refs(data)
        if kind == "json-string-table.v1":
            return {**headers, "encoding": {"kind": kind, "strings": table}, "data": data}
        data = {"strings": table, "data": data}
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    compressed = (
        base64.b85encode(lzma.compress(payload)).decode()
        if kind == "json+lzma-base85" else base64.b64encode(zlib.compress(payload)).decode()
    )
    return {**headers, "encoding": {"kind": kind, "decoded_bytes": len(payload)}, "data": compressed}


class HashmarksTaskEvidenceCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = json.loads(FIXTURE.read_text())

    def test_supported_schemas_keep_historical_v2_and_v3(self) -> None:
        self.assertEqual(
            TASK_EVIDENCE_SCHEMAS,
            frozenset(
                {
                    "hashmarks.task-evidence.v2",
                    "hashmarks.task-evidence.v3",
                    "hashmarks.task-evidence.v5",
                    INITIAL_SCHEMA,
                }
            ),
        )
        self.assertTrue(
            is_task_evidence_packet({"schema": "hashmarks.task-evidence.v2"})
        )
        self.assertTrue(
            is_task_evidence_packet({"schema": "hashmarks.task-evidence.v3"})
        )
        self.assertFalse(
            is_task_evidence_packet({"schema": "hashmarks.task-evidence.v4"})
        )
        self.assertFalse(is_task_evidence_packet({"schema": "hashmarks.task-evidence.v5"}))

    def test_candidate_matching_supports_v2_and_compact_v3_shapes(self) -> None:
        expected = {"path": "src/owner.py", "symbol": "target"}
        cases = (
            {"path": "src/owner.py", "name": "target"},
            {"path": "src/owner.py", "qualname": "Owner.target"},
            {"path": "src/owner.py", "symbol": "Owner.target"},
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    retrieval_candidate_symbol(candidate),
                    "target",
                )
                self.assertTrue(
                    retrieval_candidate_matches(candidate, expected)
                )

    def test_direct_evidence_grading_accepts_task_evidence_v3(self) -> None:
        result = grade_direct_claims(
            {"family": "semantics"},
            "hashmarks",
            json.dumps(
                {
                    "schema": "hashmarks.task-evidence.v3",
                    "evidence_receipt": {
                        "repository_identity": "sha256:repo",
                        "evidence_identity": "sha256:evidence",
                    },
                    "evidence_packet_identity": "sha256:packet",
                }
            ),
        )

        self.assertTrue(result["parse_valid"])
        self.assertEqual(result["provenance_structure"], "PRESENT")

    def test_actual_current_producer_capture_decodes_without_strengthening(self) -> None:
        original = deepcopy(self.capture)
        logical = self.capture["initial_logical"]
        for value in (self.capture["initial_wire"], self.capture["mcp_result"], json.dumps(self.capture["mcp_result"])):
            with self.subTest(type=type(value)):
                self.assertEqual(decode_task_evidence(value), logical)
        self.assertEqual(decode_task_evidence(self.capture["full"]), self.capture["full"])
        self.assertEqual(decode_task_evidence(self.capture["mcp_full_result"]), self.capture["resolved_full"])
        self.assertEqual(logical["ownership"]["status"], "ambiguous")
        self.assertIsNone(logical["ownership"]["owner"])
        self.assertEqual(logical["freshness"]["state"], "unknown")
        self.assertEqual(logical["initial_projection"]["negative_evidence_from_omissions"], "not-admissible")
        self.assertEqual(self.capture, original)

    def test_all_initial_codecs_preserve_json_types_and_unknown_extensions(self) -> None:
        logical = deepcopy(self.capture["initial_logical"])
        logical["future"] = {"values": ["0", 0, False, None, 0.0, "💚é"], "unknown": None}
        logical["evidence_packet_identity"] = packet_identity(logical)
        for kind in ("json-string-table.v1", "json-string-table.v1+zlib", "json+zlib", "json+lzma-base85"):
            with self.subTest(kind=kind):
                decoded = decode_task_evidence(wire_packet(logical, kind))
                self.assertEqual(decoded, logical)
                self.assertEqual([type(item) for item in decoded["future"]["values"][:5]], [str, int, bool, type(None), float])

    def test_current_direct_grade_retains_ambiguous_and_resolved_authority(self) -> None:
        for name in ("full", "initial_wire", "mcp_result"):
            result = grade_direct_claims({"family": "code_owners", "expected": {"owner_state": "ambiguous"}}, "hashmarks", json.dumps(self.capture[name]))
            self.assertTrue(result["parse_valid"])
            self.assertEqual(result["provenance_structure"], "PRESENT")
            self.assertEqual(result["answer_correctness"], "PASS")
        result = grade_direct_claims({"family": "code_owners", "expected": {"owner_state": "resolved", "path": "a.py"}}, "hashmarks", json.dumps(self.capture["resolved_full"]))
        self.assertEqual(result["answer_correctness"], "PASS")

    def test_truncation_and_unresolved_authority_are_not_parse_failures_or_absence(self) -> None:
        logical = deepcopy(self.capture["initial_logical"])
        logical["ownership"]["status"] = "unresolved"
        logical["ownership"]["ambiguity"]["ambiguous"] = False
        logical["retrieval"]["bounds"]["canonical_completeness"] = "incomplete"
        logical["retrieval"]["bounds"]["canonical_truncation"] = "truncated"
        logical["evidence_packet_identity"] = packet_identity(logical)
        wire = wire_packet(logical, "json+zlib")
        result = grade_direct_claims({"family": "code_owners", "expected": {"owner_state": "resolved", "path": "missing.py"}}, "hashmarks", json.dumps(wire))
        self.assertTrue(result["parse_valid"])
        self.assertEqual(result["answer_correctness"], "UNRESOLVED")
        evidence = _hashmarks_evidence(wire, {"path": "missing.py", "symbol": "target"})
        self.assertEqual(evidence["retrieval_truncation_state"], "truncated")
        self.assertEqual(evidence["initial_projection"]["negative_evidence_from_omissions"], "not-admissible")
        self.assertIsNone(decode_task_evidence(wire)["ownership"]["owner"])

    def test_unresolved_current_owner_is_not_graded_as_unique(self) -> None:
        packet = deepcopy(self.capture["resolved_full"])
        packet["ownership"]["status"] = "unresolved"
        packet["evidence_packet_identity"] = packet_identity(packet)
        result = grade_direct_claims({"family": "code_owners", "expected": {"owner_state": "resolved", "path": "a.py"}}, "hashmarks", json.dumps(packet))
        self.assertEqual(result["answer_correctness"], "UNRESOLVED")

    def test_initial_trace_uses_decoded_candidates_and_retains_projection(self) -> None:
        evidence = _hashmarks_evidence(self.capture["mcp_result"], {"path": "missing.py", "symbol": "missing"})
        self.assertEqual(evidence["packet_status"], "parsed")
        self.assertEqual(evidence["disclosure"], "initial")
        self.assertEqual(evidence["freshness_state"], "unknown")
        self.assertEqual(evidence["initial_projection"], self.capture["initial_logical"]["initial_projection"])
        self.assertEqual(evidence["expected_target_observability"], "absent-from-returned-candidates")
        metadata, paths, symbols = _repository_candidate_evidence(self.capture["mcp_result"])
        self.assertEqual(metadata["state"], "candidate-locators-observed")
        self.assertEqual(paths, {"a.py", "b.py"})
        self.assertIn("target_impl", symbols)

    def test_readable_text_and_unfetched_resource_are_not_lossless_packets(self) -> None:
        envelopes = (
            {"content": self.capture["mcp_result"]["content"]},
            {"content": [{"type": "resource_link", "uri": "hashmarks://evidence/not-fetched", "name": "full"}]},
        )
        for value in envelopes:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    decode_task_evidence(value)
                self.assertFalse(grade_direct_claims({"family": "semantics"}, "hashmarks", json.dumps(value))["parse_valid"])
                self.assertEqual(_hashmarks_evidence(value, None), {"packet_status": "unparseable"})

    def test_mcp_exact_json_text_is_supported_but_conflicting_packets_fail(self) -> None:
        packet = self.capture["full"]
        self.assertEqual(decode_task_evidence({"structuredContent": None, "content": [{"type": "text", "text": json.dumps(packet)}]}), packet)
        envelope = deepcopy(self.capture["mcp_result"])
        envelope["content"].append({"type": "text", "text": json.dumps(packet)})
        with self.assertRaisesRegex(ValueError, "conflicting"):
            decode_task_evidence(envelope)
        envelope = deepcopy(self.capture["mcp_result"])
        envelope["isError"] = True
        with self.assertRaises(ValueError):
            decode_task_evidence(envelope)

    def test_compressed_initial_rejects_corruption_trailing_data_and_false_sizes(self) -> None:
        for kind in ("json+zlib", "json+lzma-base85"):
            original = wire_packet(self.capture["initial_logical"], kind)
            unpack = base64.b85decode if kind.endswith("base85") else base64.b64decode
            pack = base64.b85encode if kind.endswith("base85") else base64.b64encode
            for mutation in ("size-bool", "size-wrong", "size-bound", "truncated", "trailing", "invalid", "identity", "header"):
                wire = deepcopy(original)
                if mutation == "size-bool": wire["encoding"]["decoded_bytes"] = True
                elif mutation == "size-wrong": wire["encoding"]["decoded_bytes"] -= 1
                elif mutation == "size-bound": wire["encoding"]["decoded_bytes"] = 2**32
                elif mutation == "truncated": wire["data"] = pack(unpack(wire["data"])[:-1]).decode()
                elif mutation == "trailing": wire["data"] = pack(unpack(wire["data"]) + b"extra").decode()
                elif mutation == "invalid": wire["data"] = " "
                elif mutation == "identity": wire["evidence_packet_identity"] = "sha256:" + "0" * 64
                else: wire["source_evidence_identity"] = "invalid"
                with self.subTest(kind=kind, mutation=mutation), self.assertRaises(ValueError):
                    decode_task_evidence(wire)

    def test_string_table_rejects_aliases_invalid_refs_and_expansion(self) -> None:
        original = wire_packet(self.capture["initial_logical"], "json-string-table.v1")
        for mutation in ("bool-prefix", "prefix-bound", "duplicate", "bad-ref", "leading-zero", "expansion"):
            wire = deepcopy(original)
            if mutation == "bool-prefix": wire["encoding"]["strings"][0][0] = False
            elif mutation == "prefix-bound": wire["encoding"]["strings"][0][0] = 1
            elif mutation == "duplicate": wire["encoding"]["strings"][1] = [0, wire["encoding"]["strings"][0][1]]
            elif mutation == "bad-ref": wire["data"]["999999"] = 0
            elif mutation == "leading-zero": wire["data"]["00"] = 0
            else:
                wire["encoding"]["strings"] = [[0, "x" * 1000]] + [[1000 + i, "x"] for i in range(4000)]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                decode_task_evidence(wire)

    def test_compressed_payload_rejects_duplicate_keys_header_shadowing_and_memory(self) -> None:
        original = wire_packet(self.capture["initial_logical"], "json+zlib")
        for payload in (b'{"future":0,"future":1}', b'{"schema":"shadow"}', b'[]'):
            wire = deepcopy(original)
            wire["encoding"]["decoded_bytes"] = len(payload)
            wire["data"] = base64.b64encode(zlib.compress(payload)).decode()
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_task_evidence(wire)
        wire = wire_packet(self.capture["initial_logical"], "json+lzma-base85")
        payload = b'{"facts":[]}'
        wire["encoding"]["decoded_bytes"] = len(payload)
        wire["data"] = base64.b85encode(lzma.compress(payload, preset=9)).decode()
        with self.assertRaises(ValueError):
            decode_task_evidence(wire)

    def test_duplicates_nesting_and_malformed_current_shapes_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            decode_task_evidence('{"schema":"hashmarks.task-evidence.v3","schema":"hashmarks.task-evidence.v2"}')
        nested = 0
        for _ in range(66): nested = [nested]
        with self.assertRaises(ValueError):
            decode_task_evidence({"schema": "hashmarks.task-evidence.v3", "future": nested})
        packet = deepcopy(self.capture["full"])
        packet["evidence_packet_identity"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "identity"):
            decode_task_evidence(packet)
        for section, field, value in (("ownership", "owner", {}), ("ownership", "ambiguity", {"ambiguous": "false"}), ("retrieval", "results", None), ("freshness", "state", False)):
            packet = deepcopy(self.capture["full"])
            packet[section][field] = value
            with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                decode_task_evidence(packet)
        for schema in ("hashmarks.task-evidence.v4", "hashmarks.task-evidence.v6", "hashmarks.task-evidence-initial.v3"):
            packet = deepcopy(self.capture["full"])
            packet["schema"] = schema
            self.assertFalse(is_task_evidence_packet(packet))

    def test_initial_collection_codec_rejects_overwriting_extras(self) -> None:
        encoding = {"kind": "columns-rows-with-extras.v1", "columns": ["path"]}
        self.assertEqual(decode_initial_collection([["a.py", {"unknown": None}]], encoding), [{"path": "a.py", "unknown": None}])
        with self.assertRaises(ValueError):
            decode_initial_collection([["a.py", {"path": "b.py"}]], encoding)


if __name__ == "__main__":
    unittest.main()
