"""Focused contracts for Harbor behavioral command-oracle projection."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness.harbor_behavioral import (
    HarborBehavioralError,
    admit_behavioral_oracle,
    behavioral_contract,
    behavioral_verifier,
    prepare_behavioral_workspace,
    workspace_manifest,
)
from benchmarks.harbor_matrix import (
    load_matrix,
    mode_contract,
    validate_projection,
)
from benchmarks.harness.suite import load_suite


ROOT = Path(__file__).resolve().parents[2]
SUITE = (
    ROOT
    / "benchmarks"
    / "suites"
    / "repository-intelligence"
    / "behavioral-v4"
)
CHANGE_IMPACT_MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-change-impact-ablation-v1.json"
)
POST_CHANGE_MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-post-change-ablation-v1.json"
)
CORRELATE_EVIDENCE_MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-correlate-evidence-ablation-v1.json"
)
REPOSITORY_DECLARATIONS_MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-repository-declarations-ablation-v1.json"
)
DEPENDENCY_CODEMAP_MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-dependency-codemap-ablation-v1.json"
)
VERIFICATION_EXPLANATION_MATRIX = (
    ROOT
    / "benchmarks"
    / "harbor"
    / "repository-intelligence-verification-explanation-ablation-v1.json"
)


class HarborBehavioralTests(unittest.TestCase):
    def test_behavioral_ablation_matrices_admit_frozen_tasks(self) -> None:
        suite = load_suite(SUITE)
        for path, component, count in (
            (CHANGE_IMPACT_MATRIX, "change_impact", 4),
            (POST_CHANGE_MATRIX, "post_change", 4),
            (CORRELATE_EVIDENCE_MATRIX, "correlate_evidence", 4),
            (REPOSITORY_DECLARATIONS_MATRIX, "repository_declarations", 4),
            (DEPENDENCY_CODEMAP_MATRIX, "dependency_codemap", 4),
            (
                VERIFICATION_EXPLANATION_MATRIX,
                "repository_intelligence_query",
                4,
            ),
        ):
            with self.subTest(component=component):
                matrix = load_matrix(path)
                mode = mode_contract(matrix, "matrix")
                self.assertEqual(matrix["ablation"]["component"], component)
                self.assertEqual(len(mode.tasks), count)
                validate_projection(
                    matrix=matrix,
                    suite=suite,
                    mode=mode,
                    harnesses=tuple(matrix["harnesses"]),
                    tasks=mode.tasks,
                )

    def test_correlate_evidence_matrix_matches_correlation_intent(self) -> None:
        matrix = load_matrix(CORRELATE_EVIDENCE_MATRIX)
        mode = mode_contract(matrix, "matrix")

        self.assertEqual(
            matrix["subjects"],
            [
                "none",
                "hashmarks",
                "hashmarks-no-correlate-evidence",
                "hashmarks-correlate-evidence-only",
            ],
        )
        self.assertEqual(
            matrix["tool_projections"],
            {
                "hashmarks-no-correlate-evidence": {
                    "exclude": ["correlate_evidence"],
                },
                "hashmarks-correlate-evidence-only": {
                    "include": ["correlate_evidence"],
                },
            },
        )
        self.assertEqual(
            matrix["ablation"],
            {
                "component": "correlate_evidence",
                "arms": {
                    "bare": "none",
                    "full": "hashmarks",
                    "remove": "hashmarks-no-correlate-evidence",
                    "only": "hashmarks-correlate-evidence-only",
                },
            },
        )
        self.assertEqual(
            mode.tasks,
            (
                "correlation-00",
                "correlation-01",
                "correlation-02",
                "correlation-03",
            ),
        )

    def test_repository_declarations_matrix_matches_declaration_intent(
        self,
    ) -> None:
        matrix = load_matrix(REPOSITORY_DECLARATIONS_MATRIX)
        mode = mode_contract(matrix, "matrix")

        self.assertEqual(
            matrix["subjects"],
            [
                "none",
                "hashmarks",
                "hashmarks-no-repository-declarations",
                "hashmarks-repository-declarations-only",
            ],
        )
        self.assertEqual(
            matrix["tool_projections"],
            {
                "hashmarks-no-repository-declarations": {
                    "exclude": ["repository_declarations"],
                },
                "hashmarks-repository-declarations-only": {
                    "include": ["repository_declarations"],
                },
            },
        )
        self.assertEqual(
            matrix["ablation"],
            {
                "component": "repository_declarations",
                "arms": {
                    "bare": "none",
                    "full": "hashmarks",
                    "remove": "hashmarks-no-repository-declarations",
                    "only": "hashmarks-repository-declarations-only",
                },
            },
        )
        self.assertEqual(
            mode.tasks,
            (
                "declarations-00",
                "declarations-01",
                "declarations-02",
                "declarations-03",
            ),
        )

    def test_dependency_codemap_matrix_matches_dependency_delta_intent(
        self,
    ) -> None:
        matrix = load_matrix(DEPENDENCY_CODEMAP_MATRIX)
        mode = mode_contract(matrix, "matrix")

        self.assertEqual(
            matrix["subjects"],
            [
                "none",
                "hashmarks",
                "hashmarks-no-dependency-codemap",
                "hashmarks-dependency-codemap-only",
            ],
        )
        self.assertEqual(
            matrix["tool_projections"],
            {
                "hashmarks-no-dependency-codemap": {
                    "exclude": ["dependency_codemap"],
                },
                "hashmarks-dependency-codemap-only": {
                    "include": ["dependency_codemap"],
                },
            },
        )
        self.assertEqual(
            matrix["ablation"],
            {
                "component": "dependency_codemap",
                "arms": {
                    "bare": "none",
                    "full": "hashmarks",
                    "remove": "hashmarks-no-dependency-codemap",
                    "only": "hashmarks-dependency-codemap-only",
                },
            },
        )
        self.assertEqual(
            mode.tasks,
            (
                "dependency_delta-00",
                "dependency_delta-01",
                "dependency_delta-02",
                "dependency_delta-03",
            ),
        )

    def test_verification_explanation_matrix_matches_query_facet_intent(
        self,
    ) -> None:
        matrix = load_matrix(VERIFICATION_EXPLANATION_MATRIX)
        mode = mode_contract(matrix, "matrix")

        self.assertEqual(
            matrix["query_surface_projections"],
            {
                "hashmarks-no-verification-explanation": {
                    "exclude": ["verification-explanation"],
                },
                "hashmarks-verification-explanation-only": {
                    "include": ["verification-explanation"],
                },
            },
        )
        self.assertEqual(
            matrix["ablation"]["selector"],
            {
                "argument": "surface_name",
                "value": "verification-explanation",
            },
        )
        self.assertEqual(
            mode.tasks,
            (
                "verification-00",
                "verification-01",
                "verification-02",
                "verification-03",
            ),
        )

    def test_behavioral_contract_reuses_frozen_command_oracle(self) -> None:
        suite = load_suite(SUITE)

        edit = behavioral_contract(suite, suite.tasks["change_impact-00"])
        read_only = behavioral_contract(suite, suite.tasks["post_change-02"])

        self.assertEqual(edit["kind"], "command-lexigram")
        self.assertEqual(edit["task_id"], "change_impact-00")
        self.assertEqual(
            edit["allowed_change_globs"],
            ["benchmark_case/change_impact_00/src/core.py"],
        )
        self.assertEqual(read_only["task_id"], "post_change-02")
        self.assertEqual(read_only["allowed_change_globs"], [])

    def test_behavioral_contract_rejects_noncanonical_oracle_argv(self) -> None:
        suite = load_suite(SUITE)
        task = json.loads(json.dumps(suite.tasks["change_impact-00"]))
        task["oracle"]["configuration"]["grade_argv"] = [
            "{python}",
            "{suite}/other.py",
            "grade",
            "change_impact-00",
        ]

        with self.assertRaisesRegex(
            HarborBehavioralError,
            "canonical suite oracle argv",
        ):
            behavioral_contract(suite, task)

    def test_prepare_applies_verified_mutation_then_freezes_baseline(self) -> None:
        suite = load_suite(SUITE)
        task = suite.tasks["change_impact-00"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            tests = root / "tests"
            workspace.mkdir()
            tests.mkdir()
            subprocess.run(
                ["git", "init", "-q", str(workspace)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            projection = prepare_behavioral_workspace(
                suite=suite,
                task=task,
                workspace=workspace,
                tests=tests,
                control_root=root,
            )

            core = (
                workspace
                / "benchmark_case/change_impact_00/src/core.py"
            )
            self.assertIn('return "old"', core.read_text(encoding="utf-8"))
            self.assertEqual(
                projection["mutation_identity"]["changed_paths"],
                sorted(task["mutation"]["changed_paths"]),
            )
            self.assertEqual(
                projection["schema"],
                "agentscookbook.harbor-behavioral-projection.v1",
            )
            self.assertTrue((tests / "oracle.py").is_file())
            self.assertTrue((tests / "cases.json").is_file())
            baseline = json.loads(
                (tests / "baseline.json").read_text(encoding="utf-8")
            )
            self.assertIn(
                "benchmark_case/change_impact_00/src/core.py",
                baseline,
            )
            frozen = json.loads(
                (tests / "projection.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                frozen["baseline_manifest_sha256"],
                projection["baseline_manifest_sha256"],
            )

    def test_workspace_manifest_excludes_git_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".git").mkdir()
            (workspace / ".git/config").write_text("secret", encoding="utf-8")
            (workspace / "a.txt").write_text("A", encoding="utf-8")

            manifest = workspace_manifest(workspace)

        self.assertEqual(set(manifest), {"a.txt"})

    def test_health_admission_is_bounded_and_read_only_before_mutation(self) -> None:
        suite = load_suite(SUITE)
        contract = behavioral_contract(suite, suite.tasks["change_impact-00"])
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "sentinel.txt").write_text("original", encoding="utf-8")
            result = admit_behavioral_oracle(workspace=workspace, contract=contract)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["timeout_seconds"], 10)
            self.assertEqual(
                (workspace / "sentinel.txt").read_text(encoding="utf-8"),
                "original",
            )

    def test_oracle_health_failure_blocks_mutation_and_fixture_publication(
        self,
    ) -> None:
        suite = load_suite(SUITE)
        task = suite.tasks["change_impact-00"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, tests = root / "workspace", root / "tests"
            workspace.mkdir()
            tests.mkdir()
            with (
                mock.patch(
                    "benchmarks.harness.harbor_behavioral.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 2, "", "bad case"),
                ),
                mock.patch(
                    "benchmarks.harness.harbor_behavioral.apply_mutation"
                ) as mutation,
            ):
                with self.assertRaisesRegex(
                    HarborBehavioralError, "oracle health failed"
                ):
                    prepare_behavioral_workspace(
                        suite=suite,
                        task=task,
                        workspace=workspace,
                        tests=tests,
                        control_root=root,
                    )
                mutation.assert_not_called()
            self.assertFalse((tests / "projection.json").exists())
            self.assertFalse((tests / "baseline.json").exists())

    def test_health_timeout_and_mutation_detection_fail_before_work(self) -> None:
        suite = load_suite(SUITE)
        contract = behavioral_contract(suite, suite.tasks["change_impact-00"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "benchmarks.harness.harbor_behavioral.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["health"], 10),
            ):
                with self.assertRaisesRegex(
                    HarborBehavioralError, "oracle health unavailable"
                ):
                    admit_behavioral_oracle(workspace=root, contract=contract)

            def dirty_health(*args, **kwargs):
                (root / "unexpected.txt").write_text("changed", encoding="utf-8")
                return subprocess.CompletedProcess([], 0, "", "")

            with mock.patch(
                "benchmarks.harness.harbor_behavioral.subprocess.run",
                side_effect=dirty_health,
            ):
                with self.assertRaisesRegex(
                    HarborBehavioralError, "health mutated workspace"
                ):
                    admit_behavioral_oracle(workspace=root, contract=contract)

    def _run_generated_verifier(
        self,
        oracle_source: str,
        *,
        grade_timeout: str | None = None,
    ) -> tuple[dict, dict, str]:
        suite = load_suite(SUITE)
        contract = behavioral_contract(suite, suite.tasks["post_change-00"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            tests = root / "tests"
            logs = root / "logs" / "verifier"
            workspace.mkdir()
            tests.mkdir()
            (workspace / "sentinel.txt").write_text("source", encoding="utf-8")
            (tests / "oracle.py").write_text(oracle_source, encoding="utf-8")
            (tests / "cases.json").write_bytes((SUITE / "cases.json").read_bytes())
            (tests / "baseline.json").write_text(
                json.dumps(workspace_manifest(workspace)), encoding="utf-8"
            )
            (workspace / ".agentscookbook-answer.json").write_text(
                json.dumps({"path": "src/owner.py"}), encoding="utf-8"
            )
            script = behavioral_verifier(contract)
            for original, replacement in (
                ("/logs/verifier", str(logs)),
                ("/tmp/agentscookbook-observation.json", str(root / "observation.json")),
                ("/tests", str(tests)),
                ("/workspace", str(workspace)),
            ):
                script = script.replace(original, replacement)
            if grade_timeout is not None:
                script = script.replace("timeout=40,", "timeout=" + grade_timeout + ",")
            path = root / "verify.sh"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["sh", str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return (
                json.loads((logs / "answer.json").read_text(encoding="utf-8")),
                json.loads((logs / "oracle.json").read_text(encoding="utf-8")),
                (logs / "reward.txt").read_text(encoding="utf-8"),
            )

    def test_bounded_grade_accepts_canonical_oracle_result(self) -> None:
        answer, oracle, reward = self._run_generated_verifier(
            'import json\nprint(json.dumps({"passed": True}))\n'
        )
        self.assertEqual(reward, "1\n")
        self.assertTrue(answer["match"])
        self.assertTrue(answer["tracked_clean"])
        self.assertEqual(oracle["return_code"], 0)
        self.assertFalse(oracle["timed_out"])

    def test_real_verifier_freezes_expected_claims_after_oracle_grade(self) -> None:
        answer, oracle, reward = self._run_generated_verifier(
            (SUITE / "oracle.py").read_text(encoding="utf-8")
        )
        case = json.loads((SUITE / "cases.json").read_text(encoding="utf-8"))[
            "cases"
        ]["post_change-00"]
        self.assertEqual(answer["expected"], case["expected"])
        self.assertEqual(answer["semantic_case_id"], "post_change-00")
        self.assertEqual(
            answer["oracle"]["schema"],
            "agents-cookbook-lexigram-oracle.v1",
        )
        self.assertFalse(answer["match"])
        self.assertIsNone(answer["error"])
        self.assertEqual(oracle["return_code"], 1)
        self.assertEqual(reward, "0\n")

    def test_hung_grade_reports_timeout_and_denies_reward(self) -> None:
        answer, oracle, reward = self._run_generated_verifier(
            "import time\ntime.sleep(1)\n",
            grade_timeout="0.05",
        )
        self.assertEqual(reward, "0\n")
        self.assertEqual(answer["error"], "oracle-timeout")
        self.assertFalse(answer["match"])
        self.assertTrue(oracle["timed_out"])
        self.assertIsNone(oracle["return_code"])

    def test_oversized_grade_output_denies_reward_and_bounds_artifact(self) -> None:
        answer, oracle, reward = self._run_generated_verifier(
            'import json\nprint(json.dumps({"passed": True, "padding": "x" * 75000}))\n'
        )
        self.assertEqual(reward, "0\n")
        self.assertEqual(answer["error"], "oracle-output-limit")
        self.assertFalse(answer["match"])
        self.assertTrue(oracle["stdout_truncated"])
        self.assertLessEqual(len(oracle["stdout"]), 8192)

    def test_behavioral_verifier_uses_oracle_and_post_mutation_baseline(self) -> None:
        suite = load_suite(SUITE)
        contract = behavioral_contract(suite, suite.tasks["post_change-00"])

        script = behavioral_verifier(contract)

        self.assertIn("/tests/baseline.json", script)
        self.assertIn("/tests/oracle.py", script)
        self.assertIn("BENCHMARK_OBSERVATION_PATH", script)
        self.assertIn("contamination", script)
        self.assertIn('Path("/logs/verifier/reward.txt")', script)
        self.assertNotIn("reasoning_content", script)


if __name__ == "__main__":
    unittest.main()
