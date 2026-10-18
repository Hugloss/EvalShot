"""Select frozen benchmark definitions consistently across CLI surfaces."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from benchmarks.harness.suite import SuiteDefinition


class SelectionError(ValueError):
    pass


def parse_agent_arguments(values: Sequence[str]) -> tuple[str, ...]:
    """Accept repeated --agent values and comma-separated Make selections."""
    agents: list[str] = []
    for value in values:
        for part in value.split(","):
            agent = part.strip()
            if not agent:
                raise SelectionError("benchmark agent selection contains an empty ID")
            if agent in agents:
                raise SelectionError(f"duplicate benchmark agent: {agent}")
            agents.append(agent)
    return tuple(agents)


def select_definitions(
    suite: SuiteDefinition,
    *,
    tasks: tuple[str, ...] = (),
    agents: tuple[str, ...] = (),
    subjects: tuple[str, ...] = (),
    condition: str | None = None,
) -> list[dict[str, Any]]:
    if condition and subjects:
        raise SelectionError("--condition cannot be combined with --subject")
    for label, requested, known in (
        ("task", tasks, suite.tasks),
        ("agent", agents, suite.agents),
        ("subject", subjects, suite.subjects),
    ):
        unknown = sorted(set(requested) - set(known))
        if unknown:
            raise SelectionError(f"unknown {label} ID(s): {', '.join(unknown)}")
    conditions = {row["id"]: row for row in suite.experiment["conditions"]}
    if condition and condition not in conditions:
        raise SelectionError(f"unknown condition ID: {condition}")
    if (
        condition
        and agents
        and (len(agents) != 1 or agents[0] != conditions[condition]["agent"])
    ):
        raise SelectionError(
            f"--condition {condition} requires its matching single --agent "
            f"{conditions[condition]['agent']}"
        )

    chosen_subjects = set(subjects)
    rows = []
    for row in suite.trial_definitions():
        frozen = conditions[row["condition_id"]]
        if tasks and row["task_id"] not in tasks:
            continue
        if condition and row["condition_id"] != condition:
            continue
        if agents and frozen["agent"] not in agents:
            continue
        if chosen_subjects and frozen["subject"] not in chosen_subjects:
            continue
        rows.append(row)
    if not rows:
        raise SelectionError("no trials matched the requested filters")
    represented_agents = {str(conditions[row["condition_id"]]["agent"]) for row in rows}
    missing_agents = sorted(set(agents) - represented_agents)
    if missing_agents:
        raise SelectionError(
            "no trials matched selected agent(s): " + ", ".join(missing_agents)
        )
    return rows


def select_scoring_definitions(
    suite: SuiteDefinition,
    *,
    agents: tuple[str, ...],
    definition_ids: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Bind scoring to exact frozen definitions when campaign authority provides them."""
    if not agents:
        raise SelectionError("select at least one benchmark agent")
    if len(set(definition_ids)) != len(definition_ids):
        raise SelectionError("duplicate benchmark definition ID in score selection")

    if not definition_ids:
        return select_definitions(suite, agents=agents)

    conditions = {str(row["id"]): row for row in suite.experiment["conditions"]}
    rows_by_id = {
        str(row["definition_id"]): row
        for row in suite.trial_definitions()
    }
    unknown = sorted(set(definition_ids) - set(rows_by_id))
    if unknown:
        raise SelectionError(
            "unknown benchmark definition ID(s): " + ", ".join(unknown)
        )

    requested = set(definition_ids)
    rows = [
        row
        for definition_id, row in rows_by_id.items()
        if definition_id in requested
    ]
    represented_agents = {
        str(conditions[str(row["condition_id"])]["agent"])
        for row in rows
    }
    unexpected_agents = sorted(represented_agents - set(agents))
    if unexpected_agents:
        raise SelectionError(
            "score selection contains definition(s) for unselected agent(s): "
            + ", ".join(unexpected_agents)
        )
    missing_agents = sorted(set(agents) - represented_agents)
    if missing_agents:
        raise SelectionError(
            "score selection contains no definition for selected agent(s): "
            + ", ".join(missing_agents)
        )
    return rows
