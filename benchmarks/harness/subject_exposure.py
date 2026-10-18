"""Mandatory subject-exposure qualification for benchmark campaigns."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from benchmarks.harness.suite import SuiteDefinition


def exposure_probe_required_tool(
    suite: SuiteDefinition,
    subject_id: str,
) -> str:
    """Return the frozen required-tool contract for one non-control subject."""
    definition = suite.subjects.get(subject_id)
    if not isinstance(definition, dict):
        raise ValueError(f"unknown benchmark subject: {subject_id}")
    if definition.get("kind") == "control":
        raise ValueError("control subjects do not have exposure probes")
    probe = definition.get("exposure_probe")
    if not isinstance(probe, dict):
        raise ValueError(
            f"subject {subject_id} has no exposure_probe contract"
        )
    required_tool = probe.get("required_tool")
    if not isinstance(required_tool, str) or not required_tool:
        raise ValueError(
            f"subject {subject_id} exposure_probe.required_tool is invalid"
        )
    if not required_tool.startswith(subject_id + "_"):
        raise ValueError(
            f"subject {subject_id} exposure probe tool must use the "
            f"{subject_id}_ prefix"
        )
    return required_tool


def validate_subject_exposure_admission_contract(
    *,
    suite: SuiteDefinition,
    expected_rows: Iterable[dict[str, Any]],
) -> list[dict[str, str]]:
    """Fail before campaign work when the frozen suite requires probe contracts."""
    contract = suite.experiment.get("subject_exposure_contract")
    if not isinstance(contract, dict) or contract.get("require_probe_contract") is not True:
        return []

    conditions = {
        str(condition["id"]): condition
        for condition in suite.experiment["conditions"]
    }
    selected_condition_ids = {
        str(row["condition_id"])
        for row in expected_rows
    }
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for condition_id in sorted(selected_condition_ids):
        condition = conditions.get(condition_id)
        if not isinstance(condition, dict):
            raise ValueError(f"unknown benchmark condition: {condition_id}")
        subject_id = str(condition["subject"])
        subject = suite.subjects.get(subject_id)
        if not isinstance(subject, dict):
            raise ValueError(f"unknown benchmark subject: {subject_id}")
        if subject.get("kind") == "control" or subject_id in seen:
            continue
        required_tool = exposure_probe_required_tool(suite, subject_id)
        result.append(
            {
                "subject_id": subject_id,
                "required_tool": required_tool,
            }
        )
        seen.add(subject_id)
    return result


def subject_exposure_qualification(
    *,
    suite: SuiteDefinition,
    expected_rows: Iterable[dict[str, Any]],
    receipts: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Require every selected non-control condition to prove subject exposure.

    This check is intentionally subject-agnostic. It relies only on the frozen
    condition/subject definitions plus canonical agent measurements, so future
    MCP subjects inherit the same qualification rule without benchmark code
    changes.
    """

    experiment = getattr(suite, "experiment", None)
    subjects = getattr(suite, "subjects", None)
    if not isinstance(experiment, dict) or not isinstance(subjects, dict):
        return {
            "status": "NOT_APPLICABLE",
            "qualified": True,
            "policy": "non-control conditions must prove observable subject use",
            "conditions": [],
            "failed_conditions": 0,
            "pending_conditions": 0,
        }

    conditions = {
        str(condition["id"]): condition
        for condition in experiment["conditions"]
    }
    expected_counts = Counter(
        str(row["condition_id"])
        for row in expected_rows
    )
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for receipt in receipts:
        condition = receipt.get("condition", {}).get("id")
        if isinstance(condition, str):
            by_condition[condition].append(receipt)

    rows: list[dict[str, Any]] = []
    for condition_id in sorted(expected_counts):
        condition = conditions.get(condition_id)
        if not isinstance(condition, dict):
            continue
        subject_id = str(condition["subject"])
        subject = subjects[subject_id]
        if subject.get("kind") == "control":
            continue

        expected = int(expected_counts[condition_id])
        observed_receipts = by_condition.get(condition_id, [])
        available = sum(
            receipt.get("authority", {}).get("subject", {}).get("available") is True
            for receipt in observed_receipts
        )
        configured = sum(
            receipt.get("measurements", {})
            .get("agent", {})
            .get("subject_tool_configured")
            is True
            for receipt in observed_receipts
        )
        invocation_observed = sum(
            isinstance(
                receipt.get("measurements", {})
                .get("agent", {})
                .get("subject_tool_invoked"),
                bool,
            )
            for receipt in observed_receipts
        )
        invoked = sum(
            receipt.get("measurements", {})
            .get("agent", {})
            .get("subject_tool_invoked")
            is True
            for receipt in observed_receipts
        )
        exposure_contract = experiment.get("subject_exposure_contract")
        required_tool = (
            exposure_probe_required_tool(suite, subject_id)
            if isinstance(exposure_contract, dict)
            and exposure_contract.get("require_probe_contract") is True
            else None
        )
        required_operation = (
            required_tool.removeprefix(subject_id + "_")
            if required_tool is not None
            else None
        )
        required_invoked = 0
        required_name_observability_complete = 0
        required_successful_result_trials = 0
        required_no_usable_result_trials = 0
        required_result_unproven_trials = 0
        if required_operation is not None:
            for receipt in observed_receipts:
                agent = receipt.get("measurements", {}).get("agent", {})
                names = agent.get("subject_tool_names")
                operation_invoked = (
                    isinstance(names, list)
                    and required_operation in names
                )
                if operation_invoked:
                    required_invoked += 1
                name_observability_complete = (
                    agent.get("subject_tool_observability") == "complete"
                )
                if name_observability_complete:
                    required_name_observability_complete += 1

                evidence = agent.get("subject_tool_result_evidence")
                matching = (
                    [
                        row
                        for row in evidence
                        if isinstance(row, dict)
                        and row.get("operation") == required_operation
                    ]
                    if isinstance(evidence, list)
                    else []
                )
                if not operation_invoked:
                    continue
                if any(
                    row.get("outcome") == "successful-result-observed"
                    for row in matching
                ):
                    required_successful_result_trials += 1
                    continue
                if (
                    name_observability_complete
                    and matching
                    and all(
                        row.get("outcome") in {"failed", "empty-result"}
                        for row in matching
                    )
                ):
                    required_no_usable_result_trials += 1
                else:
                    required_result_unproven_trials += 1
        complete = len(observed_receipts) == expected
        reasons: list[str] = []
        if complete:
            if available != expected:
                reasons.append("subject-availability-unproven")
            if configured != expected:
                reasons.append("subject-configuration-unproven")
            if invocation_observed != expected:
                reasons.append("subject-invocation-observability-incomplete")
            if invoked == 0:
                reasons.append("subject-never-invoked")
            elif required_operation is not None and required_invoked == 0:
                reasons.append(
                    "required-subject-tool-never-invoked"
                    if required_name_observability_complete == expected
                    else "required-subject-tool-invocation-unproven"
                )
            elif (
                required_operation is not None
                and required_successful_result_trials == 0
            ):
                reasons.append(
                    "required-subject-tool-result-unproven"
                    if required_result_unproven_trials > 0
                    else "required-subject-tool-no-usable-result"
                )

        rows.append(
            {
                "condition_id": condition_id,
                "subject_id": subject_id,
                "expected_trials": expected,
                "observed_trials": len(observed_receipts),
                "available_trials": available,
                "configured_trials": configured,
                "invocation_observed_trials": invocation_observed,
                "invoked_trials": invoked,
                "not_invoked_trials": invocation_observed - invoked,
                "invocation_unknown_trials": len(observed_receipts)
                - invocation_observed,
                "required_tool": required_tool,
                "required_operation": required_operation,
                "required_tool_invoked_trials": required_invoked,
                "required_tool_name_observability_complete_trials": (
                    required_name_observability_complete
                ),
                "required_tool_successful_result_trials": (
                    required_successful_result_trials
                ),
                "required_tool_no_usable_result_trials": (
                    required_no_usable_result_trials
                ),
                "required_tool_result_unproven_trials": (
                    required_result_unproven_trials
                ),
                "status": (
                    "PENDING"
                    if not complete
                    else "PASS"
                    if not reasons
                    else "FAIL"
                ),
                "reason_codes": reasons,
            }
        )

    if not rows:
        return {
            "status": "NOT_APPLICABLE",
            "qualified": True,
            "policy": "non-control conditions must prove observable subject use",
            "conditions": [],
            "failed_conditions": 0,
            "pending_conditions": 0,
        }

    failed = sum(row["status"] == "FAIL" for row in rows)
    pending = sum(row["status"] == "PENDING" for row in rows)
    return {
        "status": (
            "PASS"
            if not failed and not pending
            else "PENDING"
            if pending
            else "FAIL"
        ),
        "qualified": not failed and not pending,
        "policy": (
            "each selected non-control condition must prove complete invocation "
            "observability and at least one subject-tool invocation; when the "
            "suite requires an exposure probe contract, the exact contracted "
            "operation must be observed at least once and return at least one "
            "successful nonempty result"
        ),
        "conditions": rows,
        "failed_conditions": failed,
        "pending_conditions": pending,
    }


def subject_exposure_issues(qualification: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for row in qualification.get("conditions", []):
        if not isinstance(row, dict) or row.get("status") != "FAIL":
            continue
        condition_id = row.get("condition_id")
        subject_id = row.get("subject_id")
        reasons = row.get("reason_codes")
        if not isinstance(reasons, list):
            reasons = []
        issues.append(
            "subject exposure qualification failed for "
            f"{condition_id} ({subject_id}): "
            + ", ".join(str(reason) for reason in reasons)
        )
    return issues
