from __future__ import annotations

import copy
import unittest

from benchmarks.harness.report import ReportError, validate_comparability


def _receipt(task: str, subject: str, *, config: str, exposure: str) -> dict:
    return {
        "task": {"id": task},
        "condition": {
            "agent_definition": {"id": "opencode-native", "adapter": "opencode-native"},
            "subject_definition": {"id": subject},
        },
        "status": "INVALID",
        "authority": {
            "harness": {"source": "same"},
            "environment": {"agent": "same"},
            "agent": {
                "observed": {
                    "version": "opencode 1",
                    "executable_sha256": "a" * 64,
                    "auth_mode": "native-opencode",
                    "model": "provider/model",
                    "provider": "provider",
                    "native_config_sha256": config,
                }
            },
            "subject": {
                "available": True,
                "observed": {
                    "source": "native-agent-runtime",
                    "subject": subject,
                    "native_config_sha256": config,
                    "workspace_binding": {"verified": True},
                    "native_subject_identity": {
                        "subject": subject,
                        "executable_sha256": "b" * 64,
                    },
                    "mcp_exposure": {
                        "semantic_identity": {"subject": subject},
                        "subject_exposure_sha256": exposure,
                    },
                },
            },
        },
    }


class AuthorityScopeTests(unittest.TestCase):
    def test_project_config_may_differ_across_tasks_but_not_within_pair(self) -> None:
        left = _receipt("python-task", "none", config="1" * 64, exposure="a" * 64)
        right = _receipt("typescript-task", "none", config="2" * 64, exposure="b" * 64)
        validate_comparability([left, right])
        mismatch = copy.deepcopy(right)
        mismatch["task"]["id"] = "python-task"
        with self.assertRaisesRegex(ReportError, "mixed native config"):
            validate_comparability([left, mismatch])

    def test_workspace_transport_hash_can_vary_but_subject_executable_cannot(
        self,
    ) -> None:
        first = _receipt("task", "hashmarks", config="1" * 64, exposure="a" * 64)
        second = _receipt("task", "hashmarks", config="1" * 64, exposure="b" * 64)
        validate_comparability([first, second])
        changed = copy.deepcopy(second)
        changed["authority"]["subject"]["observed"]["native_subject_identity"][
            "executable_sha256"
        ] = "c" * 64
        with self.assertRaisesRegex(ReportError, "mixed observed subject authority"):
            validate_comparability([first, changed])

    def test_model_and_environment_drift_remain_campaign_incompatible(self) -> None:
        first = _receipt("python-task", "none", config="1" * 64, exposure="a" * 64)
        second = _receipt("typescript-task", "none", config="2" * 64, exposure="b" * 64)
        changed_model = copy.deepcopy(second)
        changed_model["authority"]["agent"]["observed"]["model"] = "provider/other"
        with self.assertRaisesRegex(ReportError, "mixed observed runtime"):
            validate_comparability([first, changed_model])
        changed_environment = copy.deepcopy(second)
        changed_environment["authority"]["environment"] = {"agent": "changed"}
        with self.assertRaisesRegex(ReportError, "mixed environment"):
            validate_comparability([first, changed_environment])


if __name__ == "__main__":
    unittest.main()
