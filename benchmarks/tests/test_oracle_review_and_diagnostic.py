from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from benchmarks.__main__ import main
from benchmarks.diagnostic import (
    DiagnosticError,
    _SCORE_REPORT_FIELDS,
    prepare_diagnostic_suite,
)
from benchmarks.harness.oracle_reviews import (
    OracleReviewError,
    oracle_review_guide,
    validate_oracle_reviews,
)
from benchmarks.harness.oracle_review_runner import (
    DECISION_PREFIX,
    REVIEWER_ID,
    _parse_decision,
    _run_review,
    _verify_expected_owner,
    run_pending_oracle_reviews,
)
from benchmarks.harness.suite import SuiteError, load_suite


SOURCE = (
    Path(__file__).resolve().parents[1] / "suites/repository-intelligence/heldout-v1"
)


class ReviewAndDiagnosticTests(unittest.TestCase):
    @staticmethod
    def _materialize_expected_owner(**kwargs) -> None:
        path = kwargs["destination"] / "hashmarks/test_shards.py"
        path.parent.mkdir(parents=True)
        path.write_text(
            "def repository_content_identity(root):\n"
            "    return root\n",
            encoding="utf-8",
        )

    @staticmethod
    def _valid_oracle_decision_payload(task_digest: str) -> dict[str, object]:
        return {
            "task_id": "locate-repository-content-identity",
            "task_digest": task_digest,
            "decision": "unique",
            "reason": "Independent source inspection found one semantic owner.",
            "observed_owner": {
                "path": "hashmarks/test_shards.py",
                "symbol": "repository_content_identity",
            },
        }

    @staticmethod
    def _remove_identity_review(root: Path) -> None:
        review_path = root / "qualification/oracle-reviews.json"
        evidence = json.loads(review_path.read_text(encoding="utf-8"))
        evidence["tasks"]["locate-repository-content-identity"]["reviews"] = []
        review_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_qualified_label_without_source_campaign_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            score = root / "score.json"
            score.write_text(
                json.dumps(
                    {
                        "schema": "agents-cookbook-heldout-observer-outcomes.v7",
                        "projection_mode": "live",
                        "campaign_qualification": {"status": "QUALIFIED"},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DiagnosticError, "source score or campaign"):
                prepare_diagnostic_suite(
                    source_suite=SOURCE,
                    source_results=root / "missing-results",
                    score_path=score,
                    destination=root / "diagnostic",
                    include_tasks={"locate-repository-content-identity"},
                )
            self.assertFalse((root / "diagnostic").exists())

    def test_required_review_cannot_be_bypassed_by_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            (copy / "qualification/oracle-reviews.json").unlink()
            with self.assertRaisesRegex(
                SuiteError, "oracle review evidence is missing"
            ):
                load_suite(copy)

    def test_explicit_review_path_is_the_bound_review_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            experiment_path = copy / "experiment.json"
            experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
            configured = "qualification/reviews-alt.json"
            experiment["oracle_reviews"] = configured
            alternate = copy / configured
            alternate.write_bytes(
                (copy / "qualification/oracle-reviews.json").read_bytes()
            )
            (copy / "qualification/oracle-reviews.json").unlink()
            experiment_path.write_text(json.dumps(experiment), encoding="utf-8")
            self.assertEqual(
                validate_oracle_reviews(load_suite(copy), require_complete=False)[
                    "first_reviewed_tasks"
                ],
                12,
            )

    def test_review_path_cannot_escape_suite_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            experiment_path = copy / "experiment.json"
            experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
            experiment["oracle_reviews"] = "../outside.json"
            experiment_path.write_text(json.dumps(experiment), encoding="utf-8")
            with self.assertRaisesRegex(SuiteError, "escapes suite root"):
                load_suite(copy)

    def test_repaired_identity_task_is_committed_as_qualified(self) -> None:
        suite = load_suite(SOURCE)
        task = suite.tasks["locate-repository-content-identity"]
        self.assertEqual(task["version"], 3)
        self.assertIn("test-selection work-selection envelope", task["prompt"])
        self.assertIn("repository-binding validation", task["prompt"])

        result = validate_oracle_reviews(suite, require_complete=True)
        self.assertEqual(result["first_reviewed_tasks"], 12)
        self.assertEqual(result["approved_tasks"], 12)
        self.assertEqual(result["pending_tasks"], [])
        self.assertEqual(result["missing_review_tasks"], [])
        self.assertEqual(result["non_unique_tasks"], [])
        self.assertEqual(
            result["escalated_tasks"],
            [
                "locate-prefix-path-enumerator",
                "locate-directory-pruning",
                "locate-resource-invalidation",
            ],
        )
        review_evidence = json.loads(
            (SOURCE / "qualification/oracle-reviews.json").read_text(
                encoding="utf-8"
            )
        )
        for task_id in result["escalated_tasks"]:
            row = review_evidence["tasks"][task_id]
            self.assertEqual(
                row["review_requirement"]["minimum_independent_reviews"],
                2,
            )
            escalation_reason = row["review_requirement"]["escalation_reason"]
            self.assertIn("Heldout run 000007", escalation_reason)
            self.assertIn(
                "report sha256:9d1a40bc6f0e5bc1831fe272b36fed3575e5559218198fbc68315dff1f746fd1",
                escalation_reason,
            )
            self.assertIn(
                "decision-evidence sha256:24d979dde9b71d945e78d3380fbd7fd3509104c1e703176a0af3493b614080c9",
                escalation_reason,
            )
            self.assertEqual(len(row["reviews"]), 2)
            self.assertEqual(
                {review["decision"] for review in row["reviews"]},
                {"unique"},
            )
        self.assertTrue(result["complete"])

    def test_missing_repaired_identity_review_is_still_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            self._remove_identity_review(copy)
            suite = load_suite(copy)
            result = validate_oracle_reviews(suite, require_complete=False)
            self.assertEqual(result["first_reviewed_tasks"], 11)
            self.assertEqual(result["approved_tasks"], 11)
            self.assertEqual(
                result["pending_tasks"],
                ["locate-repository-content-identity"],
            )
            self.assertEqual(
                result["missing_review_tasks"],
                ["locate-repository-content-identity"],
            )
            with self.assertRaisesRegex(
                OracleReviewError,
                "1 task.*still need independent review",
            ):
                validate_oracle_reviews(suite, require_complete=True)

    def test_one_review_completes_repaired_identity_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            self._remove_identity_review(copy)
            review_path = copy / "qualification/oracle-reviews.json"
            evidence = json.loads(review_path.read_text(encoding="utf-8"))
            row = evidence["tasks"]["locate-repository-content-identity"]
            row["reviews"].append(
                {
                    "decision": "unique",
                    "reason": (
                        "Independent source inspection confirms the test-shard "
                        "content identity is computed by this function."
                    ),
                    "reviewer": "independent-review-b",
                    "task_digest": row["task_digest"],
                }
            )
            review_path.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result = validate_oracle_reviews(load_suite(copy), require_complete=True)
            self.assertTrue(result["complete"])
            self.assertEqual(result["approved_tasks"], 12)

    def test_escalation_requires_a_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            suite = load_suite(copy)
            review_path = copy / "qualification/oracle-reviews.json"
            evidence = json.loads(review_path.read_text(encoding="utf-8"))
            requirement = evidence["tasks"]["locate-repository-content-identity"][
                "review_requirement"
            ]
            requirement["minimum_independent_reviews"] = 2
            requirement["escalation_reason"] = None
            review_path.write_text(json.dumps(evidence), encoding="utf-8")
            with self.assertRaisesRegex(
                OracleReviewError,
                "escalated oracle review reason missing",
            ):
                validate_oracle_reviews(suite, require_complete=False)

    def test_oracle_review_runner_reviews_only_pending_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            self._remove_identity_review(copy)
            suite = load_suite(copy)
            evidence = json.loads(
                (copy / "qualification/oracle-reviews.json").read_text(
                    encoding="utf-8"
                )
            )
            task_digest = evidence["tasks"][
                "locate-repository-content-identity"
            ]["task_digest"]

            materialize = self._materialize_expected_owner

            def review(_workspace, prompt, *, task_id):
                self.assertEqual(task_id, "locate-repository-content-identity")
                self.assertIn("locate-repository-content-identity", prompt)
                self.assertIn("hashmarks/test_shards.py", prompt)
                self.assertIn(
                    "terminal response must be exactly one JSON",
                    prompt,
                )
                self.assertIn(
                    "object and nothing else",
                    prompt,
                )
                self.assertNotIn(
                    "hashes canonical source paths and bytes",
                    prompt,
                )
                payload = {
                    "task_id": "locate-repository-content-identity",
                    "task_digest": task_digest,
                    "decision": "unique",
                    "reason": "Independent inspection found one semantic owner.",
                    "observed_owner": {
                        "path": "hashmarks/test_shards.py",
                        "symbol": "repository_content_identity",
                    },
                }
                return {
                    "stdout": "audit\n" + DECISION_PREFIX + json.dumps(payload),
                    "command_identity": "sha256:review-command",
                }

            with (
                mock.patch(
                    "benchmarks.harness.oracle_review_runner.materialize_repository",
                    side_effect=materialize,
                ),
                mock.patch(
                    "benchmarks.harness.oracle_review_runner._run_review",
                    side_effect=review,
                ),
            ):
                result = run_pending_oracle_reviews(
                    suite,
                    cache_root=Path(tmp) / "cache",
                )

            self.assertTrue(result["complete"])
            self.assertEqual(result["approved_tasks"], 12)
            self.assertEqual(
                [row["task_id"] for row in result["reviewed_tasks"]],
                ["locate-repository-content-identity"],
            )
            evidence = json.loads(
                (copy / "qualification/oracle-reviews.json").read_text(
                    encoding="utf-8"
                )
            )
            reviews = evidence["tasks"]["locate-repository-content-identity"][
                "reviews"
            ]
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[-1]["reviewer"], REVIEWER_ID)
            self.assertEqual(reviews[-1]["decision"], "unique")

    def test_oracle_review_runner_preserves_ambiguous_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            self._remove_identity_review(copy)
            suite = load_suite(copy)
            row = json.loads(
                (copy / "qualification/oracle-reviews.json").read_text(
                    encoding="utf-8"
                )
            )["tasks"]["locate-repository-content-identity"]

            materialize = self._materialize_expected_owner

            payload = {
                "task_id": "locate-repository-content-identity",
                "task_digest": row["task_digest"],
                "decision": "ambiguous",
                "reason": "Independent inspection found two defensible owners.",
                "observed_owner": {
                    "path": "hashmarks/test_shards.py",
                    "symbol": "repository_content_identity",
                },
            }
            with (
                mock.patch(
                    "benchmarks.harness.oracle_review_runner.materialize_repository",
                    side_effect=materialize,
                ),
                mock.patch(
                    "benchmarks.harness.oracle_review_runner._run_review",
                    return_value={
                        "stdout": DECISION_PREFIX + json.dumps(payload),
                        "command_identity": "sha256:review-command",
                    },
                ),
            ):
                result = run_pending_oracle_reviews(
                    suite,
                    cache_root=Path(tmp) / "cache",
                )

            self.assertFalse(result["complete"])
            self.assertEqual(
                result["pending_tasks"],
                ["locate-repository-content-identity"],
            )
            evidence = json.loads(
                (copy / "qualification/oracle-reviews.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                evidence["tasks"]["locate-repository-content-identity"][
                    "reviews"
                ][-1]["decision"],
                "ambiguous",
            )

    def test_expected_owner_presence_is_proved_before_model_review(self) -> None:
        suite = load_suite(SOURCE)
        task = suite.tasks["locate-repository-content-identity"]
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with self.assertRaisesRegex(
                OracleReviewError,
                "expected owner path is missing before review",
            ):
                _verify_expected_owner(workspace, task)

    def test_review_decision_must_bind_observed_expected_owner(self) -> None:
        suite = load_suite(SOURCE)
        task = suite.tasks["locate-repository-content-identity"]
        row = json.loads(
            (SOURCE / "qualification/oracle-reviews.json").read_text(
                encoding="utf-8"
            )
        )["tasks"]["locate-repository-content-identity"]
        payload = {
            "task_id": "locate-repository-content-identity",
            "task_digest": row["task_digest"],
            "decision": "invalid",
            "reason": "Expected owner could not be observed.",
        }
        with self.assertRaisesRegex(
            OracleReviewError,
            "did not observe the deterministically present expected owner",
        ):
            _parse_decision(
                DECISION_PREFIX + json.dumps(payload),
                task_id="locate-repository-content-identity",
                task_digest=row["task_digest"],
                expected_owner=task["oracle"]["configuration"]["expected"],
            )

    def test_review_decision_accepts_backticked_tagged_line(self) -> None:
        row = json.loads(
            (SOURCE / "qualification/oracle-reviews.json").read_text(
                encoding="utf-8"
            )
        )["tasks"]["locate-repository-content-identity"]
        payload = self._valid_oracle_decision_payload(row["task_digest"])
        result = _parse_decision(
            "Evidence checked.\n  `"
            + DECISION_PREFIX
            + json.dumps(payload)
            + "`",
            task_id="locate-repository-content-identity",
            task_digest=row["task_digest"],
            expected_owner=payload["observed_owner"],
        )
        self.assertEqual(result["decision"], "unique")

    def test_review_decision_accepts_whole_json_object(self) -> None:
        row = json.loads(
            (SOURCE / "qualification/oracle-reviews.json").read_text(
                encoding="utf-8"
            )
        )["tasks"]["locate-repository-content-identity"]
        payload = self._valid_oracle_decision_payload(row["task_digest"])
        result = _parse_decision(
            json.dumps(payload),
            task_id="locate-repository-content-identity",
            task_digest=row["task_digest"],
            expected_owner=payload["observed_owner"],
        )
        self.assertEqual(result["decision"], "unique")

    def test_review_decision_accepts_single_json_fence(self) -> None:
        row = json.loads(
            (SOURCE / "qualification/oracle-reviews.json").read_text(
                encoding="utf-8"
            )
        )["tasks"]["locate-repository-content-identity"]
        payload = self._valid_oracle_decision_payload(row["task_digest"])
        result = _parse_decision(
            "Evidence checked.\n```json\n"
            + json.dumps(payload)
            + "\n```",
            task_id="locate-repository-content-identity",
            task_digest=row["task_digest"],
            expected_owner=payload["observed_owner"],
        )
        self.assertEqual(result["decision"], "unique")

    def test_review_decision_rejects_json_hidden_in_prose_with_preview(self) -> None:
        row = json.loads(
            (SOURCE / "qualification/oracle-reviews.json").read_text(
                encoding="utf-8"
            )
        )["tasks"]["locate-repository-content-identity"]
        payload = self._valid_oracle_decision_payload(row["task_digest"])
        answer = "I think this is the result: " + json.dumps(payload) + " thanks."
        with self.assertRaisesRegex(
            OracleReviewError,
            "terminal answer preview",
        ):
            _parse_decision(
                answer,
                task_id="locate-repository-content-identity",
                task_digest=row["task_digest"],
                expected_owner=payload["observed_owner"],
            )

    def test_review_decision_rejects_multiple_conflicting_candidates(self) -> None:
        row = json.loads(
            (SOURCE / "qualification/oracle-reviews.json").read_text(
                encoding="utf-8"
            )
        )["tasks"]["locate-repository-content-identity"]
        first = self._valid_oracle_decision_payload(row["task_digest"])
        second = dict(first)
        second["decision"] = "ambiguous"
        second["reason"] = "Another owner is also defensible."
        answer = (
            DECISION_PREFIX
            + json.dumps(first)
            + "\n"
            + DECISION_PREFIX
            + json.dumps(second)
        )
        with self.assertRaisesRegex(
            OracleReviewError,
            "found 2 candidate",
        ):
            _parse_decision(
                answer,
                task_id="locate-repository-content-identity",
                task_digest=row["task_digest"],
                expected_owner=first["observed_owner"],
            )

    def test_non_unique_review_does_not_request_another_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            review_path = copy / "qualification/oracle-reviews.json"
            evidence = json.loads(review_path.read_text(encoding="utf-8"))
            row = evidence["tasks"]["locate-repository-content-identity"]
            row["reviews"].append(
                {
                    "decision": "invalid",
                    "reason": "Independent semantic review rejected the owner.",
                    "reviewer": "independent-review-b",
                    "task_digest": row["task_digest"],
                }
            )
            review_path.write_text(json.dumps(evidence), encoding="utf-8")
            with self.assertRaisesRegex(
                OracleReviewError,
                "Do not add another reviewer",
            ):
                validate_oracle_reviews(
                    load_suite(copy),
                    require_complete=True,
                )

    def test_review_runtime_reuses_qualified_opencode_session_wrapper(self) -> None:
        envelope = {
            "run": {"status": 0},
            "final_text": "review result",
            "export_parse_error": None,
        }
        process = mock.Mock(
            executable_missing=False,
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            return_code=0,
            stdout=json.dumps(envelope).encode(),
            stderr=b"",
            command_identity="sha256:runtime",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "benchmarks.harness.oracle_review_runner.run_bounded",
                return_value=process,
            ) as run,
        ):
            result = _run_review(
                Path(tmp),
                "prompt",
                task_id="locate-repository-content-identity",
            )
        argv = run.call_args.kwargs["argv"]
        self.assertEqual(argv[0], "node")
        self.assertIn("opencode-runtime.js", argv[1])
        self.assertIn("run-export", argv)
        self.assertIn("--repo", argv)
        self.assertEqual(result["stdout"], "review result")

    def test_oracle_review_cli_prints_next_action_and_succeeds(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(["oracle-review", "--suite", str(SOURCE)]),
                0,
            )
        rendered = output.getvalue()
        self.assertIn("Oracle review READY: 12/12 tasks approved", rendered)
        self.assertIn("make benchmark-oracle-review-check", rendered)
        self.assertIn("Then: make benchmark", rendered)

    def test_oracle_review_cli_execute_dispatches_runner(self) -> None:
        output = io.StringIO()
        with (
            mock.patch(
                "benchmarks.__main__.run_pending_oracle_reviews",
                return_value={
                    "reviewed_tasks": [
                        {"task_id": "locate-repository-content-identity"}
                    ],
                    "approved_tasks": 12,
                    "pending_tasks": [],
                    "complete": True,
                },
            ) as run,
            redirect_stdout(output),
        ):
            self.assertEqual(
                main(
                    [
                        "oracle-review",
                        "--suite",
                        str(SOURCE),
                        "--execute",
                    ]
                ),
                0,
            )
        self.assertTrue(run.called)
        self.assertIn('"complete": true', output.getvalue())

    def test_oracle_review_guide_reports_ready_for_committed_suite(self) -> None:
        suite = load_suite(SOURCE)
        guide = oracle_review_guide(suite)
        self.assertIn("Oracle review READY: 12/12 tasks approved", guide)
        self.assertIn("make benchmark-oracle-review-check", guide)
        self.assertIn("Then: make benchmark", guide)
        self.assertEqual(
            validate_oracle_reviews(suite, require_complete=True)[
                "approved_tasks"
            ],
            12,
        )

    def test_oracle_review_guide_hands_off_when_review_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(SOURCE, copy)
            self._remove_identity_review(copy)
            suite = load_suite(copy)
            guide = oracle_review_guide(suite)
            self.assertIn("Oracle review BLOCKED: 11/12 tasks approved", guide)
            self.assertIn("One independent source review is the default.", guide)
            self.assertIn(
                "This command does not self-approve benchmark truth.",
                guide,
            )
            self.assertIn("locate-repository-content-identity", guide)
            self.assertNotIn("locate-prefix-path-enumerator", guide)
            self.assertIn("required independent reviews: 1", guide)
            self.assertIn("existing independent reviews: 0", guide)
            self.assertNotIn("Prior heldout-v1 runs showed", guide)
            self.assertEqual(
                validate_oracle_reviews(suite, require_complete=False)[
                    "approved_tasks"
                ],
                11,
            )

    def test_diagnostic_is_separate_and_has_ten_paired_replicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            score = root / "score.json"
            source_results = root / "results"
            suite = load_suite(SOURCE)
            reports = {}
            for language in ("python", "typescript"):
                task_ids = sorted(
                    task_id
                    for task_id, task in suite.tasks.items()
                    if task["family"].startswith(language + "-")
                )
                count = len(task_ids) * 9
                reports[language] = {
                    "task_ids": task_ids,
                    "expected_trials": count,
                    "observed_trials": count,
                    "status_counts": {"PASS": count},
                    "campaign_qualification": {"status": "QUALIFIED"},
                    **{
                        field: []
                        for field in _SCORE_REPORT_FIELDS
                        if field
                        not in {
                            "expected_trials",
                            "observed_trials",
                            "status_counts",
                            "campaign_qualification",
                        }
                    },
                }
            payload = {
                "schema": "agents-cookbook-heldout-observer-outcomes.v7",
                "projection_mode": "live",
                "selection": {"agents": ["opencode-native"]},
                "expected_trials": 108,
                "observed_trials": 108,
                "languages": reports,
                "campaign_qualification": {
                    "status": "QUALIFIED",
                    "languages": {
                        name: row["campaign_qualification"]
                        for name, row in reports.items()
                    },
                },
            }
            score.write_text(json.dumps(payload), encoding="utf-8")
            target = root / "diagnostic"
            with (
                mock.patch(
                    "benchmarks.diagnostic.read_campaign",
                    return_value={
                        "campaign_id": "a" * 64,
                    },
                ),
                mock.patch(
                    "benchmarks.diagnostic.build_report",
                    side_effect=[
                        {
                            key: value
                            for key, value in reports[name].items()
                            if key != "task_ids"
                        }
                        for name in ("python", "typescript")
                    ],
                ),
            ):
                prepare_diagnostic_suite(
                    source_suite=SOURCE,
                    source_results=source_results,
                    score_path=score,
                    destination=target,
                    include_tasks={"locate-repository-content-identity"},
                )
            suite = load_suite(target)
            self.assertEqual(len(suite.trial_definitions()), 60)
            self.assertEqual(
                suite.experiment["tasks"], ["locate-repository-content-identity"]
            )
            self.assertEqual(
                suite.experiment["oracle_reviews"], "qualification/oracle-reviews.json"
            )
            self.assertIn(
                "diagnostic-only", (target / "diagnostic-source.json").read_text()
            )
            with self.assertRaises(DiagnosticError):
                prepare_diagnostic_suite(
                    source_suite=SOURCE,
                    source_results=source_results,
                    score_path=score,
                    destination=target,
                    include_tasks={"locate-repository-content-identity"},
                )

            payload["languages"]["python"]["stability"] = [{"state": "unstable"}]
            score.write_text(json.dumps(payload), encoding="utf-8")
            with (
                mock.patch(
                    "benchmarks.diagnostic.read_campaign",
                    return_value={
                        "campaign_id": "a" * 64,
                    },
                ),
                mock.patch(
                    "benchmarks.diagnostic.build_report",
                    side_effect=[
                        {
                            key: value
                            for key, value in reports[name].items()
                            if key != "task_ids" and key != "stability"
                        }
                        | {"stability": []}
                        for name in ("python", "typescript")
                    ],
                ),
            ):
                with self.assertRaisesRegex(DiagnosticError, "disagrees"):
                    prepare_diagnostic_suite(
                        source_suite=SOURCE,
                        source_results=source_results,
                        score_path=score,
                        destination=root / "tampered",
                        include_tasks={"locate-repository-content-identity"},
                    )


if __name__ == "__main__":
    unittest.main()
