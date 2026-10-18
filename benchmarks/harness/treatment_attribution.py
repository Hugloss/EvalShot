"""Contracted MCP treatment attribution from canonical trial receipts."""

from __future__ import annotations

from typing import Any


CONTRACTED_SUCCESSFUL_RESULT = "contracted-successful-result"
OTHER_SUBJECT_OPERATION = "other-subject-operation"
CONTRACTED_NO_USABLE_RESULT = "contracted-no-usable-result"
CONTRACTED_RESULT_UNPROVEN = "contracted-result-unproven"
REQUIRED_OPERATION_INVOCATION_UNPROVEN = "required-operation-invocation-unproven"
SUBJECT_NOT_INVOKED = "subject-not-invoked"
INVOCATION_UNKNOWN = "invocation-unknown"
NO_CONTRACT = "no-contract"

_CONTRACTED_FALSE_STATES = frozenset(
    {
        OTHER_SUBJECT_OPERATION,
        CONTRACTED_NO_USABLE_RESULT,
        SUBJECT_NOT_INVOKED,
    }
)
_CONTRACTED_UNKNOWN_STATES = frozenset(
    {
        CONTRACTED_RESULT_UNPROVEN,
        REQUIRED_OPERATION_INVOCATION_UNPROVEN,
        INVOCATION_UNKNOWN,
        NO_CONTRACT,
    }
)


def _contract(receipt: dict[str, Any]) -> tuple[str, str] | None:
    condition = receipt.get("condition")
    if not isinstance(condition, dict):
        return None
    subject = condition.get("subject_definition")
    if not isinstance(subject, dict):
        return None
    subject_id = subject.get("id")
    probe = subject.get("exposure_probe")
    if (
        not isinstance(subject_id, str)
        or not subject_id
        or not isinstance(probe, dict)
    ):
        return None
    required_tool = probe.get("required_tool")
    prefix = subject_id + "_"
    if (
        not isinstance(required_tool, str)
        or not required_tool.startswith(prefix)
        or len(required_tool) <= len(prefix)
    ):
        return None
    return required_tool, required_tool[len(prefix) :]


def contracted_treatment_evidence(receipt: dict[str, Any]) -> dict[str, Any]:
    """Classify whether this trial actually received the frozen MCP treatment."""
    contract = _contract(receipt)
    if contract is None:
        return {
            "required_tool": None,
            "required_operation": None,
            "state": NO_CONTRACT,
            "treatment_observed": None,
            "attribution_interpretation": "contract-not-declared",
        }

    required_tool, required_operation = contract
    agent = receipt.get("measurements", {}).get("agent", {})
    if not isinstance(agent, dict):
        agent = {}

    invoked = agent.get("subject_tool_invoked")
    if invoked is False:
        state = SUBJECT_NOT_INVOKED
    elif invoked is not True:
        state = INVOCATION_UNKNOWN
    else:
        names = agent.get("subject_tool_names")
        observed_names = (
            {
                name
                for name in names
                if isinstance(name, str) and name
            }
            if isinstance(names, list)
            else set()
        )
        name_observability = agent.get("subject_tool_observability")
        if required_operation not in observed_names:
            state = (
                OTHER_SUBJECT_OPERATION
                if name_observability == "complete"
                else REQUIRED_OPERATION_INVOCATION_UNPROVEN
            )
        else:
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
            outcomes = {
                str(row["outcome"])
                for row in matching
                if isinstance(row.get("outcome"), str)
            }
            if "successful-result-observed" in outcomes:
                state = CONTRACTED_SUCCESSFUL_RESULT
            elif (
                name_observability == "complete"
                and matching
                and outcomes
                and outcomes <= {"failed", "empty-result"}
            ):
                state = CONTRACTED_NO_USABLE_RESULT
            else:
                state = CONTRACTED_RESULT_UNPROVEN

    if state == CONTRACTED_SUCCESSFUL_RESULT:
        treatment_observed: bool | None = True
        interpretation = "contracted-treatment-observed"
    elif state in _CONTRACTED_FALSE_STATES:
        treatment_observed = False
        interpretation = {
            OTHER_SUBJECT_OPERATION: (
                "subject-use-observed-without-contracted-operation"
            ),
            CONTRACTED_NO_USABLE_RESULT: (
                "contracted-operation-without-usable-result"
            ),
            SUBJECT_NOT_INVOKED: "not-attributable-to-subject-tool",
        }[state]
    elif state in _CONTRACTED_UNKNOWN_STATES:
        treatment_observed = None
        interpretation = {
            CONTRACTED_RESULT_UNPROVEN: "contracted-operation-result-unproven",
            REQUIRED_OPERATION_INVOCATION_UNPROVEN: (
                "contracted-operation-invocation-unproven"
            ),
            INVOCATION_UNKNOWN: "invocation-unknown",
            NO_CONTRACT: "contract-not-declared",
        }[state]
    else:
        raise AssertionError(f"unhandled contracted treatment state: {state}")

    return {
        "required_tool": required_tool,
        "required_operation": required_operation,
        "state": state,
        "treatment_observed": treatment_observed,
        "attribution_interpretation": interpretation,
    }
