"""E257–E260: native export must not erase exploration or fabricate certainty.

All inputs are synthetic OpenCode exports; no model, upstream provider or
external source attestation is claimed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.mechanism_attribution import (
    FOLLOWED,
    project_atif,
)
from benchmarks.harness.opencode_native_session import inspect_native_export


PACKET = {"owner": {"path": "src/owner.py", "symbol": "resolve"}}
CALL_ID = "hm-exact"
H = lambda raw: hashlib.sha256(raw).hexdigest()


def tool(name: str, call_id: str, *, arguments: dict | None = None,
         result: object = None, status: str = "completed") -> dict:
    return {
        "type": "tool", "tool": name, "callID": call_id,
        "state": {
            "status": status,
            "input": arguments if arguments is not None else {},
            "output": (json.dumps(PACKET) if name == "hashmarks_task_evidence"
                       else "opaque source content") if result is None else result,
        },
    }


def native_packet() -> dict:
    return tool("hashmarks_task_evidence", CALL_ID)


def inspect(parts: list[dict], *, other_messages: list | None = None):
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        doc = {
            "info": {"id": "ses_native_exact", "title": "trial-exact",
                     "directory": str(work.resolve())},
            "messages": (other_messages or []) + [{
                "info": {"role": "assistant", "providerID": "agentscookbook-captured",
                         "modelID": "model-test"},
                "parts": parts,
            }],
        }
        return inspect_native_export(
            canonical(doc), session_id="ses_native_exact", title="trial-exact",
            workspace=work, expected_provider="agentscookbook-captured",
            expected_model="model-test", expected_packet=PACKET,
            expected_call_id_sha256=H(CALL_ID.encode()),
            expected_packet_sha256=H(canonical(PACKET)),
        )


def textpart(text: str = "src/owner.py") -> dict:
    return {"type": "text", "text": text}


class NativeToolOrderQualificationTests(unittest.TestCase):
    def test_native_search_before_subject_is_not_hidden_by_projection(self) -> None:
        inspected = inspect([
            tool("grep", "grep-1", arguments={"pattern": "resolve"}),
            tool("read", "read-1", arguments={"path": "src/unrelated.py"}),
            native_packet(), textpart(),
        ])
        self.assertEqual(inspected["native_tool_parts_observed"], 3)
        self.assertEqual(inspected["native_subject_tool_parts_observed"], 1)
        self.assertFalse(inspected["native_export_capture_exhaustiveness_proven"])
        self.assertEqual(
            [row["tool_calls"][0]["function_name"] for row in inspected["atif"]["steps"]],
            ["grep", "read", "mcp__hashmarks__task_evidence"],
        )
        self.assertNotIn("opaque source content", json.dumps(inspected["atif"]))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "trajectory.json"
            path.write_bytes(canonical(inspected["atif"]))
            observed = project_atif(path)
        self.assertEqual(observed["tool_calls"], 3)
        self.assertEqual(observed["native_search_calls"], 1)
        self.assertEqual(observed["native_read_calls"], 1)
        self.assertEqual(observed["subject_first_tool_call_ordinal"], 3)
        self.assertEqual(observed["subject_routing_timing"], "LATE_RESCUE")

    def test_native_read_followthrough_after_subject_is_observable(self) -> None:
        result = inspect([
            native_packet(),
            tool("read", "read-target", arguments={"path": "src/owner.py"}),
            textpart("the owner is resolve"),
        ])
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "trajectory.json"
            path.write_bytes(canonical(result["atif"]))
            observed = project_atif(path)
        self.assertEqual(observed["native_read_calls"], 1)
        self.assertEqual(observed["subject_first_tool_call_ordinal"], 1)
        self.assertEqual(observed["subject_routing_timing"], "FIRST_CHOICE")
        self.assertEqual(observed["subject_target_followthrough"], FOLLOWED)

    def test_tool_arguments_cannot_leak_unbounded_source_or_shell_content(self) -> None:
        secret = "should-not-enter-derived-evidence-" + "z" * 3000
        trace = inspect([
            tool("bash", "shell-1", arguments={"command": secret}),
            tool("read", "read-1", arguments={"path": "src/owner.py",
                                            "extra": secret}),
            native_packet(), textpart(),
        ])["atif"]
        self.assertNotIn(secret, json.dumps(trace))
        self.assertEqual(trace["steps"][0]["tool_calls"][0]["arguments"], {})
        self.assertEqual(trace["steps"][1]["tool_calls"][0]["arguments"], {
            "path": "src/owner.py",
        })

    def test_duplicate_unknown_or_incomplete_native_calls_never_disappear(self) -> None:
        bad_parts = [
            [tool("grep", CALL_ID), native_packet(), textpart()],
            [tool("grep", "grep-1", status="running"), native_packet(), textpart()],
            [tool("hashmarks_other", "unknown"), native_packet(), textpart()],
            [tool("mcp__enola__find", "foreign"), native_packet(), textpart()],
            [tool("read", "bad-args", arguments={"path": "p"}),
             {**native_packet(), "state": {"status": "completed", "output": json.dumps(PACKET),
                                            "input": ["not-a-map"]}}, textpart()],
        ]
        for parts in bad_parts:
            with self.subTest(parts=parts):
                with self.assertRaises(ValueError):
                    inspect(parts)

    def test_earlier_text_is_not_terminal_if_another_tool_follows(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing-final"):
            inspect([native_packet(), textpart("not terminal"),
                     tool("read", "after-final", arguments={"path": "src/owner.py"})])

    def test_non_assistant_tool_part_is_rejected_not_silently_excluded(self) -> None:
        fake = [{
            "info": {"role": "user"},
            "parts": [tool("bash", "forged-user-tool")],
        }]
        with self.assertRaisesRegex(ValueError, "outside-assistant"):
            inspect([native_packet(), textpart()], other_messages=fake)

    def test_snapshot_has_no_claim_of_provider_attention_or_os_origin(self) -> None:
        result = inspect([native_packet(), textpart()])
        self.assertEqual(result["atif"]["source_capture_exhaustiveness"], "UNKNOWN")
        self.assertEqual(result["atif"]["projection_scope"],
                         "all-native-completed-tool-parts")
        self.assertFalse(result["native_process_origin_proven"])
        self.assertFalse(result["provider_model_attention_proven"])
        self.assertTrue(result["native_text_after_last_tool_observed"])
        self.assertFalse(result["native_terminal_stop_proven"])


if __name__ == "__main__":
    unittest.main()
