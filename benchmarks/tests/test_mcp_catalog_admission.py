from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from benchmarks.harness.mcp_catalog import (
    probe_mcp_tool_catalog,
    probe_subject_catalog_contracts,
)
from benchmarks.harness.model import McpExposure, TrialContext
from benchmarks.harness.suite import load_runtime_suite


class McpCatalogAdmissionTests(unittest.TestCase):
    def _context(self, root: Path) -> TrialContext:
        workspace = root / "workspace"
        control = root / "control"
        workspace.mkdir()
        control.mkdir()
        return TrialContext(
            workspace=workspace,
            control_root=control,
            environment={"PATH": "/native/bin"},
        )

    def _exposure(self, root: Path) -> McpExposure:
        return McpExposure(
            name="futuremcp",
            command="/tools/futuremcp",
            args=("mcp",),
            cwd=root / "workspace",
            semantic_identity={
                "name": "futuremcp",
                "transport": "stdio",
            },
            environment={"FUTURE_MCP_MODE": "benchmark"},
        )

    def _process(self, tools: list[str], *, next_cursor: object = None):
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "futuremcp", "version": "1"},
            },
        }
        listed = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [{"name": name} for name in tools],
            },
        }
        if next_cursor is not None:
            listed["result"]["nextCursor"] = next_cursor
        stdout = (
            json.dumps(initialize, separators=(",", ":"))
            + "\n"
            + json.dumps(listed, separators=(",", ":"))
            + "\n"
        ).encode()
        return SimpleNamespace(
            executable_missing=False,
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            return_code=0,
            stdout=stdout,
            stderr=b"",
        )

    def test_required_operation_is_proven_from_raw_mcp_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self._context(root)
            exposure = self._exposure(root)
            with mock.patch(
                "benchmarks.harness.mcp_catalog.run_bounded",
                return_value=self._process(["context", "references"]),
            ) as bounded:
                proof = probe_mcp_tool_catalog(
                    context=context,
                    exposure=exposure,
                    subject_id="futuremcp",
                    required_tool="futuremcp_context",
                )

        self.assertTrue(proof["required_tool_visible"])
        self.assertEqual(proof["required_operation"], "context")
        self.assertEqual(proof["tool_names"], ["context", "references"])
        self.assertEqual(proof["tool_count"], 2)
        self.assertEqual(len(proof["catalog_sha256"]), 64)
        self.assertEqual(
            bounded.call_args.kwargs["environment"],
            {
                "PATH": "/native/bin",
                "FUTURE_MCP_MODE": "benchmark",
            },
        )
        stages = bounded.call_args.kwargs["stdin_stages"]
        self.assertEqual(
            [json.loads(line)["method"] for line in stages[0][0].splitlines()],
            ["initialize"],
        )
        self.assertEqual(
            [json.loads(line)["method"] for line in stages[1][0].splitlines()],
            ["notifications/initialized", "tools/list"],
        )

    @unittest.skipIf(os.name == "nt", "select cannot inspect stdin pipes on Windows")
    def test_catalog_waits_for_initialize_and_keeps_stdin_open(self) -> None:
        server = """
import json, os, select, sys
fd = sys.stdin.fileno()
def receive():
    data = bytearray()
    while not data.endswith(b'\\n'):
        chunk = os.read(fd, 1)
        if not chunk:
            raise SystemExit('early EOF')
        data.extend(chunk)
    return json.loads(data)
if receive()['method'] != 'initialize':
    raise SystemExit('initialize missing')
if select.select([fd], [], [], 0)[0]:
    raise SystemExit('request sent before initialize response')
print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {
    'protocolVersion': '2024-11-05'}}), flush=True)
if [receive()['method'], receive()['method']] != [
    'notifications/initialized', 'tools/list'
]:
    raise SystemExit('catalog request order wrong')
if select.select([fd], [], [], 0)[0] and not os.read(fd, 1):
    raise SystemExit('stdin closed before catalog response')
print(json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': {
    'tools': [{'name': 'context'}]}}), flush=True)
if os.read(fd, 1):
    raise SystemExit('unexpected follow-up')
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self._context(root)
            exposure = McpExposure(
                name="futuremcp",
                command=sys.executable,
                args=("-u", "-c", server),
                cwd=context.workspace,
                semantic_identity={"transport": "stdio"},
            )
            proof = probe_mcp_tool_catalog(
                context=context,
                exposure=exposure,
                subject_id="futuremcp",
                required_tool="futuremcp_context",
            )
        self.assertEqual(proof["tool_names"], ["context"])

    def test_initialize_error_stops_catalog_request(self) -> None:
        server = """
import json, os, sys
fd = sys.stdin.fileno()
data = bytearray()
while not data.endswith(b'\\n'):
    data.extend(os.read(fd, 1))
assert json.loads(data)['method'] == 'initialize'
print(json.dumps({'jsonrpc': '2.0', 'id': 1,
    'error': {'code': -32600, 'message': 'not ready'}}), flush=True)
if os.read(fd, 1):
    raise SystemExit('catalog sent after rejected initialize')
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self._context(root)
            exposure = McpExposure(
                name="futuremcp",
                command=sys.executable,
                args=("-u", "-c", server),
                cwd=context.workspace,
                semantic_identity={"transport": "stdio"},
            )
            with self.assertRaisesRegex(ValueError, "MCP initialize failed"):
                probe_mcp_tool_catalog(
                    context=context,
                    exposure=exposure,
                    subject_id="futuremcp",
                    required_tool="futuremcp_context",
                )

    def test_timeout_names_missing_initialize_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self._context(root)
            exposure = self._exposure(root)
            process = self._process([])
            process.timed_out = True
            process.stdout = b""
            process.stderr = b"startup stalled"
            with mock.patch(
                "benchmarks.harness.mcp_catalog.run_bounded", return_value=process
            ):
                with self.assertRaisesRegex(
                    ValueError, "waiting for initialize response: startup stalled"
                ):
                    probe_mcp_tool_catalog(
                        context=context,
                        exposure=exposure,
                        subject_id="futuremcp",
                        required_tool="futuremcp_context",
                    )

    def test_timeout_names_missing_catalog_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self._context(root)
            process = self._process([])
            process.timed_out = True
            process.stdout = process.stdout.splitlines(keepends=True)[0]
            with mock.patch(
                "benchmarks.harness.mcp_catalog.run_bounded", return_value=process
            ):
                with self.assertRaisesRegex(
                    ValueError, "waiting for tools/list response"
                ):
                    probe_mcp_tool_catalog(
                        context=context,
                        exposure=self._exposure(root),
                        subject_id="futuremcp",
                        required_tool="futuremcp_context",
                    )

    @unittest.skipIf(os.name == "nt", "executable script fixtures use POSIX shebangs")
    def test_subject_adapters_reach_staged_mcp_catalogs(self) -> None:
        server = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path

subject = Path(sys.argv[0]).name
workspace = Path.cwd()
if workspace.name != 'workspace' or workspace.parent.name != subject:
    raise SystemExit('wrong workspace')
if subject == 'hashmarks':
    if sys.argv[1:3] != ['--workspace', '.'] or sys.argv[-1] != 'mcp':
        raise SystemExit('wrong Hashmarks command')
    if sys.argv[3] != '--state-dir' or Path(sys.argv[4]).parent.name != 'control':
        raise SystemExit('wrong Hashmarks state directory')
    if not os.environ.get('HASHMARKS_BENCH_SOURCE'):
        raise SystemExit('missing Hashmarks source')
    tool = 'task_evidence'
elif subject == 'enola':
    if len(sys.argv) != 2 or json.loads(Path(sys.argv[1]).read_text())['repo'] != str(workspace):
        raise SystemExit('wrong Enola config')
    if os.environ.get('ENOLA_NO_UPDATE_CHECK') != '1':
        raise SystemExit('missing Enola isolation')
    tool = 'explore'
else:
    raise SystemExit('unknown subject')

def receive():
    data = bytearray()
    while not data.endswith(b'\\n'):
        chunk = os.read(sys.stdin.fileno(), 1)
        if not chunk:
            raise SystemExit('early EOF')
        data.extend(chunk)
    return json.loads(data)

if receive()['method'] != 'initialize':
    raise SystemExit('initialize missing')
print(json.dumps({{'jsonrpc': '2.0', 'id': 1, 'result': {{
    'protocolVersion': '2024-11-05'}}}}), flush=True)
if [receive()['method'], receive()['method']] != [
    'notifications/initialized', 'tools/list'
]:
    raise SystemExit('wrong MCP order')
print(json.dumps({{'jsonrpc': '2.0', 'id': 2, 'result': {{
    'tools': [{{'name': tool}}]}}}}), flush=True)
if os.read(sys.stdin.fileno(), 1):
    raise SystemExit('unexpected follow-up')
"""
        suite_root = (
            Path(__file__).resolve().parents[1]
            / "suites" / "repository-intelligence" / "heldout-v1"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "hashmarks-source"
            hashmarks = source_root / ".venv" / "bin" / "hashmarks"
            enola = root / "bin" / "enola"
            for executable in (hashmarks, enola):
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text(server, encoding="utf-8")
                executable.chmod(0o755)
            proofs = probe_subject_catalog_contracts(
                suite=load_runtime_suite(suite_root),
                contracts=[
                    {"subject_id": "hashmarks", "required_tool": "hashmarks_task_evidence"},
                    {"subject_id": "enola", "required_tool": "enola_explore"},
                ],
                source={
                    "PATH": str(enola.parent) + os.pathsep + os.environ.get("PATH", ""),
                    "HASHMARKS_BENCH_SOURCE": str(source_root),
                },
                root=root / "admission",
            )
        self.assertEqual(
            [(proof["subject_id"], proof["required_operation"]) for proof in proofs],
            [("hashmarks", "task_evidence"), ("enola", "explore")],
        )

    def test_process_exits_without_initialize_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self._context(root)
            exposure = McpExposure(
                name="futuremcp",
                command=sys.executable,
                args=("-c", "raise SystemExit(0)"),
                cwd=context.workspace,
                semantic_identity={"transport": "stdio"},
            )
            with self.assertRaisesRegex(ValueError, "no initialize response"):
                probe_mcp_tool_catalog(
                    context=context,
                    exposure=exposure,
                    subject_id="futuremcp",
                    required_tool="futuremcp_context",
                )

    def test_missing_required_operation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "benchmarks.harness.mcp_catalog.run_bounded",
                return_value=self._process(["search", "references"]),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "required operation is not exposed: context",
                ):
                    probe_mcp_tool_catalog(
                        context=self._context(root),
                        exposure=self._exposure(root),
                        subject_id="futuremcp",
                        required_tool="futuremcp_context",
                    )

    def test_paginated_catalog_is_not_silently_treated_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "benchmarks.harness.mcp_catalog.run_bounded",
                return_value=self._process(["search"], next_cursor="page-2"),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "complete catalog proof is unavailable",
                ):
                    probe_mcp_tool_catalog(
                        context=self._context(root),
                        exposure=self._exposure(root),
                        subject_id="futuremcp",
                        required_tool="futuremcp_context",
                    )

    def test_non_stdio_subject_cannot_claim_stdio_catalog_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exposure = self._exposure(root)
            exposure = McpExposure(
                name=exposure.name,
                command=exposure.command,
                args=exposure.args,
                cwd=exposure.cwd,
                semantic_identity={
                    "name": "futuremcp",
                    "transport": "http",
                },
                environment=exposure.environment,
            )
            with self.assertRaisesRegex(ValueError, "supports stdio only"):
                probe_mcp_tool_catalog(
                    context=self._context(root),
                    exposure=exposure,
                    subject_id="futuremcp",
                    required_tool="futuremcp_context",
                )


if __name__ == "__main__":
    unittest.main()
