"""Contracts for the experimental Harbor cross-harness projection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harbor_matrix import (
    MATRIX_SCHEMA,
    REPORT_SCHEMA,
    HarborMatrixError,
    HarborSettings,
    MatrixMode,
    build_report,
    harbor_argv,
    load_matrix,
    plan_rows,
    preflight,
    subject_query_surface_projection,
    subject_tool_projection,
    write_mcp_config,
)
from benchmarks.config import BenchmarkConfig
from benchmarks.harness.harbor_backend import (
    credential_file,
    mcp_configs,
    settings_from_config,
)
from benchmarks.matrix_profiles import load_profile
from benchmarks.harness.suite import load_suite


ROOT = Path(__file__).resolve().parents[2]
MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-v1.json"
)
ABLATION_MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-ablation-v1.json"
)
FIND_ABLATION_MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-find-ablation-v1.json"
)
VERIFICATION_EXPLANATION_MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-verification-explanation-ablation-v1.json"
)
SUITE = (
    ROOT
    / "benchmarks"
    / "suites"
    / "repository-intelligence"
    / "heldout-v1"
)
BEHAVIORAL_SUITE = (
    ROOT
    / "benchmarks"
    / "suites"
    / "repository-intelligence"
    / "behavioral-v4"
)


class HarborMatrixTests(unittest.TestCase):
    def test_checked_in_matrix_has_small_smoke_and_paired_matrix(self) -> None:
        matrix = load_matrix(MATRIX)

        self.assertEqual(
            matrix["schema"],
            MATRIX_SCHEMA,
        )
        self.assertEqual(
            matrix["subjects"],
            ["none", "hashmarks"],
        )
        self.assertEqual(
            matrix["harnesses"],
            ["opencode", "codex", "claude-code"],
        )
        self.assertEqual(
            matrix["modes"]["smoke"],
            {
                "tasks": ["locate-prefix-path-enumerator"],
                "attempts": 1,
            },
        )
        self.assertEqual(
            matrix["modes"]["matrix"]["attempts"],
            3,
        )

    def test_checked_in_ablation_matrix_has_four_controlled_arms(self) -> None:
        matrix = load_matrix(ABLATION_MATRIX)

        self.assertEqual(
            matrix["subjects"],
            [
                "none",
                "hashmarks",
                "hashmarks-no-task-evidence",
                "hashmarks-task-evidence-only",
            ],
        )
        self.assertEqual(
            matrix["tool_projections"],
            {
                "hashmarks-no-task-evidence": {
                    "exclude": ["task_evidence"],
                },
                "hashmarks-task-evidence-only": {
                    "include": ["task_evidence"],
                },
            },
        )
        self.assertEqual(
            matrix["ablation"],
            {
                "component": "task_evidence",
                "arms": {
                    "bare": "none",
                    "full": "hashmarks",
                    "remove": "hashmarks-no-task-evidence",
                    "only": "hashmarks-task-evidence-only",
                },
            },
        )

    def test_checked_in_find_ablation_uses_known_exact_symbol_controls(
        self,
    ) -> None:
        matrix = load_matrix(FIND_ABLATION_MATRIX)

        self.assertEqual(
            matrix["ablation"],
            {
                "component": "find",
                "arms": {
                    "bare": "none",
                    "full": "hashmarks",
                    "remove": "hashmarks-no-find",
                    "only": "hashmarks-find-only",
                },
            },
        )
        self.assertEqual(
            matrix["tool_projections"],
            {
                "hashmarks-no-find": {"exclude": ["find"]},
                "hashmarks-find-only": {"include": ["find"]},
            },
        )
        self.assertEqual(
            matrix["modes"]["matrix"]["tasks"],
            [
                "lookup-known-symbol-paths-under",
                "lookup-known-symbol-sync-remove-stale-paths",
                "lookup-known-symbol-run-poll-delay",
            ],
        )

    def test_verification_explanation_ablation_is_selector_scoped(
        self,
    ) -> None:
        matrix = load_matrix(VERIFICATION_EXPLANATION_MATRIX)
        canonical_surfaces = (
            "change-intelligence",
            "verification-explanation",
            "freshness",
        )
        canonical_tools = (
            "repository_context",
            "repository_intelligence_query",
            "task_evidence",
        )

        self.assertEqual(
            matrix["ablation"],
            {
                "component": "repository_intelligence_query",
                "selector": {
                    "argument": "surface_name",
                    "value": "verification-explanation",
                },
                "arms": {
                    "bare": "none",
                    "full": "hashmarks",
                    "remove": "hashmarks-no-verification-explanation",
                    "only": "hashmarks-verification-explanation-only",
                },
            },
        )
        self.assertEqual(
            subject_tool_projection(
                matrix,
                "hashmarks-no-verification-explanation",
                canonical_tools,
            ),
            canonical_tools,
        )
        self.assertEqual(
            subject_tool_projection(
                matrix,
                "hashmarks-verification-explanation-only",
                canonical_tools,
            ),
            ("repository_intelligence_query",),
        )
        self.assertEqual(
            subject_query_surface_projection(
                matrix,
                "hashmarks-no-verification-explanation",
                canonical_surfaces,
            ),
            ("change-intelligence", "freshness"),
        )
        self.assertEqual(
            subject_query_surface_projection(
                matrix,
                "hashmarks-verification-explanation-only",
                canonical_surfaces,
            ),
            ("verification-explanation",),
        )

    def test_selector_ablation_rejects_tool_level_removal_drift(self) -> None:
        matrix = load_matrix(VERIFICATION_EXPLANATION_MATRIX)
        matrix["tool_projections"][
            "hashmarks-no-verification-explanation"
        ] = {"exclude": ["repository_intelligence_query"]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "matrix.json"
            path.write_text(json.dumps(matrix), encoding="utf-8")
            with self.assertRaisesRegex(
                HarborMatrixError,
                "remove arm must preserve the full tool catalog",
            ):
                load_matrix(path)

    def test_harbor_only_exact_symbol_tasks_do_not_expand_native_experiment(
        self,
    ) -> None:
        suite = load_suite(SUITE)
        native_tasks = set(suite.experiment["tasks"])
        harbor_only = {
            "lookup-known-symbol-paths-under",
            "lookup-known-symbol-sync-remove-stale-paths",
            "lookup-known-symbol-run-poll-delay",
        }

        self.assertEqual(len(native_tasks), 12)
        self.assertTrue(harbor_only.issubset(suite.tasks))
        self.assertTrue(harbor_only.isdisjoint(native_tasks))

    def test_ablation_contract_rejects_projection_semantic_drift(self) -> None:
        matrix = load_matrix(FIND_ABLATION_MATRIX)
        matrix["tool_projections"]["hashmarks-no-find"] = {
            "exclude": ["task_evidence"]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "matrix.json"
            path.write_text(
                json.dumps(matrix),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                HarborMatrixError,
                "remove arm must exclude only its component",
            ):
                load_matrix(path)

    def test_task_evidence_ablation_projects_exact_canonical_tool_sets(self) -> None:
        matrix = load_matrix(ABLATION_MATRIX)
        canonical = (
            "repository_context",
            "find",
            "task_evidence",
            "change_impact",
        )

        self.assertEqual(
            subject_tool_projection(
                matrix,
                "hashmarks-no-task-evidence",
                canonical,
            ),
            (
                "repository_context",
                "find",
                "change_impact",
            ),
        )
        self.assertEqual(
            subject_tool_projection(
                matrix,
                "hashmarks-task-evidence-only",
                canonical,
            ),
            ("task_evidence",),
        )
        self.assertEqual(
            subject_tool_projection(matrix, "hashmarks", canonical),
            canonical,
        )
        self.assertIsNone(
            subject_tool_projection(matrix, "none", canonical)
        )

    def test_plan_pairs_bare_and_hashmarks_per_harness(self) -> None:
        rows = plan_rows(
            harnesses=("opencode", "codex"),
            tasks=("task-a",),
            attempts=2,
        )

        self.assertEqual(len(rows), 8)
        self.assertEqual(
            {
                (
                    row["harness"],
                    row["subject"],
                    row["attempt"],
                )
                for row in rows
            },
            {
                ("opencode", "none", 1),
                ("opencode", "none", 2),
                ("opencode", "hashmarks", 1),
                ("opencode", "hashmarks", 2),
                ("codex", "none", 1),
                ("codex", "none", 2),
                ("codex", "hashmarks", 1),
                ("codex", "hashmarks", 2),
            },
        )

    def test_hashmarks_is_a_runtime_mcp_overlay_not_a_different_task_image(
        self,
    ) -> None:
        settings = HarborSettings(
            executable="harbor",
            model="provider/model",
            root=Path("/runs"),
            hashmarks_source=Path("/hashmarks"),
            passthrough_env_keys=(),
        )
        task = Path("/tasks/localize")
        jobs = Path("/runs/jobs")
        mcp = Path("/runs/hashmarks.mcp.json")

        bare = harbor_argv(
            settings=settings,
            task_path=task,
            harness="codex",
            trial_id="bare",
            jobs_root=jobs,
            credential_file=None,
            mcp_config=None,
        )
        treated = harbor_argv(
            settings=settings,
            task_path=task,
            harness="codex",
            trial_id="treated",
            jobs_root=jobs,
            credential_file=None,
            mcp_config=mcp,
        )

        self.assertEqual(
            bare[bare.index("-p") + 1],
            treated[treated.index("-p") + 1],
        )
        self.assertNotIn("--mcp-config", bare)
        self.assertEqual(
            treated[treated.index("--mcp-config") + 1],
            str(mcp),
        )

    def test_mcp_config_is_trial_scoped_stdio_with_explicit_workspace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_mcp_config(Path(tmp) / "hashmarks.mcp.json")
            value = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            value,
            {
                "mcpServers": {
                    "hashmarks": {
                        "command": "hashmarks",
                        "args": [
                            "--workspace",
                            "/workspace",
                            "--state-dir",
                            "/tmp/hashmarks-state",
                            "mcp",
                        ],
                    }
                }
            },
        )

    def test_mcp_config_can_freeze_one_projected_tool_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_mcp_config(
                root / "task-evidence-only.mcp.json",
                tool_names=("task_evidence",),
            )
            value = json.loads(path.read_text(encoding="utf-8"))
            configs = mcp_configs(
                root,
                preflight_receipt={
                    "hashmarks": {
                        "treatments": {
                            "hashmarks": {
                                "tools": ["find", "task_evidence"],
                                "projection_identity": "sha256:full",
                                "source_contract_identity": "sha256:full",
                                "full_contract": True,
                            },
                            "hashmarks-task-evidence-only": {
                                "tools": ["task_evidence"],
                                "projection_identity": "sha256:only",
                                "source_contract_identity": "sha256:full",
                                "full_contract": False,
                            },
                        }
                    }
                },
            )

            self.assertEqual(
                value["mcpServers"]["hashmarks"]["args"][-3:],
                ["mcp", "--tool", "task_evidence"],
            )
            self.assertIsNone(configs["none"])
            self.assertIn("hashmarks", configs)
            self.assertIn("hashmarks-task-evidence-only", configs)
            projected = json.loads(
                configs["hashmarks-task-evidence-only"].read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                projected["mcpServers"]["hashmarks"]["args"][-3:],
                ["mcp", "--tool", "task_evidence"],
            )

    def test_mcp_config_can_freeze_query_surface_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_mcp_config(
                Path(tmp) / "verification-only.mcp.json",
                tool_names=("repository_intelligence_query",),
                query_surfaces=("verification-explanation",),
            )
            value = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            value["mcpServers"]["hashmarks"]["args"][-5:],
            [
                "mcp",
                "--tool",
                "repository_intelligence_query",
                "--query-surface",
                "verification-explanation",
            ],
        )

    def test_settings_keep_secret_values_out_of_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / ".env"
            env.write_text(
                "\n".join(
                    (
                        f"HASHMARKS_BENCH_SOURCE={root / 'Hashmarks'}",
                        "BENCHMARK_HARBOR_MODEL=provider/model",
                        "BENCHMARK_PASSTHROUGH_ENV_KEYS=OPENAI_API_KEY",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch(
                "benchmarks.harness.harbor_backend.shutil.which",
                return_value="/usr/bin/tool",
            ):
                settings = settings_from_config(
                    BenchmarkConfig.load(
                        env,
                        host={"OPENAI_API_KEY": "secret-value", "PATH": "/bin"},
                    ),
                    load_profile("harbor-smoke"),
                )

        self.assertEqual(
            settings.passthrough_env_keys,
            ("OPENAI_API_KEY",),
        )
        self.assertNotIn(
            "secret-value",
            repr(settings),
        )

    def test_harbor_credential_file_is_private_and_replaced_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = HarborSettings(
                executable="harbor",
                model="provider/model",
                root=root,
                hashmarks_source=root,
                passthrough_env_keys=("OPENAI_API_KEY",),
            )
            first = credential_file(
                root, settings=settings, host={"OPENAI_API_KEY": "first"},
            )
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            self.assertIn("first", first.read_text(encoding="utf-8"))
            second = credential_file(
                root, settings=settings, host={"OPENAI_API_KEY": "second"},
            )
            self.assertEqual(second, first)
            self.assertEqual(second.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("first", second.read_text(encoding="utf-8"))

    def test_preflight_is_model_free_and_binds_hashmarks_contract(self) -> None:
        suite = load_suite(SUITE)
        settings = HarborSettings(
            executable="harbor",
            model="provider/model",
            root=Path("/runs"),
            hashmarks_source=Path("/hashmarks"),
            passthrough_env_keys=("OPENAI_API_KEY",),
        )
        matrix = load_matrix(MATRIX)
        mode = MatrixMode(
            tasks=("locate-prefix-path-enumerator",),
            attempts=1,
        )

        with (
            mock.patch(
                "benchmarks.harbor_matrix._hashmarks_identity",
                return_value={
                    "commit": "a" * 40,
                    "tree": "b" * 40,
                    "working_copy_sha256": "c" * 64,
                    "working_copy_clean": True,
                    "executable": {
                        "path": "/hashmarks/.venv/bin/hashmarks",
                        "sha256": "d" * 64,
                        "version": "hashmarks version 0.test",
                    },
                },
            ),
            mock.patch(
                "benchmarks.harbor_matrix._hashmarks_probe",
                return_value={
                    "contract_identity": "sha256:mcp",
                    "operation_contract_identity": "sha256:ops",
                    "tool_count": 4,
                    "tools": [
                        "repository_context",
                        "find",
                        "task_evidence",
                        "change_impact",
                    ],
                },
            ),
            mock.patch(
                "benchmarks.harbor_matrix._require_command",
                side_effect=("harbor 0.test", "27.0"),
            ) as command,
        ):
            receipt = preflight(
                settings=settings,
                matrix=matrix,
                suite=suite,
                mode=mode,
                harnesses=("opencode",),
                tasks=("locate-prefix-path-enumerator",),
                host={"OPENAI_API_KEY": "secret-value"},
            )

        self.assertTrue(receipt["ready"])
        self.assertEqual(
            receipt["hashmarks"]["mcp_contract_identity"],
            "sha256:mcp",
        )
        self.assertEqual(
            receipt["hashmarks"]["executable"]["sha256"],
            "d" * 64,
        )
        self.assertEqual(
            receipt["trials"],
            2,
        )
        self.assertFalse(
            receipt["secrets_in_receipt"],
        )
        self.assertNotIn(
            "secret-value",
            json.dumps(receipt),
        )
        self.assertEqual(
            command.call_count,
            2,
        )

    def test_ablation_preflight_binds_projection_identity_and_observed_tools(
        self,
    ) -> None:
        suite = load_suite(SUITE)
        settings = HarborSettings(
            executable="harbor",
            model="provider/model",
            root=Path("/runs"),
            hashmarks_source=Path("/hashmarks"),
            passthrough_env_keys=(),
        )
        matrix = load_matrix(ABLATION_MATRIX)
        mode = MatrixMode(
            tasks=("locate-prefix-path-enumerator",),
            attempts=1,
        )
        canonical = [
            "repository_context",
            "find",
            "task_evidence",
            "change_impact",
        ]

        def probe(
            _settings,
            _host,
            *,
            executable,
            tool_names=None,
            query_surfaces=None,
        ):
            self.assertEqual(executable, "/hashmarks/.venv/bin/hashmarks")
            self.assertIsNone(query_surfaces)
            if tool_names is None:
                return {
                    "contract_identity": "sha256:mcp",
                    "operation_contract_identity": "sha256:ops",
                    "tool_count": len(canonical),
                    "tools": canonical,
                }
            selected = list(tool_names)
            return {
                "contract_identity": "sha256:mcp",
                "operation_contract_identity": "sha256:ops",
                "tool_count": len(canonical),
                "tools": canonical,
                "projection": {
                    "source_contract_identity": "sha256:mcp",
                    "tools": selected,
                    "observed_tools": selected,
                    "projection_identity": "sha256:" + "-".join(selected),
                },
            }

        with (
            mock.patch(
                "benchmarks.harbor_matrix._hashmarks_identity",
                return_value={
                    "commit": "a" * 40,
                    "tree": "b" * 40,
                    "working_copy_sha256": "c" * 64,
                    "working_copy_clean": True,
                    "executable": {
                        "path": "/hashmarks/.venv/bin/hashmarks",
                        "sha256": "d" * 64,
                        "version": "hashmarks version 0.test",
                    },
                },
            ),
            mock.patch(
                "benchmarks.harbor_matrix._hashmarks_probe",
                side_effect=probe,
            ),
            mock.patch(
                "benchmarks.harbor_matrix._require_command",
                side_effect=("harbor 0.test", "27.0"),
            ),
        ):
            receipt = preflight(
                settings=settings,
                matrix=matrix,
                suite=suite,
                mode=mode,
                harnesses=("opencode",),
                tasks=("locate-prefix-path-enumerator",),
                host={},
            )

        treatments = receipt["hashmarks"]["treatments"]
        self.assertEqual(receipt["trials"], 4)
        self.assertTrue(treatments["hashmarks"]["full_contract"])
        self.assertEqual(
            treatments["hashmarks-no-task-evidence"]["tools"],
            [
                "repository_context",
                "find",
                "change_impact",
            ],
        )
        self.assertEqual(
            treatments["hashmarks-task-evidence-only"]["tools"],
            ["task_evidence"],
        )
        self.assertFalse(
            treatments["hashmarks-task-evidence-only"]["full_contract"]
        )
        self.assertTrue(
            str(
                treatments["hashmarks-task-evidence-only"][
                    "projection_identity"
                ]
            ).startswith("sha256:")
        )
        self.assertEqual(
            receipt["hashmarks"]["ablation"],
            matrix["ablation"],
        )

    def test_selector_preflight_binds_product_owned_query_surfaces(
        self,
    ) -> None:
        suite = load_suite(BEHAVIORAL_SUITE)
        settings = HarborSettings(
            executable="harbor",
            model="provider/model",
            root=Path("/runs"),
            hashmarks_source=Path("/hashmarks"),
            passthrough_env_keys=(),
        )
        matrix = load_matrix(VERIFICATION_EXPLANATION_MATRIX)
        mode = MatrixMode(tasks=("verification-00",), attempts=1)
        canonical_tools = [
            "repository_context",
            "task_evidence",
            "repository_intelligence_query",
        ]
        canonical_surfaces = [
            "change-intelligence",
            "verification-explanation",
            "freshness",
        ]

        def probe(
            _settings,
            _host,
            *,
            executable,
            tool_names=None,
            query_surfaces=None,
        ):
            self.assertEqual(executable, "/hashmarks/bin/hashmarks")
            if tool_names is None and query_surfaces is None:
                return {
                    "contract_identity": "sha256:mcp",
                    "operation_contract_identity": "sha256:ops",
                    "tool_count": len(canonical_tools),
                    "tools": canonical_tools,
                    "repository_intelligence_query_surfaces": canonical_surfaces,
                }
            selected_tools = (
                canonical_tools
                if tool_names is None
                else list(tool_names)
            )
            selected_surfaces = (
                canonical_surfaces
                if query_surfaces is None
                else list(query_surfaces)
            )
            return {
                "contract_identity": "sha256:mcp",
                "operation_contract_identity": "sha256:ops",
                "tool_count": len(canonical_tools),
                "tools": canonical_tools,
                "repository_intelligence_query_surfaces": canonical_surfaces,
                "projection": {
                    "source_contract_identity": "sha256:mcp",
                    "tools": selected_tools,
                    "observed_tools": selected_tools,
                    "repository_intelligence_query_surfaces": selected_surfaces,
                    "observed_repository_intelligence_query_surfaces": (
                        selected_surfaces
                    ),
                    "projection_identity": (
                        "sha256:"
                        + "-".join(selected_tools)
                        + ":"
                        + "-".join(selected_surfaces)
                    ),
                },
            }

        with (
            mock.patch(
                "benchmarks.harbor_matrix._hashmarks_identity",
                return_value={
                    "commit": "a" * 40,
                    "tree": "b" * 40,
                    "working_copy_sha256": "c" * 64,
                    "working_copy_clean": True,
                    "executable": {
                        "path": "/hashmarks/bin/hashmarks",
                        "sha256": "d" * 64,
                        "version": "hashmarks version 0.test",
                    },
                },
            ),
            mock.patch(
                "benchmarks.harbor_matrix._hashmarks_probe",
                side_effect=probe,
            ),
            mock.patch(
                "benchmarks.harbor_matrix._require_command",
                side_effect=("harbor 0.test", "27.0"),
            ),
        ):
            receipt = preflight(
                settings=settings,
                matrix=matrix,
                suite=suite,
                mode=mode,
                harnesses=("codex",),
                tasks=("verification-00",),
                host={},
            )

        treatments = receipt["hashmarks"]["treatments"]
        self.assertEqual(
            receipt["hashmarks"][
                "canonical_repository_intelligence_query_surfaces"
            ],
            canonical_surfaces,
        )
        self.assertEqual(
            treatments["hashmarks-no-verification-explanation"]["tools"],
            canonical_tools,
        )
        self.assertEqual(
            treatments["hashmarks-no-verification-explanation"][
                "repository_intelligence_query_surfaces"
            ],
            ["change-intelligence", "freshness"],
        )
        self.assertEqual(
            treatments["hashmarks-verification-explanation-only"]["tools"],
            ["repository_intelligence_query"],
        )
        self.assertEqual(
            treatments["hashmarks-verification-explanation-only"][
                "repository_intelligence_query_surfaces"
            ],
            ["verification-explanation"],
        )
        self.assertEqual(
            receipt["hashmarks"]["ablation"],
            matrix["ablation"],
        )

    def test_report_calculates_hashmarks_uplift_and_harness_spread(self) -> None:
        rows = []
        for harness, bare, treated in (
            ("opencode", (True, False), (True, True)),
            ("codex", (False, False), (True, True)),
        ):
            for subject, outcomes in (
                ("none", bare),
                ("hashmarks", treated),
            ):
                for attempt, success in enumerate(outcomes, 1):
                    rows.append(
                        {
                            "harness": harness,
                            "subject": subject,
                            "attempt": attempt,
                            "status": "COMPLETE",
                            "success": success,
                        }
                    )

        report = build_report(rows)

        self.assertEqual(
            report["schema"],
            REPORT_SCHEMA,
        )
        self.assertEqual(
            report["hashmarks_uplift"],
            {
                "codex": 1.0,
                "opencode": 0.5,
            },
        )
        self.assertEqual(
            report["harness_spread"],
            {
                "bare": 0.5,
                "hashmarks": 0.0,
                "reduction": 0.5,
                "reduction_fraction": 1.0,
            },
        )


if __name__ == "__main__":
    unittest.main()
