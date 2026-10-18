"""One committed selector for benchmark trial populations and backends."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.harbor_matrix import load_matrix, mode_contract, validate_projection
from benchmarks.harness.identity import digest
from benchmarks.harness.suite import SuiteDefinition, load_suite


REGISTRY = Path(__file__).with_name("matrices.json")


class MatrixProfileError(ValueError):
    pass


@dataclass(frozen=True)
class MatrixProfile:
    name: str
    backend: str
    identity: str
    root: Path | None
    projection: Path | None = None
    mode: str | None = None


def load_profile(name: str) -> MatrixProfile:
    try:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixProfileError(f"cannot load benchmark matrix registry: {exc}") from exc
    if not isinstance(registry, dict) or registry.get("schema") != "agentscookbook.benchmark-matrices.v1":
        raise MatrixProfileError("invalid benchmark matrix registry")
    profiles = registry.get("profiles")
    profile = profiles.get(name) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        choices = ", ".join(sorted(profiles)) if isinstance(profiles, dict) else "none"
        raise MatrixProfileError(f"unknown matrix {name!r}; available: {choices}")
    backend = profile.get("backend")
    if backend not in {"native", "harbor"}:
        raise MatrixProfileError(f"invalid backend for matrix {name}")
    projection = Path(profile["projection"]) if backend == "harbor" else None
    mode = str(profile["mode"]) if backend == "harbor" else None
    root = Path(profile["root"]) if backend == "harbor" else None
    return MatrixProfile(name, backend, digest({"name": name, "profile": profile}), root, projection, mode)


def harbor_suite(profile: MatrixProfile) -> tuple[SuiteDefinition, dict[str, Any]]:
    """Project one Harbor mode into the ordinary frozen definition model."""
    if profile.backend != "harbor" or profile.projection is None or profile.mode is None:
        raise MatrixProfileError("selected matrix is not a Harbor profile")
    projection = load_matrix(profile.projection)
    source = load_suite(Path(str(projection["suite"])))
    mode = mode_contract(projection, profile.mode)
    harnesses = tuple(str(value) for value in projection["harnesses"])
    tasks = mode.tasks
    validate_projection(
        matrix=projection,
        suite=source,
        mode=mode,
        harnesses=harnesses,
        tasks=tasks,
    )
    subjects = tuple(str(value) for value in projection["subjects"])
    conditions = [
        {
            "id": f"{subject}-{harness}",
            "subject": subject,
            "agent": harness,
            "trials": mode.attempts,
            "replicate_ids": list(range(1, mode.attempts + 1)),
        }
        for harness in harnesses
        for subject in subjects
    ]
    experiment = {
        "id": f"harbor-{profile.name}",
        "version": 1,
        "suite": source.experiment["suite"],
        "tasks": list(tasks),
        "conditions": conditions,
        "scoring": {
            "id": "harbor-reward-v1",
            "version": 1,
            "metrics": ["reward"],
        },
        "matrix_profile_identity": profile.identity,
        "projection_identity": digest(projection),
    }
    suite = SuiteDefinition(
        root=source.root,
        experiment=experiment,
        tasks={task: source.tasks[task] for task in tasks},
        subjects={
            subject: (
                source.subjects[subject]
                if subject in source.subjects
                else {
                    **source.subjects["hashmarks"],
                    "id": subject,
                    "identity": {
                        **source.subjects["hashmarks"]["identity"],
                        "id": subject,
                    },
                    "configuration": {
                        **source.subjects["hashmarks"].get("configuration", {}),
                    },
                }
            )
            for subject in subjects
        },
        agents={
            harness: {"id": harness, "adapter": "harbor", "configuration": {"harness": harness}}
            for harness in harnesses
        },
    )
    return suite, projection
