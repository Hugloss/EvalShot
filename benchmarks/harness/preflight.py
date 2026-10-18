"""Preflight benchmark admission without invoking the coding agent."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.harness.admission import TrialAdmissionError, admit_trial
from benchmarks.harness.bundle import verify_bundle
from benchmarks.harness.contamination import classify_contamination
from benchmarks.harness.model import TrialStatus
from benchmarks.harness.suite import SuiteDefinition
from benchmarks.harness.workspace import snapshot


@dataclass(frozen=True)
class PreflightResult:
    definition_id: str
    trial_id: str | None
    task_id: str
    condition_id: str
    status: str
    reason: str | None
    subject_available: bool | None
    agent_available: bool | None
    oracle_healthy: bool | None
    model: str | None
    provider: str | None
    workspace_binding: dict[str, Any] | None
    existing_result_status: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "definition_id": self.definition_id,
            "trial_id": self.trial_id,
            "task_id": self.task_id,
            "condition_id": self.condition_id,
            "status": self.status,
            "reason": self.reason,
            "subject_available": self.subject_available,
            "agent_available": self.agent_available,
            "oracle_healthy": self.oracle_healthy,
            "model": self.model,
            "provider": self.provider,
            "workspace_binding": self.workspace_binding,
            "existing_result_status": self.existing_result_status,
        }


def _binding_summary(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    return {
        "verified": payload.get("verified"),
        "subject": payload.get("subject"),
        "method": payload.get("method"),
        "reason_code": payload.get("reason_code"),
        "reason": payload.get("reason"),
    }


def preflight_trial(
    *,
    suite: SuiteDefinition,
    task_id: str,
    condition_id: str,
    trial_index: int,
    harness_root: Path,
    cache_root: Path,
    work_root: Path,
    results_root: Path | None = None,
    local_source: Path | None = None,
    codex_auth: Path | None = None,
    source: Mapping[str, str] | None = None,
) -> PreflightResult:
    definition = next(
        (
            row["definition_id"]
            for row in suite.trial_definitions()
            if row["task_id"] == task_id
            and row["condition_id"] == condition_id
            and int(row["trial"]) == trial_index
        ),
        "",
    )
    try:
        with admit_trial(
            suite=suite,
            task_id=task_id,
            condition_id=condition_id,
            trial_index=trial_index,
            harness_root=harness_root,
            cache_root=cache_root,
            work_root=work_root,
            local_source=local_source,
            codex_auth=codex_auth,
            source=source,
        ) as admission:
            status, reason = admission.initial_outcome()
            observed_state = snapshot(admission.context.workspace)
            contamination_config = admission.task["contamination"]
            allowed_generated = tuple(
                sorted(
                    set(contamination_config["allowed_generated_globs"])
                    | set(admission.generated_globs())
                )
            )
            contamination = classify_contamination(
                before=admission.admitted_state,
                after=observed_state,
                allowed_change_globs=tuple(
                    contamination_config["allowed_change_globs"]
                ),
                allowed_generated_globs=allowed_generated,
            )
            if contamination["contaminated"]:
                status = TrialStatus.CONTAMINATED
                reason = "preflight changed workspace outside frozen allowances"

            cleanup = admission.cleanup_subject()
            cleanup_process = cleanup.payload.get("process")
            if (
                isinstance(cleanup_process, dict)
                and (
                    cleanup_process.get("timed_out")
                    or cleanup_process.get("executable_missing")
                    or cleanup_process.get("return_code") not in (None, 0)
                )
                and status is None
            ):
                status = TrialStatus.INCOMPLETE
                reason = "subject cleanup failed during preflight"

            existing_status = None
            if results_root is not None:
                result_dir = results_root / admission.trial_id
                if result_dir.exists():
                    valid, invalid_reason = verify_bundle(result_dir)
                    if not valid:
                        return PreflightResult(
                            definition_id=admission.definition_id,
                            trial_id=admission.trial_id,
                            task_id=task_id,
                            condition_id=condition_id,
                            status="INVALID_RESULT",
                            reason=invalid_reason,
                            subject_available=admission.subject_authority["available"],
                            agent_available=admission.agent_authority["available"],
                            oracle_healthy=admission.oracle_authority["healthy"],
                            model=admission.agent_prepare.payload.get("model"),
                            provider=admission.agent_prepare.payload.get("provider"),
                            workspace_binding=_binding_summary(
                                admission.agent_prepare.payload.get("workspace_binding")
                            ),
                            existing_result_status=None,
                        )
                    existing = json.loads(
                        (result_dir / "result.json").read_text(encoding="utf-8")
                    )
                    existing_status = str(existing.get("status"))
                    if status is not None:
                        status_name = status.value
                    elif existing_status in {
                        "PASS",
                        "FAIL",
                        "NO_QUALIFYING_DEFECT",
                    }:
                        status_name = "COMPLETE"
                    else:
                        status_name = f"RECORDED_{existing_status}"
                        reason = (
                            "existing immutable receipt is not a valid "
                            "experimental outcome; use a new campaign root "
                            "after correcting the underlying condition"
                        )
                    return PreflightResult(
                        definition_id=admission.definition_id,
                        trial_id=admission.trial_id,
                        task_id=task_id,
                        condition_id=condition_id,
                        status=status_name,
                        reason=reason,
                        subject_available=admission.subject_authority["available"],
                        agent_available=admission.agent_authority["available"],
                        oracle_healthy=admission.oracle_authority["healthy"],
                        model=admission.agent_prepare.payload.get("model"),
                        provider=admission.agent_prepare.payload.get("provider"),
                        workspace_binding=_binding_summary(
                            admission.agent_prepare.payload.get("workspace_binding")
                        ),
                        existing_result_status=existing_status,
                    )

            return PreflightResult(
                definition_id=admission.definition_id,
                trial_id=admission.trial_id,
                task_id=task_id,
                condition_id=condition_id,
                status=status.value if status is not None else "READY",
                reason=reason,
                subject_available=admission.subject_authority["available"],
                agent_available=admission.agent_authority["available"],
                oracle_healthy=admission.oracle_authority["healthy"],
                model=admission.agent_prepare.payload.get("model"),
                provider=admission.agent_prepare.payload.get("provider"),
                workspace_binding=_binding_summary(
                    admission.agent_prepare.payload.get("workspace_binding")
                ),
                existing_result_status=existing_status,
            )
    except (TrialAdmissionError, OSError, ValueError) as exc:
        return PreflightResult(
            definition_id=str(definition),
            trial_id=None,
            task_id=task_id,
            condition_id=condition_id,
            status="ERROR",
            reason=str(exc),
            subject_available=None,
            agent_available=None,
            oracle_healthy=None,
            model=None,
            provider=None,
            workspace_binding=None,
            existing_result_status=None,
        )
