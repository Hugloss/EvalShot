"""Prepare separate, tool-routing diagnostic suites."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.config import BenchmarkConfig
from benchmarks.harness.identity import canonical_json, digest
from benchmarks.harness.oracle_reviews import oracle_review_path, validate_oracle_reviews
from benchmarks.harness.subject_exposure import exposure_probe_required_tool
from benchmarks.harness.suite import load_suite


class ToolProbeError(ValueError):
    pass


DEFAULT_TASKS = (
    "locate-prefix-path-enumerator",
    "locate-directory-pruning",
    "locate-resource-invalidation",
)


def _task_file(suite_root: Path, task_id: str) -> Path:
    return suite_root / "tasks" / f"{task_id}.json"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_tool_probe_suite(
    *, source_suite: Path, destination: Path, subject: str,
    task_ids: tuple[str, ...] = DEFAULT_TASKS,
    runtime_env_file: Path | None = None,
    reuse: bool = False,
) -> dict[str, Any]:
    suite = load_suite(source_suite)
    validate_oracle_reviews(suite, require_complete=True)
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ToolProbeError("select unique tool-probe tasks")
    unknown = set(task_ids) - set(suite.tasks)
    if unknown:
        raise ToolProbeError("unknown tool-probe tasks: " + ", ".join(sorted(unknown)))
    for task_id in task_ids:
        if suite.tasks[task_id]["oracle"]["adapter"] != "repository-location-json":
            raise ToolProbeError(f"tool-probe task needs a location oracle: {task_id}")
    destination = destination.resolve()
    required_tool = exposure_probe_required_tool(suite, subject)
    source_reviews = json.loads(
        oracle_review_path(suite).read_text(encoding="utf-8")
    )
    reviews = {
        "schema": "agents-cookbook-oracle-reviews.v1",
        "tasks": {task_id: source_reviews["tasks"][task_id] for task_id in task_ids},
    }
    agent = json.loads(json.dumps(suite.agents["opencode-native"]))
    agent["identity"]["version"] += "-required-tool-probe-v3"
    agent["configuration"]["diagnostic_required_tool"] = required_tool
    experiment = {
        "id": f"repository-intelligence-{subject}-required-tool-probe-v3",
        "version": 3,
        "suite": "repository-intelligence-tool-probe",
        "oracle_reviews": "qualification/oracle-reviews.json",
        "tasks": list(task_ids),
        "conditions": [{
            "id": f"{subject}-opencode-native-required",
            "subject": subject,
            "agent": "opencode-native",
            "trials": 3,
            "replicate_ids": [9101, 9102, 9103],
        }],
        "scoring": {
            "id": "repository-intelligence-tool-probe-score",
            "version": 3,
            "metrics": [
                "task_success",
                "subject_tool_invoked",
                "subject_before_native_discovery",
            ],
        },
    }
    provenance = {
        "schema": "agents-cookbook-tool-probe-source.v3",
        "purpose": "diagnostic-only; excluded from heldout score",
        "source_suite": str(suite.root),
        "source_experiment_sha256": digest(suite.experiment),
        "source_tasks_sha256": {
            task_id: _file_sha256(_task_file(suite.root, task_id)) for task_id in task_ids
        },
        "source_reviews_sha256": digest(reviews),
        "subject": subject,
        "required_tool": required_tool,
        "tasks": list(task_ids),
        "replicates_per_task": 3,
        "oracle_reviews": "reused-unchanged-source-task-reviews",
    }
    env_lines = None
    if runtime_env_file is not None:
        config = BenchmarkConfig.load(runtime_env_file)
        if subject == "hashmarks" and not config.values.get("HASHMARKS_BENCH_SOURCE"):
            raise ToolProbeError("runtime env file needs HASHMARKS_BENCH_SOURCE")
        campaign_root = destination.parent / "runs"
        env_lines = [
            *(f"{key}={config.values[key]}" for key in (
                "HASHMARKS_BENCH_SOURCE", "BENCHMARK_OPENCODE_AGENT",
                "BENCHMARK_PASSTHROUGH_ENV_KEYS",
            ) if key in config.values and (key != "HASHMARKS_BENCH_SOURCE" or subject == "hashmarks")),
            "BENCHMARK_AGENT=opencode-native",
            f"BENCHMARK_SUITE_PATH={destination}",
            f"BENCHMARK_CAMPAIGN_ROOT={campaign_root}",
            f"BENCHMARK_HARNESS_REPO_ROOT={Path(__file__).resolve().parents[1]}",
            f"BENCHMARK_SCORE_SCRIPT_PATH={destination / 'score.py'}",
            "BENCHMARK_SCORE_OUTPUT_PATH=score.json",
        ]
    if destination.exists():
        if not reuse:
            raise ToolProbeError(f"tool-probe destination already exists: {destination}")
        existing = load_suite(destination)
        validate_oracle_reviews(existing, require_complete=True)
        try:
            recorded = json.loads((destination / "diagnostic-source.json").read_text())
        except (OSError, ValueError) as exc:
            raise ToolProbeError("existing tool probe provenance is unreadable") from exc
        if recorded != provenance or existing.experiment != experiment or (
            existing.agents.get("opencode-native") != agent
        ) or (
            existing.subjects.get(subject) != suite.subjects[subject]
        ) or any(
            _task_file(destination, task_id).read_bytes()
            != _task_file(suite.root, task_id).read_bytes()
            for task_id in task_ids
        ) or (
            json.loads((destination / "qualification/oracle-reviews.json").read_text()) != reviews
        ) or (env_lines is not None and (
            destination / ".env"
        ).read_text(encoding="utf-8") != "\n".join(env_lines) + "\n"):
            raise ToolProbeError("existing tool probe differs from current reviewed source or runtime choices")
        return provenance
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".tool-probe-", dir=destination.parent))
    try:
        (temporary / "experiment.json").write_bytes(canonical_json(experiment))
        for kind in ("tasks", "agents", "subjects", "qualification"):
            (temporary / kind).mkdir()
        (temporary / "agents/opencode-native.json").write_bytes(
            canonical_json(agent)
        )
        (temporary / f"subjects/{subject}.json").write_bytes(
            canonical_json(suite.subjects[subject])
        )
        for task_id in task_ids:
            shutil.copyfile(_task_file(suite.root, task_id), _task_file(temporary, task_id))
        (temporary / "qualification/oracle-reviews.json").write_bytes(canonical_json(reviews))
        (temporary / "score.py").write_text(
            "from pathlib import Path\n"
            "from benchmarks.tool_probe_score import main\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main(Path(__file__).resolve().parent))\n",
            encoding="utf-8",
        )
        environment_lines = env_lines or [
            "# Supply local native runtime choices before execution.",
            *(["HASHMARKS_BENCH_SOURCE=/absolute/path/to/Hashmarks"] if subject == "hashmarks" else []),
            "BENCHMARK_OPENCODE_AGENT=build",
            "BENCHMARK_PASSTHROUGH_ENV_KEYS=",
            "BENCHMARK_AGENT=opencode-native",
            f"BENCHMARK_SUITE_PATH={destination}",
            f"BENCHMARK_CAMPAIGN_ROOT={destination.parent / 'runs'}",
            f"BENCHMARK_HARNESS_REPO_ROOT={Path(__file__).resolve().parents[1]}",
            f"BENCHMARK_SCORE_SCRIPT_PATH={destination / 'score.py'}",
            "BENCHMARK_SCORE_OUTPUT_PATH=score.json",
        ]
        (temporary / ".env.example").write_text(
            "\n".join(environment_lines) + "\n", encoding="utf-8"
        )
        if env_lines is not None:
            (temporary / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        (temporary / "diagnostic-source.json").write_bytes(canonical_json(provenance))
        validate_oracle_reviews(load_suite(temporary), require_complete=True)
        os.rename(temporary, destination)
        return provenance
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
