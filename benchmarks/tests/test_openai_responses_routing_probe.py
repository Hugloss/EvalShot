from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from benchmarks.__main__ import main as benchmark_main
from benchmarks.openai_responses_routing_probe import (
    OpenAIRoutingProbeError,
    _NativeRepository,
    _request_payload,
    _running_tunnel,
    _tunnel_environment,
    run_probe,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


def _workspace(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "benchmark@example.invalid")
    _git(root, "config", "user.name", "Benchmark")
    (root / "owner.py").write_text(
        "def checkout_discount() -> int:\n"
        "    return 7\n",
        encoding="utf-8",
    )
    (root / "other.py").write_text(
        "def unrelated() -> int:\n"
        "    return 1\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def _fake_hashmarks(root: Path) -> Path:
    executable = root / "hashmarks"
    executable.write_text(
        "#!/bin/sh\necho 'hashmarks version 0.0-test'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _fake_tunnel_client(root: Path) -> Path:
    executable = root / "tunnel-client"
    executable.write_text(
        "#!/bin/sh\necho 'tunnel-client 1.2.3'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _handoff(
    path: Path,
    workspace: Path,
    executable: Path,
) -> Path:
    executable = executable.resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    payload = {
        "schema": "hashmarks.chatgpt-secure-mcp-tunnel-handoff.v1",
        "status": "READY",
        "workspace": str(workspace.resolve()),
        "source": None,
        "hashmarks": {
            "executable": str(executable),
            "executable_sha256": digest,
            "version": "hashmarks version 0.0-test",
        },
        "mcp_command_argv": [
            str(executable),
            "--workspace",
            str(workspace.resolve()),
            "mcp",
        ],
        "mcp_command": (
            f"{executable} --workspace {workspace.resolve()} mcp"
        ),
        "mcp": {
            "tools": [
                {"name": "repository_context"},
                {"name": "find"},
                {"name": "task_evidence"},
            ]
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@contextlib.contextmanager
def _fake_tunnel(**_kwargs):
    yield {
        "version": "tunnel-client 1.2.3",
        "executable_sha256": "c" * 64,
        "health_url": "http://127.0.0.1:12345",
    }


class OpenAIResponsesRoutingProbeTests(unittest.TestCase):
    def test_native_repository_grep_and_read_are_bounded_tracked_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "owner.py").write_text(
                "first\nCheckout Discount owner\nlast\n",
                encoding="utf-8",
            )
            repository = _NativeRepository(root, ["owner.py"])

            grep = repository.grep("checkout discount")
            self.assertEqual(grep["matches"][0]["path"], "owner.py")
            self.assertEqual(grep["matches"][0]["line"], 2)

            read = repository.read("owner.py", 2, 3)
            self.assertEqual(read["text"], "Checkout Discount owner\nlast")

            with self.assertRaisesRegex(
                OpenAIRoutingProbeError,
                "not tracked",
            ):
                repository.read("missing.py", 1, 1)

    def test_request_is_stateless_serial_and_neutral(self) -> None:
        payload = _request_payload(
            model="gpt-test",
            tools=[],
            items=[{"role": "user", "content": "locate owner"}],
        )
        self.assertIs(payload["store"], False)
        self.assertIs(payload["parallel_tool_calls"], False)
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertNotIn("Hashmarks", payload["instructions"])
        self.assertNotIn("task_evidence", payload["instructions"])

    def test_tunnel_environment_drops_openai_credentials(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PATH": "/bin",
                "HOME": "/home/test",
                "OPENAI_API_KEY": "openai-secret",
                "CONTROL_PLANE_API_KEY": "control-secret",
                "OPENAI_ADMIN_KEY": "admin-secret",
            },
            clear=True,
        ):
            environment = _tunnel_environment()

        self.assertEqual(environment["PATH"], "/bin")
        self.assertEqual(environment["HOME"], "/home/test")
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("CONTROL_PLANE_API_KEY", environment)
        self.assertNotIn("OPENAI_ADMIN_KEY", environment)

    def test_running_tunnel_uses_file_secret_reference(self) -> None:
        class Process:
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                self.returncode = 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.returncode = -9

        captured: dict[str, object] = {}

        def fake_popen(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["env"] = dict(kwargs["env"])
            return Process()

        with tempfile.TemporaryDirectory() as tmp:
            client = Path(tmp) / "tunnel-client"
            client.write_bytes(b"binary")
            with (
                mock.patch(
                    "benchmarks.openai_responses_routing_probe._run",
                    return_value=mock.Mock(stdout="1.2.3\n"),
                ),
                mock.patch(
                    "benchmarks.openai_responses_routing_probe._sha256_file",
                    return_value="d" * 64,
                ),
                mock.patch(
                    "benchmarks.openai_responses_routing_probe._wait_tunnel",
                    return_value="http://127.0.0.1:1234",
                ),
                mock.patch(
                    "benchmarks.openai_responses_routing_probe.subprocess.Popen",
                    side_effect=fake_popen,
                ),
                mock.patch.dict(
                    os.environ,
                    {
                        "PATH": "/bin",
                        "OPENAI_API_KEY": "openai-secret",
                        "CONTROL_PLANE_API_KEY": "control-secret",
                    },
                    clear=True,
                ),
            ):
                with _running_tunnel(
                    tunnel_client=client,
                    tunnel_id="tunnel_" + "a" * 32,
                    mcp_command="/opt/hashmarks --workspace /repo mcp",
                    control_plane_api_key="control-secret",
                ):
                    pass

        argv = captured["argv"]
        assert isinstance(argv, list)
        rendered = " ".join(str(value) for value in argv)
        self.assertIn("--mcp.command", argv)
        self.assertIn("/opt/hashmarks --workspace /repo mcp", argv)
        self.assertIn("file:", rendered)
        self.assertNotIn("control-secret", rendered)
        environment = captured["env"]
        assert isinstance(environment, dict)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("CONTROL_PLANE_API_KEY", environment)

    def test_hashmarks_first_response_scores_pass_without_persisting_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            hashmarks = _fake_hashmarks(root)
            handoff = _handoff(root / "handoff.json", workspace, hashmarks)
            tunnel_client = _fake_tunnel_client(root)

            def requester(payload, *, api_key):
                self.assertEqual(api_key, "openai-secret")
                self.assertIs(payload["store"], False)
                tool_types = [tool["type"] for tool in payload["tools"]]
                self.assertEqual(tool_types, ["mcp", "function", "function"])
                self.assertEqual(
                    payload["tools"][0]["allowed_tools"],
                    ["task_evidence", "find"],
                )
                return {
                    "id": "resp_hashmarks_first",
                    "status": "completed",
                    "usage": {"total_tokens": 123},
                    "output": [
                        {
                            "type": "mcp_list_tools",
                            "server_label": "hashmarks",
                            "tools": [{"name": "task_evidence"}],
                        },
                        {
                            "type": "mcp_call",
                            "server_label": "hashmarks",
                            "name": "task_evidence",
                            "arguments": json.dumps(
                                {"task": "locate checkout discount owner"}
                            ),
                            "output": json.dumps(
                                {"schema": "hashmarks.task-evidence.v3"}
                            ),
                            "error": None,
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "owner.py owns the behavior",
                                }
                            ],
                        },
                    ],
                }

            with mock.patch(
                "benchmarks.openai_responses_routing_probe._running_tunnel",
                _fake_tunnel,
            ):
                receipt = run_probe(
                    workspace=workspace,
                    handoff_path=handoff,
                    tunnel_client=tunnel_client,
                    tunnel_id="tunnel_" + "1" * 32,
                    model="gpt-test",
                    prompt="Locate the implementation owner for checkout discount.",
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    requester=requester,
                )

            self.assertEqual(receipt["score"]["outcome"], "PASS")
            self.assertEqual(
                receipt["trace"]["calls"][0]["tool"],
                "mcp__hashmarks__task_evidence",
            )
            self.assertEqual(receipt["openai"]["model"], "gpt-test")
            self.assertEqual(receipt["final_text"], "owner.py owns the behavior")
            rendered = json.dumps(receipt, sort_keys=True)
            self.assertNotIn("openai-secret", rendered)
            self.assertNotIn("control-secret", rendered)

    def test_hashmarks_find_call_is_preserved_in_ordered_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            hashmarks = _fake_hashmarks(root)
            handoff = _handoff(root / "handoff.json", workspace, hashmarks)
            tunnel_client = _fake_tunnel_client(root)

            def requester(payload, *, api_key):
                self.assertEqual(api_key, "openai-secret")
                return {
                    "id": "resp_find_first",
                    "status": "completed",
                    "output": [
                        {
                            "type": "mcp_list_tools",
                            "server_label": "hashmarks",
                            "tools": [
                                {"name": "task_evidence"},
                                {"name": "find"},
                            ],
                        },
                        {
                            "type": "mcp_call",
                            "server_label": "hashmarks",
                            "name": "find",
                            "arguments": json.dumps({"query": "normalized_call"}),
                            "output": json.dumps(
                                {
                                    "schema": "hashmarks.find.v1",
                                    "results": [
                                        {
                                            "path": "benchmarks/tool_routing.py",
                                            "name": "normalized_call",
                                        }
                                    ],
                                }
                            ),
                            "error": None,
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "benchmarks/tool_routing.py",
                                }
                            ],
                        },
                    ],
                }

            with mock.patch(
                "benchmarks.openai_responses_routing_probe._running_tunnel",
                _fake_tunnel,
            ):
                receipt = run_probe(
                    workspace=workspace,
                    handoff_path=handoff,
                    tunnel_client=tunnel_client,
                    tunnel_id="tunnel_" + "8" * 32,
                    model="gpt-test",
                    prompt="Locate exact symbol normalized_call.",
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    requester=requester,
                )

            self.assertEqual(
                receipt["trace"]["calls"][0]["tool"],
                "mcp__hashmarks__find",
            )
            self.assertEqual(
                receipt["trace"]["calls"][0]["status"],
                "completed",
            )
            self.assertEqual(
                receipt["trace"]["calls"][0]["input"],
                {"query": "normalized_call"},
            )
            self.assertEqual(receipt["score"]["outcome"], "FAIL")

    def test_missing_hashmarks_catalog_scores_environment_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            hashmarks = _fake_hashmarks(root)
            handoff = _handoff(root / "handoff.json", workspace, hashmarks)
            tunnel_client = _fake_tunnel_client(root)

            def requester(payload, *, api_key):
                self.assertEqual(api_key, "openai-secret")
                return {
                    "id": "resp_no_hashmarks",
                    "status": "completed",
                    "output": [
                        {
                            "type": "mcp_list_tools",
                            "server_label": "hashmarks",
                            "tools": [],
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Unable to inspect repository.",
                                }
                            ],
                        },
                    ],
                }

            with mock.patch(
                "benchmarks.openai_responses_routing_probe._running_tunnel",
                _fake_tunnel,
            ):
                receipt = run_probe(
                    workspace=workspace,
                    handoff_path=handoff,
                    tunnel_client=tunnel_client,
                    tunnel_id="tunnel_" + "6" * 32,
                    model="gpt-test",
                    prompt="Locate the implementation owner.",
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    requester=requester,
                )

            self.assertEqual(receipt["score"]["outcome"], "ENVIRONMENT_BLOCKED")
            self.assertEqual(
                receipt["score"]["catalog_admission"]["reason_codes"],
                ["required-subject-tool-missing"],
            )

    def test_native_grep_first_then_hashmarks_scores_fail_and_replays_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            hashmarks = _fake_hashmarks(root)
            handoff = _handoff(root / "handoff.json", workspace, hashmarks)
            tunnel_client = _fake_tunnel_client(root)
            calls = 0

            def requester(payload, *, api_key):
                nonlocal calls
                calls += 1
                self.assertEqual(api_key, "openai-secret")
                if calls == 1:
                    return {
                        "id": "resp_native_first",
                        "status": "completed",
                        "output": [
                            {
                                "type": "mcp_list_tools",
                                "server_label": "hashmarks",
                                "tools": [{"name": "task_evidence"}],
                            },
                            {
                                "type": "function_call",
                                "call_id": "call_grep",
                                "name": "grep",
                                "arguments": json.dumps(
                                    {"query": "checkout_discount"}
                                ),
                                "status": "completed",
                            },
                        ],
                    }

                function_outputs = [
                    item
                    for item in payload["input"]
                    if isinstance(item, dict)
                    and item.get("type") == "function_call_output"
                ]
                self.assertEqual(len(function_outputs), 1)
                self.assertEqual(function_outputs[0]["call_id"], "call_grep")
                self.assertIn("agents-cookbook-native-grep.v1", function_outputs[0]["output"])
                return {
                    "id": "resp_hashmarks_second",
                    "status": "completed",
                    "output": [
                        {
                            "type": "mcp_call",
                            "server_label": "hashmarks",
                            "name": "task_evidence",
                            "arguments": json.dumps(
                                {"task": "locate checkout discount owner"}
                            ),
                            "output": json.dumps(
                                {"schema": "hashmarks.task-evidence.v3"}
                            ),
                            "error": None,
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "owner.py",
                                }
                            ],
                        },
                    ],
                }

            with mock.patch(
                "benchmarks.openai_responses_routing_probe._running_tunnel",
                _fake_tunnel,
            ):
                receipt = run_probe(
                    workspace=workspace,
                    handoff_path=handoff,
                    tunnel_client=tunnel_client,
                    tunnel_id="tunnel_" + "2" * 32,
                    model="gpt-test",
                    prompt="Locate the implementation owner for checkout discount.",
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    requester=requester,
                )

            self.assertEqual(calls, 2)
            self.assertEqual(receipt["score"]["outcome"], "FAIL")
            self.assertEqual(
                [row["tool"] for row in receipt["trace"]["calls"]],
                ["grep", "mcp__hashmarks__task_evidence"],
            )

    def test_stale_hashmarks_handoff_fails_before_tunnel_or_model_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            hashmarks = _fake_hashmarks(root)
            handoff = _handoff(root / "handoff.json", workspace, hashmarks)
            hashmarks.write_text(
                "#!/bin/sh\necho 'hashmarks version changed'\n",
                encoding="utf-8",
            )
            hashmarks.chmod(0o755)
            tunnel_client = _fake_tunnel_client(root)

            with (
                mock.patch(
                    "benchmarks.openai_responses_routing_probe._running_tunnel"
                ) as tunnel,
                self.assertRaisesRegex(
                    OpenAIRoutingProbeError,
                    "executable changed after tunnel handoff qualification",
                ),
            ):
                run_probe(
                    workspace=workspace,
                    handoff_path=handoff,
                    tunnel_client=tunnel_client,
                    tunnel_id="tunnel_" + "a" * 32,
                    model="gpt-test",
                    prompt="Locate owner.",
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    requester=mock.Mock(),
                )

            tunnel.assert_not_called()

    def test_dirty_workspace_fails_before_tunnel_or_model_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            hashmarks = _fake_hashmarks(root)
            handoff = _handoff(root / "handoff.json", workspace, hashmarks)
            (workspace / "owner.py").write_text("changed\n", encoding="utf-8")
            tunnel_client = _fake_tunnel_client(root)

            with (
                mock.patch(
                    "benchmarks.openai_responses_routing_probe._running_tunnel"
                ) as tunnel,
                self.assertRaisesRegex(
                    OpenAIRoutingProbeError,
                    "clean tracked workspace",
                ),
            ):
                run_probe(
                    workspace=workspace,
                    handoff_path=handoff,
                    tunnel_client=tunnel_client,
                    tunnel_id="tunnel_" + "3" * 32,
                    model="gpt-test",
                    prompt="Locate owner.",
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    requester=mock.Mock(),
                )

            tunnel.assert_not_called()

    def test_cli_writes_canonical_artifacts_and_returns_routing_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "repo"
            workspace.mkdir()
            handoff = root / "handoff.json"
            handoff.write_text("{}", encoding="utf-8")
            tunnel_client = _fake_tunnel_client(root)
            prompt = root / "prompt.txt"
            prompt.write_text("Locate owner.\n", encoding="utf-8")
            output = root / "out"
            receipt = {
                "catalog": {"schema": "catalog"},
                "trace": {"schema": "trace"},
                "score": {"outcome": "FAIL"},
                "schema": "receipt",
            }

            stdout = StringIO()
            with (
                mock.patch(
                    "benchmarks.__main__.run_openai_routing_probe",
                    return_value=receipt,
                ) as runner,
                mock.patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "openai-secret",
                        "CONTROL_PLANE_API_KEY": "control-secret",
                    },
                    clear=False,
                ),
                redirect_stdout(stdout),
            ):
                code = benchmark_main(
                    [
                        "openai-routing-probe",
                        "--workspace",
                        str(workspace),
                        "--handoff",
                        str(handoff),
                        "--tunnel-client",
                        str(tunnel_client),
                        "--tunnel-id",
                        "tunnel_" + "5" * 32,
                        "--model",
                        "gpt-test",
                        "--prompt-file",
                        str(prompt),
                        "--output-dir",
                        str(output),
                    ]
                )

            self.assertEqual(code, 1)
            kwargs = runner.call_args.kwargs
            self.assertEqual(kwargs["openai_api_key"], "openai-secret")
            self.assertEqual(kwargs["control_plane_api_key"], "control-secret")
            self.assertEqual(kwargs["prompt"], "Locate owner.\n")
            self.assertEqual(
                json.loads((output / "score.json").read_text(encoding="utf-8")),
                {"outcome": "FAIL"},
            )
            self.assertEqual(
                stdout.getvalue().strip(),
                str(output / "receipt.json"),
            )

    def test_handoff_bound_to_other_workspace_fails_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            other = root / "other"
            other.mkdir()
            hashmarks = _fake_hashmarks(root)
            handoff = _handoff(root / "handoff.json", other, hashmarks)
            tunnel_client = _fake_tunnel_client(root)

            with self.assertRaisesRegex(
                OpenAIRoutingProbeError,
                "different workspace",
            ):
                run_probe(
                    workspace=workspace,
                    handoff_path=handoff,
                    tunnel_client=tunnel_client,
                    tunnel_id="tunnel_" + "4" * 32,
                    model="gpt-test",
                    prompt="Locate owner.",
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    requester=mock.Mock(),
                )


if __name__ == "__main__":
    unittest.main()
