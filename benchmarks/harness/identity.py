"""Stable identities for frozen benchmark definitions and observed executions."""

from __future__ import annotations

import hashlib
import json
from typing import Any


EXECUTION_EVIDENCE_CONTRACT = "benchmark-execution-evidence.v2"
SCORE_PROJECTION_CONTRACT = "benchmark-score-projection.v2"
REPLICATE_EVIDENCE_CONTRACT = "benchmark-execution-evidence.v3"
REPLICATE_SCORE_CONTRACT = "benchmark-score-projection.v3"


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def definition_id(
    *,
    experiment: dict[str, Any],
    task: dict[str, Any],
    condition: dict[str, Any],
    trial: int,
    seed: int | None = None,
    replicate_id: int | None = None,
) -> str:
    if (seed is None) == (replicate_id is None):
        raise ValueError("provide exactly one legacy seed or replicate_id")
    return digest(
        {
            "experiment": experiment,
            "task": task,
            "condition": condition,
            "trial": trial,
            **(
                {"seed": seed}
                if seed is not None
                else {
                    "identity_contract": "benchmark-definition.v2",
                    "replicate_id": replicate_id,
                }
            ),
        }
    )


def execution_id(
    *,
    definition: str,
    subject_identity: dict[str, Any],
    agent_identity: dict[str, Any],
    oracle_identity: dict[str, Any],
    harness_identity: dict[str, Any],
    environment_identity: dict[str, Any],
    mutation_identity: dict[str, Any] | None,
) -> str:
    return digest(
        {
            "definition_id": definition,
            "subject": subject_identity,
            "agent": agent_identity,
            "oracle": oracle_identity,
            "harness": harness_identity,
            "environment": environment_identity,
            "mutation": mutation_identity,
        }
    )


def execution_task_contract(task: dict[str, Any]) -> dict[str, Any]:
    """Task inputs that can affect an agent execution, excluding score authority."""
    return {
        key: task[key]
        for key in (
            "id",
            "family",
            "repository",
            "prompt",
            "mode",
            "mutation",
            "fixtures",
            "budgets",
            "contamination",
        )
        if key in task
    }


def execution_evidence_id(
    *,
    task: dict[str, Any],
    condition: dict[str, Any],
    trial: int,
    seed: int | None = None,
    replicate_id: int | None = None,
    subject_identity: dict[str, Any],
    agent_identity: dict[str, Any],
    harness_identity: dict[str, Any],
    environment_identity: dict[str, Any],
    mutation_identity: dict[str, Any] | None,
    agent_answer: str | None,
    workspace_root: str,
    location_observation: dict[str, Any] | None,
    agent_trace_sha256: str,
    campaign_id: str | None = None,
    admitted_state_sha256: str | None = None,
) -> str:
    """Identify frozen execution evidence independently of scoring authority."""
    if (seed is None) == (replicate_id is None):
        raise ValueError("provide exactly one legacy seed or replicate_id")
    if replicate_id is not None and not campaign_id:
        raise ValueError("replicate evidence requires campaign_id")
    if replicate_id is not None and not admitted_state_sha256:
        raise ValueError("replicate evidence requires admitted_state_sha256")
    return digest(
        {
            "contract": (
                EXECUTION_EVIDENCE_CONTRACT
                if seed is not None
                else REPLICATE_EVIDENCE_CONTRACT
            ),
            "task": execution_task_contract(task),
            "condition": condition,
            "trial": trial,
            **({"seed": seed} if seed is not None else {"replicate_id": replicate_id}),
            **(
                {}
                if seed is not None
                else {
                    "campaign_id": campaign_id,
                    "admitted_state_sha256": admitted_state_sha256,
                }
            ),
            "subject": subject_identity,
            "agent": agent_identity,
            "harness": harness_identity,
            "environment": environment_identity,
            "mutation": mutation_identity,
            "agent_answer": agent_answer,
            "workspace_root": workspace_root,
            "location_observation": location_observation,
            "agent_trace_sha256": agent_trace_sha256,
        }
    )


def score_projection_id(
    *,
    execution_evidence: str,
    oracle_identity: dict[str, Any],
    contract: str = SCORE_PROJECTION_CONTRACT,
) -> str:
    """Identify one deterministic score projection over frozen execution evidence."""
    return digest(
        {
            "contract": contract,
            "execution_evidence_id": execution_evidence,
            "oracle": oracle_identity,
        }
    )
