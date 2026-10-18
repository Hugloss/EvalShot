from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from benchmarks.harness.campaign_authority import (
    CampaignAuthorityError,
    admit_campaign,
    campaign_suite_identity,
    campaign_trial_id,
    audit_campaign,
    claim_launch,
    launch_state,
    read_campaign,
    read_interrupted_attempts,
    record_interrupted_attempt,
    verify_campaign_suite_authority,
)
from benchmarks.harness.identity import definition_id, execution_evidence_id
from benchmarks.harness.suite import SuiteDefinition


def _suite() -> SuiteDefinition:
    experiment = {
        "id": "campaign-test",
        "version": 1,
        "suite": "test",
        "tasks": ["task-a", "task-b"],
        "conditions": [
            {
                "id": "bare",
                "agent": "agent",
                "subject": "none",
                "trials": 1,
                "replicate_ids": [9],
            },
            {
                "id": "assisted",
                "agent": "agent",
                "subject": "tool",
                "trials": 1,
                "replicate_ids": [9],
            },
        ],
        "scoring": {"id": "score", "version": 1, "metrics": ["task_success"]},
        "subject_exposure_contract": {"require_probe_contract": True},
    }
    tasks = {
        name: {"id": name, "prompt": name, "budgets": {"timeout_seconds": 1}}
        for name in experiment["tasks"]
    }
    subjects = {
        "none": {"id": "none", "kind": "control"},
        "tool": {
            "id": "tool",
            "kind": "repository_intelligence",
            "exposure_probe": {"required_tool": "tool_context"},
        },
    }
    agents = {"agent": {"id": "agent"}}
    return SuiteDefinition(Path("."), experiment, tasks, subjects, agents)


def _fake_condition_authority(admission):
    return {
        "agent": {
            "declared": {"id": admission.condition["agent"]},
            "model": "same-model",
            "native_config_sha256": admission.task["id"],
        },
        "subject": {
            "declared": {"id": admission.condition["subject"]},
            "observed": None,
            "source_identity": None,
        },
    }


class CampaignAuthorityTests(unittest.TestCase):
    def test_frozen_suite_authority_has_one_owner(self) -> None:
        suite = _suite()
        campaign = {"suite_identity": campaign_suite_identity(suite)}
        verify_campaign_suite_authority(suite=suite, campaign=campaign)

        changed_tasks = dict(suite.tasks)
        changed_tasks["task-a"] = {
            **suite.tasks["task-a"],
            "prompt": "changed after campaign admission",
        }
        changed = SuiteDefinition(
            suite.root,
            suite.experiment,
            changed_tasks,
            suite.subjects,
            suite.agents,
        )
        with self.assertRaisesRegex(
            CampaignAuthorityError,
            "saved campaign suite authority differs from current suite",
        ):
            verify_campaign_suite_authority(suite=changed, campaign=campaign)

    def test_campaign_checks_every_task_and_subject_before_freezing(self) -> None:
        suite = _suite()
        rows = suite.trial_definitions()
        seen = []
        transported_harness = []

        @contextmanager
        def fake_admission(**kwargs):
            task_id = kwargs["task_id"]
            condition_id = kwargs["condition_id"]
            seen.append((task_id, condition_id))
            transported_harness.append(kwargs.get("harness_authority"))
            condition = next(
                value
                for value in suite.experiment["conditions"]
                if value["id"] == condition_id
            )
            yield SimpleNamespace(
                task=suite.tasks[task_id],
                condition=condition,
                admitted_state={"source": "same"},
                mutation_authority=None,
                initial_outcome=lambda: (None, None),
                cleanup_subject=lambda: None,
                admission_timings_ms={"total": 10},
                definition_id=next(
                    row["definition_id"]
                    for row in rows
                    if row["task_id"] == task_id and row["condition_id"] == condition_id
                ),
            )

        catalog_proofs = [
            {
                "subject_id": "tool",
                "required_tool": "tool_context",
                "required_operation": "context",
                "protocol_version": "2024-11-05",
                "tool_names": ["context"],
                "required_tool_visible": True,
                "tool_count": 1,
                "catalog_sha256": "a" * 64,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = dict(
                suite=suite,
                rows=rows,
                results_root=root / "results",
                harness_root=root,
                cache_root=root / "cache",
                work_root=root / "work",
            )
            with (
                mock.patch(
                    "benchmarks.harness.campaign_authority.admit_trial", fake_admission
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.condition_authority",
                    side_effect=_fake_condition_authority,
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.harness_identity",
                    return_value={"commit": "stable-harness"},
                ) as harness_identity,
                mock.patch(
                    "benchmarks.harness.campaign_authority.probe_subject_catalog_contracts",
                    return_value=catalog_proofs,
                ),
            ):
                progress = []
                audited = audit_campaign(
                    **{k: v for k, v in options.items() if k != "results_root"},
                    on_progress=progress.append,
                    subject_catalog_proofs=catalog_proofs,
                )
                self.assertEqual(len(seen), 4)
                self.assertEqual(harness_identity.call_count, 2)
                self.assertEqual(
                    transported_harness,
                    [{"commit": "stable-harness"}] * 4,
                )
                condition_progress = [
                    row for row in progress if row["stage"] == "condition-authority"
                ]
                self.assertEqual(len(condition_progress), 4)
                self.assertEqual(
                    [row["index"] for row in condition_progress],
                    [1, 2, 3, 4],
                )
                self.assertTrue(
                    all(row["total"] == 4 for row in condition_progress)
                )
                self.assertEqual(
                    progress[-1],
                    {
                        "stage": "campaign-authority",
                        "status": "verified",
                        "total": 4,
                    },
                )
                self.assertFalse((root / "results/.campaign").exists())
                campaign = admit_campaign(**options)
                self.assertEqual(len(seen), 8)
                self.assertEqual(audited, campaign)
                self.assertEqual(
                    campaign["contract"], "benchmark-campaign-authority.v5"
                )
                self.assertNotEqual(
                    campaign["task_conditions"]["task-a"]["bare"]["agent"][
                        "native_config_sha256"
                    ],
                    campaign["task_conditions"]["task-b"]["bare"]["agent"][
                        "native_config_sha256"
                    ],
                )
                self.assertEqual(read_campaign(root / "results"), campaign)
                self.assertEqual(admit_campaign(**options), campaign)
                with self.assertRaisesRegex(CampaignAuthorityError, "selection"):
                    admit_campaign(**{**options, "rows": rows[:1]})
                self.assertEqual(
                    claim_launch(
                        results_root=root / "results",
                        campaign=campaign,
                        definition_id=rows[0]["definition_id"],
                        trial_id="a" * 64,
                    ),
                    1,
                )
                self.assertEqual(
                    launch_state(
                        results_root=root / "results",
                        campaign=campaign,
                        definition_id=rows[0]["definition_id"],
                        trial_id="a" * 64,
                    ),
                    "INTERRUPTED",
                )
                with self.assertRaisesRegex(CampaignAuthorityError, "already claimed"):
                    claim_launch(
                        results_root=root / "results",
                        campaign=campaign,
                        definition_id=rows[0]["definition_id"],
                        trial_id="a" * 64,
                    )
                self.assertEqual(
                    record_interrupted_attempt(
                        results_root=root / "results",
                        campaign=campaign,
                        definition_id=rows[0]["definition_id"],
                        trial_id="a" * 64,
                    ),
                    1,
                )
                self.assertEqual(
                    launch_state(
                        results_root=root / "results",
                        campaign=campaign,
                        definition_id=rows[0]["definition_id"],
                        trial_id="a" * 64,
                    ),
                    "UNCLAIMED",
                )
                attempts = read_interrupted_attempts(
                    root / "results",
                    campaign["campaign_id"],
                )
                self.assertEqual(
                    attempts[rows[0]["definition_id"]][0]["attempt"],
                    1,
                )
                self.assertEqual(
                    claim_launch(
                        results_root=root / "results",
                        campaign=campaign,
                        definition_id=rows[0]["definition_id"],
                        trial_id="a" * 64,
                    ),
                    2,
                )

            def drifted(admission):
                value = _fake_condition_authority(admission)
                if admission.task["id"] == "task-b":
                    value["agent"]["model"] = "different-model"
                return value

            with (
                mock.patch(
                    "benchmarks.harness.campaign_authority.admit_trial", fake_admission
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.condition_authority",
                    side_effect=drifted,
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.harness_identity",
                    return_value={"commit": "stable-harness"},
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.probe_subject_catalog_contracts",
                    return_value=catalog_proofs,
                ),
            ):
                with self.assertRaisesRegex(CampaignAuthorityError, "runtime or model"):
                    admit_campaign(**options)

    def test_campaign_rejects_missing_subject_exposure_contract_before_work(
        self,
    ) -> None:
        suite = _suite()
        suite.subjects["tool"] = {
            key: value
            for key, value in suite.subjects["tool"].items()
            if key != "exposure_probe"
        }
        rows = suite.trial_definitions()

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "benchmarks.harness.campaign_authority.admit_trial",
            side_effect=AssertionError("participant admission must not start"),
        ) as admit:
            root = Path(tmp)
            with self.assertRaisesRegex(
                CampaignAuthorityError,
                "has no exposure_probe contract",
            ):
                audit_campaign(
                    suite=suite,
                    rows=rows,
                    harness_root=root,
                    cache_root=root / "cache",
                    work_root=root / "work",
                )
        admit.assert_not_called()

    def test_campaign_rejects_missing_required_tool_before_trial_admission(
        self,
    ) -> None:
        suite = _suite()
        rows = suite.trial_definitions()
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "benchmarks.harness.campaign_authority.probe_subject_catalog_contracts",
                side_effect=ValueError(
                    "MCP required operation is not exposed: context"
                ),
            ),
            mock.patch(
                "benchmarks.harness.campaign_authority.admit_trial",
                side_effect=AssertionError("trial admission must not start"),
            ) as admit,
        ):
            root = Path(tmp)
            with self.assertRaisesRegex(
                CampaignAuthorityError,
                "subject MCP catalog admission failed: "
                "MCP required operation is not exposed: context",
            ):
                admit_campaign(
                    suite=suite,
                    rows=rows,
                    results_root=root / "results",
                    harness_root=root,
                    cache_root=root / "cache",
                    work_root=root / "work",
                )
        admit.assert_not_called()

    def test_campaign_trial_id_ignores_ephemeral_subject_exposure_hash(self) -> None:
        frozen = {
            "agent": {
                "declared": {"id": "agent"},
                "version": "opencode 1",
                "executable_sha256": "a" * 64,
                "model": "liteLLM/gemma4",
                "provider": "liteLLM",
                "native_config_sha256": "b" * 64,
            },
            "subject": {
                "declared": {"id": "tool"},
                "observed": {
                    "subject": "tool",
                    "native_subject_identity": {
                        "verified": True,
                        "subject": "tool",
                        "executable_sha256": "c" * 64,
                    },
                    "mcp_semantic_identity": {
                        "name": "tool",
                        "workspace": "trial-workspace",
                    },
                },
                "source_identity": {"sha256": "d" * 64},
                "mcp_exposure": {
                    "name": "tool",
                    "command": "/opt/tool",
                    "semantic_identity": {
                        "name": "tool",
                        "workspace": "trial-workspace",
                    },
                },
            },
            "harness": {"commit": "e" * 40},
            "environment": {"environment_sha256": "f" * 64},
        }
        campaign = {
            "task_conditions": {"task-a": {"assisted": frozen}},
        }

        def admission(exposure_sha: str):
            return SimpleNamespace(
                definition_id="1" * 64,
                task={"id": "task-a"},
                condition={"id": "assisted"},
                oracle_authority={
                    "declared": {"participant_id": "oracle", "version": "1"},
                    "healthy": True,
                },
                mutation_authority={"sha256": "2" * 64},
                subject_authority={
                    "declared": {"id": "tool"},
                    "available": True,
                    "observed": {
                        "mcp_exposure": {
                            "subject_exposure_sha256": exposure_sha,
                        }
                    },
                },
                agent_authority={
                    "declared": {"id": "agent"},
                    "available": True,
                    "observed": {
                        "mcp_exposure": {
                            "subject_exposure_sha256": exposure_sha,
                        }
                    },
                },
            )

        first = campaign_trial_id(
            campaign=campaign,
            admission=admission("3" * 64),
        )
        second = campaign_trial_id(
            campaign=campaign,
            admission=admission("4" * 64),
        )
        self.assertEqual(first, second)

        changed = {
            **campaign,
            "task_conditions": {
                "task-a": {
                    "assisted": {
                        **frozen,
                        "agent": {
                            **frozen["agent"],
                            "native_config_sha256": "9" * 64,
                        },
                    }
                }
            },
        }
        self.assertNotEqual(
            first,
            campaign_trial_id(
                campaign=changed,
                admission=admission("3" * 64),
            ),
        )

    def test_replicate_definition_identity_does_not_reinterpret_legacy_seed(
        self,
    ) -> None:
        suite = _suite()
        condition = suite.expanded_condition(suite.experiment["conditions"][0])
        common = dict(
            experiment=suite.experiment,
            task=suite.tasks["task-a"],
            condition=condition,
            trial=0,
        )
        self.assertNotEqual(
            definition_id(**common, seed=9), definition_id(**common, replicate_id=9)
        )

    def test_admitted_workspace_is_bound_into_v3_evidence(self) -> None:
        arguments = dict(
            task={"id": "task-a", "prompt": "find it"},
            condition={"id": "bare"},
            trial=0,
            replicate_id=9,
            campaign_id="c" * 64,
            subject_identity={},
            agent_identity={},
            harness_identity={},
            environment_identity={},
            mutation_identity=None,
            agent_answer="answer",
            workspace_root="/tmp/work",
            location_observation=None,
            agent_trace_sha256="d" * 64,
        )
        self.assertNotEqual(
            execution_evidence_id(**arguments, admitted_state_sha256="a" * 64),
            execution_evidence_id(**arguments, admitted_state_sha256="b" * 64),
        )
        with self.assertRaisesRegex(ValueError, "admitted_state"):
            execution_evidence_id(**arguments)

    def test_campaign_rejects_harness_drift_after_condition_checks(self) -> None:
        suite = _suite()
        rows = suite.trial_definitions()[:1]

        @contextmanager
        def fake_admission(**kwargs):
            condition = suite.experiment["conditions"][0]
            yield SimpleNamespace(
                task=suite.tasks["task-a"],
                condition=condition,
                admitted_state={"source": "same"},
                mutation_authority=None,
                initial_outcome=lambda: (None, None),
                cleanup_subject=lambda: None,
                admission_timings_ms={"total": 1},
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch(
                    "benchmarks.harness.campaign_authority.admit_trial",
                    fake_admission,
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.condition_authority",
                    side_effect=_fake_condition_authority,
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.harness_identity",
                    side_effect=[
                        {"commit": "before"},
                        {"commit": "after"},
                    ],
                ),
            ):
                with self.assertRaisesRegex(
                    CampaignAuthorityError,
                    "harness authority changed during campaign admission",
                ):
                    audit_campaign(
                        suite=suite,
                        rows=rows,
                        harness_root=root,
                        cache_root=root / "cache",
                        work_root=root / "work",
                    )

    def test_late_preflight_failure_publishes_no_campaign_or_launch(self) -> None:
        suite = _suite()
        rows = suite.trial_definitions()

        @contextmanager
        def fake_admission(**kwargs):
            failed = (
                kwargs["task_id"] == "task-b" and kwargs["condition_id"] == "assisted"
            )
            condition = next(
                value
                for value in suite.experiment["conditions"]
                if value["id"] == kwargs["condition_id"]
            )
            yield SimpleNamespace(
                task=suite.tasks[kwargs["task_id"]],
                condition=condition,
                admitted_state={"source": "same"},
                mutation_authority=None,
                initial_outcome=lambda: (
                    ("INCOMPLETE", "unavailable") if failed else (None, None)
                ),
                cleanup_subject=lambda: None,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch(
                    "benchmarks.harness.campaign_authority.admit_trial", fake_admission
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.condition_authority",
                    side_effect=_fake_condition_authority,
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.harness_identity",
                    return_value={"commit": "stable-harness"},
                ),
                mock.patch(
                    "benchmarks.harness.campaign_authority.probe_subject_catalog_contracts",
                    return_value=[
                        {
                            "subject_id": "tool",
                            "required_tool": "tool_context",
                            "required_operation": "context",
                            "protocol_version": "2024-11-05",
                            "tool_names": ["context"],
                            "required_tool_visible": True,
                            "tool_count": 1,
                            "catalog_sha256": "b" * 64,
                        }
                    ],
                ),
            ):
                with self.assertRaisesRegex(
                    CampaignAuthorityError, "cannot enter campaign"
                ):
                    admit_campaign(
                        suite=suite,
                        rows=rows,
                        results_root=root / "results",
                        harness_root=root,
                        cache_root=root / "cache",
                        work_root=root / "work",
                    )
            self.assertFalse((root / "results/.campaign/authority.json").exists())
            self.assertFalse((root / "results/.campaign/claims").exists())

    def test_model_free_audit_rejects_stale_results_before_admission(self) -> None:
        suite = _suite()
        rows = suite.trial_definitions()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "results" / "old-receipt").mkdir(parents=True)
            with mock.patch(
                "benchmarks.harness.campaign_authority.admit_trial",
                side_effect=AssertionError("stale results must fail first"),
            ):
                with self.assertRaisesRegex(
                    CampaignAuthorityError, "no campaign authority"
                ):
                    audit_campaign(
                        suite=suite,
                        rows=rows,
                        results_root=root / "results",
                        harness_root=root,
                        cache_root=root / "cache",
                        work_root=root / "work",
                    )


if __name__ == "__main__":
    unittest.main()
