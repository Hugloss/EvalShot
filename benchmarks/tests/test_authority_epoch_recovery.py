from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from benchmarks.adapters.opencode_native import _enrich_authority_revalidation
from benchmarks.harness.campaign import campaign_status
from benchmarks.harness.campaign_authority import (
    CampaignAuthorityError,
    authority_epoch_projection,
    bind_authority_epoch,
    claim_launch,
    launch_state,
    read_authority_epochs,
    read_interrupted_attempts,
    record_interrupted_attempt,
)
from benchmarks.harness.identity import canonical_json, digest
from benchmarks.harness.decision_evidence import build_decision_evidence
from benchmarks.harness.model import Observation
from benchmarks.harness.report import build_report
from benchmarks.harness.runner import _agent_failure
from benchmarks.harness.suite import SuiteDefinition


def _authority(executable: str = "a" * 64) -> dict:
    return {
        "agent": {
            "declared": {"participant_id": "agent"},
            "version": "opencode 1",
            "executable_sha256": executable,
            "model": "gemma4",
            "provider": "litellm",
        },
        "subject": {
            "declared": {"participant_id": "none"},
            "observed": {"source": "agent-native-control"},
            "source_identity": None,
            "mcp_exposure": None,
        },
        "harness": {"commit": "h" * 40},
        "environment": {"environment_sha256": "e" * 64},
    }


def _campaign(base: dict, definition_id: str = "d" * 64) -> dict:
    payload = {
        "contract": "benchmark-campaign-authority.v5",
        "selected_definitions": [definition_id],
        "task_conditions": {"task-a": {"bare": base}},
        "agents": {},
        "subjects": {},
        "subject_exposure_contracts": [],
        "subject_tool_catalog_proofs": [],
        "task_inputs": {},
        "suite_identity": "s" * 64,
        "oracle_review_sha256": None,
    }
    payload["campaign_id"] = digest(payload)
    return payload


def _write_campaign(results_root: Path, campaign: dict) -> None:
    directory = results_root / ".campaign"
    directory.mkdir(parents=True)
    (directory / "authority.json").write_bytes(canonical_json(campaign))


def _suite() -> SuiteDefinition:
    experiment = {
        "id": "authority-epoch-test",
        "version": 1,
        "suite": "test",
        "tasks": ["task-a"],
        "conditions": [
            {
                "id": "bare",
                "agent": "agent",
                "subject": "none",
                "trials": 1,
                "replicate_ids": [1],
            }
        ],
        "scoring": {"id": "score", "version": 1, "metrics": ["task_success"]},
    }
    return SuiteDefinition(
        Path("."),
        experiment,
        {
            "task-a": {
                "id": "task-a",
                "prompt": "find owner",
                "budgets": {"timeout_seconds": 1},
            }
        },
        {"none": {"id": "none", "kind": "control"}},
        {"agent": {"id": "agent"}},
    )


class AuthorityEpochRecoveryTests(unittest.TestCase):
    def test_participant_drift_creates_immutable_epoch_and_reuses_it(self) -> None:
        base = _authority()
        campaign = _campaign(base)
        changed = _authority("b" * 64)

        with tempfile.TemporaryDirectory() as tmp:
            results_root = Path(tmp) / "results"
            _write_campaign(results_root, campaign)

            first = bind_authority_epoch(
                results_root=results_root,
                campaign=campaign,
                task_id="task-a",
                condition_id="bare",
                observed_authority=changed,
                participant_observation={"agent": {"version": "opencode 2"}},
            )
            self.assertEqual(first.epoch, 2)
            self.assertTrue(first.transitioned)
            self.assertEqual(first.changed_components, ("agent",))
            self.assertIn("agent.executable_sha256", first.changed_fields)

            repeated = bind_authority_epoch(
                results_root=results_root,
                campaign=campaign,
                task_id="task-a",
                condition_id="bare",
                observed_authority=changed,
            )
            self.assertEqual(repeated.epoch, 2)
            self.assertFalse(repeated.transitioned)

            records = read_authority_epochs(
                results_root,
                campaign["campaign_id"],
            )[("task-a", "bare")]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["epoch"], 2)
            self.assertEqual(
                records[0]["trigger"]["reason_code"],
                "participant-authority-drift",
            )
            self.assertIn("bind_authority_epoch", records[0]["trigger"]["stack"])

    def test_nonparticipant_authority_drift_remains_fatal(self) -> None:
        base = _authority()
        campaign = _campaign(base)
        changed = _authority()
        changed["harness"] = {"commit": "changed"}

        with tempfile.TemporaryDirectory() as tmp:
            results_root = Path(tmp) / "results"
            _write_campaign(results_root, campaign)
            with self.assertRaisesRegex(
                CampaignAuthorityError,
                "non-recoverable trial authority changed",
            ):
                bind_authority_epoch(
                    results_root=results_root,
                    campaign=campaign,
                    task_id="task-a",
                    condition_id="bare",
                    observed_authority=changed,
                )
            self.assertEqual(
                read_authority_epochs(results_root, campaign["campaign_id"]),
                {},
            )

    def test_stale_launch_is_preserved_when_epoch_changes_trial_identity(self) -> None:
        campaign = _campaign(_authority())
        definition_id = campaign["selected_definitions"][0]
        with tempfile.TemporaryDirectory() as tmp:
            results_root = Path(tmp) / "results"
            _write_campaign(results_root, campaign)
            self.assertEqual(
                claim_launch(
                    results_root=results_root,
                    campaign=campaign,
                    definition_id=definition_id,
                    trial_id="1" * 64,
                ),
                1,
            )
            self.assertEqual(
                launch_state(
                    results_root=results_root,
                    campaign=campaign,
                    definition_id=definition_id,
                    trial_id="2" * 64,
                ),
                "STALE_INTERRUPTED",
            )
            self.assertEqual(
                record_interrupted_attempt(
                    results_root=results_root,
                    campaign=campaign,
                    definition_id=definition_id,
                    trial_id=None,
                ),
                1,
            )
            attempts = read_interrupted_attempts(
                results_root,
                campaign["campaign_id"],
            )[definition_id]
            self.assertEqual(attempts[0]["trial_id"], "1" * 64)
            self.assertEqual(
                claim_launch(
                    results_root=results_root,
                    campaign=campaign,
                    definition_id=definition_id,
                    trial_id="2" * 64,
                ),
                2,
            )

    def test_campaign_status_marks_epoch_evidence_tainted(self) -> None:
        suite = _suite()
        row = suite.trial_definitions()[0]
        base = _authority()
        campaign = _campaign(base, str(row["definition_id"]))
        changed = _authority("b" * 64)

        with tempfile.TemporaryDirectory() as tmp:
            results_root = Path(tmp) / "results"
            _write_campaign(results_root, campaign)
            bind_authority_epoch(
                results_root=results_root,
                campaign=campaign,
                task_id="task-a",
                condition_id="bare",
                observed_authority=changed,
            )
            status = campaign_status(
                suite=suite,
                results_root=results_root,
                selected_definitions={str(row["definition_id"])},
            )
            self.assertTrue(status["evidence_tainted"])
            self.assertEqual(status["authority_epoch_transitions"], 1)
            self.assertFalse(status["qualified"])
            self.assertEqual(
                status["authority_epochs"][0]["changed_components"],
                ["agent"],
            )

    def test_report_and_decision_use_epoch_taint_as_qualification_authority(
        self,
    ) -> None:
        suite = _suite()
        row = suite.trial_definitions()[0]
        definition_id = str(row["definition_id"])
        base = _authority()
        campaign = _campaign(base, definition_id)
        changed = _authority("b" * 64)

        with tempfile.TemporaryDirectory() as tmp:
            results_root = Path(tmp) / "results"
            _write_campaign(results_root, campaign)
            bind_authority_epoch(
                results_root=results_root,
                campaign=campaign,
                task_id="task-a",
                condition_id="bare",
                observed_authority=changed,
            )
            projection = authority_epoch_projection(
                results_root=results_root,
                campaign_id=campaign["campaign_id"],
                selected_pairs={("task-a", "bare")},
            )
            self.assertTrue(projection["evidence_tainted"])
            self.assertEqual(projection["authority_epoch_transitions"], 1)

            receipt = {
                "definition_id": definition_id,
                "trial_id": "f" * 64,
                "task": {"id": "task-a"},
                "condition": {
                    "id": "bare",
                    "agent_definition": {"id": "agent"},
                    "subject_definition": {"id": "none", "adapter": "none"},
                },
                "status": "PASS",
                "authority": {
                    "agent": {},
                    "subject": {},
                    "oracle": {},
                    "harness": {},
                    "environment": {},
                    "mutation": None,
                },
                "execution": {
                    "campaign_id": campaign["campaign_id"],
                    "replicate_id": 1,
                    "trial_index": 0,
                    "authority_epoch": {
                        "epoch": 2,
                        "epoch_id": projection["authority_epochs"][0]["epoch_id"],
                        "transitioned": True,
                        "changed_components": ["agent"],
                        "changed_fields": ["agent.executable_sha256"],
                    },
                },
                "measurements": {"agent": {}},
                "scoring": {"oracle_grade": {}},
                "reason": None,
            }

            with (
                mock.patch(
                    "benchmarks.harness.report.validate_comparability"
                ) as comparable,
                mock.patch(
                    "benchmarks.harness.report.subject_exposure_qualification",
                    return_value={
                        "status": "PASS",
                        "qualified": True,
                        "conditions": [],
                    },
                ),
                mock.patch(
                    "benchmarks.harness.report._aggregate_condition",
                    return_value={},
                ),
                mock.patch(
                    "benchmarks.harness.report._subject_adoption_summary",
                    return_value=[],
                ),
                mock.patch(
                    "benchmarks.harness.report._analysis_evidence",
                    return_value={},
                ),
                mock.patch(
                    "benchmarks.harness.report._diagnostic",
                    return_value={
                        "primary": "semantic-correct",
                        "flags": {},
                        "stage": None,
                        "reason_code": None,
                        "reason": None,
                        "location_topology": None,
                        "diagnostic_source": "receipt",
                    },
                ),
                mock.patch(
                    "benchmarks.harness.report._decision_summary",
                    return_value={"campaign": {"qualification": "NOT_QUALIFIED"}},
                ) as decision_summary,
            ):
                report = build_report(
                    suite=suite,
                    results_root=results_root,
                    selected_definitions={definition_id},
                    projected_receipts=[receipt],
                )

            comparable.assert_called_once_with(
                [receipt],
                allow_participant_authority_mix=True,
            )
            self.assertFalse(
                decision_summary.call_args.kwargs["campaign_qualified"]
            )
            self.assertEqual(report["schema"]["version"], 21)
            self.assertEqual(
                report["campaign_qualification"]["status"],
                "NOT_QUALIFIED",
            )
            self.assertTrue(
                report["campaign_qualification"]["evidence_tainted"]
            )
            self.assertEqual(
                report["campaign_qualification"]["authority_epoch_transitions"],
                1,
            )
            self.assertTrue(
                report["campaign_qualification"]["mixed_execution_authority"]
            )
            self.assertEqual(
                report["comparative_analysis"]["status"],
                "SUPPRESSED",
            )
            self.assertEqual(
                report["comparative_analysis"]["reason_code"],
                "participant-authority-epoch-taint",
            )
            self.assertEqual(report["paired_assistance"], [])
            self.assertEqual(report["stability"], [])
            self.assertEqual(report["context_invariance"], [])
            self.assertEqual(report["cross_agent_observations"], [])
            self.assertEqual(report["task_agent_authority"], [])

            decision = build_decision_evidence(report)
            self.assertEqual(
                decision["schema"],
                "agents-cookbook-benchmark-decision-evidence.v8",
            )
            self.assertIn(
                "participant-authority-transition-observed",
                decision["evidence_signals"],
            )
            self.assertEqual(
                decision["campaign"]["qualification"]["status"],
                "NOT_QUALIFIED",
            )
            self.assertEqual(
                decision["campaign"]["comparative_analysis"]["status"],
                "SUPPRESSED",
            )

    def test_runtime_drift_is_classified_and_carries_forensic_stack(self) -> None:
        revalidation = {
            "status": "failed",
            "reason": "OpenCode executable authority changed",
        }
        evidence = {
            "opencode_executable_path": "/tool/opencode",
            "opencode_executable_sha256": "a" * 64,
            "opencode_version": "opencode 1",
            "opencode_executable_metadata": {
                "inode": 1,
                "size": 100,
                "mtime_ns": 10,
            },
        }
        current = Observation(
            {
                "resolved_path": "/tool/opencode",
                "executable_sha256": "b" * 64,
                "version": "opencode 2",
                "file_metadata": {
                    "inode": 2,
                    "size": 120,
                    "mtime_ns": 20,
                },
                "executable_stable": True,
            },
            "",
        )
        with mock.patch(
            "benchmarks.adapters.opencode_native._observe_opencode_executable",
            return_value=current,
        ):
            enriched = _enrich_authority_revalidation(
                revalidation=revalidation,
                context=SimpleNamespace(),
                environment={},
                evidence=evidence,
            )
        self.assertEqual(enriched["reason_code"], "execution-authority-drift")
        self.assertEqual(enriched["authority_kind"], "opencode-executable")
        self.assertIn("sha256", enriched["changed_fields"])
        self.assertIn("version", enriched["changed_fields"])
        self.assertIn("_enrich_authority_revalidation", enriched["diagnostic"])

        failure = _agent_failure(
            Observation(
                {
                    "authority_revalidation": enriched,
                    "process": {"return_code": 0},
                    "terminal_event": {
                        "type": "turn.failed",
                        "reason": enriched["reason"],
                    },
                    "final_message": None,
                },
                "",
            )
        )
        self.assertEqual(
            failure,
            ("execution-authority-drift", "OpenCode executable authority changed"),
        )


if __name__ == "__main__":
    unittest.main()
