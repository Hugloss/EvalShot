"""Aggregate complete benchmark receipts without ranking products."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from statistics import mean, median
from typing import Any

from benchmarks.tool_routing import (
    ROUTING_FIRST_CHOICE,
    ROUTING_LATE_RESCUE,
    ROUTING_NEVER_INVOKED,
    ROUTING_NOT_CONFIGURED,
    ROUTING_UNKNOWN,
    subject_routing_timing,
)

from benchmarks.adapters.oracles import (
    REPOSITORY_LOCATION_NORMALIZATION_POLICY,
    REPOSITORY_LOCATION_SCORING_POLICY,
    score_repository_location,
)
from benchmarks.harness.bundle import verify_bundle
from benchmarks.harness.campaign_authority import (
    CampaignAuthorityError,
    authority_epoch_projection,
    read_launch_claims,
    read_campaign,
)
from benchmarks.harness.subject_exposure import subject_exposure_qualification
from benchmarks.harness.treatment_attribution import (
    CONTRACTED_SUCCESSFUL_RESULT,
    contracted_treatment_evidence,
)
from benchmarks.harness.suite import SuiteDefinition
from benchmarks.harness.identity import digest, execution_task_contract


class ReportError(ValueError):
    pass


_VALID_OUTCOMES = {"PASS", "FAIL", "NO_QUALIFYING_DEFECT"}
_NUMERIC_AGENT_METRICS = (
    "duration_ms",
    "command_calls",
    "tool_calls",
    "mcp_calls",
    "subject_mcp_calls",
    "mcp_result_bytes",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
)


def _receipts(results_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not results_root.exists():
        return rows
    for directory in sorted(path for path in results_root.iterdir() if path.is_dir()):
        if directory.name.startswith("."):
            continue
        valid, reason = verify_bundle(directory)
        if not valid:
            raise ReportError(f"invalid published result bundle {directory}: {reason}")
        try:
            value = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportError(
                f"cannot load complete receipt {directory}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise ReportError(f"receipt is not an object: {directory}")
        rows.append(value)
    return rows


def _condition_id(receipt: dict[str, Any]) -> str:
    value = receipt.get("condition", {}).get("id")
    if not isinstance(value, str) or not value:
        raise ReportError("receipt has no condition id")
    return value


def _task_id(receipt: dict[str, Any]) -> str:
    value = receipt.get("task", {}).get("id")
    if not isinstance(value, str) or not value:
        raise ReportError("receipt has no task id")
    return value


def _agent_metric(receipt: dict[str, Any], name: str) -> int | float | None:
    value = receipt.get("measurements", {}).get("agent", {}).get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _oracle_bool(receipt: dict[str, Any], name: str) -> bool | None:
    value = receipt.get("scoring", {}).get("oracle_grade", {}).get(name)
    return value if isinstance(value, bool) else None


def _semantic_success(receipt: dict[str, Any]) -> bool | None:
    value = _oracle_bool(receipt, "semantic_success")
    if value is not None:
        return value
    if receipt.get("status") in _VALID_OUTCOMES:
        return receipt.get("status") == "PASS"
    return None


def _semantic_gradeable(receipt: dict[str, Any]) -> bool:
    value = _oracle_bool(receipt, "semantic_gradeable")
    if value is not None:
        return value
    return receipt.get("status") in _VALID_OUTCOMES


def _replicate_id(receipt: dict[str, Any]) -> int:
    """Return the frozen paired replicate identifier.

    The historical receipt field is named seed. Native agent adapters do not
    transport it as a provider/model sampling seed, so reports must not imply
    deterministic inference from this value.
    """
    execution = receipt["execution"]
    return int(execution.get("replicate_id", execution.get("seed")))


def _metric_summary(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for name in _NUMERIC_AGENT_METRICS:
        values = [
            value for row in receipts if (value := _agent_metric(row, name)) is not None
        ]
        metrics[name] = {
            "observations": len(values),
            "mean": mean(values) if values else None,
            "total": sum(values) if values else None,
        }
    return metrics


def _subject_routing_timing_metric(receipt: dict[str, Any]) -> str | None:
    agent = receipt.get("measurements", {}).get("agent", {})
    if not isinstance(agent, dict):
        return None
    explicit = agent.get("subject_routing_timing")
    allowed = {
        ROUTING_FIRST_CHOICE,
        ROUTING_LATE_RESCUE,
        ROUTING_NEVER_INVOKED,
        ROUTING_UNKNOWN,
        ROUTING_NOT_CONFIGURED,
    }
    if isinstance(explicit, str) and explicit in allowed:
        return explicit

    sequence = agent.get("tool_sequence")
    configured = agent.get("subject_tool_configured")
    invoked = agent.get("subject_tool_invoked")
    if (
        not isinstance(sequence, list)
        or not all(isinstance(name, str) and name for name in sequence)
        or not isinstance(configured, bool)
        or not isinstance(invoked, bool)
    ):
        return None

    ordinals = agent.get("subject_tool_call_ordinals")
    if not isinstance(ordinals, list):
        first = agent.get("subject_first_tool_call_ordinal")
        ordinals = [first] if isinstance(first, int) and not isinstance(first, bool) else []
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in ordinals):
        return None

    observability = agent.get("tool_strategy_observability")
    order_complete = not (
        isinstance(observability, str) and "partial" in observability
    )
    return subject_routing_timing(
        sequence,
        ordinals,
        configured=configured,
        invocation_observed=invoked,
        order_complete=order_complete,
    )


def _tool_strategy_summary(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    observability = Counter()
    tool_names = Counter()
    routing_timing = Counter()
    first_subject_ordinals: list[int | float] = []
    sequence_lengths: list[int] = []
    observed_sequences = 0

    for row in receipts:
        agent = row.get("measurements", {}).get("agent", {})
        if not isinstance(agent, dict):
            continue

        state = agent.get("tool_strategy_observability")
        if isinstance(state, str) and state:
            observability[state] += 1

        counts = agent.get("tool_name_counts")
        if isinstance(counts, dict):
            for name, value in counts.items():
                if (
                    isinstance(name, str)
                    and name
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and value >= 0
                ):
                    tool_names[name] += int(value)

        sequence = agent.get("tool_sequence")
        if isinstance(sequence, list) and all(
            isinstance(name, str) and name for name in sequence
        ):
            observed_sequences += 1
            sequence_lengths.append(len(sequence))

        ordinal = agent.get("subject_first_tool_call_ordinal")
        if (
            isinstance(ordinal, (int, float))
            and not isinstance(ordinal, bool)
            and ordinal >= 1
        ):
            first_subject_ordinals.append(ordinal)

        timing = _subject_routing_timing_metric(row)
        if timing is not None:
            routing_timing[timing] += 1

    partial = sum(
        count
        for state, count in observability.items()
        if "partial" in state
    )
    return {
        "observability_counts": dict(sorted(observability.items())),
        "partial_observability_trials": partial,
        "tool_name_counts": dict(sorted(tool_names.items())),
        "sequence_observations": observed_sequences,
        "sequence_length": {
            "observations": len(sequence_lengths),
            "mean": mean(sequence_lengths) if sequence_lengths else None,
            "median": median(sequence_lengths) if sequence_lengths else None,
            "min": min(sequence_lengths) if sequence_lengths else None,
            "max": max(sequence_lengths) if sequence_lengths else None,
        },
        "subject_first_tool_call_ordinal": {
            "observations": len(first_subject_ordinals),
            "mean": (
                mean(first_subject_ordinals)
                if first_subject_ordinals
                else None
            ),
            "median": (
                median(first_subject_ordinals)
                if first_subject_ordinals
                else None
            ),
            "min": min(first_subject_ordinals) if first_subject_ordinals else None,
            "max": max(first_subject_ordinals) if first_subject_ordinals else None,
        },
        "subject_routing_timing": {
            "observations": sum(routing_timing.values()),
            "counts": dict(sorted(routing_timing.items())),
            "invoked_ordered_observations": (
                routing_timing[ROUTING_FIRST_CHOICE]
                + routing_timing[ROUTING_LATE_RESCUE]
            ),
            "first_choice_rate": (
                routing_timing[ROUTING_FIRST_CHOICE]
                / (
                    routing_timing[ROUTING_FIRST_CHOICE]
                    + routing_timing[ROUTING_LATE_RESCUE]
                )
                if (
                    routing_timing[ROUTING_FIRST_CHOICE]
                    + routing_timing[ROUTING_LATE_RESCUE]
                )
                else None
            ),
        },
        "full_order_retained_in_receipts": True,
        "arguments_or_source_contents_included": False,
    }


def _format_contract_summary(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    observed: list[tuple[bool, bool | None, str | None]] = []
    for row in receipts:
        grade = row.get("scoring", {}).get("oracle_grade", {})
        compliant = grade.get("format_compliant")
        if not isinstance(compliant, bool):
            continue
        gradeable = grade.get("semantic_gradeable")
        answer_shape = grade.get("answer_shape")
        observed.append(
            (
                compliant,
                gradeable if isinstance(gradeable, bool) else None,
                answer_shape if isinstance(answer_shape, str) else None,
            )
        )

    shapes = Counter(shape for _compliant, _gradeable, shape in observed if shape)
    compliant_count = sum(compliant for compliant, _gradeable, _shape in observed)
    gradeable = [
        value
        for _compliant, value, _shape in observed
        if isinstance(value, bool)
    ]
    if not observed:
        state = "not-observed"
        interpretation = "no strict format evidence is available"
    elif compliant_count == len(observed):
        state = "strict-contract-compliant"
        interpretation = "all observed answers satisfy the strict output contract"
    elif (
        compliant_count == 0
        and len(gradeable) == len(observed)
        and all(gradeable)
    ):
        state = "strict-contract-saturated-noncompliant"
        interpretation = (
            "all observed answers violate the strict output contract while remaining "
            "semantically gradeable; inspect answer_shapes instead of treating format "
            "noncompliance as semantic failure"
        )
    else:
        state = "mixed"
        interpretation = (
            "strict output compliance varies across observed answers; inspect "
            "answer_shapes and semantic gradeability separately"
        )
    return {
        "state": state,
        "observations": len(observed),
        "compliant": compliant_count,
        "noncompliant": len(observed) - compliant_count,
        "answer_shapes": dict(sorted(shapes.items())),
        "semantic_gradeable_observations": len(gradeable),
        "semantic_gradeable": sum(gradeable),
        "interpretation": interpretation,
    }


def _aggregate_condition(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("status")) for row in receipts)
    valid = [row for row in receipts if row.get("status") in _VALID_OUTCOMES]
    passed = [row for row in valid if row.get("status") == "PASS"]
    tool_available = [
        row
        for row in receipts
        if row.get("authority", {}).get("subject", {}).get("available") is True
        and row.get("measurements", {}).get("agent", {}).get("subject_tool_configured")
        is True
        and isinstance(
            row.get("measurements", {}).get("agent", {}).get("subject_tool_invoked"),
            bool,
        )
    ]
    invoked = [
        row
        for row in tool_available
        if row.get("measurements", {}).get("agent", {}).get("subject_tool_invoked")
        is True
    ]
    observability = sorted(
        {
            value
            for row in receipts
            if isinstance(
                (
                    value := row.get("measurements", {})
                    .get("agent", {})
                    .get("source_read_observability")
                ),
                str,
            )
        }
    )
    semantic_rows = [
        value
        for row in valid
        if (value := _oracle_bool(row, "semantic_success")) is not None
    ]
    gradeable_rows = [
        value
        for row in valid
        if (value := _oracle_bool(row, "semantic_gradeable")) is not None
    ]
    semantic_statuses = Counter(
        value
        for row in valid
        if isinstance(
            (
                value := row.get("scoring", {})
                .get("oracle_grade", {})
                .get("semantic_status")
            ),
            str,
        )
    )
    format_rows = [
        value
        for row in valid
        if (value := _oracle_bool(row, "format_compliant")) is not None
    ]
    return {
        "trials": len(receipts),
        "valid_outcomes": len(valid),
        "statuses": dict(sorted(statuses.items())),
        "task_success_rate": len(passed) / len(valid) if valid else None,
        "semantic_success_rate": (
            sum(semantic_rows) / len(semantic_rows) if semantic_rows else None
        ),
        "semantic_success_denominator": len(semantic_rows),
        "semantic_gradeable_rate": (
            sum(gradeable_rows) / len(gradeable_rows) if gradeable_rows else None
        ),
        "semantic_gradeable_denominator": len(gradeable_rows),
        "semantic_statuses": dict(sorted(semantic_statuses.items())),
        "format_compliance_rate": (
            sum(format_rows) / len(format_rows) if format_rows else None
        ),
        "format_compliance_denominator": len(format_rows),
        "format_contract": _format_contract_summary(valid),
        "subject_tool_adoption_rate": (
            len(invoked) / len(tool_available) if tool_available else None
        ),
        "subject_tool_adoption_denominator": len(tool_available),
        "source_read_observability": observability,
        "tool_strategy": _tool_strategy_summary(receipts),
        "metrics": _metric_summary(receipts),
        "valid_outcome_metrics": _metric_summary(valid),
    }


def _subject_adoption_summary(
    receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Describe whether configured repository-intelligence subjects were used."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for receipt in receipts:
        subject = receipt.get("condition", {}).get("subject_definition", {}).get("id")
        agent = receipt.get("condition", {}).get("agent_definition", {}).get("id")
        if not isinstance(subject, str) or subject == "none":
            continue
        if not isinstance(agent, str) or not agent:
            continue
        grouped[(agent, subject)].append(receipt)

    rows: list[dict[str, Any]] = []
    for (agent, subject), group in sorted(grouped.items()):
        available = [
            row
            for row in group
            if row.get("authority", {}).get("subject", {}).get("available") is True
        ]
        configured = [
            row
            for row in group
            if row.get("measurements", {}).get("agent", {}).get(
                "subject_tool_configured"
            )
            is True
        ]
        observed = [
            row
            for row in group
            if isinstance(
                row.get("measurements", {}).get("agent", {}).get(
                    "subject_tool_invoked"
                ),
                bool,
            )
        ]
        invoked = [
            row
            for row in observed
            if row.get("measurements", {}).get("agent", {}).get(
                "subject_tool_invoked"
            )
            is True
        ]
        not_invoked = len(observed) - len(invoked)
        calls = [
            value
            for row in group
            if (
                value := _agent_metric(row, "subject_mcp_calls")
            )
            is not None
        ]
        routing_timing = Counter(
            timing
            for row in group
            if (timing := _subject_routing_timing_metric(row)) is not None
        )
        ordered_invocations = (
            routing_timing[ROUTING_FIRST_CHOICE]
            + routing_timing[ROUTING_LATE_RESCUE]
        )
        tools = sorted(
            {
                name
                for row in group
                for name in (
                    row.get("measurements", {})
                    .get("agent", {})
                    .get("subject_tool_names", [])
                )
                if isinstance(name, str) and name
            }
        )
        output_contract_failures = sum(
            row.get("scoring", {})
            .get("oracle_grade", {})
            .get("format_compliant")
            is False
            and row.get("scoring", {})
            .get("oracle_grade", {})
            .get("semantic_gradeable")
            is False
            for row in group
        )
        format_noncompliant = sum(
            row.get("scoring", {}).get("oracle_grade", {}).get("format_compliant")
            is False
            for row in group
        )
        semantic_incorrect = sum(
            row.get("scoring", {})
            .get("oracle_grade", {})
            .get("semantic_status")
            == "INCORRECT"
            for row in group
        )
        if not available:
            state = "subject-unavailable"
        elif not observed:
            state = "invocation-unobserved"
        elif not invoked:
            state = "configured-never-invoked"
        elif len(invoked) == len(observed):
            state = "invoked-all-observed"
        else:
            state = "invoked-some-observed"
        rows.append(
            {
                "agent_id": agent,
                "subject_id": subject,
                "trials": len(group),
                "available_trials": len(available),
                "configured_trials": len(configured),
                "invocation_observed_trials": len(observed),
                "invoked_trials": len(invoked),
                "not_invoked_trials": not_invoked,
                "invocation_unknown_trials": len(group) - len(observed),
                "subject_mcp_calls": sum(calls),
                "subject_tool_names": tools,
                "routing_timing_counts": dict(sorted(routing_timing.items())),
                "routing_timing_observed_trials": sum(routing_timing.values()),
                "routing_timing_unobserved_trials": (
                    len(group) - sum(routing_timing.values())
                ),
                "first_choice_trials": routing_timing[ROUTING_FIRST_CHOICE],
                "late_rescue_trials": routing_timing[ROUTING_LATE_RESCUE],
                "never_invoked_timing_trials": routing_timing[
                    ROUTING_NEVER_INVOKED
                ],
                "routing_timing_unknown_trials": routing_timing[ROUTING_UNKNOWN],
                "first_choice_rate": (
                    routing_timing[ROUTING_FIRST_CHOICE] / ordered_invocations
                    if ordered_invocations
                    else None
                ),
                "output_contract_failures": output_contract_failures,
                "format_noncompliant_trials": format_noncompliant,
                "semantic_incorrect_trials": semantic_incorrect,
                "status_counts": dict(
                    sorted(Counter(str(row.get("status")) for row in group).items())
                ),
                "state": state,
            }
        )
    return rows


def _agent_id(receipt: dict[str, Any]) -> str:
    value = receipt.get("condition", {}).get("agent_definition", {}).get("id")
    if not isinstance(value, str) or not value:
        raise ReportError("receipt has no frozen agent id")
    return value


def _subject_id(receipt: dict[str, Any]) -> str:
    value = receipt.get("condition", {}).get("subject_definition", {}).get("id")
    if not isinstance(value, str) or not value:
        raise ReportError("receipt has no frozen subject id")
    return value


def _comparison_identity(receipt: dict[str, Any]) -> dict[str, Any]:
    """Runtime authority shared by conditions; trial MCP wiring is excluded."""
    agent = receipt.get("authority", {}).get("agent", {})
    observed = agent.get("observed", {})
    if not isinstance(observed, dict):
        observed = {}
    adapter = receipt["condition"]["agent_definition"]["adapter"]
    common = {
        "adapter": adapter,
        "version": observed.get("version"),
        "executable_sha256": observed.get("executable_sha256"),
        "auth_mode": observed.get("auth_mode"),
        "model": observed.get("model"),
    }
    if adapter == "opencode-native":
        common.update(
            provider=observed.get("provider"),
            native_config_sha256=observed.get("native_config_sha256"),
        )
    elif adapter == "codex":
        common["reasoning_effort"] = observed.get("reasoning_effort")
        common["native_config_sha256"] = observed.get("native_config_sha256")
    return common


def validate_comparability(
    receipts: list[dict[str, Any]],
    *,
    allow_participant_authority_mix: bool = False,
) -> None:
    if not allow_participant_authority_mix:
        _check_comparable_agents(receipts)
    _check_comparable_evidence(
        receipts,
        allow_subject_authority_mix=allow_participant_authority_mix,
    )
    _check_comparable_localization_scoring(receipts)
    _check_localization_grades(receipts)


def _task_agent_authority(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    identities = {
        (_task_id(receipt), _agent_id(receipt)): _comparison_identity(receipt)
        for receipt in receipts
    }
    return [
        {"task_id": task_id, "agent_id": agent_id, **identity}
        for (task_id, agent_id), identity in sorted(identities.items())
    ]


def _check_localization_grades(receipts: list[dict[str, Any]]) -> None:
    for receipt in receipts:
        if receipt.get("status") not in _VALID_OUTCOMES:
            continue
        task = receipt.get("task", {})
        if task.get("oracle", {}).get("adapter") != "repository-location-json":
            continue
        grade = receipt.get("scoring", {}).get("oracle_grade")
        declared = receipt.get("authority", {}).get("oracle", {}).get("declared", {})
        policy = declared.get("provenance", {})
        observed = receipt.get("execution", {}).get("location_observation")
        if not isinstance(grade, dict) or not all(
            isinstance(grade.get(key), bool)
            for key in ("semantic_success", "semantic_gradeable", "format_compliant")
        ):
            raise ReportError("valid localization receipt has incomplete oracle grade")
        if (
            grade.get("semantic_status") not in {"CORRECT", "INCORRECT", "UNSCORABLE"}
            or grade.get("normalization_policy") != policy.get("normalization_policy")
            or grade.get("scoring_policy") != policy.get("scoring_policy")
            or policy.get("normalization_policy")
            != REPOSITORY_LOCATION_NORMALIZATION_POLICY
            or policy.get("scoring_policy") != REPOSITORY_LOCATION_SCORING_POLICY
            or grade.get("expected") != task["oracle"]["configuration"]["expected"]
            or grade.get("semantic_success")
            != (grade.get("semantic_status") == "CORRECT")
            or grade.get("semantic_gradeable")
            != (grade.get("semantic_status") != "UNSCORABLE")
            or not isinstance(observed, dict)
            or observed.get("normalization_policy")
            != REPOSITORY_LOCATION_NORMALIZATION_POLICY
            or grade
            != score_repository_location(
                observed, expected=task["oracle"]["configuration"]["expected"]
            )
            or receipt["status"] != ("PASS" if grade.get("passed") else "FAIL")
        ):
            raise ReportError(
                "valid localization receipt has inconsistent oracle grade"
            )


def _check_comparable_localization_scoring(
    receipts: list[dict[str, Any]],
) -> None:
    policies = {
        value
        for receipt in receipts
        if isinstance(
            (
                value := receipt.get("scoring", {})
                .get("oracle_grade", {})
                .get("scoring_policy")
            ),
            str,
        )
    }
    if len(policies) > 1:
        raise ReportError(
            "mixed localization scoring policy; use separate result campaigns"
        )


def _check_comparable_agents(receipts: list[dict[str, Any]]) -> None:
    global_identities: dict[str, dict[str, Any]] = {}
    task_identities: dict[tuple[str, str], dict[str, Any]] = {}
    for receipt in receipts:
        agent = _agent_id(receipt)
        identity = _comparison_identity(receipt)
        if not any(
            identity.get(key) is not None
            for key in ("version", "executable_sha256", "model", "native_config_sha256")
        ):
            continue
        global_identity = {
            key: value
            for key, value in identity.items()
            if key != "native_config_sha256"
        }
        prior_global = global_identities.setdefault(agent, global_identity)
        if prior_global != global_identity:
            raise ReportError(
                f"mixed observed runtime authority for agent {agent}; "
                "use separate result campaigns"
            )
        task_key = (_task_id(receipt), agent)
        prior_task = task_identities.setdefault(task_key, identity)
        if prior_task != identity:
            raise ReportError(
                f"mixed native config authority for task {task_key[0]} / {agent}; "
                "paired conditions are not comparable"
            )


def _check_comparable_evidence(
    receipts: list[dict[str, Any]],
    *,
    allow_subject_authority_mix: bool = False,
) -> None:
    if not receipts:
        return
    for field in ("harness",):
        baseline = receipts[0].get("authority", {}).get(field)
        if any(
            receipt.get("authority", {}).get(field) != baseline
            for receipt in receipts[1:]
        ):
            raise ReportError(f"mixed {field} authority; use separate result campaigns")
    environments: dict[str, Any] = {}
    subjects: dict[tuple[str, str, str], Any] = {}
    subject_executables: dict[str, str] = {}
    for receipt in receipts:
        agent = _agent_id(receipt)
        environment = receipt.get("authority", {}).get("environment")
        if agent in environments and environments[agent] != environment:
            raise ReportError(
                f"mixed environment authority for agent {agent}; "
                "use separate result campaigns"
            )
        environments[agent] = environment
        authority = receipt.get("authority", {}).get("subject", {})
        if authority.get("available") is not True:
            continue
        if allow_subject_authority_mix:
            continue
        subject = (_task_id(receipt), agent, _subject_id(receipt))
        observed = authority.get("observed")
        native = (
            observed.get("native_subject_identity")
            if isinstance(observed, dict)
            else None
        )
        executable_hash = (
            native.get("executable_sha256") if isinstance(native, dict) else None
        )
        if isinstance(executable_hash, str):
            subject_id = subject[2]
            if (
                subject_id in subject_executables
                and subject_executables[subject_id] != executable_hash
            ):
                raise ReportError(
                    f"mixed observed subject authority for {subject_id}; "
                    "use separate result campaigns"
                )
            subject_executables[subject_id] = executable_hash
        comparable_observed = observed
        if isinstance(observed, dict) and "native_subject_identity" in observed:
            exposure = observed.get("mcp_exposure") or {}
            comparable_observed = {
                "source": observed.get("source"),
                "subject": observed.get("subject"),
                "native_subject_identity": observed.get("native_subject_identity"),
                "native_config_sha256": observed.get("native_config_sha256"),
                "workspace_binding": observed.get("workspace_binding"),
                "mcp_semantic_identity": (
                    exposure.get("semantic_identity")
                    if isinstance(exposure, dict)
                    else None
                ),
            }
        if subject in subjects and subjects[subject] != comparable_observed:
            raise ReportError(
                f"mixed observed subject authority for {subject}; "
                "use separate result campaigns"
            )
        subjects[subject] = comparable_observed


def _condition_context_key(condition: dict[str, Any]) -> tuple[str | None, str | None]:
    context = condition.get("context")
    if not isinstance(context, dict):
        return None, None
    group = context.get("group")
    variant = context.get("variant")
    return (
        group if isinstance(group, str) else None,
        variant if isinstance(variant, str) else None,
    )


def _receipt_context_key(receipt: dict[str, Any]) -> tuple[str | None, str | None]:
    return _condition_context_key(receipt.get("condition", {}))


def _pair_key(
    receipt: dict[str, Any],
) -> tuple[str, str, int, int, str | None, str | None]:
    execution = receipt["execution"]
    agent_id = _agent_id(receipt)
    context_group, context_variant = _receipt_context_key(receipt)
    return (
        _task_id(receipt),
        agent_id,
        int(execution["trial_index"]),
        _replicate_id(receipt),
        context_group,
        context_variant,
    )


def _subject_use_state(receipt: dict[str, Any]) -> str:
    value = (
        receipt.get("measurements", {})
        .get("agent", {})
        .get("subject_tool_invoked")
    )
    if value is True:
        return "invoked"
    if value is False:
        return "not-invoked"
    return "unknown"


def _attribution_interpretation(subject_use_state: str) -> str:
    if subject_use_state == "invoked":
        return "subject-use-observed"
    if subject_use_state == "not-invoked":
        return "not-attributable-to-subject-tool"
    return "invocation-unknown"


def repository_location_outcome_topology(
    receipt: dict[str, Any],
) -> str | None:
    """Classify repository-location outcomes without changing oracle truth."""
    if receipt.get("status") not in _VALID_OUTCOMES:
        return None

    task = receipt.get("task", {})
    oracle = task.get("oracle", {}) if isinstance(task, dict) else {}
    if (
        not isinstance(oracle, dict)
        or oracle.get("adapter") != "repository-location-json"
    ):
        return None

    grade = receipt.get("scoring", {}).get("oracle_grade", {})
    if not isinstance(grade, dict):
        return None
    if grade.get("semantic_gradeable") is not True:
        return "ungradeable"

    expected = grade.get("expected")
    actual = grade.get("normalized_actual")
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return None
    expected_path = expected.get("path")
    expected_symbol = expected.get("symbol")
    actual_path = actual.get("path")
    actual_symbol = actual.get("symbol")
    if not all(
        isinstance(value, str) and value
        for value in (expected_path, expected_symbol, actual_path, actual_symbol)
    ):
        return None

    if actual_path == expected_path and actual_symbol == expected_symbol:
        return "exact"
    if actual_path == expected_path:
        return "same-file-wrong-symbol"
    if actual_symbol == expected_symbol:
        return "same-symbol-wrong-file"
    if PurePosixPath(actual_path).parent == PurePosixPath(expected_path).parent:
        return "same-directory-location-mismatch"
    return "different-location"


def _assistance_transition(
    baseline: dict[str, Any],
    assisted: dict[str, Any],
) -> str | None:
    if not _semantic_gradeable(baseline) or not _semantic_gradeable(assisted):
        return None
    left = _semantic_success(baseline)
    right = _semantic_success(assisted)
    if left is None or right is None:
        return None
    if not left and right:
        return "gain"
    if left and right:
        return "preserved"
    if not left and not right:
        return "unresolved"
    return "regression"


def _pair_input_id(receipt: dict[str, Any]) -> str:
    authority = receipt.get("authority", {})
    return digest(
        {
            "task": execution_task_contract(receipt["task"]),
            "agent": _comparison_identity(receipt),
            "harness": authority.get("harness"),
            "environment": authority.get("environment"),
            "mutation": authority.get("mutation"),
            "admitted_state": receipt.get("execution", {}).get("admitted_state_sha256"),
            "replicate_id": _replicate_id(receipt),
            "prompt_context": receipt.get("condition", {}).get("context"),
        }
    )


def classify_assistance_pair(
    baseline: dict[str, Any], assisted: dict[str, Any]
) -> str | None:
    """Use the report's evidence contract for both live and final pair summaries."""
    if (
        baseline.get("status") not in _VALID_OUTCOMES
        or assisted.get("status") not in _VALID_OUTCOMES
    ):
        return None
    if _pair_input_id(baseline) != _pair_input_id(assisted):
        raise ReportError(f"paired non-subject authority differs for {_pair_key(assisted)}")
    return _assistance_transition(baseline, assisted)


def _paired_assistance(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bare: dict[
        tuple[str, str, int, int, str | None, str | None], dict[str, Any]
    ] = {}
    assisted: list[dict[str, Any]] = []
    for receipt in receipts:
        if receipt.get("status") not in _VALID_OUTCOMES:
            continue
        adapter = (
            receipt.get("condition", {}).get("subject_definition", {}).get("adapter")
        )
        if adapter == "none":
            key = _pair_key(receipt)
            if key in bare:
                raise ReportError(f"multiple bare executions for pair {key}")
            bare[key] = receipt
        else:
            assisted.append(receipt)

    rows: list[dict[str, Any]] = []
    for receipt in assisted:
        key = _pair_key(receipt)
        baseline = bare.get(key)
        if baseline is None:
            continue
        pair_input = _pair_input_id(baseline)
        transition = classify_assistance_pair(baseline, receipt)
        row: dict[str, Any] = {
            "task_id": key[0],
            "agent_id": key[1],
            "trial_index": key[2],
            "replicate_id": key[3],
            "context_group": key[4],
            "context_variant": key[5],
            **({"seed": key[3]} if "seed" in receipt["execution"] else {}),
            "condition_id": _condition_id(receipt),
            "bare_trial_id": baseline["trial_id"],
            "assisted_trial_id": receipt["trial_id"],
            "subject_id": receipt["condition"]["subject_definition"]["id"],
            "pair_input_id": pair_input,
            "bare_status": baseline["status"],
            "assisted_status": receipt["status"],
            "task_success_delta": (
                int(receipt["status"] == "PASS") - int(baseline["status"] == "PASS")
            ),
            "assistance_transition": transition,
            "bare_location_topology": repository_location_outcome_topology(baseline),
            "assisted_location_topology": repository_location_outcome_topology(receipt),
        }
        invoked = (
            receipt.get("measurements", {})
            .get("agent", {})
            .get("subject_tool_invoked")
        )
        row["subject_tool_invoked"] = invoked if isinstance(invoked, bool) else None
        row["subject_use_state"] = _subject_use_state(receipt)
        row["attribution_interpretation"] = _attribution_interpretation(
            row["subject_use_state"]
        )
        configured = (
            receipt.get("measurements", {})
            .get("agent", {})
            .get("subject_tool_configured")
        )
        row["subject_tool_configured"] = (
            configured if isinstance(configured, bool) else None
        )
        tool_names = (
            receipt.get("measurements", {})
            .get("agent", {})
            .get("subject_tool_names")
        )
        row["subject_tool_names"] = sorted(
            {
                name
                for name in tool_names
                if isinstance(name, str) and name
            }
        ) if isinstance(tool_names, list) else []
        treatment = contracted_treatment_evidence(receipt)
        row["contracted_required_tool"] = treatment["required_tool"]
        row["contracted_required_operation"] = treatment["required_operation"]
        row["contracted_exposure_state"] = treatment["state"]
        row["contracted_treatment_observed"] = treatment["treatment_observed"]
        row["subject_use_interpretation"] = row["attribution_interpretation"]
        row["attribution_interpretation"] = (
            treatment["attribution_interpretation"]
            if treatment["state"] != "no-contract"
            else row["subject_use_interpretation"]
        )
        subject_calls = _agent_metric(receipt, "subject_mcp_calls")
        row["subject_mcp_calls"] = subject_calls
        for metric in (
            "duration_ms",
            "command_calls",
            "tool_calls",
            "mcp_calls",
            "input_tokens",
            "output_tokens",
        ):
            left = _agent_metric(baseline, metric)
            right = _agent_metric(receipt, metric)
            row[f"{metric}_delta"] = (
                right - left if left is not None and right is not None else None
            )
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            str(row["task_id"]),
            str(row["agent_id"]),
            int(row["trial_index"]),
            str(row["condition_id"]),
        ),
    )


def _paired_assistance_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        transition = row.get("assistance_transition")
        if isinstance(transition, str):
            grouped[(str(row["agent_id"]), str(row["subject_id"]))][transition] += 1
    return [
        {
            "agent_id": key[0],
            "subject_id": key[1],
            "comparison_scope": "subject-configured-condition-vs-bare",
            "attribution_requires_observed_subject_use": True,
            "contracted_attribution_requires": (
                "exact-required-operation-with-successful-nonempty-result"
            ),
            "total_pairs": sum(counts.values()),
            "transitions": {
                name: counts.get(name, 0)
                for name in ("gain", "preserved", "unresolved", "regression")
            },
        }
        for key, counts in sorted(grouped.items())
    ]


def _paired_assistance_usage_summary(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        state = str(row.get("subject_use_state") or "unknown")
        grouped[
            (str(row["agent_id"]), str(row["subject_id"]), state)
        ].append(row)

    summaries: list[dict[str, Any]] = []
    for (agent_id, subject_id, state), group in sorted(grouped.items()):
        transitions = Counter(
            str(row["assistance_transition"])
            for row in group
            if isinstance(row.get("assistance_transition"), str)
        )
        metric_summary: dict[str, dict[str, int | float | None]] = {}
        for metric in (
            "duration_ms",
            "command_calls",
            "tool_calls",
            "mcp_calls",
            "input_tokens",
            "output_tokens",
        ):
            values = [
                value
                for row in group
                if isinstance(
                    (value := row.get(f"{metric}_delta")),
                    (int, float),
                )
                and not isinstance(value, bool)
            ]
            metric_summary[metric] = {
                "observations": len(values),
                "mean": mean(values) if values else None,
                "median": median(values) if values else None,
            }
        summaries.append(
            {
                "agent_id": agent_id,
                "subject_id": subject_id,
                "invocation_state": state,
                "total_pairs": len(group),
                "subject_mcp_calls": sum(
                    int(value)
                    for row in group
                    if isinstance(
                        (value := row.get("subject_mcp_calls")),
                        (int, float),
                    )
                    and not isinstance(value, bool)
                ),
                "transitions": {
                    name: transitions.get(name, 0)
                    for name in ("gain", "preserved", "unresolved", "regression")
                },
                "delta_metrics": metric_summary,
            }
        )
    return summaries


def _paired_assistance_treatment_summary(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        state = str(row.get("contracted_exposure_state") or "no-contract")
        grouped[
            (str(row["agent_id"]), str(row["subject_id"]), state)
        ].append(row)

    summaries: list[dict[str, Any]] = []
    for (agent_id, subject_id, state), group in sorted(grouped.items()):
        transitions = Counter(
            str(row["assistance_transition"])
            for row in group
            if isinstance(row.get("assistance_transition"), str)
        )
        metric_summary: dict[str, dict[str, int | float | None]] = {}
        for metric in (
            "duration_ms",
            "command_calls",
            "tool_calls",
            "mcp_calls",
            "input_tokens",
            "output_tokens",
        ):
            values = [
                value
                for row in group
                if isinstance(
                    (value := row.get(f"{metric}_delta")),
                    (int, float),
                )
                and not isinstance(value, bool)
            ]
            metric_summary[metric] = {
                "observations": len(values),
                "mean": mean(values) if values else None,
                "median": median(values) if values else None,
            }
        treatment_values = {
            row.get("contracted_treatment_observed")
            for row in group
            if row.get("contracted_treatment_observed") in {True, False, None}
        }
        treatment_observed = (
            True
            if treatment_values == {True}
            else False
            if treatment_values == {False}
            else None
        )
        summaries.append(
            {
                "agent_id": agent_id,
                "subject_id": subject_id,
                "contracted_exposure_state": state,
                "contracted_treatment_observed": treatment_observed,
                "total_pairs": len(group),
                "transitions": {
                    name: transitions.get(name, 0)
                    for name in ("gain", "preserved", "unresolved", "regression")
                },
                "delta_metrics": metric_summary,
            }
        )
    return summaries


def _task_assistance_evidence(
    rows: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str, str],
        dict[str, list[dict[str, Any]]],
    ] = defaultdict(lambda: {"pairs": [], "exclusions": []})
    for row in rows:
        key = (
            str(row["task_id"]),
            str(row["agent_id"]),
            str(row["condition_id"]),
            str(row["subject_id"]),
        )
        grouped[key]["pairs"].append(row)
    for row in exclusions:
        key = (
            str(row["task_id"]),
            str(row["agent_id"]),
            str(row["condition_id"]),
            str(row["subject_id"]),
        )
        grouped[key]["exclusions"].append(row)

    output: list[dict[str, Any]] = []
    for (task_id, agent_id, condition_id, subject_id), evidence in sorted(
        grouped.items()
    ):
        pairs = evidence["pairs"]
        excluded = evidence["exclusions"]
        transitions = Counter(
            str(row["assistance_transition"])
            for row in pairs
            if isinstance(row.get("assistance_transition"), str)
        )
        by_invocation: dict[str, list[dict[str, Any]]] = {
            "invoked": [],
            "not-invoked": [],
            "unknown": [],
        }
        by_treatment: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in pairs:
            state = str(row.get("subject_use_state") or "unknown")
            by_invocation[state].append(row)
            treatment_state = str(
                row.get("contracted_exposure_state") or "no-contract"
            )
            by_treatment[treatment_state].append(row)

        transition_by_invocation = {}
        economics_by_invocation = {}
        for state, state_rows in by_invocation.items():
            counts = Counter(
                str(row["assistance_transition"])
                for row in state_rows
                if isinstance(row.get("assistance_transition"), str)
            )
            transition_by_invocation[state] = {
                name: counts.get(name, 0)
                for name in ("gain", "preserved", "unresolved", "regression")
            }
            metrics: dict[str, dict[str, int | float | None]] = {}
            for metric in (
                "duration_ms",
                "command_calls",
                "tool_calls",
                "mcp_calls",
                "input_tokens",
                "output_tokens",
            ):
                values = [
                    value
                    for row in state_rows
                    if isinstance(
                        (value := row.get(f"{metric}_delta")),
                        (int, float),
                    )
                    and not isinstance(value, bool)
                ]
                metrics[metric] = {
                    "observations": len(values),
                    "mean": mean(values) if values else None,
                    "median": median(values) if values else None,
                }
            economics_by_invocation[state] = metrics

        transition_by_treatment: dict[str, dict[str, int]] = {}
        economics_by_treatment: dict[
            str,
            dict[str, dict[str, int | float | None]],
        ] = {}
        for state, state_rows in sorted(by_treatment.items()):
            counts = Counter(
                str(row["assistance_transition"])
                for row in state_rows
                if isinstance(row.get("assistance_transition"), str)
            )
            transition_by_treatment[state] = {
                name: counts.get(name, 0)
                for name in ("gain", "preserved", "unresolved", "regression")
            }
            metrics: dict[str, dict[str, int | float | None]] = {}
            for metric in (
                "duration_ms",
                "command_calls",
                "tool_calls",
                "mcp_calls",
                "input_tokens",
                "output_tokens",
            ):
                values = [
                    value
                    for row in state_rows
                    if isinstance(
                        (value := row.get(f"{metric}_delta")),
                        (int, float),
                    )
                    and not isinstance(value, bool)
                ]
                metrics[metric] = {
                    "observations": len(values),
                    "mean": mean(values) if values else None,
                    "median": median(values) if values else None,
                }
            economics_by_treatment[state] = metrics

        contracted_rows = by_treatment.get(CONTRACTED_SUCCESSFUL_RESULT, [])
        definite_non_treatment_rows = [
            row
            for row in pairs
            if row.get("contracted_treatment_observed") is False
        ]
        unproven_treatment_rows = [
            row
            for row in pairs
            if row.get("contracted_treatment_observed") is None
        ]
        contracted_transitions = transition_by_treatment.get(
            CONTRACTED_SUCCESSFUL_RESULT,
            {
                "gain": 0,
                "preserved": 0,
                "unresolved": 0,
                "regression": 0,
            },
        )

        bare_semantic_failures = (
            transitions.get("gain", 0) + transitions.get("unresolved", 0)
        )
        bare_semantic_successes = (
            transitions.get("preserved", 0) + transitions.get("regression", 0)
        )
        assisted_semantic_failures = (
            transitions.get("unresolved", 0) + transitions.get("regression", 0)
        )
        assisted_semantic_successes = (
            transitions.get("gain", 0) + transitions.get("preserved", 0)
        )
        invoked_transitions = transition_by_invocation["invoked"]
        not_invoked_transitions = transition_by_invocation["not-invoked"]

        signals: list[str] = []
        if excluded:
            signals.append("runtime-or-comparability-exclusions")
        if pairs and bare_semantic_failures:
            signals.append("bare-headroom-observed")
        elif pairs and not bare_semantic_failures:
            signals.append("bare-no-headroom-observed")
        if by_invocation["invoked"]:
            signals.append("subject-invocation-observed")
            if invoked_transitions["gain"]:
                signals.append("invoked-gain-observed")
            if invoked_transitions["regression"]:
                signals.append("invoked-regression-observed")
            if not invoked_transitions["gain"]:
                signals.append("invoked-no-gain-observed")
        elif pairs and not by_invocation["unknown"]:
            signals.append("subject-not-invoked")
        if bare_semantic_failures and not by_invocation["invoked"]:
            signals.append("bare-headroom-without-subject-invocation")
        if not_invoked_transitions["regression"]:
            signals.append("regression-without-subject-invocation")
        if contracted_rows:
            signals.append("contracted-treatment-observed")
            if contracted_transitions["gain"]:
                signals.append("contracted-treatment-gain-observed")
            if contracted_transitions["regression"]:
                signals.append("contracted-treatment-regression-observed")
            if not contracted_transitions["gain"]:
                signals.append("contracted-treatment-no-gain-observed")
        if definite_non_treatment_rows:
            signals.append("subject-use-without-contracted-treatment")
        if unproven_treatment_rows:
            signals.append("contracted-treatment-unproven")
        if bare_semantic_failures and not contracted_rows:
            signals.append("bare-headroom-without-contracted-treatment")
        if any(
            row.get("assistance_transition") == "regression"
            for row in definite_non_treatment_rows
        ):
            signals.append("regression-without-contracted-treatment")

        tools = sorted(
            {
                name
                for row in pairs
                for name in row.get("subject_tool_names", [])
                if isinstance(name, str) and name
            }
        )
        output.append(
            {
                "task_id": task_id,
                "agent_id": agent_id,
                "condition_id": condition_id,
                "subject_id": subject_id,
                "expected_pairs": len(pairs) + len(excluded),
                "comparable_pairs": len(pairs),
                "excluded_pairs": len(excluded),
                "excluded_statuses": {
                    "bare": dict(
                        sorted(Counter(str(row["bare_status"]) for row in excluded).items())
                    ),
                    "assisted": dict(
                        sorted(
                            Counter(
                                str(row["assisted_status"]) for row in excluded
                            ).items()
                        )
                    ),
                },
                "bare_semantic": {
                    "successes": bare_semantic_successes,
                    "failures": bare_semantic_failures,
                },
                "assisted_semantic": {
                    "successes": assisted_semantic_successes,
                    "failures": assisted_semantic_failures,
                },
                "transitions": {
                    name: transitions.get(name, 0)
                    for name in ("gain", "preserved", "unresolved", "regression")
                },
                "subject_use": {
                    "invoked_pairs": len(by_invocation["invoked"]),
                    "not_invoked_pairs": len(by_invocation["not-invoked"]),
                    "unknown_pairs": len(by_invocation["unknown"]),
                    "subject_mcp_calls": sum(
                        int(value)
                        for row in pairs
                        if isinstance(
                            (value := row.get("subject_mcp_calls")),
                            (int, float),
                        )
                        and not isinstance(value, bool)
                    ),
                    "subject_tool_names": tools,
                },
                "contracted_treatment": {
                    "observed_pairs": len(contracted_rows),
                    "definite_non_treatment_pairs": len(
                        definite_non_treatment_rows
                    ),
                    "unproven_pairs": len(unproven_treatment_rows),
                    "state_counts": dict(
                        sorted(
                            Counter(
                                str(
                                    row.get("contracted_exposure_state")
                                    or "no-contract"
                                )
                                for row in pairs
                            ).items()
                        )
                    ),
                },
                "transitions_by_invocation": transition_by_invocation,
                "delta_metrics_by_invocation": economics_by_invocation,
                "transitions_by_contracted_treatment": transition_by_treatment,
                "delta_metrics_by_contracted_treatment": economics_by_treatment,
                "evidence_signals": signals,
            }
        )
    return output


def _pairwise_disagreement_rate(values: list[Any]) -> float | None:
    if len(values) < 2:
        return None
    rendered = [
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for value in values
    ]
    pairs = 0
    disagreements = 0
    for index, left in enumerate(rendered):
        for right in rendered[index + 1 :]:
            pairs += 1
            disagreements += left != right
    return disagreements / pairs if pairs else None


def _route_observation(receipt: dict[str, Any]) -> list[str] | None:
    sequence = receipt.get("measurements", {}).get("agent", {}).get("tool_sequence")
    if not isinstance(sequence, list) or not all(isinstance(item, str) for item in sequence):
        return None
    return list(sequence)


def _authority_use_observation(receipt: dict[str, Any]) -> bool | None:
    if _subject_id(receipt) == "none":
        return None
    value = (
        receipt.get("measurements", {})
        .get("agent", {})
        .get("subject_tool_invoked")
    )
    return value if isinstance(value, bool) else None


def _stability(
    receipts: list[dict[str, Any]],
    *,
    expected: dict[str, dict[str, Any]],
    suite: SuiteDefinition,
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str, str | None, str | None], list[dict[str, Any]]
    ] = defaultdict(list)
    expected_ids: dict[
        tuple[str, str, str, str | None, str | None], list[int]
    ] = defaultdict(list)
    conditions = {row["id"]: row for row in suite.experiment["conditions"]}
    for row in expected.values():
        condition = conditions[row["condition_id"]]
        context_group, context_variant = _condition_context_key(condition)
        key = (
            str(row["task_id"]),
            str(condition["agent"]),
            str(condition["subject"]),
            context_group,
            context_variant,
        )
        expected_ids[key].append(int(row.get("replicate_id", row.get("seed"))))
    for receipt in receipts:
        context_group, context_variant = _receipt_context_key(receipt)
        grouped[
            (
                _task_id(receipt),
                _agent_id(receipt),
                _subject_id(receipt),
                context_group,
                context_variant,
            )
        ].append(receipt)

    rows: list[dict[str, Any]] = []
    for key, identifiers in sorted(
        expected_ids.items(),
        key=lambda item: tuple("" if value is None else str(value) for value in item[0]),
    ):
        group = grouped.get(key, [])
        valid = [row for row in group if row.get("status") in _VALID_OUTCOMES]
        gradeable = [row for row in valid if _semantic_gradeable(row)]
        correct = sum(_semantic_success(row) is True for row in gradeable)
        normalized_answers = [
            row.get("scoring", {}).get("oracle_grade", {}).get("normalized_actual")
            for row in gradeable
        ]
        answer_counts = Counter(
            json.dumps(value, sort_keys=True)
            for value in normalized_answers
        )
        raw_answers = [
            value
            for row in valid
            if isinstance((value := row.get("execution", {}).get("agent_answer")), str)
        ]
        semantic_values = [
            value
            for row in gradeable
            if isinstance((value := _semantic_success(row)), bool)
        ]
        route_values = [
            value
            for row in valid
            if (value := _route_observation(row)) is not None
        ]
        authority_values = [
            value
            for row in valid
            if (value := _authority_use_observation(row)) is not None
        ]
        if len(valid) != len(identifiers):
            state = "execution-unstable"
        elif len(gradeable) != len(valid):
            state = "not-gradeable" if not gradeable else "unstable"
        elif correct == len(gradeable):
            state = "stable-correct"
        elif correct == 0 and len(answer_counts) == 1:
            state = "stable-incorrect"
        else:
            state = "unstable"
        rows.append(
            {
                "task_id": key[0],
                "agent_id": key[1],
                "subject_id": key[2],
                "context_group": key[3],
                "context_variant": key[4],
                "replicates": len(identifiers),
                "observed_replicates": len(group),
                "replicate_ids": sorted(identifiers),
                "observed_replicate_ids": sorted(_replicate_id(row) for row in group),
                "valid_outcomes": len(valid),
                "gradeable_outcomes": len(gradeable),
                "semantic_correct": correct,
                "semantic_incorrect": len(gradeable) - correct,
                "location_topologies": dict(
                    sorted(
                        Counter(
                            topology
                            for row in valid
                            if (
                                topology := repository_location_outcome_topology(row)
                            )
                            is not None
                        ).items()
                    )
                ),
                "format_compliant": sum(
                    _oracle_bool(row, "format_compliant") is True for row in valid
                ),
                "answer_distribution": dict(sorted(answer_counts.items())),
                "answer_distribution_source": (
                    "scoring.oracle_grade.normalized_actual"
                ),
                "raw_answer_flip_rate": _pairwise_disagreement_rate(raw_answers),
                "raw_answer_flip_observations": len(raw_answers),
                "normalized_answer_flip_rate": _pairwise_disagreement_rate(
                    normalized_answers
                ),
                "normalized_answer_flip_observations": len(normalized_answers),
                "answer_flip_rate": _pairwise_disagreement_rate(raw_answers),
                "answer_flip_observations": len(raw_answers),
                "answer_flip_source": "execution.agent_answer.raw-text",
                "semantic_flip_rate": _pairwise_disagreement_rate(semantic_values),
                "semantic_flip_observations": len(semantic_values),
                "route_flip_rate": _pairwise_disagreement_rate(route_values),
                "route_flip_observations": len(route_values),
                "authority_flip_rate": _pairwise_disagreement_rate(authority_values),
                "authority_flip_observations": len(authority_values),
                "flip_rate_policy": "pairwise-disagreement-among-observed-replicates",
                "state": state,
            }
        )
    return rows


def _context_semantic_transition(
    baseline: dict[str, Any], variant: dict[str, Any]
) -> str | None:
    if not all(row.get("status") in _VALID_OUTCOMES for row in (baseline, variant)):
        return None
    if not _semantic_gradeable(baseline) or not _semantic_gradeable(variant):
        return None
    left = _semantic_success(baseline)
    right = _semantic_success(variant)
    if left is True and right is True:
        return "preserved-correct"
    if left is True and right is False:
        return "regressed"
    if left is False and right is True:
        return "recovered"
    return "preserved-incorrect"


def _counterfactual_pair_id(
    baseline: dict[str, Any], variant: dict[str, Any], *, group: str
) -> str:
    authority = baseline.get("authority", {})
    context = variant.get("condition", {}).get("context", {})
    return digest(
        {
            "contract": "benchmark-counterfactual-pair.v1",
            "group": group,
            "variant": context.get("variant"),
            "task": execution_task_contract(baseline["task"]),
            "oracle": baseline["task"].get("oracle"),
            "agent": _comparison_identity(baseline),
            "subject": baseline["condition"].get("subject_definition"),
            "harness": authority.get("harness"),
            "environment": authority.get("environment"),
            "mutation": authority.get("mutation"),
            "admitted_state": baseline.get("execution", {}).get("admitted_state_sha256"),
            "replicate_id": _replicate_id(baseline),
        }
    )


def _counterfactual_context(
    receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str, str, int], list[dict[str, Any]]
    ] = defaultdict(list)
    for receipt in receipts:
        context = receipt.get("condition", {}).get("context")
        if not isinstance(context, dict):
            continue
        grouped[
            (
                str(context["group"]),
                _task_id(receipt),
                _agent_id(receipt),
                _subject_id(receipt),
                _replicate_id(receipt),
            )
        ].append(receipt)

    comparisons: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        variants: dict[str, dict[str, Any]] = {}
        neutral: list[dict[str, Any]] = []
        for row in rows:
            context = row["condition"]["context"]
            variant = str(context["variant"])
            if variant in variants:
                raise ReportError(f"duplicate context execution for {key} / {variant}")
            variants[variant] = row
            if context.get("kind") == "neutral":
                neutral.append(row)
        if len(neutral) > 1:
            raise ReportError(f"context comparison {key} has multiple neutral executions")
        if not neutral:
            continue
        baseline = neutral[0]
        baseline_variant = str(baseline["condition"]["context"]["variant"])
        for variant_name, variant in sorted(variants.items()):
            if variant_name == baseline_variant:
                continue
            transition = _context_semantic_transition(baseline, variant)
            left_answer = baseline.get("execution", {}).get("agent_answer")
            right_answer = variant.get("execution", {}).get("agent_answer")
            left_grade = baseline.get("scoring", {}).get("oracle_grade", {})
            right_grade = variant.get("scoring", {}).get("oracle_grade", {})
            normalized_observed = (
                "normalized_actual" in left_grade and "normalized_actual" in right_grade
            )
            left_route = _route_observation(baseline)
            right_route = _route_observation(variant)
            left_authority = _authority_use_observation(baseline)
            right_authority = _authority_use_observation(variant)
            comparisons.append(
                {
                    "pair_id": _counterfactual_pair_id(
                        baseline, variant, group=key[0]
                    ),
                    "context_group": key[0],
                    "task_id": key[1],
                    "agent_id": key[2],
                    "subject_id": key[3],
                    "replicate_id": key[4],
                    "baseline_variant": baseline_variant,
                    "variant": variant_name,
                    "variant_kind": variant["condition"]["context"]["kind"],
                    "baseline_trial_id": baseline["trial_id"],
                    "variant_trial_id": variant["trial_id"],
                    "baseline_status": baseline["status"],
                    "variant_status": variant["status"],
                    "comparison_state": (
                        "comparable" if transition is not None else "uncomparable"
                    ),
                    "semantic_transition": transition,
                    "answer_changed": (
                        left_answer != right_answer
                        if isinstance(left_answer, str) and isinstance(right_answer, str)
                        else None
                    ),
                    "normalized_answer_changed": (
                        left_grade.get("normalized_actual")
                        != right_grade.get("normalized_actual")
                        if normalized_observed
                        else None
                    ),
                    "route_changed": (
                        left_route != right_route
                        if left_route is not None and right_route is not None
                        else None
                    ),
                    "authority_use_changed": (
                        left_authority != right_authority
                        if left_authority is not None and right_authority is not None
                        else None
                    ),
                }
            )
    return comparisons


def _counterfactual_context_summary(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row["context_group"]),
                str(row["variant"]),
                str(row["agent_id"]),
                str(row["subject_id"]),
            )
        ].append(row)
    summaries = []
    for key, group in sorted(grouped.items()):
        transitions = Counter(
            str(row["semantic_transition"])
            for row in group
            if isinstance(row.get("semantic_transition"), str)
        )
        summary: dict[str, Any] = {
            "context_group": key[0],
            "variant": key[1],
            "agent_id": key[2],
            "subject_id": key[3],
            "pairs": len(group),
            "comparable_pairs": sum(
                row["comparison_state"] == "comparable" for row in group
            ),
            "semantic_transitions": dict(sorted(transitions.items())),
        }
        for field in (
            "answer_changed",
            "normalized_answer_changed",
            "route_changed",
            "authority_use_changed",
        ):
            values = [row[field] for row in group if isinstance(row.get(field), bool)]
            summary[field] = {
                "observations": len(values),
                "changed": sum(values),
                "rate": sum(values) / len(values) if values else None,
            }
        summaries.append(summary)
    return summaries


def _analysis_evidence(
    *,
    suite: SuiteDefinition,
    comparisons: list[dict[str, Any]],
) -> dict[str, Any] | None:
    contract = suite.experiment.get("analysis_contract")
    if not isinstance(contract, dict):
        return None
    comparable = [row for row in comparisons if row["comparison_state"] == "comparable"]
    arms: dict[tuple[str, str, str, str, str], set[int]] = defaultdict(set)
    for row in comparable:
        arms[
            (
                str(row["context_group"]),
                str(row["task_id"]),
                str(row["agent_id"]),
                str(row["subject_id"]),
                str(row["variant"]),
            )
        ].add(int(row["replicate_id"]))
    minimum_observed_replicates = min(
        (len(values) for values in arms.values()),
        default=0,
    )
    pairs_met = len(comparable) >= int(contract["minimum_pairs"])
    replicates_met = (
        minimum_observed_replicates >= int(contract["minimum_replicates"])
    )
    return {
        "contract": dict(contract),
        "observed_comparable_pairs": len(comparable),
        "minimum_observed_replicates_per_variant_arm": minimum_observed_replicates,
        "minimum_pairs_met": pairs_met,
        "minimum_replicates_met": replicates_met,
        "evidence_state": (
            "minimum-evidence-observed"
            if pairs_met and replicates_met
            else "below-minimum"
        ),
        "claim_scope": "descriptive-only",
        "cross_agent_ranking_permitted": False,
    }

def _bounded_report_reason(value: Any, *, limit: int = 1_000) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = value.strip()
    if not rendered:
        return None
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + "…"


def _diagnostic(receipt: dict[str, Any]) -> dict[str, Any]:
    status = receipt.get("status")
    grade = receipt.get("scoring", {}).get("oracle_grade", {})
    process = receipt.get("execution", {}).get("agent_terminal") or {}
    source = receipt.get("diagnostic")
    if not isinstance(source, dict):
        source = None
    stage = source.get("stage") if source is not None else None
    reason_code = source.get("reason_code") if source is not None else None
    flags = {
        "contamination": status == "CONTAMINATED",
        "output_contract": grade.get("format_compliant") is False,
        "semantic_ungradeable": grade.get("semantic_gradeable") is False,
        "semantic_incorrect": grade.get("semantic_status") == "INCORRECT",
    }
    raw_reason = str(receipt.get("reason") or "")
    reason = raw_reason.lower()
    if reason_code == "interrupted-launch":
        primary = "runtime"
    elif reason_code == "agent-timeout":
        primary = "timeout"
    elif reason_code == "oracle-invalid":
        primary = "oracle-invalid-or-ambiguous"
    elif reason_code in {
        "agent-terminal-failed",
        "agent-terminal-missing",
        "agent-final-answer-missing",
    }:
        primary = "agent-terminal"
    elif isinstance(reason_code, str) and reason_code.startswith("agent-"):
        primary = "runtime"
    elif flags["contamination"]:
        primary = "contamination"
    elif status == "INVALID" and "oracle" in reason:
        primary = "oracle-invalid-or-ambiguous"
    elif status == "INCOMPLETE" and "timed out" in reason:
        primary = "timeout"
    elif status == "INCOMPLETE" and process.get("type") == "turn.failed":
        primary = "agent-terminal"
    elif status in {"INCOMPLETE", "INVALID"}:
        primary = "runtime"
    elif flags["semantic_incorrect"]:
        primary = "semantic-incorrect"
    elif flags["output_contract"] and not grade.get("semantic_gradeable"):
        primary = "output-contract"
    elif flags["semantic_ungradeable"]:
        primary = "semantic-ungradeable"
    elif status == "PASS":
        primary = "semantic-correct"
    else:
        primary = "semantic-incorrect" if status == "FAIL" else "runtime"
    return {
        "primary": primary,
        "flags": flags,
        "stage": stage,
        "reason_code": reason_code,
        "reason": _bounded_report_reason(raw_reason),
        "location_topology": repository_location_outcome_topology(receipt),
        "diagnostic_source": "receipt" if source is not None else "legacy-inferred",
    }


def _pair_exclusions(
    *,
    expected: dict[str, dict[str, Any]],
    by_definition: dict[str, list[dict[str, Any]]],
    suite: SuiteDefinition,
    interrupted: set[str] | None = None,
    comparison_block_reason: str | None = None,
) -> list[dict[str, Any]]:
    interrupted = interrupted or set()
    conditions = {row["id"]: row for row in suite.experiment["conditions"]}
    groups: dict[
        tuple[str, str, int, str | None, str | None],
        dict[str, dict[str, Any]],
    ] = defaultdict(dict)
    for definition, row in expected.items():
        condition = conditions[row["condition_id"]]
        context_group, context_variant = _condition_context_key(condition)
        key = (
            str(row["task_id"]),
            str(condition["agent"]),
            int(row.get("replicate_id", row.get("seed"))),
            context_group,
            context_variant,
        )
        groups[key][str(row["condition_id"])] = {
            "definition_id": definition,
            "subject_id": str(condition["subject"]),
            "receipt": by_definition.get(definition, [None])[0],
        }
    exclusions = []
    for key, arms in sorted(groups.items()):
        controls = [arm for arm in arms.values() if arm["subject_id"] == "none"]
        if len(controls) > 1:
            raise ReportError(f"multiple bare executions for pair {key}")
        bare = controls[0] if controls else None
        for condition_id, arm in sorted(arms.items()):
            if arm["subject_id"] == "none":
                continue
            left = bare["receipt"] if bare else None
            right = arm["receipt"]
            if (
                left
                and right
                and all(row.get("status") in _VALID_OUTCOMES for row in (left, right))
                and comparison_block_reason is None
            ):
                continue
            exclusions.append(
                {
                    **(
                        {"reason_code": comparison_block_reason}
                        if comparison_block_reason is not None
                        else {}
                    ),
                    "task_id": key[0],
                    "agent_id": key[1],
                    "replicate_id": key[2],
                    "context_group": key[3],
                    "context_variant": key[4],
                    "condition_id": condition_id,
                    "subject_id": arm["subject_id"],
                    "bare_definition_id": bare["definition_id"] if bare else None,
                    "assisted_definition_id": arm["definition_id"],
                    "bare_status": left.get("status")
                    if left
                    else (
                        "INTERRUPTED"
                        if bare and bare["definition_id"] in interrupted
                        else "MISSING"
                    ),
                    "assisted_status": right.get("status")
                    if right
                    else (
                        "INTERRUPTED"
                        if arm["definition_id"] in interrupted
                        else "MISSING"
                    ),
                }
            )
    return exclusions


def _cross_agent_observations(
    receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, int, int, str | None, str | None],
        dict[str, dict[str, Any]],
    ] = defaultdict(dict)
    for receipt in receipts:
        if receipt.get("status") not in _VALID_OUTCOMES:
            continue
        execution = receipt["execution"]
        context_group, context_variant = _receipt_context_key(receipt)
        key = (
            _task_id(receipt),
            _subject_id(receipt),
            int(execution["trial_index"]),
            _replicate_id(receipt),
            context_group,
            context_variant,
        )
        agent = _agent_id(receipt)
        if agent in grouped[key]:
            raise ReportError(
                f"multiple executions for cross-agent observation {key} / {agent}"
            )
        grouped[key][agent] = {
            "status": receipt["status"],
            "duration_ms": _agent_metric(receipt, "duration_ms"),
            "tool_calls": _agent_metric(receipt, "tool_calls"),
            "mcp_calls": _agent_metric(receipt, "mcp_calls"),
            "subject_mcp_calls": _agent_metric(
                receipt,
                "subject_mcp_calls",
            ),
            "input_tokens": _agent_metric(receipt, "input_tokens"),
            "output_tokens": _agent_metric(receipt, "output_tokens"),
            "subject_tool_invoked": (
                receipt.get("measurements", {})
                .get("agent", {})
                .get("subject_tool_invoked")
            ),
        }

    return [
        {
            "task_id": key[0],
            "subject_id": key[1],
            "trial_index": key[2],
            "replicate_id": key[3],
            "context_group": key[4],
            "context_variant": key[5],
            "agents": dict(sorted(agents.items())),
        }
        for key, agents in sorted(
            grouped.items(),
            key=lambda item: tuple(
                "" if value is None else str(value) for value in item[0]
            ),
        )
        if len(agents) > 1
    ]



def _decision_summary(
    *,
    expected: dict[str, dict[str, Any]],
    status_counts: Counter[str],
    campaign_complete: bool,
    invalid_outcomes: int,
    campaign_qualified: bool,
    task_assistance: list[dict[str, Any]],
    subject_adoption: list[dict[str, Any]],
    usage_summary: list[dict[str, Any]],
    treatment_summary: list[dict[str, Any]],
    condition_summaries: dict[str, dict[str, Any]],
    agent_profiles: dict[str, dict[str, Any]],
    context_invariance: list[dict[str, Any]],
    analysis_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Lift decision-critical evidence without creating a ranking authority."""

    selected_task_ids = sorted(
        {
            str(row["task_id"])
            for row in expected.values()
            if isinstance(row.get("task_id"), str)
        }
    )
    evidence_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in task_assistance:
        task_id = row.get("task_id")
        if isinstance(task_id, str):
            evidence_by_task[task_id].append(row)

    headroom_task_ids: list[str] = []
    no_headroom_task_ids: list[str] = []
    unobserved_headroom_task_ids: list[str] = []
    for task_id in selected_task_ids:
        comparable = [
            row
            for row in evidence_by_task.get(task_id, [])
            if int(row.get("comparable_pairs", 0) or 0) > 0
        ]
        if not comparable:
            unobserved_headroom_task_ids.append(task_id)
            continue
        if any(
            int(row.get("bare_semantic", {}).get("failures", 0) or 0) > 0
            for row in comparable
        ):
            headroom_task_ids.append(task_id)
        else:
            no_headroom_task_ids.append(task_id)

    generic_invoked_gain_task_ids = sorted(
        {
            str(row["task_id"])
            for row in task_assistance
            if isinstance(row.get("task_id"), str)
            and int(
                row.get("transitions_by_invocation", {})
                .get("invoked", {})
                .get("gain", 0)
                or 0
            )
            > 0
        }
    )
    generic_invoked_regression_task_ids = sorted(
        {
            str(row["task_id"])
            for row in task_assistance
            if isinstance(row.get("task_id"), str)
            and int(
                row.get("transitions_by_invocation", {})
                .get("invoked", {})
                .get("regression", 0)
                or 0
            )
            > 0
        }
    )
    contracted_gain_task_ids = sorted(
        {
            str(row["task_id"])
            for row in task_assistance
            if isinstance(row.get("task_id"), str)
            and int(
                row.get("transitions_by_contracted_treatment", {})
                .get(CONTRACTED_SUCCESSFUL_RESULT, {})
                .get("gain", 0)
                or 0
            )
            > 0
        }
    )
    contracted_regression_task_ids = sorted(
        {
            str(row["task_id"])
            for row in task_assistance
            if isinstance(row.get("task_id"), str)
            and int(
                row.get("transitions_by_contracted_treatment", {})
                .get(CONTRACTED_SUCCESSFUL_RESULT, {})
                .get("regression", 0)
                or 0
            )
            > 0
        }
    )

    usage_index = {
        (
            str(row.get("agent_id")),
            str(row.get("subject_id")),
            str(row.get("invocation_state")),
        ): row
        for row in usage_summary
        if isinstance(row, dict)
    }

    def treatment_rows(
        agent_id: str,
        subject_id: str,
        observed: bool | None,
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in treatment_summary
            if isinstance(row, dict)
            and str(row.get("agent_id")) == agent_id
            and str(row.get("subject_id")) == subject_id
            and row.get("contracted_treatment_observed") is observed
        ]

    def aggregate_treatment(
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        transitions = Counter()
        state_rows: dict[str, dict[str, Any]] = {}
        pairs = 0
        for row in rows:
            row_pairs = int(row.get("total_pairs", 0) or 0)
            pairs += row_pairs
            state = row.get("contracted_exposure_state")
            if isinstance(state, str):
                state_rows[state] = {
                    "pairs": row_pairs,
                    "transitions": row.get("transitions", {}),
                    "delta_metrics": row.get("delta_metrics", {}),
                }
            row_transitions = row.get("transitions")
            if isinstance(row_transitions, dict):
                for name in ("gain", "preserved", "unresolved", "regression"):
                    transitions[name] += int(row_transitions.get(name, 0) or 0)
        return {
            "pairs": pairs,
            "states": dict(sorted(state_rows.items())),
            "transitions": {
                name: transitions[name]
                for name in ("gain", "preserved", "unresolved", "regression")
            },
            "economics_aggregation": "per-contracted-state-only",
        }

    subject_rows: list[dict[str, Any]] = []
    for adoption in subject_adoption:
        agent_id = str(adoption.get("agent_id"))
        subject_id = str(adoption.get("subject_id"))
        invoked = usage_index.get((agent_id, subject_id, "invoked"), {})
        not_invoked = usage_index.get((agent_id, subject_id, "not-invoked"), {})
        generic_gain_tasks = sorted(
            {
                str(row["task_id"])
                for row in task_assistance
                if row.get("agent_id") == agent_id
                and row.get("subject_id") == subject_id
                and isinstance(row.get("task_id"), str)
                and int(
                    row.get("transitions_by_invocation", {})
                    .get("invoked", {})
                    .get("gain", 0)
                    or 0
                )
                > 0
            }
        )
        generic_regression_tasks = sorted(
            {
                str(row["task_id"])
                for row in task_assistance
                if row.get("agent_id") == agent_id
                and row.get("subject_id") == subject_id
                and isinstance(row.get("task_id"), str)
                and int(
                    row.get("transitions_by_invocation", {})
                    .get("invoked", {})
                    .get("regression", 0)
                    or 0
                )
                > 0
            }
        )
        contracted_gain_tasks = sorted(
            {
                str(row["task_id"])
                for row in task_assistance
                if row.get("agent_id") == agent_id
                and row.get("subject_id") == subject_id
                and isinstance(row.get("task_id"), str)
                and int(
                    row.get("transitions_by_contracted_treatment", {})
                    .get(CONTRACTED_SUCCESSFUL_RESULT, {})
                    .get("gain", 0)
                    or 0
                )
                > 0
            }
        )
        contracted_regression_tasks = sorted(
            {
                str(row["task_id"])
                for row in task_assistance
                if row.get("agent_id") == agent_id
                and row.get("subject_id") == subject_id
                and isinstance(row.get("task_id"), str)
                and int(
                    row.get("transitions_by_contracted_treatment", {})
                    .get(CONTRACTED_SUCCESSFUL_RESULT, {})
                    .get("regression", 0)
                    or 0
                )
                > 0
            }
        )
        observed_treatment = aggregate_treatment(
            treatment_rows(agent_id, subject_id, True)
        )
        definite_non_treatment = aggregate_treatment(
            treatment_rows(agent_id, subject_id, False)
        )
        unproven_treatment = aggregate_treatment(
            treatment_rows(agent_id, subject_id, None)
        )
        subject_rows.append(
            {
                "agent_id": agent_id,
                "subject_id": subject_id,
                "availability": {
                    "trials": int(adoption.get("trials", 0) or 0),
                    "available_trials": int(adoption.get("available_trials", 0) or 0),
                    "configured_trials": int(adoption.get("configured_trials", 0) or 0),
                    "invocation_observed_trials": int(
                        adoption.get("invocation_observed_trials", 0) or 0
                    ),
                    "invoked_trials": int(adoption.get("invoked_trials", 0) or 0),
                    "not_invoked_trials": int(
                        adoption.get("not_invoked_trials", 0) or 0
                    ),
                    "invocation_unknown_trials": int(
                        adoption.get("invocation_unknown_trials", 0) or 0
                    ),
                    "state": adoption.get("state"),
                },
                "generic_subject_use": {
                    "paired_when_invoked": {
                        "pairs": int(invoked.get("total_pairs", 0) or 0),
                        "transitions": invoked.get(
                            "transitions",
                            {
                                "gain": 0,
                                "preserved": 0,
                                "unresolved": 0,
                                "regression": 0,
                            },
                        ),
                        "delta_metrics": invoked.get("delta_metrics", {}),
                    },
                    "paired_when_not_invoked": {
                        "pairs": int(not_invoked.get("total_pairs", 0) or 0),
                        "transitions": not_invoked.get(
                            "transitions",
                            {
                                "gain": 0,
                                "preserved": 0,
                                "unresolved": 0,
                                "regression": 0,
                            },
                        ),
                        "delta_metrics": not_invoked.get("delta_metrics", {}),
                    },
                    "gain_task_ids_when_invoked": generic_gain_tasks,
                    "regression_task_ids_when_invoked": generic_regression_tasks,
                    "claim_scope": "routing-and-adoption-descriptive-only",
                },
                "contracted_treatment": {
                    "observed": observed_treatment,
                    "definite_non_treatment": definite_non_treatment,
                    "unproven": unproven_treatment,
                    "gain_task_ids": contracted_gain_tasks,
                    "regression_task_ids": contracted_regression_tasks,
                    "attribution_scope": (
                        "exact-required-operation-with-successful-nonempty-result"
                    ),
                },
            }
        )

    saturated_conditions = sorted(
        condition_id
        for condition_id, row in condition_summaries.items()
        if row.get("format_contract", {}).get("state")
        == "strict-contract-saturated-noncompliant"
    )
    if not condition_summaries:
        strict_format_state = "not-observed"
    elif len(saturated_conditions) == len(condition_summaries):
        strict_format_state = "saturated-noncompliant"
    elif saturated_conditions:
        strict_format_state = "partially-saturated"
    else:
        strict_format_state = "not-saturated"

    source_read_observability = sorted(
        {
            str(value)
            for row in agent_profiles.values()
            if isinstance(row, dict)
            for value in row.get("source_read_observability", [])
            if isinstance(value, str)
        }
    )
    context_variants = sorted(
        {
            (
                str(row.get("context_group")),
                str(row.get("variant")),
            )
            for row in context_invariance
            if row.get("context_group") is not None
            and row.get("variant") is not None
        }
    )
    context_evidence_state = (
        str(analysis_evidence.get("evidence_state"))
        if isinstance(analysis_evidence, dict)
        and isinstance(analysis_evidence.get("evidence_state"), str)
        else "not-measured"
    )

    condition_metrics = {
        condition_id: {
            "trials": int(row.get("trials", 0) or 0),
            "valid_outcomes": int(row.get("valid_outcomes", 0) or 0),
            "semantic_success": {
                "rate": row.get("semantic_success_rate"),
                "denominator": int(
                    row.get("semantic_success_denominator", 0) or 0
                ),
            },
            "format_compliance": {
                "rate": row.get("format_compliance_rate"),
                "denominator": int(
                    row.get("format_compliance_denominator", 0) or 0
                ),
                "state": row.get("format_contract", {}).get("state"),
            },
            "subject_tool_adoption": {
                "rate": row.get("subject_tool_adoption_rate"),
                "denominator": int(
                    row.get("subject_tool_adoption_denominator", 0) or 0
                ),
            },
        }
        for condition_id, row in sorted(condition_summaries.items())
    }

    selected_task_count = len(selected_task_ids)
    return {
        "campaign": {
            "qualification": (
                "QUALIFIED" if campaign_qualified else "NOT_QUALIFIED"
            ),
            "complete": campaign_complete,
            "invalid_outcomes": invalid_outcomes,
            "status_counts": dict(sorted(status_counts.items())),
            "ranking_permitted": False,
        },
        "headroom": {
            "selected_tasks": selected_task_count,
            "headroom_observable_tasks": (
                len(headroom_task_ids) + len(no_headroom_task_ids)
            ),
            "tasks_with_bare_correctness_headroom": len(headroom_task_ids),
            "tasks_without_bare_correctness_headroom": len(no_headroom_task_ids),
            "tasks_with_unobserved_headroom": len(unobserved_headroom_task_ids),
            "headroom_task_ids": headroom_task_ids,
            "no_headroom_task_ids": no_headroom_task_ids,
            "unobserved_headroom_task_ids": unobserved_headroom_task_ids,
            "generic_subject_use": {
                "gain_task_ids": generic_invoked_gain_task_ids,
                "regression_task_ids": generic_invoked_regression_task_ids,
                "gain_task_fraction": (
                    len(generic_invoked_gain_task_ids) / selected_task_count
                    if selected_task_count
                    else None
                ),
                "claim_scope": "routing-and-adoption-descriptive-only",
            },
            "contracted_treatment": {
                "gain_task_ids": contracted_gain_task_ids,
                "regression_task_ids": contracted_regression_task_ids,
                "gain_task_fraction": (
                    len(contracted_gain_task_ids) / selected_task_count
                    if selected_task_count
                    else None
                ),
                "attribution_scope": (
                    "exact-required-operation-with-successful-nonempty-result"
                ),
            },
        },
        "subjects": subject_rows,
        "condition_metrics": condition_metrics,
        "benchmark_health": {
            "strict_format_state": strict_format_state,
            "strict_format_saturated_conditions": saturated_conditions,
            "source_read_observability": source_read_observability,
            "context_comparisons": len(context_invariance),
            "context_variants": [
                {"group": group, "variant": variant}
                for group, variant in context_variants
            ],
            "context_evidence_state": context_evidence_state,
            "replicate_identity_is_provider_sampling_seed": False,
        },
        "claim_guardrails": {
            "overall_winner": "not-permitted",
            "cross_agent_comparison": "descriptive-only",
            "subject_effect_attribution": (
                "exact-required-operation-with-successful-nonempty-result"
            ),
            "generic_subject_invocation": "routing-and-adoption-descriptive-only",
            "generalization": (
                "context-specific-descriptive-evidence"
                if context_evidence_state == "minimum-evidence-observed"
                else "not-demonstrated"
            ),
        },
    }

def build_report(
    *,
    suite: SuiteDefinition,
    results_root: Path,
    require_complete: bool = True,
    selected_definitions: set[str] | None = None,
    selection: dict[str, Any] | None = None,
    projected_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    receipts = (
        _receipts(results_root) if projected_receipts is None else projected_receipts
    )
    all_expected = {str(row["definition_id"]): row for row in suite.trial_definitions()}
    expected = (
        all_expected
        if selected_definitions is None
        else {
            key: row for key, row in all_expected.items() if key in selected_definitions
        }
    )
    if selected_definitions is not None and set(expected) != selected_definitions:
        raise ReportError("report selection contains definitions outside frozen suite")
    new_contract = any("replicate_id" in row for row in expected.values())
    interrupted: set[str] = set()
    launch_claims: dict[str, str] = {}
    campaign_id = None
    epoch_projection: dict[str, Any] = {
        "evidence_tainted": False,
        "authority_epoch_transitions": 0,
        "authority_epochs": [],
    }
    by_definition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for receipt in receipts:
        definition = receipt.get("definition_id")
        if not isinstance(definition, str):
            raise ReportError("receipt has no definition_id")
        if definition not in all_expected:
            raise ReportError(
                f"results contain execution outside frozen suite: {definition}"
            )
        if definition in expected:
            by_definition[definition].append(receipt)

    receipts = [row for rows in by_definition.values() for row in rows]

    duplicates = {
        definition: values
        for definition, values in by_definition.items()
        if len(values) > 1
    }
    if duplicates:
        raise ReportError(
            "multiple executions found for frozen definition(s): "
            + ", ".join(sorted(duplicates))
        )

    if new_contract:
        try:
            manifest = read_campaign(results_root)
            campaign_id = manifest["campaign_id"]
            if projected_receipts is None and not set(expected).issubset(
                set(manifest["selected_definitions"])
            ):
                raise CampaignAuthorityError("report selection exceeds frozen campaign")
            launch_claims = read_launch_claims(results_root, campaign_id)
            selected_pairs = {
                (str(row["task_id"]), str(row["condition_id"]))
                for row in expected.values()
            }
            epoch_projection = authority_epoch_projection(
                results_root=results_root,
                campaign_id=campaign_id,
                selected_pairs=selected_pairs,
            )
            if not set(launch_claims).issubset(set(manifest["selected_definitions"])):
                raise CampaignAuthorityError(
                    "launch claim exceeds frozen campaign selection"
                )
            interrupted = set(launch_claims)
        except CampaignAuthorityError as exc:
            raise ReportError(str(exc)) from exc
        for receipt in receipts:
            if (
                receipt.get("definition_id") in expected
                and receipt.get("execution", {}).get("campaign_id") != campaign_id
            ):
                raise ReportError("receipt belongs to a different campaign authority")
            if projected_receipts is None and launch_claims.get(
                receipt["definition_id"]
            ) != receipt.get("trial_id"):
                raise ReportError("receipt has no matching launch claim")

    missing = sorted(set(expected) - set(by_definition))
    if require_complete and missing:
        raise ReportError(
            f"campaign is incomplete: {len(missing)} frozen definition(s) missing"
        )

    participant_authority_tainted = bool(
        epoch_projection["evidence_tainted"]
    )
    validate_comparability(
        receipts,
        allow_participant_authority_mix=participant_authority_tainted,
    )

    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for receipt in receipts:
        by_condition[_condition_id(receipt)].append(receipt)
        by_agent[_agent_id(receipt)].append(receipt)

    statuses = Counter(str(row.get("status")) for row in receipts)
    invalid_outcomes = sum(
        count
        for status, count in statuses.items()
        if status not in _VALID_OUTCOMES
    )
    campaign_complete = not missing
    exposure_qualification = subject_exposure_qualification(
        suite=suite,
        expected_rows=expected.values(),
        receipts=receipts,
    )
    campaign_qualified = (
        campaign_complete
        and invalid_outcomes == 0
        and exposure_qualification["qualified"]
        and not participant_authority_tainted
    )
    comparison_block_reason = (
        "participant-authority-epoch-taint"
        if participant_authority_tainted
        else None
    )
    paired_assistance = (
        []
        if participant_authority_tainted
        else _paired_assistance(receipts)
    )
    stability = (
        []
        if participant_authority_tainted
        else _stability(receipts, expected=expected, suite=suite)
    )
    context_invariance = (
        []
        if participant_authority_tainted
        else _counterfactual_context(receipts)
    )
    context_invariance_summary = _counterfactual_context_summary(context_invariance)
    analysis_evidence = _analysis_evidence(
        suite=suite,
        comparisons=context_invariance,
    )
    pair_exclusions = _pair_exclusions(
        expected=expected,
        by_definition=by_definition,
        suite=suite,
        interrupted=interrupted,
        comparison_block_reason=comparison_block_reason,
    )
    paired_assistance_summary = _paired_assistance_summary(paired_assistance)
    paired_assistance_usage_summary = _paired_assistance_usage_summary(
        paired_assistance
    )
    paired_assistance_treatment_summary = _paired_assistance_treatment_summary(
        paired_assistance
    )
    task_assistance_evidence = _task_assistance_evidence(
        paired_assistance,
        pair_exclusions,
    )
    subject_adoption = _subject_adoption_summary(receipts)
    condition_summaries = {
        condition: _aggregate_condition(rows)
        for condition, rows in sorted(by_condition.items())
    }
    agent_profiles = {
        agent: _aggregate_condition(rows)
        for agent, rows in sorted(by_agent.items())
    }
    decision_summary = _decision_summary(
        expected=expected,
        status_counts=statuses,
        campaign_complete=campaign_complete,
        invalid_outcomes=invalid_outcomes,
        campaign_qualified=campaign_qualified,
        task_assistance=task_assistance_evidence,
        subject_adoption=subject_adoption,
        usage_summary=paired_assistance_usage_summary,
        treatment_summary=paired_assistance_treatment_summary,
        condition_summaries=condition_summaries,
        agent_profiles=agent_profiles,
        context_invariance=context_invariance,
        analysis_evidence=analysis_evidence,
    )
    return {
        "schema": {
            "name": "agents-cookbook-benchmark-report",
            "version": 21,
        },
        "suite": suite.experiment["suite"],
        "experiment": {
            "id": suite.experiment["id"],
            "version": suite.experiment["version"],
        },
        "selection": selection
        or {
            "tasks": [],
            "agents": [],
            "subjects": [],
            "condition": None,
            "bare_control_included": False,
        },
        "expected_trials": len(expected),
        "observed_trials": len(receipts),
        "missing_definitions": missing,
        "interrupted_definitions": sorted(interrupted & set(missing)),
        "status_counts": dict(sorted(statuses.items())),
        "conditions": condition_summaries,
        "paired_assistance": paired_assistance,
        "paired_assistance_summary": paired_assistance_summary,
        "paired_assistance_usage_summary": paired_assistance_usage_summary,
        "paired_assistance_treatment_summary": paired_assistance_treatment_summary,
        "paired_assistance_exclusions": pair_exclusions,
        "expected_assistance_pairs": len(paired_assistance) + len(pair_exclusions),
        "task_assistance_evidence": task_assistance_evidence,
        "stability": stability,
        "context_invariance": context_invariance,
        "context_invariance_summary": context_invariance_summary,
        "analysis_evidence": analysis_evidence,
        "task_agent_authority": (
            []
            if participant_authority_tainted
            else _task_agent_authority(receipts)
        ),
        "subject_adoption": subject_adoption,
        "diagnostics": [
            {
                "definition_id": row["definition_id"],
                "trial_id": row["trial_id"],
                "task_id": _task_id(row),
                "agent_id": _agent_id(row),
                "subject_id": _subject_id(row),
                **_diagnostic(row),
            }
            for row in sorted(receipts, key=lambda item: item["definition_id"])
        ],
        "agent_profiles": agent_profiles,
        "decision_summary": decision_summary,
        "cross_agent_observations": (
            []
            if participant_authority_tainted
            else _cross_agent_observations(receipts)
        ),
        "comparative_analysis": {
            "status": (
                "SUPPRESSED"
                if participant_authority_tainted
                else "AVAILABLE"
            ),
            "reason_code": comparison_block_reason,
            "suppressed_surfaces": (
                [
                    "paired_assistance",
                    "stability",
                    "context_invariance",
                    "cross_agent_observations",
                    "task_agent_authority",
                ]
                if participant_authority_tainted
                else []
            ),
        },
        "campaign_qualification": {
            "status": "QUALIFIED" if campaign_qualified else "NOT_QUALIFIED",
            "complete": campaign_complete,
            "invalid_outcomes": invalid_outcomes,
            "mixed_execution_authority": participant_authority_tainted,
            "mixed_localization_scoring_policy": False,
            "evidence_tainted": participant_authority_tainted,
            "authority_epoch_transitions": epoch_projection[
                "authority_epoch_transitions"
            ],
            "authority_epochs": epoch_projection["authority_epochs"],
            "subject_exposure": exposure_qualification,
        },
        "authority": {
            "overall_winner": None,
            "ranking_performed": False,
            "mixed_execution_definitions_rejected": True,
            "invalid_outcomes_excluded_from_success_rates": True,
            "economics_include_invalid_and_incomplete_trials": True,
            "paired_assistance_scope": "valid-outcomes-only",
            "paired_treatment_attribution": (
                "exact-contracted-operation-with-usable-result"
            ),
            "subject_exposure_qualification": (
                "mandatory-for-selected-non-control-conditions"
            ),
            "participant_authority_epoch_policy": (
                "any-transition-blocks-qualification-and-comparative-analysis"
            ),
            "replicate_identity_field": (
                "execution.replicate_id"
                if any("replicate_id" in row["execution"] for row in receipts)
                else "execution.seed"
            ),
            "replicate_identity_is_provider_sampling_seed": False,
            "cross_agent_rows_are_descriptive": True,
            "context_comparison": "within-agent-subject-paired-replicates",
        },
    }
