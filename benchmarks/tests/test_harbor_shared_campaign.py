"""Harbor matrices use the ordinary campaign store and recovery contract."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.__main__ import main
from benchmarks.harbor_matrix import HarborSettings
from benchmarks.matrix_profiles import harbor_suite, load_profile


class HarborSharedCampaignTests(unittest.TestCase):
    def test_registry_projects_canonical_and_ablation_trial_counts(self) -> None:
        for name, count in (
            ("harbor-smoke", 6),
            ("harbor-full", 54),
            ("harbor-ablation-smoke", 12),
            ("harbor-ablation-full", 108),
            ("harbor-find-ablation-smoke", 12),
            ("harbor-find-ablation-full", 108),
            ("harbor-change-impact-ablation-smoke", 12),
            ("harbor-change-impact-ablation-full", 144),
            ("harbor-post-change-ablation-smoke", 12),
            ("harbor-post-change-ablation-full", 144),
            ("harbor-correlate-evidence-ablation-smoke", 12),
            ("harbor-correlate-evidence-ablation-full", 144),
            ("harbor-repository-declarations-ablation-smoke", 12),
            ("harbor-repository-declarations-ablation-full", 144),
            ("harbor-dependency-codemap-ablation-smoke", 12),
            ("harbor-dependency-codemap-ablation-full", 144),
            ("harbor-verification-explanation-ablation-smoke", 12),
            ("harbor-verification-explanation-ablation-full", 144),
        ):
            with self.subTest(name=name):
                suite, _ = harbor_suite(load_profile(name))
                self.assertEqual(len(suite.trial_definitions()), count)

    def _environment(
        self,
        root: Path,
        *,
        ablation_component: str | None = None,
        selector_value: str | None = None,
    ):
        env = root / ".env"
        env.write_text("HASHMARKS_BENCH_SOURCE=/unused\nBENCHMARK_HARBOR_MODEL=provider/model\n")
        settings = HarborSettings(
            executable="harbor",
            model="provider/model",
            root=root,
            hashmarks_source=root,
            passthrough_env_keys=(),
        )

        def prepare(**kwargs):
            destination = kwargs["destination"]
            destination.mkdir(parents=True)
            (destination / "task.txt").write_text("frozen", encoding="utf-8")
            return destination

        full_tools = [
            "repository_context",
            "find",
            "task_evidence",
            "change_impact",
            "correlate_evidence",
            "repository_declarations",
            "dependency_codemap",
            "repository_intelligence_query",
            "post_change",
        ]
        full_query_surfaces = [
            "change-intelligence",
            "verification-explanation",
            "freshness",
        ]
        treatments = {
            "hashmarks": {
                "tools": full_tools,
                "repository_intelligence_query_surfaces": full_query_surfaces,
                "projection_identity": "sha256:full",
                "source_contract_identity": "sha256:full",
                "full_contract": True,
            },
        }
        ablation = None
        if ablation_component is not None:
            if selector_value is None:
                suffix = ablation_component.replace("_", "-")
                remove_subject = f"hashmarks-no-{suffix}"
                only_subject = f"hashmarks-{suffix}-only"
                treatments.update(
                    {
                        remove_subject: {
                            "tools": [
                                tool
                                for tool in full_tools
                                if tool != ablation_component
                            ],
                            "repository_intelligence_query_surfaces": (
                                full_query_surfaces
                            ),
                            "projection_identity": f"sha256:no-{suffix}",
                            "source_contract_identity": "sha256:full",
                            "full_contract": False,
                        },
                        only_subject: {
                            "tools": [ablation_component],
                            "repository_intelligence_query_surfaces": (
                                full_query_surfaces
                            ),
                            "projection_identity": f"sha256:{suffix}-only",
                            "source_contract_identity": "sha256:full",
                            "full_contract": False,
                        },
                    }
                )
                ablation = {
                    "component": ablation_component,
                    "arms": {
                        "bare": "none",
                        "full": "hashmarks",
                        "remove": remove_subject,
                        "only": only_subject,
                    },
                }
            else:
                remove_subject = f"hashmarks-no-{selector_value}"
                only_subject = f"hashmarks-{selector_value}-only"
                treatments.update(
                    {
                        remove_subject: {
                            "tools": full_tools,
                            "repository_intelligence_query_surfaces": [
                                value
                                for value in full_query_surfaces
                                if value != selector_value
                            ],
                            "projection_identity": (
                                f"sha256:no-{selector_value}"
                            ),
                            "source_contract_identity": "sha256:full",
                            "full_contract": False,
                        },
                        only_subject: {
                            "tools": ["repository_intelligence_query"],
                            "repository_intelligence_query_surfaces": [
                                selector_value
                            ],
                            "projection_identity": (
                                f"sha256:{selector_value}-only"
                            ),
                            "source_contract_identity": "sha256:full",
                            "full_contract": False,
                        },
                    }
                )
                ablation = {
                    "component": "repository_intelligence_query",
                    "selector": {
                        "argument": "surface_name",
                        "value": selector_value,
                    },
                    "arms": {
                        "bare": "none",
                        "full": "hashmarks",
                        "remove": remove_subject,
                        "only": only_subject,
                    },
                }
        observed = {
            "ready": True,
            "hashmarks": {
                "mcp_contract_identity": "sha256:full",
                "canonical_tools": full_tools,
                "canonical_repository_intelligence_query_surfaces": (
                    full_query_surfaces
                ),
                "treatments": treatments,
                **({"ablation": ablation} if ablation is not None else {}),
            },
        }
        patches = (
            mock.patch(
                "benchmarks.harbor_commands.settings_from_config",
                return_value=settings,
            ),
            mock.patch(
                "benchmarks.harbor_commands.observed_preflight",
                return_value=observed,
            ),
            mock.patch(
                "benchmarks.harness.harbor_backend.prepare_task",
                side_effect=prepare,
            ),
        )
        return env, patches

    @staticmethod
    def _reward_trial(**kwargs):
        job_root = kwargs["run_root"] / "jobs" / kwargs["job_name"]
        job_root.mkdir(parents=True)
        reward = job_root / "reward.txt"
        reward.write_text("1\n", encoding="utf-8")
        return {
            "trial_id": kwargs["job_name"],
            **kwargs["row"],
            "model": kwargs["settings"].model,
            "status": "COMPLETE",
            "reward": 1.0,
            "reward_path": str(reward),
            "harbor_return_code": 0,
            "stderr_tail": "",
        }

    def _call(self, *args: str) -> tuple[int, dict | None]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(args))
        rendered = output.getvalue().strip()
        return code, json.loads(rendered) if rendered else None

    def test_run_report_and_resume_share_numbered_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(root)
            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ) as execute:
                code, _ = self._call(
                    "run", "--new", "--matrix", "harbor-smoke",
                    "--env-file", str(env), "--root", str(root), "--no-json-results",
                )
                self.assertEqual(code, 0)
                self.assertEqual(execute.call_count, 6)
                code, report = self._call(
                    "report", "--matrix", "harbor-smoke", "--root", str(root),
                )
                self.assertEqual(code, 0)
                self.assertEqual(report["expected_trials"], 6)
                self.assertTrue(report["qualified"])
                self.assertEqual(
                    report["mechanism_attribution"]["summary"]["paired_observations"],
                    3,
                )
                code, mechanism = self._call(
                    "explain", "--matrix", "harbor-smoke", "--root", str(root),
                )
                self.assertEqual(code, 0)
                self.assertTrue(mechanism["campaign_qualified"])
                self.assertEqual(mechanism["summary"]["paired_observations"], 3)
                self.assertEqual(execute.call_count, 6)
                code, _ = self._call(
                    "run", "--resume", "--run-id", "000001",
                    "--matrix", "harbor-smoke", "--env-file", str(env),
                    "--root", str(root), "--no-json-results",
                )
                self.assertEqual(code, 0)
                self.assertEqual(execute.call_count, 6)
            self.assertTrue((root / "runs/000001/results/.campaign/authority.json").is_file())
            self.assertTrue((root / "runs/000001/reports/report.json").is_file())
            self.assertTrue((root / "runs/000001/reports/mechanism.json").is_file())
            self.assertFalse((root / "runs/000001/reports/ablation.json").exists())

    def test_ablation_run_persists_and_reports_matched_quartets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(
                root,
                ablation_component="task_evidence",
            )
            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ) as execute:
                code, _ = self._call(
                    "run",
                    "--new",
                    "--matrix",
                    "harbor-ablation-smoke",
                    "--env-file",
                    str(env),
                    "--root",
                    str(root),
                    "--no-json-results",
                )
                self.assertEqual(code, 0)
                self.assertEqual(execute.call_count, 12)

                code, report = self._call(
                    "ablation",
                    "--matrix",
                    "harbor-ablation-smoke",
                    "--root",
                    str(root),
                )
                self.assertEqual(code, 0)
                self.assertTrue(report["applicable"])
                self.assertTrue(report["campaign_qualified"])
                self.assertEqual(report["summary"]["matched_quartets"], 3)
                self.assertEqual(
                    report["summary"]["classification"],
                    {"UNQUALIFIED_TREATMENT_AUTHORITY": 3},
                )
                self.assertTrue(
                    all(
                        row["treatment_authority_error"].endswith(
                            "UNQUALIFIED_TRACE_INCOMPLETE"
                        )
                        for row in report["quartets"]
                    )
                )
                self.assertTrue(
                    all(
                        row["observed_catalog_advertisement_proven"] is False
                        for row in report["quartets"]
                    )
                )
                self.assertEqual(execute.call_count, 12)

            ablation_path = root / "runs/000001/reports/ablation.json"
            self.assertTrue(ablation_path.is_file())
            stored = json.loads(ablation_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["schema"], "agentscookbook.harbor-ablation-report.v2")
            self.assertEqual(stored["summary"]["matched_quartets"], 3)

    def test_find_ablation_uses_same_campaign_and_report_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(
                root,
                ablation_component="find",
            )
            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ) as execute:
                code, _ = self._call(
                    "run",
                    "--new",
                    "--matrix",
                    "harbor-find-ablation-smoke",
                    "--env-file",
                    str(env),
                    "--root",
                    str(root),
                    "--no-json-results",
                )
                self.assertEqual(code, 0)
                self.assertEqual(execute.call_count, 12)

                code, report = self._call(
                    "ablation",
                    "--matrix",
                    "harbor-find-ablation-smoke",
                    "--root",
                    str(root),
                )
                self.assertEqual(code, 0)
                self.assertEqual(report["component"], "find")
                self.assertEqual(
                    report["arms"],
                    {
                        "bare": "none",
                        "full": "hashmarks",
                        "remove": "hashmarks-no-find",
                        "only": "hashmarks-find-only",
                    },
                )
                self.assertEqual(report["summary"]["matched_quartets"], 3)
                self.assertEqual(execute.call_count, 12)

    def test_changed_path_ablations_share_generic_campaign_lifecycle(
        self,
    ) -> None:
        for matrix, component in (
            ("harbor-change-impact-ablation-smoke", "change_impact"),
            ("harbor-post-change-ablation-smoke", "post_change"),
        ):
            with self.subTest(matrix=matrix), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                env, patches = self._environment(
                    root,
                    ablation_component=component,
                )
                with patches[0], patches[1], patches[2], mock.patch(
                    "benchmarks.harness.harbor_backend.execute_trial",
                    side_effect=self._reward_trial,
                ) as execute:
                    code, _ = self._call(
                        "run",
                        "--new",
                        "--matrix",
                        matrix,
                        "--env-file",
                        str(env),
                        "--root",
                        str(root),
                        "--no-json-results",
                    )
                    self.assertEqual(code, 0)
                    self.assertEqual(execute.call_count, 12)

                    code, report = self._call(
                        "ablation",
                        "--matrix",
                        matrix,
                        "--root",
                        str(root),
                    )
                    self.assertEqual(code, 0)
                    self.assertEqual(report["component"], component)
                    self.assertEqual(
                        report["summary"]["matched_quartets"],
                        3,
                    )
                    self.assertEqual(execute.call_count, 12)

    def test_correlate_evidence_ablation_uses_generic_campaign_lifecycle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(
                root,
                ablation_component="correlate_evidence",
            )
            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ) as execute:
                code, _ = self._call(
                    "run",
                    "--new",
                    "--matrix",
                    "harbor-correlate-evidence-ablation-smoke",
                    "--env-file",
                    str(env),
                    "--root",
                    str(root),
                    "--no-json-results",
                )
                self.assertEqual(code, 0)
                self.assertEqual(execute.call_count, 12)

                code, report = self._call(
                    "ablation",
                    "--matrix",
                    "harbor-correlate-evidence-ablation-smoke",
                    "--root",
                    str(root),
                )
                self.assertEqual(code, 0)
                self.assertEqual(report["component"], "correlate_evidence")
                self.assertEqual(
                    report["arms"],
                    {
                        "bare": "none",
                        "full": "hashmarks",
                        "remove": "hashmarks-no-correlate-evidence",
                        "only": "hashmarks-correlate-evidence-only",
                    },
                )
                self.assertEqual(report["summary"]["matched_quartets"], 3)
                self.assertEqual(execute.call_count, 12)

    def test_repository_declarations_ablation_uses_generic_campaign_lifecycle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(
                root,
                ablation_component="repository_declarations",
            )
            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ) as execute:
                code, _ = self._call(
                    "run",
                    "--new",
                    "--matrix",
                    "harbor-repository-declarations-ablation-smoke",
                    "--env-file",
                    str(env),
                    "--root",
                    str(root),
                    "--no-json-results",
                )
                self.assertEqual(code, 0)
                self.assertEqual(execute.call_count, 12)

                code, report = self._call(
                    "ablation",
                    "--matrix",
                    "harbor-repository-declarations-ablation-smoke",
                    "--root",
                    str(root),
                )
                self.assertEqual(code, 0)
                self.assertEqual(report["component"], "repository_declarations")
                self.assertEqual(
                    report["arms"],
                    {
                        "bare": "none",
                        "full": "hashmarks",
                        "remove": "hashmarks-no-repository-declarations",
                        "only": "hashmarks-repository-declarations-only",
                    },
                )
                self.assertEqual(report["summary"]["matched_quartets"], 3)
                self.assertEqual(execute.call_count, 12)

    def test_dependency_codemap_ablation_uses_generic_campaign_lifecycle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(
                root,
                ablation_component="dependency_codemap",
            )
            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ) as execute:
                code, _ = self._call(
                    "run",
                    "--new",
                    "--matrix",
                    "harbor-dependency-codemap-ablation-smoke",
                    "--env-file",
                    str(env),
                    "--root",
                    str(root),
                    "--no-json-results",
                )
                self.assertEqual(code, 0)
                self.assertEqual(execute.call_count, 12)

                code, report = self._call(
                    "ablation",
                    "--matrix",
                    "harbor-dependency-codemap-ablation-smoke",
                    "--root",
                    str(root),
                )
                self.assertEqual(code, 0)
                self.assertEqual(report["component"], "dependency_codemap")
                self.assertEqual(
                    report["arms"],
                    {
                        "bare": "none",
                        "full": "hashmarks",
                        "remove": "hashmarks-no-dependency-codemap",
                        "only": "hashmarks-dependency-codemap-only",
                    },
                )
                self.assertEqual(report["summary"]["matched_quartets"], 3)
                self.assertEqual(execute.call_count, 12)

    def test_verification_facet_ablation_uses_generic_campaign_lifecycle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(
                root,
                ablation_component="repository_intelligence_query",
                selector_value="verification-explanation",
            )
            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ) as execute:
                code, _ = self._call(
                    "run",
                    "--new",
                    "--matrix",
                    "harbor-verification-explanation-ablation-smoke",
                    "--env-file",
                    str(env),
                    "--root",
                    str(root),
                    "--no-json-results",
                )
                self.assertEqual(code, 0)
                self.assertEqual(execute.call_count, 12)

                code, report = self._call(
                    "ablation",
                    "--matrix",
                    "harbor-verification-explanation-ablation-smoke",
                    "--root",
                    str(root),
                )
                self.assertEqual(code, 0)
                self.assertEqual(
                    report["component"],
                    "repository_intelligence_query",
                )
                self.assertEqual(
                    report["selector"],
                    {
                        "argument": "surface_name",
                        "value": "verification-explanation",
                    },
                )
                self.assertEqual(report["summary"]["matched_quartets"], 3)
                self.assertEqual(execute.call_count, 12)

    def test_interrupted_launch_gets_new_job_name_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(root)
            jobs = []

            def interrupted_once(**kwargs):
                jobs.append(kwargs["job_name"])
                if len(jobs) == 1:
                    raise KeyboardInterrupt()
                return self._reward_trial(**kwargs)

            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=interrupted_once,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    self._call(
                        "run", "--new", "--matrix", "harbor-smoke",
                        "--env-file", str(env), "--root", str(root),
                    )
                code, status = self._call(
                    "status", "--matrix", "harbor-smoke", "--root", str(root),
                )
                self.assertEqual(code, 0)
                self.assertEqual(status["interrupted_trials"], 1)
                code, _ = self._call(
                    "run", "--resume", "--run-id", "000001",
                    "--matrix", "harbor-smoke", "--env-file", str(env),
                    "--root", str(root), "--no-json-results",
                )
                self.assertEqual(code, 0)
                self.assertNotEqual(jobs[0], jobs[1])
                self.assertTrue(jobs[0].endswith("a000001"))
                self.assertTrue(jobs[1].endswith("a000002"))
                code, status = self._call(
                    "status", "--matrix", "harbor-smoke", "--root", str(root),
                )
                self.assertEqual(status["recovered_interruption_attempts"], 1)

    def test_completed_operational_failure_is_preserved_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(root)
            calls = []

            def one_missing_reward(**kwargs):
                calls.append(kwargs["job_name"])
                if len(calls) == 1:
                    return {
                        "trial_id": kwargs["job_name"],
                        **kwargs["row"],
                        "model": kwargs["settings"].model,
                        "status": "INCOMPLETE",
                        "reward": None,
                        "reward_path": None,
                        "harbor_return_code": 1,
                        "stderr_tail": "Harbor failed",
                    }
                return self._reward_trial(**kwargs)

            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=one_missing_reward,
            ):
                code, _ = self._call(
                    "run", "--new", "--matrix", "harbor-smoke",
                    "--env-file", str(env), "--root", str(root), "--no-json-results",
                )
                self.assertEqual(code, 2)
                self.assertEqual(len(calls), 6)
                code, report = self._call(
                    "report", "--matrix", "harbor-smoke", "--root", str(root),
                )
                self.assertEqual(code, 2)
                self.assertFalse(report["qualified"])
                self.assertEqual(report["incomplete"], 1)
                self.assertTrue(all(value is None for value in report["hashmarks_uplift"].values()))
                code, _ = self._call(
                    "run", "--resume", "--run-id", "000001",
                    "--matrix", "harbor-smoke", "--env-file", str(env),
                    "--root", str(root), "--no-json-results",
                )
                self.assertEqual(code, 2)
                self.assertEqual(len(calls), 6)

    def test_resume_rejects_changed_preflight_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(root)
            admitted = {
                "ready": True,
                "hashmarks": {
                    "mcp_contract_identity": "sha256:full",
                    "canonical_tools": [
                        "repository_context",
                        "find",
                        "task_evidence",
                    ],
                    "treatments": {
                        "hashmarks": {
                            "tools": [
                                "repository_context",
                                "find",
                                "task_evidence",
                            ],
                            "projection_identity": "sha256:full",
                            "source_contract_identity": "sha256:full",
                            "full_contract": True,
                        }
                    },
                },
            }
            changed = {**admitted, "harbor": "changed"}
            with patches[0], patches[2], mock.patch(
                "benchmarks.harbor_commands.observed_preflight",
                side_effect=(admitted, changed),
            ), mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ) as execute:
                self.assertEqual(self._call(
                    "run", "--new", "--matrix", "harbor-smoke",
                    "--env-file", str(env), "--root", str(root), "--no-json-results",
                )[0], 0)
                with self.assertRaisesRegex(SystemExit, "authority changed"):
                    self._call(
                        "run", "--resume", "--run-id", "000001",
                        "--matrix", "harbor-smoke", "--env-file", str(env),
                        "--root", str(root), "--no-json-results",
                    )
                self.assertEqual(execute.call_count, 6)

    def test_resume_rejects_tampered_mcp_treatment_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(root)
            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ) as execute:
                self.assertEqual(
                    self._call(
                        "run",
                        "--new",
                        "--matrix",
                        "harbor-smoke",
                        "--env-file",
                        str(env),
                        "--root",
                        str(root),
                        "--no-json-results",
                    )[0],
                    0,
                )
                self.assertEqual(execute.call_count, 6)

                config = root / "runs/000001/hashmarks.mcp.json"
                config.write_text("{}\n", encoding="utf-8")

                with self.assertRaisesRegex(
                    SystemExit,
                    "MCP treatment config changed",
                ):
                    self._call(
                        "run",
                        "--resume",
                        "--run-id",
                        "000001",
                        "--matrix",
                        "harbor-smoke",
                        "--env-file",
                        str(env),
                        "--root",
                        str(root),
                        "--no-json-results",
                    )
                self.assertEqual(execute.call_count, 6)

    def test_corrupt_reward_blocks_status_and_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, patches = self._environment(root)
            with patches[0], patches[1], patches[2], mock.patch(
                "benchmarks.harness.harbor_backend.execute_trial",
                side_effect=self._reward_trial,
            ):
                self.assertEqual(self._call(
                    "run", "--new", "--matrix", "harbor-smoke",
                    "--env-file", str(env), "--root", str(root), "--no-json-results",
                )[0], 0)
            receipt = next((root / "runs/000001/results").glob("*/reward.txt"))
            receipt.write_text("0\n", encoding="utf-8")
            report_path = root / "runs/000001/reports/report.json"
            score_path = root / "runs/000001/reports/score.json"
            self.assertTrue(report_path.is_file())
            self.assertTrue(score_path.is_file())
            code, status = self._call(
                "status", "--matrix", "harbor-smoke", "--root", str(root),
            )
            self.assertEqual(code, 2)
            self.assertEqual(len(status["corrupt_bundles"]), 1)
            self.assertFalse(report_path.exists())
            self.assertFalse(score_path.exists())
            code, report = self._call(
                "report", "--matrix", "harbor-smoke", "--root", str(root),
            )
            self.assertEqual(code, 2)
            self.assertFalse(report["qualified"])
            self.assertTrue(all(value is None for value in report["hashmarks_uplift"].values()))


if __name__ == "__main__":
    unittest.main()
