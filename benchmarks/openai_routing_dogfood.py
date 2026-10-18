"""Multi-task external OpenAI routing dogfood campaign."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.config import BenchmarkConfigError, load_env_values
from benchmarks.openai_responses_routing_probe import (
    OpenAIRoutingProbeError,
    preflight_probe,
    run_probe,
)

_MANIFEST_SCHEMA = "agents-cookbook-openai-routing-dogfood-manifest.v3"
_SUMMARY_SCHEMA = "agents-cookbook-openai-routing-dogfood-summary.v3"
_MAX_TASKS = 32
_MAX_REPEATS = 5
_FORBIDDEN_PROMPT_TERMS = (
    "hashmarks",
    "task_evidence",
    "mcp__",
    "grep",
)
_ROUTING_EXPECTATIONS = frozenset(
    {
        "hashmarks-task-evidence-first",
        "hashmarks-find-first",
        "native-read-first",
    }
)

_ROUTING_CONFIG_KEYS = frozenset(
    {
        "OPENAI_ROUTING_WORKSPACE",
        "OPENAI_ROUTING_HANDOFF",
        "OPENAI_ROUTING_TUNNEL_CLIENT",
        "OPENAI_ROUTING_TUNNEL_ID",
        "OPENAI_ROUTING_MODEL",
        "OPENAI_ROUTING_MANIFEST",
        "OPENAI_ROUTING_REPEATS",
        "OPENAI_ROUTING_RUN_ROOT",
    }
)


@dataclass(frozen=True)
class OpenAIRoutingSettings:
    workspace: Path
    handoff: Path
    tunnel_client: Path
    tunnel_id: str
    model: str
    manifest: Path
    repeats: int
    run_root: Path

    @classmethod
    def load(
        cls,
        env_file: Path,
        *,
        host: Mapping[str, str] | None = None,
    ) -> "OpenAIRoutingSettings":
        try:
            values = load_env_values(
                env_file,
                allowed_keys=_ROUTING_CONFIG_KEYS,
            )
        except BenchmarkConfigError as exc:
            raise OpenAIRoutingDogfoodError(str(exc)) from exc
        host_values = dict(os.environ if host is None else host)

        workspace = Path(
            values.get("OPENAI_ROUTING_WORKSPACE") or "."
        ).expanduser().resolve()
        handoff = Path(
            values.get("OPENAI_ROUTING_HANDOFF")
            or "../Hashmarks/dist/chatgpt-secure-mcp-tunnel-handoff.json"
        ).expanduser().resolve()
        tunnel_value = values.get("OPENAI_ROUTING_TUNNEL_CLIENT")
        if tunnel_value:
            tunnel_client = Path(tunnel_value).expanduser().resolve()
        else:
            discovered = shutil.which(
                "tunnel-client",
                path=host_values.get("PATH", ""),
            )
            if discovered is None:
                raise OpenAIRoutingDogfoodError(
                    "OPENAI_ROUTING_TUNNEL_CLIENT is not configured and "
                    "tunnel-client is not on PATH"
                )
            tunnel_client = Path(discovered).resolve()

        tunnel_id = values.get("OPENAI_ROUTING_TUNNEL_ID", "").strip()
        model = values.get("OPENAI_ROUTING_MODEL", "").strip()
        if not tunnel_id:
            raise OpenAIRoutingDogfoodError(
                "set OPENAI_ROUTING_TUNNEL_ID in .env"
            )
        if not model:
            raise OpenAIRoutingDogfoodError(
                "set OPENAI_ROUTING_MODEL in .env"
            )

        manifest = Path(
            values.get("OPENAI_ROUTING_MANIFEST")
            or "benchmarks/dogfood/openai-routing-v3.json"
        ).expanduser().resolve()
        run_root = Path(
            values.get("OPENAI_ROUTING_RUN_ROOT")
            or ".benchmark-runs/openai-routing"
        ).expanduser().resolve()
        repeats_raw = values.get("OPENAI_ROUTING_REPEATS") or "1"
        try:
            repeats = int(repeats_raw)
        except ValueError as exc:
            raise OpenAIRoutingDogfoodError(
                "OPENAI_ROUTING_REPEATS must be an integer"
            ) from exc
        if not 1 <= repeats <= _MAX_REPEATS:
            raise OpenAIRoutingDogfoodError(
                f"OPENAI_ROUTING_REPEATS must be between 1 and {_MAX_REPEATS}"
            )
        return cls(
            workspace=workspace,
            handoff=handoff,
            tunnel_client=tunnel_client,
            tunnel_id=tunnel_id,
            model=model,
            manifest=manifest,
            repeats=repeats,
            run_root=run_root,
        )


def routing_run_root(env_file: Path) -> Path:
    try:
        values = load_env_values(
            env_file,
            allowed_keys=_ROUTING_CONFIG_KEYS,
        )
    except BenchmarkConfigError as exc:
        raise OpenAIRoutingDogfoodError(str(exc)) from exc
    return Path(
        values.get("OPENAI_ROUTING_RUN_ROOT")
        or ".benchmark-runs/openai-routing"
    ).expanduser().resolve()


class OpenAIRoutingDogfoodError(RuntimeError):
    pass


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def load_manifest(path: Path, *, workspace: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenAIRoutingDogfoodError(
            f"routing dogfood manifest is unreadable: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != _MANIFEST_SCHEMA:
        raise OpenAIRoutingDogfoodError(
            f"routing dogfood manifest schema must be {_MANIFEST_SCHEMA}"
        )
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise OpenAIRoutingDogfoodError("routing dogfood manifest name is required")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise OpenAIRoutingDogfoodError("routing dogfood manifest tasks are required")
    if len(tasks) > _MAX_TASKS:
        raise OpenAIRoutingDogfoodError(
            f"routing dogfood manifest exceeds {_MAX_TASKS} tasks"
        )

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    workspace = workspace.resolve()
    for index, raw_task in enumerate(tasks):
        if not isinstance(raw_task, dict):
            raise OpenAIRoutingDogfoodError(f"tasks[{index}] must be an object")
        task_id = raw_task.get("id")
        prompt = raw_task.get("prompt")
        routing_expectation = raw_task.get("routing_expectation")
        expected_paths = raw_task.get("expected_paths")
        if not isinstance(task_id, str) or re.fullmatch(
            r"[a-z0-9][a-z0-9-]{0,79}",
            task_id,
        ) is None:
            raise OpenAIRoutingDogfoodError(
                f"tasks[{index}].id must be a stable lowercase slug"
            )
        if task_id in seen:
            raise OpenAIRoutingDogfoodError(
                f"duplicate routing dogfood task id: {task_id}"
            )
        seen.add(task_id)
        if not isinstance(prompt, str) or not prompt.strip():
            raise OpenAIRoutingDogfoodError(
                f"tasks[{index}].prompt must be nonempty"
            )
        if len(prompt) > 2_000:
            raise OpenAIRoutingDogfoodError(
                f"tasks[{index}].prompt exceeds 2000 characters"
            )
        if routing_expectation not in _ROUTING_EXPECTATIONS:
            raise OpenAIRoutingDogfoodError(
                f"tasks[{index}].routing_expectation must be one of: "
                + ", ".join(sorted(_ROUTING_EXPECTATIONS))
            )
        lowered = prompt.casefold()
        forbidden = [
            term
            for term in _FORBIDDEN_PROMPT_TERMS
            if term in lowered
        ]
        if forbidden:
            raise OpenAIRoutingDogfoodError(
                f"tasks[{index}].prompt names routing/tool authority: {forbidden}"
            )
        if not isinstance(expected_paths, list) or not expected_paths:
            raise OpenAIRoutingDogfoodError(
                f"tasks[{index}].expected_paths must be a nonempty list"
            )
        paths: list[str] = []
        for value in expected_paths:
            if not isinstance(value, str) or not value.strip():
                raise OpenAIRoutingDogfoodError(
                    f"tasks[{index}].expected_paths contains an invalid path"
                )
            relative = value.strip()
            target = (workspace / relative).resolve()
            try:
                target.relative_to(workspace)
            except ValueError as exc:
                raise OpenAIRoutingDogfoodError(
                    f"tasks[{index}] expected path escapes workspace: {relative}"
                ) from exc
            if not target.is_file():
                raise OpenAIRoutingDogfoodError(
                    f"tasks[{index}] expected path does not exist: {relative}"
                )
            paths.append(relative)
        normalized.append(
            {
                "id": task_id,
                "routing_expectation": routing_expectation,
                "prompt": prompt.strip(),
                "expected_paths": paths,
            }
        )
    return {
        "schema": _MANIFEST_SCHEMA,
        "name": name.strip(),
        "manifest_sha256": _digest_bytes(raw),
        "tasks": normalized,
    }


def _preflight_evidence(
    *,
    manifest: dict[str, Any],
    probe: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "agents-cookbook-openai-routing-dogfood-preflight.v1",
        "status": "READY",
        "manifest": {
            "name": manifest["name"],
            "sha256": manifest["manifest_sha256"],
            "task_count": len(manifest["tasks"]),
            "routing_expectations": dict(
                sorted(
                    Counter(
                        task["routing_expectation"]
                        for task in manifest["tasks"]
                    ).items()
                )
            ),
        },
        "probe": probe,
    }


def preflight_campaign(
    *,
    manifest_path: Path,
    workspace: Path,
    handoff_path: Path,
    tunnel_client: Path,
    tunnel_id: str,
    model: str,
    openai_api_key: str,
    control_plane_api_key: str,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path, workspace=workspace)
    probe = preflight_probe(
        workspace=workspace,
        handoff_path=handoff_path,
        tunnel_client=tunnel_client,
        tunnel_id=tunnel_id,
        model=model,
        openai_api_key=openai_api_key,
        control_plane_api_key=control_plane_api_key,
    )
    after = load_manifest(manifest_path, workspace=workspace)
    if after["manifest_sha256"] != manifest["manifest_sha256"]:
        raise OpenAIRoutingDogfoodError(
            "routing dogfood manifest changed during preflight"
        )
    return _preflight_evidence(manifest=manifest, probe=probe)

def _usage_total(receipt: dict[str, Any]) -> int:
    total = 0
    openai = receipt.get("openai")
    usage = openai.get("usage") if isinstance(openai, dict) else None
    if not isinstance(usage, list):
        return 0
    for row in usage:
        if isinstance(row, dict):
            value = row.get("total_tokens")
            if isinstance(value, int) and not isinstance(value, bool):
                total += value
    return total


def _routing_fit(
    *,
    routing_expectation: str,
    raw_outcome: str,
    first_tool: str | None,
) -> str:
    if raw_outcome == "ENVIRONMENT_BLOCKED":
        return raw_outcome
    if routing_expectation == "hashmarks-task-evidence-first":
        if raw_outcome not in {"PASS", "FAIL", "UNKNOWN"}:
            return "UNKNOWN"
        return raw_outcome
    if first_tool is None:
        return "UNKNOWN"
    if routing_expectation == "hashmarks-find-first":
        return "PASS" if first_tool == "mcp__hashmarks__find" else "FAIL"
    if routing_expectation == "native-read-first":
        return "PASS" if first_tool == "read" else "FAIL"
    raise OpenAIRoutingDogfoodError(
        f"unsupported routing expectation: {routing_expectation}"
    )


def _answer_path_match(
    final_text: object,
    expected_paths: list[str],
) -> bool:
    if not isinstance(final_text, str):
        return False
    lowered = final_text.casefold()
    return any(path.casefold() in lowered for path in expected_paths)


def _run_directories(root: Path) -> list[tuple[str, Path]]:
    runs = root.resolve() / "runs"
    if not runs.exists():
        return []
    if runs.is_symlink() or not runs.is_dir():
        raise OpenAIRoutingDogfoodError(
            f"invalid OpenAI routing runs directory: {runs}"
        )
    found: list[tuple[str, Path]] = []
    for path in runs.iterdir():
        name = path.name
        if (
            path.is_symlink()
            or not path.is_dir()
            or not name.isascii()
            or not name.isdecimal()
            or name != f"{int(name):06d}"
        ):
            raise OpenAIRoutingDogfoodError(
                f"unexpected OpenAI routing run entry: {path}"
            )
        found.append((name, path))
    found.sort(key=lambda item: int(item[0]))
    return found


def allocate_run_directory(root: Path) -> tuple[str, Path]:
    root = root.resolve()
    runs = root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    existing = _run_directories(root)
    candidate = (
        max((int(run_id) for run_id, _ in existing), default=0) + 1
    )
    while True:
        run_id = f"{candidate:06d}"
        path = runs / run_id
        try:
            path.mkdir()
        except FileExistsError:
            candidate += 1
            continue
        return run_id, path


def list_dogfood_runs(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run_id, path in _run_directories(root):
        summary_path = path / "summary.json"
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise OpenAIRoutingDogfoodError(
                    f"OpenAI routing run {run_id} has unreadable summary: {exc}"
                ) from exc
            if not isinstance(summary, dict):
                raise OpenAIRoutingDogfoodError(
                    f"OpenAI routing run {run_id} summary must be an object"
                )
            aggregate = summary.get("aggregate")
            candidate = summary.get("candidate")
            runtime = (
                candidate.get("hashmarks_runtime")
                if isinstance(candidate, dict)
                else None
            )
            rows.append(
                {
                    "run_id": run_id,
                    "status": summary.get("status", "UNKNOWN"),
                    "path": str(path),
                    "hashmarks_version": (
                        runtime.get("version")
                        if isinstance(runtime, dict)
                        else None
                    ),
                    "hashmarks_executable_sha256": (
                        runtime.get("executable_sha256")
                        if isinstance(runtime, dict)
                        else None
                    ),
                    "hashmarks_first_rate": (
                        aggregate.get("hashmarks_first_rate")
                        if isinstance(aggregate, dict)
                        else None
                    ),
                    "routing_fit_rate": (
                        aggregate.get("routing_fit_rate")
                        if isinstance(aggregate, dict)
                        else None
                    ),
                    "semantic_task_evidence_first_rate": (
                        aggregate.get("semantic_task_evidence_first_rate")
                        if isinstance(aggregate, dict)
                        else None
                    ),
                    "exact_symbol_hashmarks_find_rate": (
                        aggregate.get("exact_symbol_hashmarks_find_rate")
                        if isinstance(aggregate, dict)
                        else None
                    ),
                    "known_path_native_read_rate": (
                        aggregate.get("known_path_native_read_rate")
                        if isinstance(aggregate, dict)
                        else None
                    ),
                    "outcomes": (
                        aggregate.get("outcomes")
                        if isinstance(aggregate, dict)
                        else None
                    ),
                }
            )
            continue
        start_error = path / "start-error.json"
        start_reason = None
        if start_error.is_file():
            try:
                start_payload = json.loads(
                    start_error.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise OpenAIRoutingDogfoodError(
                    f"OpenAI routing run {run_id} has unreadable start error: {exc}"
                ) from exc
            if isinstance(start_payload, dict) and isinstance(
                start_payload.get("error"),
                str,
            ):
                start_reason = start_payload["error"]
        error_files = list((path / "trials").glob("*/error.json")) if (
            path / "trials"
        ).is_dir() else []
        rows.append(
            {
                "run_id": run_id,
                "status": (
                    "PRECHECK_FAILED"
                    if start_error.is_file()
                    else "INCOMPLETE"
                    if error_files
                    else "INTERRUPTED"
                ),
                "path": str(path),
                "reason": start_reason,
                "hashmarks_version": None,
                "hashmarks_executable_sha256": None,
                "hashmarks_first_rate": None,
                "routing_fit_rate": None,
                "semantic_task_evidence_first_rate": None,
                "exact_symbol_hashmarks_find_rate": None,
                "known_path_native_read_rate": None,
                "outcomes": None,
            }
        )
    return rows


def select_dogfood_run(
    root: Path,
    run_id: str | None = None,
) -> dict[str, object]:
    rows = list_dogfood_runs(root)
    if not rows:
        raise OpenAIRoutingDogfoodError(
            f"no OpenAI routing dogfood runs in {root}; "
            "use make benchmark-openai-routing"
        )
    if run_id is None:
        return rows[-1]
    for row in rows:
        if row["run_id"] == run_id:
            return row
    raise OpenAIRoutingDogfoodError(
        f"unknown OpenAI routing dogfood run {run_id!r}"
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_campaign(
    *,
    manifest_path: Path,
    workspace: Path,
    handoff_path: Path,
    tunnel_client: Path,
    tunnel_id: str,
    model: str,
    repeats: int,
    output_dir: Path,
    openai_api_key: str,
    control_plane_api_key: str,
    probe_runner: Callable[..., dict[str, Any]] = run_probe,
    prepared_output_dir: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    if not 1 <= repeats <= _MAX_REPEATS:
        raise OpenAIRoutingDogfoodError(
            f"repeats must be between 1 and {_MAX_REPEATS}"
        )
    output_dir = output_dir.resolve()
    if prepared_output_dir:
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise OpenAIRoutingDogfoodError(
                f"prepared dogfood output directory is not empty: {output_dir}"
            )
    elif output_dir.exists():
        raise OpenAIRoutingDogfoodError(
            f"dogfood output directory already exists: {output_dir}"
        )

    manifest = load_manifest(manifest_path, workspace=workspace)
    probe_preflight = preflight_probe(
        workspace=workspace,
        handoff_path=handoff_path,
        tunnel_client=tunnel_client,
        tunnel_id=tunnel_id,
        model=model,
        openai_api_key=openai_api_key,
        control_plane_api_key=control_plane_api_key,
    )
    after_preflight = load_manifest(manifest_path, workspace=workspace)
    if after_preflight["manifest_sha256"] != manifest["manifest_sha256"]:
        raise OpenAIRoutingDogfoodError(
            "routing dogfood manifest changed during preflight"
        )
    preflight = _preflight_evidence(
        manifest=manifest,
        probe=probe_preflight,
    )
    if not prepared_output_dir:
        output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / "preflight.json", preflight)
    _write_json(
        output_dir / "manifest.json",
        {
            "schema": manifest["schema"],
            "name": manifest["name"],
            "manifest_sha256": manifest["manifest_sha256"],
            "tasks": manifest["tasks"],
        },
    )

    rows: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    routing_fit_counts: Counter[str] = Counter()
    first_tool_counts: Counter[str] = Counter()
    total_tokens = 0

    aborted = False
    for task in manifest["tasks"]:
        if aborted:
            break
        for repeat in range(repeats):
            trial_id = f"{task['id']}-r{repeat + 1:02d}"
            trial_dir = output_dir / "trials" / trial_id
            started = time.monotonic()
            try:
                receipt = probe_runner(
                    workspace=workspace,
                    handoff_path=handoff_path,
                    tunnel_client=tunnel_client,
                    tunnel_id=tunnel_id,
                    model=model,
                    prompt=task["prompt"],
                    openai_api_key=openai_api_key,
                    control_plane_api_key=control_plane_api_key,
                )
            except OpenAIRoutingProbeError as exc:
                elapsed_ms = int(round((time.monotonic() - started) * 1000))
                error = {
                    "schema": "agents-cookbook-openai-routing-dogfood-error.v1",
                    "trial_id": trial_id,
                    "task_id": task["id"],
                    "repeat": repeat + 1,
                    "elapsed_ms": elapsed_ms,
                    "error": str(exc),
                }
                _write_json(trial_dir / "error.json", error)
                rows.append(
                    {
                        "trial_id": trial_id,
                        "task_id": task["id"],
                        "repeat": repeat + 1,
                        "routing_expectation": task["routing_expectation"],
                        "outcome": "INCOMPLETE",
                        "routing_fit": "INCOMPLETE",
                        "answer_path_match": False,
                        "elapsed_ms": elapsed_ms,
                        "tokens": 0,
                        "first_tool": None,
                    }
                )
                outcome_counts["INCOMPLETE"] += 1
                routing_fit_counts["INCOMPLETE"] += 1
                aborted = True
                break

            elapsed_ms = int(round((time.monotonic() - started) * 1000))
            score = receipt.get("score")
            if not isinstance(score, dict) or not isinstance(
                score.get("outcome"),
                str,
            ):
                raise OpenAIRoutingDogfoodError(
                    f"trial {trial_id} returned no routing outcome"
                )
            outcome = score["outcome"]
            trace = receipt.get("trace")
            calls = trace.get("calls") if isinstance(trace, dict) else None
            first_tool = None
            if isinstance(calls, list) and calls and isinstance(calls[0], dict):
                value = calls[0].get("tool")
                if isinstance(value, str):
                    first_tool = value
                    first_tool_counts[value] += 1
            routing_fit = _routing_fit(
                routing_expectation=task["routing_expectation"],
                raw_outcome=outcome,
                first_tool=first_tool,
            )
            path_match = _answer_path_match(
                receipt.get("final_text"),
                task["expected_paths"],
            )
            tokens = _usage_total(receipt)
            total_tokens += tokens
            outcome_counts[outcome] += 1
            routing_fit_counts[routing_fit] += 1

            for filename in ("catalog", "trace", "score"):
                payload = receipt.get(filename)
                if not isinstance(payload, dict):
                    raise OpenAIRoutingDogfoodError(
                        f"trial {trial_id} has no {filename} artifact"
                    )
                _write_json(trial_dir / f"{filename}.json", payload)
            _write_json(trial_dir / "receipt.json", receipt)
            rows.append(
                {
                    "trial_id": trial_id,
                    "task_id": task["id"],
                    "repeat": repeat + 1,
                    "routing_expectation": task["routing_expectation"],
                    "outcome": outcome,
                    "routing_fit": routing_fit,
                    "answer_path_match": path_match,
                    "elapsed_ms": elapsed_ms,
                    "tokens": tokens,
                    "first_tool": first_tool,
                }
            )

    pass_count = outcome_counts["PASS"]
    fail_count = outcome_counts["FAIL"]
    scoreable = pass_count + fail_count
    fit_pass = routing_fit_counts["PASS"]
    fit_fail = routing_fit_counts["FAIL"]
    fit_scoreable = fit_pass + fit_fail
    semantic_rows = [
        row
        for row in rows
        if row.get("routing_expectation") == "hashmarks-task-evidence-first"
        and row.get("routing_fit") in {"PASS", "FAIL"}
    ]
    symbol_find_rows = [
        row
        for row in rows
        if row.get("routing_expectation") == "hashmarks-find-first"
        and row.get("routing_fit") in {"PASS", "FAIL"}
    ]
    native_read_rows = [
        row
        for row in rows
        if row.get("routing_expectation") == "native-read-first"
        and row.get("routing_fit") in {"PASS", "FAIL"}
    ]
    summary = {
        "schema": _SUMMARY_SCHEMA,
        "run_id": run_id,
        "status": (
            "COMPLETE"
            if outcome_counts["INCOMPLETE"] == 0
            else "INCOMPLETE"
        ),
        "manifest": {
            "name": manifest["name"],
            "sha256": manifest["manifest_sha256"],
            "task_count": len(manifest["tasks"]),
            "repeats": repeats,
        },
        "candidate": {
            "hashmarks_handoff_sha256": probe_preflight.get(
                "hashmarks_handoff_sha256"
            ),
            "hashmarks_runtime": probe_preflight.get("hashmarks_runtime"),
        },
        "openai": probe_preflight.get("openai"),
        "authority": {
            "diagnostic_only": True,
            "heldout_comparable": False,
            "parallel_trials": False,
            "automatic_retry": False,
            "abort_on_infrastructure_error": True,
        },
        "trials": rows,
        "aggregate": {
            "total_trials": len(rows),
            "outcomes": dict(sorted(outcome_counts.items())),
            "routing_fit_counts": dict(sorted(routing_fit_counts.items())),
            "scoreable_trials": scoreable,
            "hashmarks_first_rate": (
                pass_count / scoreable if scoreable else None
            ),
            "routing_fit_scoreable_trials": fit_scoreable,
            "routing_fit_rate": (
                fit_pass / fit_scoreable if fit_scoreable else None
            ),
            "semantic_task_evidence_first_rate": (
                sum(row["routing_fit"] == "PASS" for row in semantic_rows)
                / len(semantic_rows)
                if semantic_rows
                else None
            ),
            "exact_symbol_hashmarks_find_rate": (
                sum(row["routing_fit"] == "PASS" for row in symbol_find_rows)
                / len(symbol_find_rows)
                if symbol_find_rows
                else None
            ),
            "known_path_native_read_rate": (
                sum(row["routing_fit"] == "PASS" for row in native_read_rows)
                / len(native_read_rows)
                if native_read_rows
                else None
            ),
            "answer_path_matches": sum(
                row["answer_path_match"] is True for row in rows
            ),
            "answer_path_match_rate": (
                sum(row["answer_path_match"] is True for row in rows) / len(rows)
                if rows
                else None
            ),
            "first_tool_counts": dict(sorted(first_tool_counts.items())),
            "total_tokens": total_tokens,
            "total_elapsed_ms": sum(row["elapsed_ms"] for row in rows),
        },
    }
    _write_json(output_dir / "summary.json", summary)
    return summary


def run_saved_campaign(
    *,
    settings: OpenAIRoutingSettings,
    openai_api_key: str,
    control_plane_api_key: str,
    probe_runner: Callable[..., dict[str, Any]] = run_probe,
) -> tuple[str, Path, dict[str, Any]]:
    run_id, output_dir = allocate_run_directory(settings.run_root)
    try:
        summary = run_campaign(
            manifest_path=settings.manifest,
            workspace=settings.workspace,
            handoff_path=settings.handoff,
            tunnel_client=settings.tunnel_client,
            tunnel_id=settings.tunnel_id,
            model=settings.model,
            repeats=settings.repeats,
            output_dir=output_dir,
            openai_api_key=openai_api_key,
            control_plane_api_key=control_plane_api_key,
            probe_runner=probe_runner,
            prepared_output_dir=True,
            run_id=run_id,
        )
    except (
        OSError,
        OpenAIRoutingProbeError,
        OpenAIRoutingDogfoodError,
        ValueError,
    ) as exc:
        _write_json(
            output_dir / "start-error.json",
            {
                "schema": "agents-cookbook-openai-routing-start-error.v1",
                "run_id": run_id,
                "error": str(exc),
            },
        )
        raise
    return run_id, output_dir, summary


def campaign_exit_code(summary: dict[str, Any]) -> int:
    aggregate = summary.get("aggregate")
    if not isinstance(aggregate, dict):
        raise OpenAIRoutingDogfoodError("dogfood summary aggregate is unavailable")
    routing_fit = aggregate.get("routing_fit_counts")
    if not isinstance(routing_fit, dict):
        raise OpenAIRoutingDogfoodError(
            "dogfood summary routing-fit outcomes are unavailable"
        )
    if int(routing_fit.get("INCOMPLETE", 0)) > 0:
        return 3
    if int(routing_fit.get("ENVIRONMENT_BLOCKED", 0)) > 0:
        return 2
    if int(routing_fit.get("UNKNOWN", 0)) > 0:
        return 3
    if int(routing_fit.get("FAIL", 0)) > 0:
        return 1
    return 0
