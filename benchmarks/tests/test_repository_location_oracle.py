from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from benchmarks.adapters.oracles import (
    REPOSITORY_LOCATION_NORMALIZATION_POLICY,
    REPOSITORY_LOCATION_SCORING_POLICY,
    RepositoryLocationOracle,
)
from benchmarks.adapters.registry import build_oracle
from benchmarks.harness.identity import (
    EXECUTION_EVIDENCE_CONTRACT,
    REPLICATE_EVIDENCE_CONTRACT,
    REPLICATE_SCORE_CONTRACT,
    canonical_json,
    digest,
    execution_evidence_id,
    score_projection_id,
)
from benchmarks.harness.model import Observation, TrialContext
from benchmarks.harness.campaign import campaign_status
from benchmarks.harness.receipt import write_receipt
from benchmarks.harness.regrade import (
    RegradeError,
    project_campaign_receipts,
    regrade_repository_location_bundle,
)
from benchmarks.harness.report import (
    ReportError,
    _aggregate_condition,
    _check_localization_grades,
    build_report,
    repository_location_outcome_topology,
)
from benchmarks.harness.suite import SuiteDefinition, load_suite


EXPECTED = {
    "path": "hashmarks/codemap/repository_index_store.py",
    "symbol": "paths_under",
}


class RepositoryLocationOracleTests(unittest.TestCase):
    def _context(self, root: Path) -> TrialContext:
        workspace = root / "workspace"
        control = root / "control"
        target = workspace / EXPECTED["path"]
        target.parent.mkdir(parents=True)
        target.write_text("pass\n", encoding="utf-8")
        control.mkdir()
        return TrialContext(workspace, control, {})

    def _oracle(self) -> RepositoryLocationOracle:
        return RepositoryLocationOracle(
            "repository-location-test",
            "2",
            EXPECTED,
        )

    def _grade(self, context: TrialContext, final_message: str) -> Observation:
        return self._oracle().grade(
            context,
            Observation({"final_message": final_message}, ""),
        )

    def _bundle(
        self,
        root: Path,
        context: TrialContext,
        answer: str,
        *,
        task: dict | None = None,
        condition: dict | None = None,
        experiment: dict | None = None,
        definition_id: str | None = None,
        status: str = "PASS",
        trial_id: str = "a" * 64,
        trial_index: int = 0,
        seed: int = 1,
        replicate_id: int | None = None,
        observation_policy: str | None = None,
        oracle_identity: dict | None = None,
    ) -> Path:
        bundle = root / trial_id
        bundle.mkdir()
        campaign_id = None
        if replicate_id is not None:
            campaign_data = {
                "contract": "benchmark-campaign-authority.v1",
                "selected_definitions": [definition_id or "b" * 64],
            }
            campaign_id = digest(campaign_data)
            campaign_data["campaign_id"] = campaign_id
            campaign_dir = root / ".campaign"
            campaign_dir.mkdir(exist_ok=True)
            (campaign_dir / "authority.json").write_bytes(canonical_json(campaign_data))
            claims_dir = campaign_dir / "claims"
            claims_dir.mkdir(exist_ok=True)
            (claims_dir / f"{definition_id or 'b' * 64}.json").write_bytes(
                canonical_json(
                    {
                        "campaign_id": campaign_id,
                        "definition_id": definition_id or "b" * 64,
                        "trial_id": trial_id,
                        "attempt": 1,
                    }
                )
            )
        events = b""
        seal = {
            "trial_id": trial_id,
            "event_count": 0,
            "events_sha256": hashlib.sha256(events).hexdigest(),
        }
        artifacts = {}
        for name, filename, payload in (
            ("events", "events.jsonl", events),
            ("events_seal", "events.jsonl.seal.json", canonical_json(seal)),
            ("agent_trace", "agent-trace.jsonl", b"trace"),
        ):
            (bundle / filename).write_bytes(payload)
            artifacts[name] = {
                "path": filename,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        task = task or {"id": "locate", "family": "python-localization"}
        condition = condition or {"id": "bare"}
        authority = {
            "subject": {},
            "agent": {},
            "harness": {},
            "environment": {},
            "mutation": None,
            "oracle": {
                "declared": oracle_identity
                or dataclasses.asdict(self._oracle().identity())
            },
        }
        observed = self._oracle().observe(
            context, Observation({"final_message": answer}, "")
        )
        if observation_policy is not None:
            observed["normalization_policy"] = observation_policy
        evidence_id = execution_evidence_id(
            task=task,
            condition=condition,
            trial=trial_index,
            **(
                {"seed": seed}
                if replicate_id is None
                else {
                    "replicate_id": replicate_id,
                    "campaign_id": campaign_id,
                    "admitted_state_sha256": "a" * 64,
                }
            ),
            subject_identity=authority["subject"],
            agent_identity=authority["agent"],
            harness_identity=authority["harness"],
            environment_identity=authority["environment"],
            mutation_identity=None,
            agent_answer=answer,
            workspace_root=str(context.workspace),
            location_observation=observed,
            agent_trace_sha256=artifacts["agent_trace"]["sha256"],
        )
        write_receipt(
            bundle,
            {
                "definition_id": definition_id or "b" * 64,
                "trial_id": trial_id,
                "experiment": experiment
                or {"suite": "repository-intelligence", "id": "test"},
                "task": task,
                "condition": condition,
                "status": status,
                "authority": authority,
                "execution": {
                    "trial_index": trial_index,
                    **(
                        {"seed": seed}
                        if replicate_id is None
                        else {
                            "replicate_id": replicate_id,
                            "campaign_id": campaign_id,
                            "admitted_state_sha256": "a" * 64,
                        }
                    ),
                    "events": seal,
                    "artifacts": artifacts,
                    "agent_answer": answer,
                    "workspace_root": str(context.workspace),
                    "location_observation": observed,
                    "evidence_contract": (
                        EXECUTION_EVIDENCE_CONTRACT
                        if replicate_id is None
                        else REPLICATE_EVIDENCE_CONTRACT
                    ),
                    "evidence_identity": evidence_id,
                },
                "scoring": {
                    "projection_identity": score_projection_id(
                        execution_evidence=evidence_id,
                        oracle_identity=authority["oracle"]["declared"],
                        **(
                            {"contract": REPLICATE_SCORE_CONTRACT}
                            if replicate_id is not None
                            else {}
                        ),
                    ),
                    "oracle_grade": self._oracle().grade_observed(observed).payload,
                },
                "measurements": {"agent": {}},
                "reason": None,
            },
        )
        return bundle

    def test_repository_location_outcome_topology_is_descriptive_only(self) -> None:
        base = {
            "status": "FAIL",
            "task": {"oracle": {"adapter": "repository-location-json"}},
            "scoring": {
                "oracle_grade": {
                    "semantic_gradeable": True,
                    "expected": EXPECTED,
                }
            },
        }

        same_file = copy.deepcopy(base)
        same_file["scoring"]["oracle_grade"]["normalized_actual"] = {
            "path": EXPECTED["path"],
            "symbol": "other_symbol",
        }
        self.assertEqual(
            repository_location_outcome_topology(same_file),
            "same-file-wrong-symbol",
        )

        same_symbol = copy.deepcopy(base)
        same_symbol["scoring"]["oracle_grade"]["normalized_actual"] = {
            "path": "hashmarks/codemap/other.py",
            "symbol": EXPECTED["symbol"],
        }
        self.assertEqual(
            repository_location_outcome_topology(same_symbol),
            "same-symbol-wrong-file",
        )

        sibling = copy.deepcopy(base)
        sibling["scoring"]["oracle_grade"]["normalized_actual"] = {
            "path": "hashmarks/codemap/repository_file_discovery.py",
            "symbol": "_iter_admitted_repository_files",
        }
        self.assertEqual(
            repository_location_outcome_topology(sibling),
            "same-directory-location-mismatch",
        )

        distant = copy.deepcopy(base)
        distant["scoring"]["oracle_grade"]["normalized_actual"] = {
            "path": "hashmarks/other.py",
            "symbol": "other",
        }
        self.assertEqual(
            repository_location_outcome_topology(distant),
            "different-location",
        )

        ungradeable = copy.deepcopy(base)
        ungradeable["scoring"]["oracle_grade"]["semantic_gradeable"] = False
        ungradeable["scoring"]["oracle_grade"]["normalized_actual"] = None
        self.assertEqual(
            repository_location_outcome_topology(ungradeable),
            "ungradeable",
        )

        incomplete = copy.deepcopy(sibling)
        incomplete["status"] = "INCOMPLETE"
        self.assertIsNone(repository_location_outcome_topology(incomplete))

    def test_registry_builds_repository_location_oracle(self) -> None:
        oracle = build_oracle(
            {
                "adapter": "repository-location-json",
                "identity": {"id": "location", "version": "2"},
                "configuration": {"expected": EXPECTED},
            },
            timeout_seconds=30,
        )
        self.assertIsInstance(oracle, RepositoryLocationOracle)

    def test_bare_json_is_semantically_correct_and_format_compliant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                '{"path":"hashmarks/codemap/repository_index_store.py",'
                '"symbol":"paths_under"}',
            )
            self.assertTrue(grade.payload["passed"])
            self.assertTrue(grade.payload["semantic_success"])
            self.assertTrue(grade.payload["format_compliant"])
            self.assertTrue(grade.payload["semantic_gradeable"])
            self.assertEqual(grade.payload["semantic_status"], "CORRECT")
            self.assertEqual(
                grade.payload["normalization_policy"],
                REPOSITORY_LOCATION_NORMALIZATION_POLICY,
            )
            self.assertEqual(
                grade.payload["scoring_policy"],
                REPOSITORY_LOCATION_SCORING_POLICY,
            )
            self.assertEqual(grade.payload["normalized_actual"], EXPECTED)

    def test_single_json_fence_preserves_semantics_but_not_format_compliance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                "```json\n"
                '{"path":"hashmarks/codemap/repository_index_store.py",'
                '"symbol":"paths_under"}\n'
                "```",
            )
            self.assertTrue(grade.payload["passed"])
            self.assertTrue(grade.payload["semantic_success"])
            self.assertFalse(grade.payload["format_compliant"])
            self.assertIn("json-fence-unwrapped", grade.payload["normalizations"])

    def test_qualified_symbol_normalizes_to_terminal_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                '{"path":"hashmarks/codemap/repository_index_store.py",'
                '"symbol":"WorkspaceMapStore.paths_under"}',
            )
            self.assertTrue(grade.payload["semantic_success"])
            self.assertEqual(
                grade.payload["normalized_actual"]["symbol"],
                "paths_under",
            )
            self.assertIn(
                "qualified-symbol-to-terminal",
                grade.payload["normalizations"],
            )

    def test_workspace_absolute_path_normalizes_to_repository_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            absolute = (context.workspace / EXPECTED["path"]).resolve()
            grade = self._grade(
                context,
                json.dumps(
                    {
                        "path": str(absolute),
                        "symbol": "paths_under",
                    }
                ),
            )
            self.assertTrue(grade.payload["semantic_success"])
            self.assertIn(
                "workspace-absolute-path-to-relative",
                grade.payload["normalizations"],
            )
            self.assertEqual(grade.payload["normalized_actual"], EXPECTED)

    def test_wrong_location_remains_semantic_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                '{"path":"hashmarks/codemap/repository_file_discovery.py",'
                '"symbol":"_iter_admitted_repository_files"}',
            )
            self.assertFalse(grade.payload["passed"])
            self.assertFalse(grade.payload["semantic_success"])
            self.assertTrue(grade.payload["semantic_gradeable"])
            self.assertEqual(grade.payload["semantic_status"], "INCORRECT")
            self.assertTrue(grade.payload["format_compliant"])

    def test_prose_around_inline_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                'Here is the answer: {"path":"hashmarks/codemap/'
                'repository_index_store.py","symbol":"paths_under"}',
            )
            self.assertFalse(grade.payload["semantic_success"])
            self.assertFalse(grade.payload["semantic_gradeable"])
            self.assertEqual(grade.payload["semantic_status"], "UNSCORABLE")
            self.assertFalse(grade.payload["format_compliant"])
            self.assertIn("actual_text", grade.payload)

    def test_one_json_fence_inside_prose_preserves_semantics_not_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                "I have completed the objective.\n\n"
                "```json\n"
                '{"path":"hashmarks/codemap/repository_index_store.py",'
                '"symbol":"paths_under"}\n'
                "```\n"
                "Done.",
            )
            self.assertTrue(grade.payload["passed"])
            self.assertTrue(grade.payload["semantic_success"])
            self.assertTrue(grade.payload["semantic_gradeable"])
            self.assertEqual(grade.payload["semantic_status"], "CORRECT")
            self.assertFalse(grade.payload["format_compliant"])
            self.assertEqual(
                grade.payload["answer_shape"],
                "PROSE_WITH_JSON_FENCE",
            )
            self.assertIn(
                "embedded-json-fence-extracted",
                grade.payload["normalizations"],
            )

    def test_multiple_json_fences_remain_ungradeable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                "First:\n```json\n"
                '{"path":"hashmarks/codemap/repository_index_store.py",'
                '"symbol":"paths_under"}\n```\n'
                "Second:\n```json\n{}\n```",
            )
            self.assertFalse(grade.payload["semantic_success"])
            self.assertFalse(grade.payload["semantic_gradeable"])
            self.assertFalse(grade.payload["format_compliant"])
            self.assertIn("multiple code fences", grade.payload["reason"])

    def test_malformed_json_fence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                "```json\n"
                '{"path":"hashmarks/codemap/repository_index_store.py",'
                '"symbol":"paths_under"}',
            )
            self.assertFalse(grade.payload["semantic_success"])
            self.assertFalse(grade.payload["format_compliant"])

    def test_multiple_objects_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                '{"path":"hashmarks/codemap/repository_index_store.py",'
                '"symbol":"paths_under"}\n{}',
            )
            self.assertFalse(grade.payload["semantic_success"])
            self.assertFalse(grade.payload["format_compliant"])

    def test_duplicate_json_key_is_unscorable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                '{"path":"wrong.py","path":"hashmarks/codemap/'
                'repository_index_store.py","symbol":"paths_under"}',
            )
            self.assertEqual(grade.payload["semantic_status"], "UNSCORABLE")
            self.assertFalse(grade.payload["format_compliant"])
            self.assertIn("duplicate key", grade.payload["reason"])

    def test_report_separates_semantic_success_from_format_compliance(self) -> None:
        receipts = [
            {
                "status": "PASS",
                "execution": {},
                "scoring": {
                    "oracle_grade": {
                        "semantic_success": True,
                        "semantic_gradeable": True,
                        "semantic_status": "CORRECT",
                        "format_compliant": False,
                    }
                },
                "authority": {"subject": {"available": False}},
                "measurements": {"agent": {}},
            },
            {
                "status": "FAIL",
                "execution": {},
                "scoring": {
                    "oracle_grade": {
                        "semantic_success": False,
                        "semantic_gradeable": True,
                        "semantic_status": "INCORRECT",
                        "format_compliant": True,
                    }
                },
                "authority": {"subject": {"available": False}},
                "measurements": {"agent": {}},
            },
        ]
        report = _aggregate_condition(receipts)
        self.assertEqual(report["task_success_rate"], 0.5)
        self.assertEqual(report["semantic_success_rate"], 0.5)
        self.assertEqual(report["semantic_success_denominator"], 2)
        self.assertEqual(report["semantic_gradeable_rate"], 1.0)
        self.assertEqual(report["semantic_gradeable_denominator"], 2)
        self.assertEqual(
            report["semantic_statuses"],
            {"CORRECT": 1, "INCORRECT": 1},
        )
        self.assertEqual(report["format_compliance_rate"], 0.5)
        self.assertEqual(report["format_compliance_denominator"], 2)

        contaminated = dict(receipts[0], status="CONTAMINATED")
        invalid_report = _aggregate_condition([contaminated])
        self.assertEqual(invalid_report["valid_outcomes"], 0)
        self.assertIsNone(invalid_report["semantic_success_rate"])
        self.assertEqual(invalid_report["semantic_statuses"], {})
        self.assertIsNone(invalid_report["format_compliance_rate"])

    def test_valid_localization_requires_complete_grade(self) -> None:
        receipt = {
            "status": "PASS",
            "task": {
                "oracle": {
                    "adapter": "repository-location-json",
                    "configuration": {"expected": EXPECTED},
                }
            },
            "authority": {
                "oracle": {"declared": dataclasses.asdict(self._oracle().identity())}
            },
            "scoring": {"oracle_grade": {}},
            "execution": {
                "location_observation": {
                    "semantic_gradeable": True,
                    "normalized_actual": EXPECTED,
                    "format_compliant": True,
                    "normalization_policy": REPOSITORY_LOCATION_NORMALIZATION_POLICY,
                }
            },
        }
        with self.assertRaisesRegex(ReportError, "incomplete oracle grade"):
            _check_localization_grades([receipt])
        receipt["scoring"]["oracle_grade"] = (
            self._oracle()
            .grade_observed(
                {
                    "semantic_gradeable": True,
                    "normalized_actual": EXPECTED,
                    "format_compliant": True,
                    "normalization_policy": REPOSITORY_LOCATION_NORMALIZATION_POLICY,
                }
            )
            .payload
        )
        receipt["authority"]["oracle"]["declared"]["provenance"][
            "normalization_policy"
        ] = "repository-location-normalization.v1"
        with self.assertRaisesRegex(ReportError, "inconsistent oracle grade"):
            _check_localization_grades([receipt])
        receipt["authority"]["oracle"]["declared"]["provenance"][
            "normalization_policy"
        ] = REPOSITORY_LOCATION_NORMALIZATION_POLICY
        _check_localization_grades([receipt])
        receipt["status"] = "FAIL"
        with self.assertRaisesRegex(ReportError, "inconsistent oracle grade"):
            _check_localization_grades([receipt])
        receipt["status"] = "CONTAMINATED"
        _check_localization_grades([receipt])

    def test_execution_evidence_identity_ignores_scoring_only_task_changes(
        self,
    ) -> None:
        task_v1 = {
            "id": "locate",
            "version": 1,
            "family": "python-localization",
            "repository": {"url": "x", "commit": "a", "tree": "b"},
            "prompt": "find it",
            "mode": "read_only",
            "mutation": None,
            "budgets": {"timeout_seconds": 1},
            "contamination": {
                "allowed_change_globs": [],
                "allowed_generated_globs": [],
            },
            "oracle": {"adapter": "expected-json"},
        }
        task_v2 = {
            **task_v1,
            "version": 2,
            "oracle": {"adapter": "repository-location-json"},
        }
        kwargs = {
            "condition": {"id": "bare", "agent": "a", "subject": "none"},
            "trial": 0,
            "seed": 1,
            "subject_identity": {"available": True},
            "agent_identity": {"available": True},
            "harness_identity": {"commit": "h"},
            "environment_identity": {"env": "e"},
            "mutation_identity": None,
            "agent_answer": "answer",
            "workspace_root": "/tmp/workspace",
            "location_observation": {"semantic_gradeable": True},
            "agent_trace_sha256": "a" * 64,
        }
        self.assertEqual(
            execution_evidence_id(task=task_v1, **kwargs),
            execution_evidence_id(task=task_v2, **kwargs),
        )
        baseline = execution_evidence_id(task=task_v1, **kwargs)
        for field, changed in (
            ("agent_answer", "different"),
            ("workspace_root", "/tmp/other"),
            ("location_observation", {"semantic_gradeable": False}),
            ("agent_trace_sha256", "b" * 64),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(
                    baseline,
                    execution_evidence_id(task=task_v1, **{**kwargs, field: changed}),
                )

    def test_offline_regrade_reuses_frozen_execution_without_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            answer = (
                "```json\n"
                '{"path":"hashmarks/codemap/repository_index_store.py",'
                '"symbol":"WorkspaceMapStore.paths_under"}\n'
                "```"
            )
            bundle = self._bundle(Path(tmp), context, answer)
            shutil.rmtree(context.workspace)
            first = regrade_repository_location_bundle(bundle, self._oracle())
            second = regrade_repository_location_bundle(bundle, self._oracle())
            self.assertEqual(first, second)
            self.assertRegex(first["execution_evidence_id"], r"^[0-9a-f]{64}$")
            self.assertTrue(first["oracle_grade"]["semantic_success"])
            self.assertFalse(first["oracle_grade"]["format_compliant"])
            self.assertIn(
                "qualified-symbol-to-terminal",
                first["oracle_grade"]["normalizations"],
            )

            changed_oracle = RepositoryLocationOracle(
                "repository-location-test",
                "3",
                {
                    "path": "hashmarks/codemap/repository_index_store.py",
                    "symbol": "different_symbol",
                },
            )
            changed = regrade_repository_location_bundle(bundle, changed_oracle)
            self.assertEqual(
                changed["execution_evidence_id"], first["execution_evidence_id"]
            )
            self.assertNotEqual(
                changed["projection_identity"],
                first["projection_identity"],
            )
            self.assertFalse(changed["oracle_grade"]["semantic_success"])
            receipt_path = bundle / "result.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["execution"]["agent_answer"] = "different"
            receipt_path.write_bytes(canonical_json(receipt))
            with self.assertRaisesRegex(RegradeError, "invalid source bundle"):
                regrade_repository_location_bundle(bundle, self._oracle())
            payload = canonical_json(receipt)
            digest = hashlib.sha256(payload).hexdigest()
            (bundle / "result.sha256").write_text(
                f"{digest}  result.json\n", encoding="utf-8"
            )
            (bundle / "completion.json").write_bytes(
                canonical_json({"result_sha256": digest})
            )
            with self.assertRaisesRegex(RegradeError, "evidence identity mismatch"):
                regrade_repository_location_bundle(bundle, self._oracle())

    def test_project_campaign_regrades_only_compatible_verified_execution(self) -> None:
        suite = load_suite(
            Path(__file__).resolve().parents[1]
            / "suites/repository-intelligence/heldout-v1"
        )
        row = next(
            row
            for row in suite.trial_definitions()
            if row["task_id"] == "locate-prefix-path-enumerator"
            and row["condition_id"] == "none-opencode-native"
            and row["trial"] == 0
        )
        condition = next(
            suite.expanded_condition(value)
            for value in suite.experiment["conditions"]
            if value["id"] == row["condition_id"]
        )
        answer = json.dumps(EXPECTED)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self._context(root)
            results = root / "results"
            results.mkdir()
            self._bundle(
                results,
                context,
                answer,
                task=suite.tasks[row["task_id"]],
                condition=condition,
                experiment=suite.experiment,
                definition_id=row["definition_id"],
                replicate_id=row["replicate_id"],
            )
            first, lineage = project_campaign_receipts(
                suite=suite,
                source_results=results,
                selected_definitions={row["definition_id"]},
            )
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["status"], "PASS")
            self.assertEqual(len(lineage), 1)

            claim = results / ".campaign" / "claims" / f"{row['definition_id']}.json"
            claim.unlink()
            with self.assertRaisesRegex(ReportError, "launch claim"):
                build_report(
                    suite=suite,
                    results_root=results,
                    selected_definitions={row["definition_id"]},
                )
            status_without_claim = campaign_status(
                suite=suite,
                results_root=results,
                selected_definitions={row["definition_id"]},
            )
            self.assertFalse(status_without_claim["qualified"])
            self.assertEqual(len(status_without_claim["corrupt_bundles"]), 1)
            with self.assertRaisesRegex(RegradeError, "launch claim"):
                project_campaign_receipts(
                    suite=suite,
                    source_results=results,
                    selected_definitions={row["definition_id"]},
                )
            claim.write_bytes(
                canonical_json(
                    {
                        "campaign_id": json.loads(
                            (results / ".campaign" / "authority.json").read_text()
                        )["campaign_id"],
                        "definition_id": row["definition_id"],
                        "trial_id": "b" * 64,
                        "attempt": 1,
                    }
                )
            )
            with self.assertRaisesRegex(ReportError, "launch claim"):
                build_report(
                    suite=suite,
                    results_root=results,
                    selected_definitions={row["definition_id"]},
                )
            claim.write_bytes(
                canonical_json(
                    {
                        "campaign_id": json.loads(
                            (results / ".campaign" / "authority.json").read_text()
                        )["campaign_id"],
                        "definition_id": row["definition_id"],
                        "trial_id": "a" * 64,
                        "attempt": 1,
                    }
                )
            )
            foreign_claim = claim.parent / f"{'c' * 64}.json"
            foreign_claim.write_bytes(
                canonical_json(
                    {
                        "campaign_id": json.loads(
                            (results / ".campaign" / "authority.json").read_text()
                        )["campaign_id"],
                        "definition_id": "c" * 64,
                        "trial_id": "d" * 64,
                        "attempt": 1,
                    }
                )
            )
            with self.assertRaisesRegex(ReportError, "exceeds frozen campaign"):
                build_report(
                    suite=suite,
                    results_root=results,
                    selected_definitions={row["definition_id"]},
                )
            foreign_claim.unlink()

            changed_task = copy.deepcopy(suite.tasks[row["task_id"]])
            changed_task["oracle"]["configuration"]["expected"]["symbol"] = "other"
            changed_suite = SuiteDefinition(
                suite.root,
                suite.experiment,
                {**suite.tasks, row["task_id"]: changed_task},
                suite.subjects,
                suite.agents,
            )
            changed_row = next(
                value
                for value in changed_suite.trial_definitions()
                if value["task_id"] == row["task_id"]
                and value["condition_id"] == row["condition_id"]
                and value["trial"] == 0
            )
            second, _ = project_campaign_receipts(
                suite=changed_suite,
                source_results=results,
                selected_definitions={changed_row["definition_id"]},
            )
            self.assertEqual(second[0]["status"], "FAIL")
            self.assertEqual(
                second[0]["execution"]["evidence_identity"],
                first[0]["execution"]["evidence_identity"],
            )
            self.assertNotEqual(
                second[0]["scoring"]["projection_identity"],
                first[0]["scoring"]["projection_identity"],
            )
            changed_task["prompt"] = "different prompt"
            changed_row = next(
                value
                for value in changed_suite.trial_definitions()
                if value["task_id"] == row["task_id"]
                and value["condition_id"] == row["condition_id"]
                and value["trial"] == 0
            )
            with self.assertRaisesRegex(RegradeError, "execution inputs changed"):
                project_campaign_receipts(
                    suite=changed_suite,
                    source_results=results,
                    selected_definitions={changed_row["definition_id"]},
                )

            empty_results = root / "empty-results"
            empty_results.mkdir()
            with self.assertRaisesRegex(RegradeError, "incomplete"):
                project_campaign_receipts(
                    suite=suite,
                    source_results=empty_results,
                    selected_definitions={row["definition_id"]},
                )

            invalid_results = root / "invalid-results"
            invalid_results.mkdir()
            self._bundle(
                invalid_results,
                context,
                answer,
                task=suite.tasks[row["task_id"]],
                condition=condition,
                experiment=suite.experiment,
                definition_id=row["definition_id"],
                status="CONTAMINATED",
                replicate_id=row["replicate_id"],
            )
            with self.assertRaisesRegex(RegradeError, "not qualified"):
                project_campaign_receipts(
                    suite=suite,
                    source_results=invalid_results,
                    selected_definitions={row["definition_id"]},
                )

            old_policy_results = root / "old-policy-results"
            old_policy_results.mkdir()
            self._bundle(
                old_policy_results,
                context,
                answer,
                task=suite.tasks[row["task_id"]],
                condition=condition,
                experiment=suite.experiment,
                definition_id=row["definition_id"],
                replicate_id=row["replicate_id"],
                observation_policy="repository-location-normalization.v1",
            )
            with self.assertRaisesRegex(RegradeError, "normalization policy differs"):
                project_campaign_receipts(
                    suite=suite,
                    source_results=old_policy_results,
                    selected_definitions={row["definition_id"]},
                )

    def test_project_campaign_requires_unchanged_repair_oracle(self) -> None:
        suite = load_suite(
            Path(__file__).resolve().parents[1]
            / "suites/repository-intelligence/heldout-v1"
        )
        row = next(
            row
            for row in suite.trial_definitions()
            if row["task_id"] == "repair-partial-receipt-regression"
            and row["condition_id"] == "none-opencode-native"
            and row["trial"] == 0
        )
        task = suite.tasks[row["task_id"]]
        condition = next(
            suite.expanded_condition(value)
            for value in suite.experiment["conditions"]
            if value["id"] == row["condition_id"]
        )
        oracle = build_oracle(
            task["oracle"],
            timeout_seconds=task["budgets"]["timeout_seconds"],
            suite_root=suite.root,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self._context(root)
            results = root / "results"
            results.mkdir()
            self._bundle(
                results,
                context,
                json.dumps(EXPECTED),
                task=task,
                condition=condition,
                experiment=suite.experiment,
                definition_id=row["definition_id"],
                replicate_id=row["replicate_id"],
                oracle_identity=dataclasses.asdict(oracle.identity()),
            )
            projected, _ = project_campaign_receipts(
                suite=suite,
                source_results=results,
                selected_definitions={row["definition_id"]},
            )
            self.assertEqual(projected[0]["scoring"]["oracle_grade"]["passed"], True)

            changed_task = copy.deepcopy(task)
            changed_task["oracle"]["configuration"]["grade_argv"].append("--different")
            changed_suite = SuiteDefinition(
                suite.root,
                suite.experiment,
                {**suite.tasks, row["task_id"]: changed_task},
                suite.subjects,
                suite.agents,
            )
            changed_row = next(
                value
                for value in changed_suite.trial_definitions()
                if value["task_id"] == row["task_id"]
                and value["condition_id"] == row["condition_id"]
                and value["trial"] == 0
            )
            with self.assertRaisesRegex(
                RegradeError, "non-localization oracle changed"
            ):
                project_campaign_receipts(
                    suite=changed_suite,
                    source_results=results,
                    selected_definitions={changed_row["definition_id"]},
                )

    def test_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp))
            grade = self._grade(
                context,
                '{"path":"../outside.py","symbol":"paths_under"}',
            )
            self.assertFalse(grade.payload["semantic_success"])
            self.assertTrue(grade.payload["format_compliant"])
            self.assertIn("escapes", grade.payload["reason"])


if __name__ == "__main__":
    unittest.main()
