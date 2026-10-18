"""E273–E276 native MCP bridge attack ring and executable subprocess proof.

The local stdio end-to-end ring uses an executable deterministic Hashmarks
fixture, NOT an actual Hashmarks release or a real OpenCode model/provider.
The actual bridge executable subprocess and JSON-RPC bytes are exercised.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.native_mcp_bridge_contract import (
    BridgeState, freeze_bridge_contract, validate_bridge_contract,
    write_bridge_contract,
)
from benchmarks.harness.native_mcp_stdio_bridge import run_stdio, sha_file
from benchmarks.harness.native_mcp_live_smoke import smoke_real_native_mcp
from benchmarks.harness.native_mcp_wire_probe import probe_native_mcp
from benchmarks.harness.native_bwrap_execution import build_isolated_argv
from benchmarks.harness.opencode_native_gateway import gateway_config
from benchmarks.harness.trusted_treatments import select_treatment
from benchmarks.tests.test_opencode_native_gateway import manifest, source


def selection():
    frozen = manifest()
    cell = next(row for row in frozen["assignments"] if row["arm"] == "variant")
    return select_treatment(
        frozen, trial_id=cell["trial_id"], harness=cell["harness"],
        task=cell["task"], replicate=cell["replicate"],
        arm=cell["arm"], current=source(),
    )


def call_frame(*, identity=3, name="task_evidence"):
    return canonical({
        "jsonrpc": "2.0", "id": identity, "method": "tools/call",
        "params": {"name": name, "arguments": {"task": "Find owner of src/owner.py"}},
    })


def result_frame(identity=3, *, packet=None, alternate=None):
    packet = source()["content"] if packet is None else packet
    result = {"content": [{"type": "text", "text": json.dumps(packet)}]}
    if alternate is not None:
        result["structuredContent"] = alternate
    return canonical({"jsonrpc": "2.0", "id": identity, "result": result})


CATALOG_TOOL = {
    "name": "task_evidence",
    "inputSchema": {
        "type": "object",
        "properties": {"task": {"type": "string"}},
        "required": ["task"],
        "additionalProperties": False,
    },
}


def admitted_state(contract):
    state = BridgeState(contract)
    state.receive_request(canonical({
        "jsonrpc": "2.0", "id": "init",
        "method": "initialize", "params": {"protocolVersion": "2024-11-05"},
    }))
    state.receive_request(canonical({
        "jsonrpc": "2.0", "id": "list",
        "method": "tools/list", "params": {},
    }))
    state.receive_response(canonical({
        "jsonrpc": "2.0", "id": "init",
        "result": {"serverInfo": {"name": "Hashmarks", "version": "fixture"}},
    }))
    state.receive_response(canonical({
        "jsonrpc": "2.0", "id": "list",
        "result": {"tools": [CATALOG_TOOL]},
    }))
    return state


class NativeMcpBridgeContractAttacks(unittest.TestCase):
    def setUp(self):
        self.chosen = selection()
        self.digest = "a" * 64
        self.contract = freeze_bridge_contract(
            self.chosen, original_packet=source()["content"],
            hashmarks_sha256=self.digest,
        )

    def test_frozen_identity_and_semantics_roundtrip(self):
        self.assertEqual(validate_bridge_contract(
            self.contract, hashmarks_sha256=self.digest,
        ), self.contract)
        self.assertEqual(self.contract["selected_text"],
                         self.chosen["selected_content"])
        self.assertEqual(self.contract["original_packet"], source()["content"])
        for name, value in (
            ("selected_text", "{}"),
            ("original_packet", {"owner": "different.py"}),
            ("contract_sha256", "0" * 64),
            ("manifest_sha256", "changed"),
        ):
            broken = {**self.contract, name: value}
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_bridge_contract(broken, hashmarks_sha256=self.digest)
        with self.assertRaises(ValueError):
            freeze_bridge_contract(
                {**self.chosen, "selected_content": json.loads(
                    self.chosen["selected_content"],
                )}, original_packet=source()["content"],
                hashmarks_sha256=self.digest,
            )

    def test_actual_original_required_before_presenting_selected_text(self):
        state = admitted_state(self.contract)
        first = call_frame()
        self.assertEqual(state.receive_request(first), first)
        actual = result_frame()
        transformed = state.receive_response(actual)
        decoded = json.loads(transformed)
        self.assertEqual(decoded["result"], {
            "content": [{
                "type": "text", "text": self.chosen["selected_content"],
            }],
        })
        self.assertEqual(state.subject_calls, 1)
        self.assertEqual(state.subject_responses, 1)
        with self.assertRaisesRegex(ValueError, "extra-subject"):
            state.receive_request(call_frame(identity=4))

    def test_subject_response_replay_and_boolean_id_alias_are_rejected(self):
        state = admitted_state(self.contract)
        state.receive_request(call_frame(identity=1))
        with self.assertRaisesRegex(ValueError, "id-type-confusion"):
            state.receive_response(canonical({
                "jsonrpc": "2.0", "id": True,
                "result": {"content": [{
                    "type": "text", "text": json.dumps(source()["content"]),
                }]},
            }))
        self.assertEqual(state.subject_responses, 0)
        original = result_frame(identity=1)
        state.receive_response(original)
        with self.assertRaisesRegex(ValueError, "replayed-subject-response"):
            state.receive_response(original)
        self.assertEqual(state.subject_responses, 1)

    def test_native_tool_errors_wrong_packet_parallel_or_forged_sources_block(self):
        bads = [
            (result_frame(packet={"owner": "wrong"}), "packet-drift"),
            (result_frame(alternate={"owner": "wrong"}), "structured-content-drift"),
            (canonical({"jsonrpc": "2.0", "id": 3, "error": {"code": -1}}), "failed"),
            (canonical({"jsonrpc": "2.0", "id": 3,
                        "result": {"content": []}}), "ambiguous"),
            (canonical({"jsonrpc": "2.0", "id": 3,
                        "result": {"content": [
                            {"type": "text", "text": "{}"},
                            {"type": "text", "text": "{}"},
                        ]}}), "ambiguous"),
        ]
        for raw, message in bads:
            with self.subTest(message=message):
                state = admitted_state(self.contract)
                state.receive_request(call_frame())
                with self.assertRaisesRegex(ValueError, message):
                    state.receive_response(raw)
                self.assertEqual(state.subject_responses, 0)
        for malicious in (
            call_frame(name="other-tool"),
            canonical({"jsonrpc":"2.0","id":True,"method":"tools/call",
                       "params":{"name":"find","arguments":{}}}),
        ):
            with self.assertRaises(ValueError):
                BridgeState(self.contract).receive_request(malicious)

    def test_catalog_server_identity_and_schema_gate_subject_release(self):
        for bad_init in (
            {"serverInfo": {"name": "Alien", "version": "1"}},
            {"serverInfo": {"name": "Hashmarks", "version": ""}},
        ):
            state = BridgeState(self.contract)
            state.receive_request(canonical({
                "jsonrpc": "2.0", "id": "init", "method": "initialize",
                "params": {},
            }))
            with self.subTest(info=bad_init), self.assertRaisesRegex(
                ValueError, "server-identity",
            ):
                state.receive_response(canonical({
                    "jsonrpc": "2.0", "id": "init", "result": bad_init,
                }))
        # Missing listed tool: the same original packet is not enough.
        state = BridgeState(self.contract)
        state.receive_request(canonical({
            "jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {},
        }))
        state.receive_request(canonical({
            "jsonrpc": "2.0", "id": "catalog", "method": "tools/list",
            "params": {},
        }))
        state.receive_response(canonical({
            "jsonrpc": "2.0", "id": "init",
            "result": {"serverInfo": {"name": "Hashmarks", "version": "1"}},
        }))
        state.receive_response(canonical({
            "jsonrpc": "2.0", "id": "catalog",
            "result": {"tools": [{
                "name": "find", "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }]},
        }))
        with self.assertRaisesRegex(ValueError, "not-listed"):
            state.receive_request(call_frame())
        self.assertEqual(state.subject_calls, 0)
        for schema in (
            {"type": "array", "properties": {}},
            {"type": "object", "properties": {"task": {}},
             "required": ["absent"]},
        ):
            state = BridgeState(self.contract)
            state.receive_request(canonical({
                "jsonrpc": "2.0", "id": "init", "method": "initialize",
                "params": {},
            }))
            state.receive_request(canonical({
                "jsonrpc": "2.0", "id": "catalog", "method": "tools/list",
                "params": {},
            }))
            state.receive_response(canonical({
                "jsonrpc": "2.0", "id": "init",
                "result": {"serverInfo": {"name": "Hashmarks", "version": "1"}},
            }))
            with self.subTest(schema=schema), self.assertRaisesRegex(
                ValueError, "catalog-unqualified",
            ):
                state.receive_response(canonical({
                    "jsonrpc": "2.0", "id": "catalog",
                    "result": {"tools": [{
                        "name": "task_evidence", "inputSchema": schema,
                    }]},
                }))

    def test_catalog_required_argument_and_type_mismatch_block_original(self):
        for params in ({}, {"task": 123}, {"task": "owner", "injected": "x"}):
            state = admitted_state(self.contract)
            raw = canonical({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "task_evidence", "arguments": params},
            })
            with self.subTest(params=params), self.assertRaisesRegex(
                ValueError, "catalog-",
            ):
                state.receive_request(raw)
            self.assertEqual(state.subject_calls, 0)
            self.assertEqual(state.subject_responses, 0)

    def test_speculative_subject_rejected_until_real_catalog_response(self):
        # An issued initialize/list request is not completed catalog proof.
        state = BridgeState(self.contract)
        for mid, method in (("init", "initialize"), ("list", "tools/list")):
            state.receive_request(canonical({
                "jsonrpc": "2.0", "id": mid, "method": method, "params": {},
            }))
        with self.assertRaisesRegex(ValueError, "before-verified-catalog"):
            state.receive_request(call_frame())
        self.assertEqual(state.subject_calls, 0)
        self.assertFalse(any(m == "tools/call" for m in state.inflight.values()))
        state.receive_response(canonical({
            "jsonrpc": "2.0", "id": "init",
            "result": {"serverInfo": {"name": "Hashmarks", "version": "fixture"}},
        }))
        with self.assertRaisesRegex(ValueError, "before-verified-catalog"):
            state.receive_request(call_frame())
        state.receive_response(canonical({
            "jsonrpc": "2.0", "id": "list",
            "result": {"tools": [CATALOG_TOOL]},
        }))
        self.assertEqual(state.receive_request(call_frame()), call_frame())

    def test_rejected_arguments_never_enter_request_ledger(self):
        state = admitted_state(self.contract)
        prior_ids, prior_inflight = set(state.seen_ids), dict(state.inflight)
        for args in ({}, {"task": False}, {"task": "owner", "secret": "x"}):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "catalog-"):
                state.receive_request(canonical({
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "task_evidence", "arguments": args},
                }))
            self.assertEqual(state.subject_calls, 0)
            self.assertEqual(state.seen_ids, prior_ids)
            self.assertEqual(state.inflight, prior_inflight)

    def test_create_only_contract_and_guest_mcp_binding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "workspace"
            work.mkdir()
            run = root / "run"
            run.mkdir()
            hashmarks = root / "hashmarks"
            hashmarks.write_text("#!/bin/sh\nexit 0\n")
            hashmarks.chmod(0o700)
            python = Path("/usr/bin/python3")
            opencode = root / "opencode"
            opencode.write_text("#!/bin/sh\\nexit 0\\n")
            opencode.chmod(0o700)
            for name in ("home", "config", "cache", "data", "state", "gateway"):
                (run / name).mkdir()
            (run / "opencode.json").write_text("{}")
            write_bridge_contract(run / "native-mcp-contract.json", self.contract)
            with self.assertRaisesRegex(ValueError, "create-only"):
                write_bridge_contract(run / "native-mcp-contract.json", self.contract)
            config = gateway_config(
                port=42424, model_id="gpt-fixture",
                hashmarks_executable=hashmarks, workspace=work,
                bridge_python=python, bridge_hashmarks_sha256=self.digest,
            )
            self.assertEqual(config["mcp"]["hashmarks"]["command"], [
                str(python), "/run/native-mcp-bridge.py",
                "--hashmarks", str(hashmarks), "--sha256", self.digest,
                "--workspace", str(work), "--contract",
                "/run/native-mcp-contract.json",
            ])
            substrate = {"bwrap": "/usr/bin/bwrap", "python": str(python)}
            environment = {
                "HOME": str(run / "home"), "XDG_CONFIG_HOME": str(run / "config"),
                "XDG_CACHE_HOME": str(run / "cache"), "XDG_DATA_HOME": str(run / "data"),
                "XDG_STATE_HOME": str(run / "state"),
                "OPENCODE_CONFIG": str(run / "opencode.json"),
                "OPENCODE_CONFIG_DIR": str(run / "config"),
                "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_AUTO_SHARE": "false",
                "BENCHMARK_NATIVE_GATEWAY_TOKEN": "x" * 32, "PATH": os.defpath,
            }
            argv = build_isolated_argv(
                substrate, executable=opencode, workspace=work,
                run_root=run, gateway_dir=run / "gateway", environment=environment,
                command=[str(hashmarks), "test"], worker_port=42424,
                hashmarks_executable=hashmarks, use_native_mcp_bridge=True,
            )
            for guest in (
                "/run/native-mcp-bridge.py",
                "/run/native_mcp_bridge_contract.py",
                "/run/native-mcp-contract.json",
            ):
                self.assertIn(guest, argv)
            self.assertIn("--ro-bind", argv)
            self.assertNotIn("--share-net", argv)



class _SequencedMcpInput:
    """Test-only parent that waits for actual MCP responses before calling."""

    def __init__(self, *, call):
        self.waiting = queue.Queue()
        self.call = call
        self.waiting.put(canonical({
            "jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }) + b"\n")

    def readline(self, limit):
        return self.waiting.get(timeout=5)


class _SequencedMcpOutput(io.BytesIO):
    def __init__(self, source_input):
        super().__init__()
        self.source_input = source_input

    def write(self, data):
        size = super().write(data)
        # Advance only on the native child response, not on an assumption
        # that catalog identity and tool schema will eventually pass.
        mid = json.loads(data)["id"]
        if mid == "init":
            self.source_input.waiting.put(canonical({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list",
                "params": {},
            }) + b"\n")
        elif mid == 1:
            self.source_input.waiting.put(self.source_input.call + b"\n")
            self.source_input.waiting.put(b"")
        return size


class ExecutableHashmarksStdioFixture(unittest.TestCase):
    def _fixture(self, path: Path, *, wrong=False):
        packet = {"owner": "wrong.py"} if wrong else source()["content"]
        payload = json.dumps(packet)
        code = """#!/usr/bin/python3
import json
import sys
for line in sys.stdin.buffer:
    request = json.loads(line)
    mid = request.get('id')
    # notifications/initialized has no response in JSON-RPC 2.0.
    if mid is None:
        continue
    method = request.get('method')
    if method == 'notifications/initialized':
        continue
    if method == 'initialize':
        response = {'jsonrpc':'2.0', 'id':mid, 'result':{
            'serverInfo': {'name':'Hashmarks', 'version':'fixture'}}}
    elif method == 'tools/list':
        response = {'jsonrpc':'2.0', 'id':mid, 'result':{
            'tools':[{'name':'task_evidence',
              'inputSchema':{'type':'object','properties':{
                'task':{'type':'string'}},'required':['task'],
                'additionalProperties':False}}]}}
    elif method == 'tools/call':
        response = {'jsonrpc':'2.0', 'id':mid, 'result':{
            'content':[{'type':'text','text':%r}],
            'structuredContent': json.loads(%r)}}
    else:
        response = {'jsonrpc':'2.0','id':mid,'error':{'code':-32601}}
    sys.stdout.write(json.dumps(response, separators=(',',':'))+'\\n')
    sys.stdout.flush()
""" % (payload, payload)
        path.write_text(code)
        path.chmod(0o700)

    def test_real_local_hashmarks_protocol_smoke_uses_pinned_binary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "workspace"
            work.mkdir()
            (work / ".git").mkdir()
            executable = root / "hashmarks"
            self._fixture(executable)
            plan = manifest()
            cell = next(c for c in plan["assignments"] if c["arm"] == "variant")
            output = smoke_real_native_mcp(
                plan, trial_id=cell["trial_id"],
                workspace=work, binary=executable,
                sha256=sha_file(executable),
                source=source(), replaced=None,
                tool_name="task_evidence",
                tool_args={"task": "Find owner of src/owner.py"},
            )
            self.assertTrue(output["mcp_stdio_source_and_treatment_qualified"])
            self.assertTrue(output["actual_external_hashmarks_subprocess_called"])
            self.assertFalse(output["provider_evidence_delivery_proven"])
            self.assertEqual(output["model_requests_performed"], 0)
            self._fixture(executable, wrong=True)
            with self.assertRaises(ValueError):
                smoke_real_native_mcp(
                    plan, trial_id=cell["trial_id"],
                    workspace=work, binary=executable,
                    sha256=sha_file(executable),
                    source=source(), replaced=None,
                    tool_name="task_evidence",
                    tool_args={"task": "Find owner of src/owner.py"},
                )

    def test_direct_pinned_mcp_wire_probe_binds_server_catalog_and_packet(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "repo"
            (work / ".git").mkdir(parents=True)
            executable = root / "hashmarks"
            self._fixture(executable)
            kwargs = {
                "binary": executable, "expected_sha256": sha_file(executable),
                "workspace": work, "tool_name": "task_evidence",
                "tool_args": {"task": "Find owner of src/owner.py"},
                "expected_original_packet": source()["content"],
            }
            report = probe_native_mcp(**kwargs)
            self.assertEqual(report["server_name"], "Hashmarks")
            self.assertEqual(report["server_version"], "fixture")
            self.assertTrue(report["bridge_response_shape_compatible"])
            self.assertTrue(report["original_structured_matches_frozen_packet"])
            self.assertTrue(report["original_text_matches_frozen_packet"])
            self.assertFalse(report["native_opencode_sdk_proven"])
            self.assertFalse(report["original_response_host_attested"])
            self.assertEqual(report["model_calls_performed"], 0)
            altered = probe_native_mcp(**{
                **kwargs, "expected_original_packet": {"owner": "foreign.py"},
            })
            self.assertFalse(altered["bridge_response_shape_compatible"])
            self.assertFalse(altered["original_text_matches_frozen_packet"])
            self.assertTrue(altered["real_installed_mcp_process_observed_locally"])
            self._fixture(executable, wrong=True)
            with self.assertRaisesRegex(ValueError, "admission-failed"):
                probe_native_mcp(**kwargs)

    def test_real_subprocess_native_stdio_roundtrip_and_fail_closed_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            executable = root / "hashmarks"
            contract = root / "contract.json"
            self._fixture(executable)
            chosen = selection()
            frozen = freeze_bridge_contract(
                chosen, original_packet=source()["content"],
                hashmarks_sha256=sha_file(executable),
            )
            write_bridge_contract(contract, frozen)
            source_input = _SequencedMcpInput(call=call_frame())
            output = _SequencedMcpOutput(source_input)
            status = run_stdio(
                hashmarks_executable=executable, hashmarks_sha256=sha_file(executable),
                workspace=workspace, contract_path=contract,
                stdin=source_input, stdout=output,
            )
            self.assertEqual(status, 0)
            lines = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0]["id"], "init")
            self.assertEqual(lines[1]["id"], 1)
            self.assertEqual(lines[2]["result"]["content"][0]["text"],
                             chosen["selected_content"])
            self.assertNotIn("structuredContent", lines[2]["result"])

            self._fixture(executable, wrong=True)
            wrong_digest = sha_file(executable)
            wrong_contract = freeze_bridge_contract(
                chosen, original_packet=source()["content"],
                hashmarks_sha256=wrong_digest,
            )
            other = root / "wrong-contract.json"
            write_bridge_contract(other, wrong_contract)
            drift_input = _SequencedMcpInput(call=call_frame())
            drift_output = _SequencedMcpOutput(drift_input)
            status = run_stdio(
                hashmarks_executable=executable, hashmarks_sha256=wrong_digest,
                workspace=workspace, contract_path=other,
                stdin=drift_input, stdout=drift_output,
            )
            self.assertEqual(status, 2)
            self.assertNotIn(chosen["selected_content"].encode(), drift_output.getvalue())

    def test_real_child_never_executes_rejected_arguments(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            executable = root / "hashmarks"
            marker = workspace / "child-was-invoked"
            self._fixture(executable)
            body = executable.read_text()
            hook = "    elif method == 'tools/call':\n"
            self.assertEqual(body.count(hook), 1)
            executable.write_text(body.replace(
                hook, hook + "        open(" + repr(str(marker))
                + ", 'w').write('executed')\n",
            ))
            executable.chmod(0o700)
            frozen = freeze_bridge_contract(
                selection(), original_packet=source()["content"],
                hashmarks_sha256=sha_file(executable),
            )
            contract = root / "contract.json"
            write_bridge_contract(contract, frozen)
            invalid = canonical({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "task_evidence", "arguments": {"task": 404}},
            })
            incoming = _SequencedMcpInput(call=invalid)
            outgoing = _SequencedMcpOutput(incoming)
            status = run_stdio(
                hashmarks_executable=executable, hashmarks_sha256=sha_file(executable),
                workspace=workspace, contract_path=contract,
                stdin=incoming, stdout=outgoing,
            )
            self.assertEqual(status, 2)
            self.assertFalse(marker.exists(),
                             "invalid tool call reached native Hashmarks child")
            self.assertNotIn(frozen["selected_text"].encode(), outgoing.getvalue())


if __name__ == "__main__":
    unittest.main()
