"""Frozen benchmark-suite loading and semantic validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.harness.identity import definition_id
from benchmarks.harness.schema_validation import (
    SchemaValidationError,
    validate_instance,
)


class SuiteError(ValueError):
    pass


def effective_prompt(task: dict[str, Any], condition: dict[str, Any]) -> str:
    """Resolve the exact prompt from frozen task + declared context once."""
    prompt = str(task["prompt"])
    context = condition.get("context")
    if not isinstance(context, dict):
        return prompt
    suffix = str(context.get("prompt_suffix", "")).strip()
    if not suffix:
        return prompt
    return f"Declared evaluation context:\n{suffix}\n\n{prompt}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot load JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SuiteError(f"{path} must contain one JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_definition(
    value: dict[str, Any],
    *,
    schema_name: str,
    source: Path,
) -> None:
    schema_path = (
        Path(__file__).resolve().parents[1] / "schema" / f"{schema_name}.schema.json"
    )
    schema = _load_json(schema_path)
    try:
        validate_instance(value, schema)
    except SchemaValidationError as exc:
        raise SuiteError(f"{source}: schema validation failed: {exc}") from exc


@dataclass(frozen=True)
class SuiteDefinition:
    root: Path
    experiment: dict[str, Any]
    tasks: dict[str, dict[str, Any]]
    subjects: dict[str, dict[str, Any]]
    agents: dict[str, dict[str, Any]]

    def expanded_condition(self, condition: dict[str, Any]) -> dict[str, Any]:
        subject_id = str(condition["subject"])
        agent_id = str(condition["agent"])
        return {
            **condition,
            "subject_definition": self.subjects[subject_id],
            "agent_definition": self.agents[agent_id],
        }

    def trial_definitions(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task_id in self.experiment["tasks"]:
            task = self.tasks[str(task_id)]
            for condition in self.experiment["conditions"]:
                expanded = self.expanded_condition(condition)
                for trial in range(int(condition["trials"])):
                    identity = (
                        {"replicate_id": condition["replicate_ids"][trial]}
                        if "replicate_ids" in condition
                        else {"seed": int(condition["seed"]) + trial}
                    )
                    rows.append(
                        {
                            "task_id": task_id,
                            "condition_id": condition["id"],
                            "trial": trial,
                            **identity,
                            "definition_id": definition_id(
                                experiment=self.experiment,
                                task=task,
                                condition=expanded,
                                trial=trial,
                                **identity,
                            ),
                        }
                    )
        return rows


def _load_experiment(root: Path) -> dict[str, Any]:
    experiment_path = root / "experiment.json"
    experiment = _load_json(experiment_path)
    _validate_definition(
        experiment,
        schema_name="experiment",
        source=experiment_path,
    )
    return experiment


def _load_named_definitions(
    root: Path,
    directory: str,
    schema_name: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((root / directory).glob("*.json")):
        value = _load_json(path)
        _validate_definition(value, schema_name=schema_name, source=path)
        definition_id = str(value.get("id", ""))
        if not definition_id or definition_id in result:
            raise SuiteError(f"invalid or duplicate {schema_name} id in {path}")
        result[definition_id] = value
    return result


def _validate_participant_references(
    experiment: dict[str, Any],
    subjects: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
) -> None:
    for condition in experiment.get("conditions", []):
        if condition.get("subject") not in subjects:
            raise SuiteError(
                f"condition {condition.get('id')} references missing subject"
            )
        if condition.get("agent") not in agents:
            raise SuiteError(
                f"condition {condition.get('id')} references missing agent"
            )
        if int(condition.get("trials", 0)) < 1:
            raise SuiteError(f"condition {condition.get('id')} has no trials")
        if "replicate_ids" in condition:
            ids = condition["replicate_ids"]
            if len(ids) != condition["trials"] or len(set(ids)) != len(ids):
                raise SuiteError(
                    f"condition {condition.get('id')} has invalid replicate_ids"
                )
        elif "seed" not in condition:
            raise SuiteError(
                f"condition {condition.get('id')} has no replicate identity"
            )
    grouped: dict[str, list[int]] = {}
    for condition in experiment.get("conditions", []):
        if "replicate_ids" not in condition:
            continue
        agent = str(condition["agent"])
        ids = condition["replicate_ids"]
        if agent in grouped and grouped[agent] != ids:
            raise SuiteError(f"agent {agent} has unpaired replicate_ids")
        grouped[agent] = ids


def _condition_replicates(condition: dict[str, Any]) -> tuple[int, ...]:
    if "replicate_ids" in condition:
        return tuple(int(value) for value in condition["replicate_ids"])
    seed = int(condition["seed"])
    return tuple(seed + index for index in range(int(condition["trials"])))


def _validate_context_conditions(experiment: dict[str, Any]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for condition in experiment.get("conditions", []):
        context = condition.get("context")
        if context is None:
            continue
        if not isinstance(context, dict):
            raise SuiteError(f"condition {condition.get('id')} has invalid context")
        key = (
            str(context["group"]),
            str(condition["agent"]),
            str(condition["subject"]),
        )
        groups.setdefault(key, []).append(condition)

    for key, conditions in groups.items():
        if len(conditions) < 2:
            raise SuiteError(
                f"context group {key[0]} / {key[1]} / {key[2]} requires a counterfactual arm"
            )
        variants = [str(row["context"]["variant"]) for row in conditions]
        if len(set(variants)) != len(variants):
            raise SuiteError(
                f"context group {key[0]} / {key[1]} / {key[2]} has duplicate variants"
            )
        neutral = [row for row in conditions if row["context"]["kind"] == "neutral"]
        if len(neutral) != 1:
            raise SuiteError(
                f"context group {key[0]} / {key[1]} / {key[2]} requires one neutral arm"
            )
        baseline = neutral[0]
        baseline_replicates = _condition_replicates(baseline)
        for condition in conditions:
            if int(condition["trials"]) != int(baseline["trials"]):
                raise SuiteError(
                    f"context group {key[0]} / {key[1]} / {key[2]} has unpaired trials"
                )
            if _condition_replicates(condition) != baseline_replicates:
                raise SuiteError(
                    f"context group {key[0]} / {key[1]} / {key[2]} has unpaired replicate ids"
                )

    analysis = experiment.get("analysis_contract")
    if analysis is not None:
        if analysis.get("cross_agent_ranking") is not False:
            raise SuiteError("analysis_contract must keep cross_agent_ranking false")
        minimum = int(analysis["minimum_replicates"])
        contextual = [
            condition
            for condition in experiment.get("conditions", [])
            if isinstance(condition.get("context"), dict)
        ]
        if contextual and any(int(row["trials"]) < minimum for row in contextual):
            raise SuiteError(
                "context condition has fewer trials than analysis_contract minimum_replicates"
            )


def load_runtime_suite(root: Path) -> SuiteDefinition:
    """Load only experiment participant authority for runtime readiness."""
    root = root.resolve()
    experiment = _load_experiment(root)
    subjects = _load_named_definitions(root, "subjects", "subject")
    agents = _load_named_definitions(root, "agents", "agent")
    if not subjects or not agents:
        raise SuiteError("runtime suite requires subjects and agents")
    _validate_participant_references(experiment, subjects, agents)
    _validate_context_conditions(experiment)
    return SuiteDefinition(
        root=root,
        experiment=experiment,
        tasks={},
        subjects=subjects,
        agents=agents,
    )


def load_suite(root: Path) -> SuiteDefinition:
    runtime = load_runtime_suite(root)
    root = runtime.root
    experiment = runtime.experiment
    subjects = runtime.subjects
    agents = runtime.agents
    tasks = _load_named_definitions(root, "tasks", "task")

    if not tasks:
        raise SuiteError("suite requires tasks")

    for task_id in experiment.get("tasks", []):
        if str(task_id) not in tasks:
            raise SuiteError(f"experiment references missing task: {task_id}")

    for task in tasks.values():
        repository = task.get("repository", {})
        for field in ("commit", "tree"):
            value = str(repository.get(field, ""))
            if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
                raise SuiteError(f"task {task['id']} has invalid repository {field}")
        mutation = task.get("mutation")
        if mutation is not None:
            artifact = (root / str(mutation["artifact"])).resolve()
            try:
                artifact.relative_to(root)
            except ValueError as exc:
                raise SuiteError(
                    f"task {task['id']} mutation escapes suite root"
                ) from exc
            if not artifact.is_file():
                raise SuiteError(f"task {task['id']} mutation artifact missing")
            actual = _sha256(artifact)
            if actual != mutation["sha256"]:
                raise SuiteError(
                    f"task {task['id']} mutation checksum mismatch: {actual}"
                )
            if not mutation.get("changed_paths"):
                raise SuiteError(
                    f"task {task['id']} mutation must freeze changed_paths"
                )
        targets: set[str] = set()
        for fixture in task.get("fixtures", []):
            artifact = (root / str(fixture["artifact"])).resolve()
            try:
                artifact.relative_to(root)
            except ValueError as exc:
                raise SuiteError(
                    f"task {task['id']} fixture escapes suite root"
                ) from exc
            if not artifact.is_file() or _sha256(artifact) != fixture["sha256"]:
                raise SuiteError(
                    f"task {task['id']} fixture missing or checksum mismatch"
                )
            target = Path(str(fixture["target"]))
            if target.is_absolute() or ".." in target.parts or str(target) in targets:
                raise SuiteError(
                    f"task {task['id']} has unsafe or duplicate fixture target"
                )
            targets.add(str(target))
        contamination = task.get("contamination")
        if not isinstance(contamination, dict):
            raise SuiteError(f"task {task['id']} must define contamination allowances")
        if "allowed_change_globs" not in contamination:
            raise SuiteError(f"task {task['id']} must define allowed_change_globs")
        if "allowed_generated_globs" not in contamination:
            raise SuiteError(f"task {task['id']} must define allowed_generated_globs")

    suite = SuiteDefinition(
        root=root,
        experiment=experiment,
        tasks=tasks,
        subjects=subjects,
        agents=agents,
    )
    if (
        "oracle_reviews" in experiment
        or (root / "qualification" / "oracle-reviews.json").is_file()
    ):
        from .oracle_reviews import (
            OracleReviewError,
            oracle_reviews_declared,
            validate_oracle_reviews,
        )

        try:
            if oracle_reviews_declared(suite):
                validate_oracle_reviews(suite, require_complete=False)
        except OracleReviewError as exc:
            raise SuiteError(str(exc)) from exc
    return suite
