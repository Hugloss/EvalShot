from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from benchmarks.__main__ import main as benchmark_main
from benchmarks.openai_responses_routing_probe import OpenAIRoutingProbeError
from benchmarks.openai_routing_dogfood import (
    OpenAIRoutingDogfoodError,
    OpenAIRoutingSettings,
    allocate_run_directory,
    campaign_exit_code,
    list_dogfood_runs,
    load_manifest,
    routing_run_root,
    run_campaign,
    run_saved_campaign,
    select_dogfood_run,
)


def _workspace(root: Path) -> Path:
    root.mkdir()
    for path in (
        "benchmarks/tool_routing.py",
        "benchmarks/tool_routing_trace.py",
    ):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n", encoding="utf-8")
    return root


def _manifest(
    path: Path,
    *,
    forbidden: bool = False,
    routing_expectation: str = "hashmarks-task-evidence-first",
) -> Path:
    prompt = (
        "Use Hashmarks task_evidence to locate the owner."
        if forbidden
        else (
            "Inspect benchmarks/tool_routing.py and report the function that projects "
            "one host call into canonical routing evidence."
            if routing_expectation == "native-read-first"
            else (
                "Locate the exact symbol normalized_call and report its repository path "
                "plus what that symbol projects."
                if routing_expectation == "hashmarks-find-first"
                else "Locate the implementation that maps host-specific repository tool "
                "names into semantic routing classes. Report the owning path."
            )
        )
    )
    path.write_text(
        json.dumps(
            {
                "schema": "agents-cookbook-openai-routing-dogfood-manifest.v3",
                "name": "fixture",
                "tasks": [
                    {
                        "id": "routing-owner",
                        "routing_expectation": routing_expectation,
                        "prompt": prompt,
                        "expected_paths": ["benchmarks/tool_routing.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _receipt(
    outcome: str,
    final_text: str,
    *,
    first_tool: str | None = None,
) -> dict[str, object]:
    tool = first_tool
    if tool is None:
        tool = (
            "mcp__hashmarks__task_evidence"
            if outcome == "PASS"
            else "grep"
        )
    return {
        "catalog": {"schema": "catalog"},
        "trace": {
            "schema": "trace",
            "calls": [{"tool": tool}],
        },
        "score": {"outcome": outcome},
        "openai": {"usage": [{"total_tokens": 123}]},
        "final_text": final_text,
    }


class OpenAIRoutingDogfoodTests(unittest.TestCase):
    def test_easy_settings_require_only_two_nonsecret_choices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tunnel_client = root / "tunnel-client"
            tunnel_client.write_text("#!/bin/sh\n", encoding="utf-8")
            tunnel_client.chmod(0o755)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "OPENAI_ROUTING_TUNNEL_ID=tunnel_" + "a" * 32,
                        "OPENAI_ROUTING_MODEL=gpt-test",
                        f"OPENAI_ROUTING_TUNNEL_CLIENT={tunnel_client}",
                        "OPENAI_API_KEY=must-not-be-read-from-file",
                        "CONTROL_PLANE_API_KEY=must-not-be-read-from-file",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch("pathlib.Path.cwd", return_value=root):
                settings = OpenAIRoutingSettings.load(
                    env_file,
                    host={"PATH": ""},
                )

            self.assertEqual(settings.tunnel_id, "tunnel_" + "a" * 32)
            self.assertEqual(settings.model, "gpt-test")
            self.assertEqual(settings.repeats, 1)
            self.assertEqual(settings.tunnel_client, tunnel_client.resolve())
            self.assertTrue(
                str(settings.handoff).endswith(
                    "Hashmarks/dist/chatgpt-secure-mcp-tunnel-handoff.json"
                )
            )

    def test_empty_optional_env_values_use_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tunnel_client = root / "tunnel-client"
            tunnel_client.write_text("#!/bin/sh\n", encoding="utf-8")
            tunnel_client.chmod(0o755)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "OPENAI_ROUTING_TUNNEL_ID=tunnel_" + "b" * 32,
                        "OPENAI_ROUTING_MODEL=gpt-test",
                        f"OPENAI_ROUTING_TUNNEL_CLIENT={tunnel_client}",
                        "OPENAI_ROUTING_WORKSPACE=",
                        "OPENAI_ROUTING_HANDOFF=",
                        "OPENAI_ROUTING_MANIFEST=",
                        "OPENAI_ROUTING_REPEATS=",
                        "OPENAI_ROUTING_RUN_ROOT=",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            settings = OpenAIRoutingSettings.load(env_file, host={"PATH": ""})

            self.assertEqual(settings.repeats, 1)
            self.assertTrue(
                str(settings.manifest).endswith(
                    "benchmarks/dogfood/openai-routing-v3.json"
                )
            )
            self.assertTrue(
                str(settings.run_root).endswith(
                    ".benchmark-runs/openai-routing"
                )
            )

    def test_numbered_runs_allocate_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_id, first = allocate_run_directory(root)
            second_id, second = allocate_run_directory(root)

            self.assertEqual(first_id, "000001")
            self.assertEqual(second_id, "000002")
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())

    def test_run_inspection_needs_only_run_root_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / "custom-runs"
            env_file = root / ".env"
            env_file.write_text(
                f"OPENAI_ROUTING_RUN_ROOT={run_root}\n",
                encoding="utf-8",
            )
            run_id, run_dir = allocate_run_directory(run_root)
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "status": "COMPLETE",
                        "aggregate": {
                            "hashmarks_first_rate": 1.0,
                            "outcomes": {"PASS": 5},
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(routing_run_root(env_file), run_root.resolve())
            rows = list_dogfood_runs(run_root)
            self.assertEqual(rows[0]["run_id"], run_id)
            self.assertEqual(rows[0]["status"], "COMPLETE")
            self.assertEqual(
                select_dogfood_run(run_root)["run_id"],
                run_id,
            )

    def test_failed_easy_start_is_visible_and_next_run_advances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = OpenAIRoutingSettings(
                workspace=root,
                handoff=root / "handoff.json",
                tunnel_client=root / "tunnel-client",
                tunnel_id="tunnel_" + "c" * 32,
                model="gpt-test",
                manifest=root / "manifest.json",
                repeats=1,
                run_root=root / "runs-root",
            )

            with mock.patch(
                "benchmarks.openai_routing_dogfood.run_campaign",
                side_effect=OpenAIRoutingDogfoodError("preflight rejected"),
            ):
                with self.assertRaisesRegex(
                    OpenAIRoutingDogfoodError,
                    "preflight rejected",
                ):
                    run_saved_campaign(
                        settings=settings,
                        openai_api_key="openai-secret",
                        control_plane_api_key="control-secret",
                    )

            rows = list_dogfood_runs(settings.run_root)
            self.assertEqual(rows[0]["run_id"], "000001")
            self.assertEqual(rows[0]["status"], "PRECHECK_FAILED")
            self.assertTrue(
                (
                    settings.run_root
                    / "runs"
                    / "000001"
                    / "start-error.json"
                ).is_file()
            )
            second_id, _ = allocate_run_directory(settings.run_root)
            self.assertEqual(second_id, "000002")

    def test_easy_cli_new_uses_env_settings_and_numbered_saved_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "OPENAI_ROUTING_TUNNEL_ID=tunnel_" + "d" * 32 + "\n"
                "OPENAI_ROUTING_MODEL=gpt-test\n",
                encoding="utf-8",
            )
            settings = OpenAIRoutingSettings(
                workspace=root,
                handoff=root / "handoff.json",
                tunnel_client=root / "tunnel-client",
                tunnel_id="tunnel_" + "d" * 32,
                model="gpt-test",
                manifest=root / "manifest.json",
                repeats=1,
                run_root=root / "run-root",
            )
            output_dir = settings.run_root / "runs" / "000001"
            summary = {
                "aggregate": {"outcomes": {"PASS": 1}},
                "status": "COMPLETE",
            }

            stdout = StringIO()
            with (
                mock.patch(
                    "benchmarks.__main__.OpenAIRoutingSettings.load",
                    return_value=settings,
                ) as loader,
                mock.patch(
                    "benchmarks.__main__.run_saved_openai_dogfood",
                    return_value=("000001", output_dir, summary),
                ) as runner,
                mock.patch(
                    "benchmarks.__main__.openai_dogfood_exit_code",
                    return_value=0,
                ),
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
                        "openai-routing-new",
                        "--env-file",
                        str(env_file),
                    ]
                )

            self.assertEqual(code, 0)
            loader.assert_called_once_with(env_file)
            kwargs = runner.call_args.kwargs
            self.assertEqual(kwargs["settings"], settings)
            self.assertEqual(kwargs["openai_api_key"], "openai-secret")
            self.assertEqual(
                kwargs["control_plane_api_key"],
                "control-secret",
            )
            rendered = json.loads(stdout.getvalue())
            self.assertEqual(rendered["run_id"], "000001")
            self.assertEqual(rendered["run_root"], str(output_dir))

    def test_easy_cli_runs_and_status_need_only_run_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / "routing-runs"
            env_file = root / ".env"
            env_file.write_text(
                f"OPENAI_ROUTING_RUN_ROOT={run_root}\n",
                encoding="utf-8",
            )
            run_id, run_dir = allocate_run_directory(run_root)
            summary = {
                "status": "COMPLETE",
                "aggregate": {
                    "outcomes": {"PASS": 1},
                    "hashmarks_first_rate": 1.0,
                },
            }
            (run_dir / "summary.json").write_text(
                json.dumps(summary),
                encoding="utf-8",
            )

            stdout = StringIO()
            with redirect_stdout(stdout):
                code = benchmark_main(
                    [
                        "openai-routing-runs",
                        "--env-file",
                        str(env_file),
                    ]
                )
            self.assertEqual(code, 0)
            listed = json.loads(stdout.getvalue())
            self.assertEqual(listed["runs"][0]["run_id"], run_id)

            stdout = StringIO()
            with redirect_stdout(stdout):
                code = benchmark_main(
                    [
                        "openai-routing-status",
                        "--env-file",
                        str(env_file),
                    ]
                )
            self.assertEqual(code, 0)
            status = json.loads(stdout.getvalue())
            self.assertEqual(status["run_id"], run_id)
            self.assertEqual(status["summary"]["status"], "COMPLETE")

    def test_manifest_rejects_tool_directing_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            manifest = _manifest(root / "manifest.json", forbidden=True)
            with self.assertRaisesRegex(
                OpenAIRoutingDogfoodError,
                "names routing/tool authority",
            ):
                load_manifest(manifest, workspace=workspace)

    def test_campaign_aggregates_routing_and_answer_sanity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            manifest = _manifest(root / "manifest.json")
            output = root / "run"
            receipts = [
                _receipt("PASS", "Owned by benchmarks/tool_routing.py"),
                _receipt("FAIL", "Owned by benchmarks/tool_routing.py"),
            ]

            with mock.patch(
                "benchmarks.openai_routing_dogfood.preflight_probe",
                return_value={
                    "schema": "preflight",
                    "status": "READY",
                    "hashmarks_handoff_sha256": "a" * 64,
                    "hashmarks_runtime": {
                        "version": "hashmarks version 0.26.1",
                        "executable_sha256": "b" * 64,
                    },
                    "openai": {"model": "gpt-test"},
                },
            ):
                summary = run_campaign(
                    manifest_path=manifest,
                    workspace=workspace,
                    handoff_path=root / "handoff.json",
                    tunnel_client=root / "tunnel-client",
                    tunnel_id="tunnel_" + "1" * 32,
                    model="gpt-test",
                    repeats=2,
                    output_dir=output,
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    probe_runner=mock.Mock(side_effect=receipts),
                )

            self.assertEqual(summary["status"], "COMPLETE")
            self.assertEqual(
                summary["aggregate"]["outcomes"],
                {"FAIL": 1, "PASS": 1},
            )
            self.assertEqual(
                summary["aggregate"]["routing_fit_counts"],
                {"FAIL": 1, "PASS": 1},
            )
            self.assertEqual(summary["aggregate"]["hashmarks_first_rate"], 0.5)
            self.assertEqual(summary["aggregate"]["routing_fit_rate"], 0.5)
            self.assertEqual(
                summary["aggregate"]["semantic_task_evidence_first_rate"],
                0.5,
            )
            self.assertIsNone(
                summary["aggregate"]["exact_symbol_hashmarks_find_rate"]
            )
            self.assertIsNone(
                summary["aggregate"]["known_path_native_read_rate"]
            )
            self.assertEqual(summary["aggregate"]["answer_path_matches"], 2)
            self.assertEqual(summary["aggregate"]["total_tokens"], 246)
            self.assertEqual(
                summary["candidate"]["hashmarks_runtime"]["version"],
                "hashmarks version 0.26.1",
            )
            self.assertEqual(
                summary["candidate"]["hashmarks_runtime"]["executable_sha256"],
                "b" * 64,
            )
            self.assertEqual(summary["openai"], {"model": "gpt-test"})
            self.assertEqual(campaign_exit_code(summary), 1)
            self.assertTrue((output / "preflight.json").is_file())
            self.assertTrue(
                (output / "trials" / "routing-owner-r01" / "receipt.json").is_file()
            )
            self.assertTrue(
                (output / "trials" / "routing-owner-r02" / "receipt.json").is_file()
            )

    def test_known_symbol_control_rewards_hashmarks_find(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            manifest = _manifest(
                root / "manifest.json",
                routing_expectation="hashmarks-find-first",
            )
            output = root / "run"

            with mock.patch(
                "benchmarks.openai_routing_dogfood.preflight_probe",
                return_value={"schema": "preflight", "status": "READY"},
            ):
                summary = run_campaign(
                    manifest_path=manifest,
                    workspace=workspace,
                    handoff_path=root / "handoff.json",
                    tunnel_client=root / "tunnel-client",
                    tunnel_id="tunnel_" + "6" * 32,
                    model="gpt-test",
                    repeats=1,
                    output_dir=output,
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    probe_runner=mock.Mock(
                        return_value=_receipt(
                            "FAIL",
                            "Owned by benchmarks/tool_routing.py",
                            first_tool="mcp__hashmarks__find",
                        )
                    ),
                )

            self.assertEqual(summary["trials"][0]["outcome"], "FAIL")
            self.assertEqual(summary["trials"][0]["routing_fit"], "PASS")
            self.assertEqual(
                summary["aggregate"]["routing_fit_counts"],
                {"PASS": 1},
            )
            self.assertEqual(
                summary["aggregate"]["exact_symbol_hashmarks_find_rate"],
                1.0,
            )
            self.assertEqual(campaign_exit_code(summary), 0)

    def test_known_symbol_control_fails_when_task_evidence_steals_find(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            manifest = _manifest(
                root / "manifest.json",
                routing_expectation="hashmarks-find-first",
            )
            output = root / "run"

            with mock.patch(
                "benchmarks.openai_routing_dogfood.preflight_probe",
                return_value={"schema": "preflight", "status": "READY"},
            ):
                summary = run_campaign(
                    manifest_path=manifest,
                    workspace=workspace,
                    handoff_path=root / "handoff.json",
                    tunnel_client=root / "tunnel-client",
                    tunnel_id="tunnel_" + "7" * 32,
                    model="gpt-test",
                    repeats=1,
                    output_dir=output,
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    probe_runner=mock.Mock(
                        return_value=_receipt(
                            "PASS",
                            "Owned by benchmarks/tool_routing.py",
                            first_tool="mcp__hashmarks__task_evidence",
                        )
                    ),
                )

            self.assertEqual(summary["trials"][0]["outcome"], "PASS")
            self.assertEqual(summary["trials"][0]["routing_fit"], "FAIL")
            self.assertEqual(
                summary["aggregate"]["exact_symbol_hashmarks_find_rate"],
                0.0,
            )
            self.assertEqual(campaign_exit_code(summary), 1)

    def test_known_path_control_rewards_native_read_not_hashmarks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            manifest = _manifest(
                root / "manifest.json",
                routing_expectation="native-read-first",
            )
            output = root / "run"

            with mock.patch(
                "benchmarks.openai_routing_dogfood.preflight_probe",
                return_value={"schema": "preflight", "status": "READY"},
            ):
                summary = run_campaign(
                    manifest_path=manifest,
                    workspace=workspace,
                    handoff_path=root / "handoff.json",
                    tunnel_client=root / "tunnel-client",
                    tunnel_id="tunnel_" + "4" * 32,
                    model="gpt-test",
                    repeats=1,
                    output_dir=output,
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    probe_runner=mock.Mock(
                        return_value=_receipt(
                            "FAIL",
                            "Owned by benchmarks/tool_routing.py",
                            first_tool="read",
                        )
                    ),
                )

            self.assertEqual(
                summary["trials"][0]["outcome"],
                "FAIL",
            )
            self.assertEqual(
                summary["trials"][0]["routing_fit"],
                "PASS",
            )
            self.assertEqual(
                summary["aggregate"]["routing_fit_counts"],
                {"PASS": 1},
            )
            self.assertEqual(
                summary["aggregate"]["known_path_native_read_rate"],
                1.0,
            )
            self.assertEqual(campaign_exit_code(summary), 0)

    def test_known_path_control_fails_when_hashmarks_steals_direct_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            manifest = _manifest(
                root / "manifest.json",
                routing_expectation="native-read-first",
            )
            output = root / "run"

            with mock.patch(
                "benchmarks.openai_routing_dogfood.preflight_probe",
                return_value={"schema": "preflight", "status": "READY"},
            ):
                summary = run_campaign(
                    manifest_path=manifest,
                    workspace=workspace,
                    handoff_path=root / "handoff.json",
                    tunnel_client=root / "tunnel-client",
                    tunnel_id="tunnel_" + "5" * 32,
                    model="gpt-test",
                    repeats=1,
                    output_dir=output,
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    probe_runner=mock.Mock(
                        return_value=_receipt(
                            "PASS",
                            "Owned by benchmarks/tool_routing.py",
                            first_tool="mcp__hashmarks__task_evidence",
                        )
                    ),
                )

            self.assertEqual(summary["trials"][0]["outcome"], "PASS")
            self.assertEqual(summary["trials"][0]["routing_fit"], "FAIL")
            self.assertEqual(
                summary["aggregate"]["known_path_native_read_rate"],
                0.0,
            )
            self.assertEqual(campaign_exit_code(summary), 1)

    def test_manifest_requires_explicit_routing_expectation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema": "agents-cookbook-openai-routing-dogfood-manifest.v3",
                        "name": "fixture",
                        "tasks": [
                            {
                                "id": "missing-expectation",
                                "prompt": "Locate the owner for routing classification.",
                                "expected_paths": ["benchmarks/tool_routing.py"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                OpenAIRoutingDogfoodError,
                "routing_expectation",
            ):
                load_manifest(manifest, workspace=workspace)

    def test_campaign_aborts_after_first_infrastructure_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            manifest = _manifest(root / "manifest.json")
            output = root / "run"
            runner = mock.Mock(
                side_effect=OpenAIRoutingProbeError("tunnel unavailable")
            )

            with mock.patch(
                "benchmarks.openai_routing_dogfood.preflight_probe",
                return_value={"schema": "preflight", "status": "READY"},
            ):
                summary = run_campaign(
                    manifest_path=manifest,
                    workspace=workspace,
                    handoff_path=root / "handoff.json",
                    tunnel_client=root / "tunnel-client",
                    tunnel_id="tunnel_" + "2" * 32,
                    model="gpt-test",
                    repeats=5,
                    output_dir=output,
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                    probe_runner=runner,
                )

            self.assertEqual(runner.call_count, 1)
            self.assertEqual(summary["status"], "INCOMPLETE")
            self.assertEqual(
                summary["aggregate"]["outcomes"],
                {"INCOMPLETE": 1},
            )
            self.assertEqual(
                summary["aggregate"]["routing_fit_counts"],
                {"INCOMPLETE": 1},
            )
            self.assertEqual(campaign_exit_code(summary), 3)
            self.assertTrue(
                (output / "trials" / "routing-owner-r01" / "error.json").is_file()
            )
            self.assertFalse(
                (output / "trials" / "routing-owner-r02").exists()
            )

    def test_campaign_refuses_existing_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = _workspace(root / "repo")
            manifest = _manifest(root / "manifest.json")
            output = root / "run"
            output.mkdir()

            with self.assertRaisesRegex(
                OpenAIRoutingDogfoodError,
                "already exists",
            ):
                run_campaign(
                    manifest_path=manifest,
                    workspace=workspace,
                    handoff_path=root / "handoff.json",
                    tunnel_client=root / "tunnel-client",
                    tunnel_id="tunnel_" + "3" * 32,
                    model="gpt-test",
                    repeats=1,
                    output_dir=output,
                    openai_api_key="openai-secret",
                    control_plane_api_key="control-secret",
                )


if __name__ == "__main__":
    unittest.main()
