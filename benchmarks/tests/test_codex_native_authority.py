"""Native capability drift cannot hide behind an unchanged agent executable.

Synthetic prepared observations cross the real admission, campaign and reporting
boundaries. No native executable, plugin or model is launched by these tests.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmarks.adapters.codex import CodexAgent
from benchmarks.adapters.codex_native_namespace import SCHEMA as NAMESPACE_SCHEMA
from benchmarks.harness.admission import _agent_authority, runtime_environment_identity
from benchmarks.harness.campaign_authority import bind_authority_epoch, condition_authority
from benchmarks.harness.identity import canonical_json, digest
from benchmarks.harness.model import McpExposure, Observation, TrialContext
from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.suite import SuiteDefinition


def _suite() -> SuiteDefinition:
    return SuiteDefinition(
        Path("."),
        {
            "id": "native-codex-authority-fixture", "version": 1, "suite": "test",
            "tasks": ["find-owner"],
            "conditions": [
                {"id": condition, "subject": subject, "agent": "native-codex",
                 "trials": 1, "seed": 1}
                for condition, subject in (("bare", "none"), ("available", "hashmarks"))
            ],
            "scoring": {"id": "fixture-score", "version": 1, "metrics": ["task_success"]},
        },
        {"find-owner": {
            "id": "find-owner", "version": 1, "family": "fixture", "mode": "read_only",
            "prompt": "Find the implementation owner.",
            "oracle": {"adapter": "expected-json", "configuration": {"expected": {"ok": True}}},
        }},
        {"none": {"id": "none", "kind": "control", "adapter": "none"},
         "hashmarks": {"id": "hashmarks", "kind": "repository_intelligence", "adapter": "hashmarks"}},
        {"native-codex": {"id": "native-codex", "adapter": "codex",
                          "configuration": {"native_host": True}}},
    )


def _prepared() -> dict:
    return {
        "available": True, "version": "codex-cli fixture", "executable_sha256": "a" * 64,
        "auth_mode": "native-host", "model": "fixture-model", "provider": "fixture-provider",
        "reasoning_effort": None, "native_config_sha256": "b" * 64,
        "native_config_observation": {
            "state": "observed", "model": "fixture-model", "provider": "fixture-provider",
            "scope": "private native configuration", "source_comparison": "unknown",
        },
        "native_state_isolation": "private-input-snapshot",
        # This is opaque producer data, deliberately unrelated to the selected
        # Hashmarks subject. Its capabilities still belong to agent authority.
        "native_plugins_observation": {
            "state": "observed", "plugins": [{
                "id": "browser@fixture-marketplace", "version": "1.0.0", "enabled": True,
                "capabilities": {"mcp_servers": ["browser"], "skills": ["browser-use"]},
            }],
        },
        "native_namespace_observation": {
            "schema": NAMESPACE_SCHEMA, "state": "observed", "bwrap_sha256": "c" * 64,
            "source_home_alias_private": True, "descendant_alias_private": True,
            "host_readonly_observed": True,
        },
        "native_mcp_servers": ["browser"],
    }


def _mutate(payload: dict, change: str) -> None:
    plugins = payload["native_plugins_observation"]["plugins"]
    if change == "plugin-version":
        plugins[0]["version"] = "2.0.0"
    elif change == "non-subject-capability":
        plugins[0]["capabilities"]["skills"].append("browser-research")
    elif change == "plugin-membership":
        plugins.append({"id": "documents@fixture-marketplace", "version": "1.0.0", "enabled": True})
    elif change == "namespace-wrapper":
        payload["native_namespace_observation"]["bwrap_sha256"] = "d" * 64
    elif change == "provider":
        payload["provider"] = "another-provider"
        payload["native_config_observation"]["provider"] = "another-provider"
    elif change == "plugin-observation-missing":
        payload.pop("native_plugins_observation")
    elif change == "namespace-observation-unknown":
        payload["native_namespace_observation"]["state"] = "unknown"
    else:
        raise AssertionError(f"unknown fixture change: {change}")


CHANGES = (
    "plugin-version", "non-subject-capability", "plugin-membership", "namespace-wrapper",
    "provider", "plugin-observation-missing", "namespace-observation-unknown",
)


def _admission(
    suite: SuiteDefinition, condition_id: str, root: Path, payload: dict,
    task_id: str = "find-owner",
):
    condition = next(c for c in suite.experiment["conditions"] if c["id"] == condition_id)
    context = TrialContext(root / "workspace", root / "control", {
        "PATH": "/usr/bin:/bin", "HOME": str(root / "control/home"),
        "CODEX_HOME": str(root / "control/codex-home"), "TMPDIR": str(root / "control/tmp"),
    })
    subject_id = condition["subject"]
    exposure = None if subject_id == "none" else McpExposure(
        subject_id, "/fixture/hashmarks", ("--workspace", str(context.workspace)),
        context.workspace, {"name": subject_id, "workspace": "trial-workspace"},
    )
    prepared = deepcopy(payload)
    prepared["mcp_exposure"] = None if exposure is None else {
        "sha256": digest(str(root)), "semantic_identity": exposure.semantic_identity,
    }
    return SimpleNamespace(
        context=context, task=suite.tasks[task_id], condition=condition,
        subject=SimpleNamespace(mcp_exposure=lambda _: exposure),
        agent_authority=_agent_authority(CodexAgent(native_host=True), Observation(prepared, "")),
        subject_authority={"available": True, "declared": {"participant_id": subject_id},
                           "observed": {"source": "fixture", "subject": subject_id}},
        harness_authority={"commit": "e" * 40},
        environment_authority=runtime_environment_identity(context.environment, control_root=context.control_root),
    )


def _receipt(suite: SuiteDefinition, admission, index: int) -> dict:
    row = next(d for d in suite.trial_definitions()
               if d["condition_id"] == admission.condition["id"]
               and d["task_id"] == admission.task["id"])
    return {
        "definition_id": row["definition_id"], "trial_id": f"{index:064x}",
        "experiment": suite.experiment, "task": admission.task,
        "condition": suite.expanded_condition(admission.condition), "status": "PASS",
        "authority": {
            "agent": admission.agent_authority, "subject": admission.subject_authority,
            "harness": admission.harness_authority, "environment": admission.environment_authority,
        },
        "execution": {"trial_index": row["trial"], "seed": row["seed"],
                      "workspace_root": str(admission.context.workspace)},
        "measurements": {"agent": {
            "subject_tool_configured": admission.condition["subject"] != "none",
            "subject_tool_invoked": admission.condition["subject"] != "none",
            "subject_mcp_calls": int(admission.condition["subject"] != "none"),
        }},
    }


class NativeCodexAuthorityTests(unittest.TestCase):
    def report(self, suite, admissions):
        receipts = [_receipt(suite, admission, i) for i, admission in enumerate(admissions)]
        with patch("benchmarks.harness.report._receipts", return_value=receipts):
            return build_report(suite=suite, results_root=Path("/unused"))

    def test_unchanged_native_authority_allows_fresh_private_paths_and_subject_overlay(self):
        suite = _suite()
        bare = _admission(suite, "bare", Path("/tmp/native-first-trial"), _prepared())
        assisted = _admission(suite, "available", Path("/tmp/native-second-trial"), _prepared())
        self.assertNotEqual(bare.context.environment, assisted.context.environment)
        report = self.report(suite, [bare, assisted])
        self.assertEqual(len(report["paired_assistance"]), 1)

    def test_changed_native_authority_cannot_enter_a_paired_report(self):
        suite = _suite()
        for change in CHANGES:
            with self.subTest(change=change):
                changed = _prepared()
                _mutate(changed, change)
                bare = _admission(suite, "bare", Path("/tmp/native-baseline"), _prepared())
                assisted = _admission(suite, "available", Path("/tmp/native-changed"), changed)
                with self.assertRaisesRegex(ReportError, "mixed (observed runtime|native config) authority"):
                    self.report(suite, [bare, assisted])

    def test_repository_specific_capabilities_may_differ_between_tasks(self):
        suite = _suite()
        other_task = "find-web-owner"
        suite.experiment["tasks"].append(other_task)
        suite.tasks[other_task] = {
            **deepcopy(suite.tasks["find-owner"]), "id": other_task,
            "prompt": "Find the web implementation owner.",
        }
        project_configuration = _prepared()
        _mutate(project_configuration, "non-subject-capability")
        project_configuration["native_config_sha256"] = "f" * 64
        project_configuration["native_config_observation"]["scope"] = "native plus repository configuration"
        admissions = [
            _admission(suite, condition, Path("/tmp") / task / condition, payload, task)
            for task, payload in (("find-owner", _prepared()), (other_task, project_configuration))
            for condition in ("bare", "available")
        ]
        report = self.report(suite, admissions)
        self.assertEqual(
            {pair["task_id"] for pair in report["paired_assistance"]},
            {"find-owner", other_task},
        )
        self.assertEqual(len(report["paired_assistance"]), 2)

    def test_native_drift_creates_an_authority_epoch_instead_of_reusing_admission(self):
        suite = _suite()
        for change in CHANGES:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                initial = _admission(suite, "available", root / "first", _prepared())
                frozen = condition_authority(initial)
                campaign = {
                    "contract": "benchmark-campaign-authority.v5",
                    "selected_definitions": [d["definition_id"] for d in suite.trial_definitions()
                                             if d["condition_id"] == "available"],
                    "task_conditions": {"find-owner": {"available": frozen}},
                }
                campaign["campaign_id"] = digest(campaign)
                directory = root / "results/.campaign"
                directory.mkdir(parents=True)
                (directory / "authority.json").write_bytes(canonical_json(campaign))
                unchanged = _admission(suite, "available", root / "second", _prepared())
                retained = bind_authority_epoch(
                    results_root=root / "results", campaign=campaign, task_id="find-owner",
                    condition_id="available", observed_authority=condition_authority(unchanged),
                )
                self.assertFalse(retained.transitioned)
                changed = _prepared()
                _mutate(changed, change)
                later = _admission(suite, "available", root / "third", changed)
                rebound = bind_authority_epoch(
                    results_root=root / "results", campaign=campaign, task_id="find-owner",
                    condition_id="available", observed_authority=condition_authority(later),
                )
                self.assertTrue(rebound.transitioned)
                self.assertEqual(rebound.epoch, retained.epoch + 1)
                self.assertEqual(rebound.changed_components, ("agent",))

    def test_historical_receipts_without_new_observations_remain_comparable_to_each_other(self):
        suite = _suite()
        historical = _prepared()
        for name in ("native_plugins_observation", "native_namespace_observation"):
            historical.pop(name)
        report = self.report(suite, [
            _admission(suite, "bare", Path("/tmp/historical-first"), historical),
            _admission(suite, "available", Path("/tmp/historical-second"), historical),
        ])
        self.assertEqual(len(report["paired_assistance"]), 1)


if __name__ == "__main__":
    unittest.main()
