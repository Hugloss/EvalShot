"""Harbor trial execution under the shared benchmark campaign authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from benchmarks.config import BenchmarkConfig
from benchmarks.harbor_matrix import (
    HarborSettings,
    build_report as build_harbor_report,
    execute_trial,
    mcp_config_payload,
    mode_contract,
    preflight,
    prepare_task,
    _credential_file,
)
from benchmarks.matrix_profiles import MatrixProfile

from .ablation_attribution import build_ablation_report
from .factorial_attribution import build_factorial_report
from .bundle import verify_bundle
from .bundle_writer import BundlePublicationError, publish_bundle
from .campaign import campaign_status
from .campaign_authority import (
    campaign_suite_identity,
    claim_launch,
    launch_state,
    publish_campaign_authority,
    record_interrupted_attempt,
)
from .identity import canonical_json, digest
from .mechanism_attribution import build_mechanism_report
from .runner import TrialRunResult, reuse_completed_trial
from .suite import SuiteDefinition


class HarborBackendError(ValueError):
    pass


def settings_from_config(
    config: BenchmarkConfig,
    profile: MatrixProfile,
) -> HarborSettings:
    source = config.values.get("HASHMARKS_BENCH_SOURCE")
    model = config.values.get("BENCHMARK_HARBOR_MODEL")
    issues = [
        f"set {name} in the selected benchmark env file"
        for name, value in (
            ("HASHMARKS_BENCH_SOURCE", source),
            ("BENCHMARK_HARBOR_MODEL", model),
        )
        if not value
    ]
    executable = config.values.get("BENCHMARK_HARBOR_EXECUTABLE") or "harbor"
    if shutil.which(executable, path=config.host.get("PATH")) is None:
        issues.append(f"install Harbor CLI or set BENCHMARK_HARBOR_EXECUTABLE ({executable} unavailable)")
    if shutil.which("docker", path=config.host.get("PATH")) is None:
        issues.append("install Docker CLI and start its daemon")
    raw_keys = config.values.get("BENCHMARK_PASSTHROUGH_ENV_KEYS", "")
    keys = tuple(dict.fromkeys(key.strip() for key in raw_keys.split(",") if key.strip()))
    invalid = sorted(key for key in keys if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None)
    if invalid:
        issues.append("invalid BENCHMARK_PASSTHROUGH_ENV_KEYS: " + ", ".join(invalid))
    absent = sorted(key for key in keys if not config.host.get(key))
    if absent:
        issues.append("set selected provider variable(s) in the host environment: " + ", ".join(absent))
    if config.values.get("BENCHMARK_HARBOR_PASSTHROUGH_ENV_KEYS"):
        issues.append("replace BENCHMARK_HARBOR_PASSTHROUGH_ENV_KEYS with BENCHMARK_PASSTHROUGH_ENV_KEYS")
    if issues:
        raise HarborBackendError("Harbor setup needs:\n- " + "\n- ".join(issues))
    if profile.root is None:
        raise HarborBackendError("Harbor matrix has no run root")
    return HarborSettings(
        executable=executable,
        model=str(model),
        root=profile.root,
        hashmarks_source=Path(str(source)).expanduser().resolve(),
        passthrough_env_keys=keys,
    )


def _tree_identity(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise HarborBackendError(f"projected Harbor task is missing or invalid: {root}")
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", os.readlink(path)))
        elif path.is_file():
            entries.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()))
        elif path.is_dir():
            entries.append((relative, "directory", None))
        else:
            raise HarborBackendError(f"unsupported projected task entry: {path}")
    return digest(entries)


def observed_preflight(
    *,
    settings: HarborSettings,
    suite: SuiteDefinition,
    projection: Mapping[str, Any],
    profile: MatrixProfile,
    host: Mapping[str, str],
) -> dict[str, Any]:
    mode = mode_contract(projection, str(profile.mode))
    return preflight(
        settings=settings,
        matrix=projection,
        suite=suite,
        mode=mode,
        harnesses=tuple(str(value) for value in projection["harnesses"]),
        tasks=mode.tasks,
        host=host,
    )


def admit_harbor_run(
    staged: Path,
    *,
    suite: SuiteDefinition,
    profile: MatrixProfile,
    settings: HarborSettings,
    preflight_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    rows = suite.trial_definitions()
    if not rows or len({row["definition_id"] for row in rows}) != len(rows):
        raise HarborBackendError("Harbor matrix has empty or duplicate trial definitions")
    tasks_root = staged / "tasks"
    task_digests = {}
    for task_id in suite.experiment["tasks"]:
        task_path = tasks_root / str(task_id)
        prepare_task(
            suite=suite,
            task_id=str(task_id),
            destination=task_path,
            cache_root=staged / "cache",
            hashmarks_source=settings.hashmarks_source,
        )
        task_digests[str(task_id)] = _tree_identity(task_path)
    configs = mcp_configs(
        staged,
        preflight_receipt=preflight_receipt,
    )
    mcp_config_digests = {
        subject: hashlib.sha256(path.read_bytes()).hexdigest()
        for subject, path in configs.items()
        if path is not None
    }
    payload = {
        "contract": "benchmark-campaign-authority.v6",
        "backend": "harbor",
        "matrix_profile": profile.name,
        "matrix_profile_identity": profile.identity,
        "suite_identity": campaign_suite_identity(suite),
        "selected_definitions": sorted(str(row["definition_id"]) for row in rows),
        "preflight": dict(preflight_receipt),
        "task_digests": task_digests,
        "mcp_config_digests": mcp_config_digests,
    }
    return publish_campaign_authority(staged / "results", payload)


def verify_harbor_run(
    run_root: Path,
    campaign: Mapping[str, Any],
    *,
    suite: SuiteDefinition,
    profile: MatrixProfile,
    preflight_receipt: Mapping[str, Any],
) -> None:
    if (
        campaign.get("backend") != "harbor"
        or campaign.get("matrix_profile") != profile.name
        or campaign.get("matrix_profile_identity") != profile.identity
        or campaign.get("suite_identity") != campaign_suite_identity(suite)
        or campaign.get("selected_definitions")
        != sorted(str(row["definition_id"]) for row in suite.trial_definitions())
        or campaign.get("preflight") != dict(preflight_receipt)
    ):
        raise HarborBackendError("saved Harbor run authority changed; start a new run")
    expected_configs = campaign.get("mcp_config_digests")
    if not isinstance(expected_configs, dict):
        raise HarborBackendError("saved Harbor MCP config authority is missing")
    configs = mcp_configs(
        run_root,
        preflight_receipt=preflight_receipt,
    )
    observed_configs = {
        subject: hashlib.sha256(path.read_bytes()).hexdigest()
        for subject, path in configs.items()
        if path is not None
    }
    if observed_configs != expected_configs:
        raise HarborBackendError(
            "saved Harbor MCP treatment configuration changed; start a new run"
        )
    expected = campaign.get("task_digests")
    if not isinstance(expected, dict):
        raise HarborBackendError("saved Harbor task authority is missing")
    for task_id in suite.experiment["tasks"]:
        if expected.get(task_id) != _tree_identity(run_root / "tasks" / str(task_id)):
            raise HarborBackendError(f"projected Harbor task changed: {task_id}")


def credential_file(
    run_root: Path,
    *,
    settings: HarborSettings,
    host: Mapping[str, str],
) -> Path | None:
    return _credential_file(run_root, keys=settings.passthrough_env_keys, host=host)


def _mcp_config_bytes(
    *,
    tools: list[str],
    canonical_tools: list[str],
    query_surfaces: list[str],
    canonical_query_surfaces: list[str],
) -> bytes:
    return (
        json.dumps(
            mcp_config_payload(
                tool_names=(
                    None
                    if tools == canonical_tools
                    else tuple(tools)
                ),
                query_surfaces=(
                    None
                    if query_surfaces == canonical_query_surfaces
                    else tuple(query_surfaces)
                ),
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def mcp_configs(
    run_root: Path,
    *,
    preflight_receipt: Mapping[str, Any],
) -> dict[str, Path | None]:
    hashmarks = preflight_receipt.get("hashmarks")
    treatments = hashmarks.get("treatments") if isinstance(hashmarks, dict) else None
    if not isinstance(treatments, dict) or "hashmarks" not in treatments:
        raise HarborBackendError("Harbor preflight has no Hashmarks treatment authority")
    full_treatment = treatments.get("hashmarks")
    canonical_tools = (
        hashmarks.get("canonical_tools")
        if isinstance(hashmarks, dict)
        else None
    )
    if not isinstance(canonical_tools, list) and isinstance(full_treatment, dict):
        canonical_tools = full_treatment.get("tools")
    canonical_query_surfaces = (
        hashmarks.get("canonical_repository_intelligence_query_surfaces")
        if isinstance(hashmarks, dict)
        else None
    )
    if (
        not isinstance(canonical_query_surfaces, list)
        and isinstance(full_treatment, dict)
    ):
        canonical_query_surfaces = full_treatment.get(
            "repository_intelligence_query_surfaces",
            [],
        )
    configs: dict[str, Path | None] = {"none": None}
    for subject, raw in sorted(treatments.items()):
        if (
            not isinstance(subject, str)
            or not subject
            or Path(subject).name != subject
            or not isinstance(raw, dict)
        ):
            raise HarborBackendError("Harbor preflight contains invalid treatment identity")
        tools = raw.get("tools")
        query_surfaces = raw.get("repository_intelligence_query_surfaces", [])
        full_contract = raw.get("full_contract")
        if (
            not isinstance(canonical_tools, list)
            or not all(isinstance(value, str) and value for value in canonical_tools)
            or not isinstance(canonical_query_surfaces, list)
            or not all(
                isinstance(value, str) and value
                for value in canonical_query_surfaces
            )
            or not isinstance(tools, list)
            or not tools
            or not all(isinstance(value, str) and value for value in tools)
            or not isinstance(query_surfaces, list)
            or not all(isinstance(value, str) and value for value in query_surfaces)
            or len(set(tools)) != len(tools)
            or len(set(query_surfaces)) != len(query_surfaces)
            or not set(tools).issubset(set(canonical_tools))
            or not set(query_surfaces).issubset(set(canonical_query_surfaces))
            or not isinstance(full_contract, bool)
            or full_contract
            != (
                tools == canonical_tools
                and query_surfaces == canonical_query_surfaces
            )
        ):
            raise HarborBackendError(
                f"Harbor preflight treatment is incomplete: {subject}"
            )
        path = run_root / f"{subject}.mcp.json"
        expected = _mcp_config_bytes(
            tools=tools,
            canonical_tools=canonical_tools,
            query_surfaces=query_surfaces,
            canonical_query_surfaces=canonical_query_surfaces,
        )
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
                raise HarborBackendError(
                    f"Harbor MCP treatment config changed: {subject}"
                )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
        configs[subject] = path
    return configs


def _redact(value: str, settings: HarborSettings, host: Mapping[str, str]) -> str:
    for secret in sorted((host[key] for key in settings.passthrough_env_keys), key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def _captured_job_artifact(
    observed: Mapping[str, Any],
    *,
    key: str,
    job_root: Path,
    label: str,
) -> bytes | None:
    value = observed.get(key)
    if value is None:
        return None
    path = Path(str(value))
    if path.is_symlink() or not path.is_file():
        raise HarborBackendError(f"Harbor {label} path is missing or invalid")
    try:
        path.resolve().relative_to(job_root)
    except ValueError as exc:
        raise HarborBackendError(
            f"Harbor {label} escaped its job directory"
        ) from exc
    return path.read_bytes()


def run_harbor_trial(
    *,
    suite: SuiteDefinition,
    row: Mapping[str, Any],
    run_root: Path,
    campaign: dict[str, Any],
    settings: HarborSettings,
    host: Mapping[str, str],
    credentials: Path | None,
    mcp: Mapping[str, Path | None],
) -> TrialRunResult:
    definition = str(row["definition_id"])
    trial_id = digest({"campaign_id": campaign["campaign_id"], "definition_id": definition})
    results_root = run_root / "results"
    state = launch_state(
        results_root=results_root,
        campaign=campaign,
        definition_id=definition,
        trial_id=trial_id,
    )
    recovered = False
    if state in {"INTERRUPTED", "STALE_INTERRUPTED"}:
        record_interrupted_attempt(
            results_root=results_root,
            campaign=campaign,
            definition_id=definition,
            trial_id=None if state == "STALE_INTERRUPTED" else trial_id,
        )
        recovered = True
    elif state == "CORRUPT":
        raise HarborBackendError(f"Harbor result bundle is corrupt: {results_root / trial_id}")
    elif state == "COMPLETE":
        return reuse_completed_trial(
            results_root=results_root,
            definition_id=definition,
            trial_id=trial_id,
        )
    task_id = str(row["task_id"])
    if campaign["task_digests"].get(task_id) != _tree_identity(run_root / "tasks" / task_id):
        raise HarborBackendError(f"projected Harbor task changed: {task_id}")
    launch_attempt = claim_launch(
        results_root=results_root,
        campaign=campaign,
        definition_id=definition,
        trial_id=trial_id,
    )
    condition = next(
        value for value in suite.experiment["conditions"]
        if value["id"] == row["condition_id"]
    )
    job_name = f"h{trial_id[:24]}-a{launch_attempt:06d}"
    jobs_root = run_root / "jobs"
    if jobs_root.is_symlink() or (jobs_root / job_name).is_symlink():
        raise HarborBackendError("Harbor job directory must not be a symlink")
    subject = str(condition["subject"])
    if subject not in mcp:
        raise HarborBackendError(
            f"Harbor treatment has no admitted MCP config: {subject}"
        )
    observed = execute_trial(
        settings=settings,
        row={
            "task": task_id,
            "harness": condition["agent"],
            "subject": subject,
            "attempt": int(row["trial"]) + 1,
        },
        task_path=run_root / "tasks" / task_id,
        run_root=run_root,
        credential_file=credentials,
        mcp_config=mcp[subject],
        host=host,
        job_name=job_name,
    )
    observed["stderr_tail"] = _redact(str(observed.get("stderr_tail") or ""), settings, host)
    reward_bytes = None
    trajectory_bytes = None
    answer_bytes = None
    if observed["status"] == "COMPLETE":
        if jobs_root.is_symlink() or (jobs_root / job_name).is_symlink():
            raise HarborBackendError("Harbor job directory must not be a symlink")
        job_root = (jobs_root / job_name).resolve()
        reward_bytes = _captured_job_artifact(
            observed,
            key="reward_path",
            job_root=job_root,
            label="reward",
        )
        if reward_bytes is None:
            raise HarborBackendError("Harbor reward path is missing")
        trajectory_bytes = _captured_job_artifact(
            observed,
            key="trajectory_path",
            job_root=job_root,
            label="ATIF trajectory",
        )
        answer_bytes = _captured_job_artifact(
            observed,
            key="answer_evidence_path",
            job_root=job_root,
            label="answer evidence",
        )
    status = (
        "PASS" if observed["reward"] > 0 else "FAIL"
    ) if observed["status"] == "COMPLETE" else "INCOMPLETE"
    receipt: dict[str, Any] = {
        "backend": "harbor",
        "definition_id": definition,
        "trial_id": trial_id,
        "task_id": task_id,
        "condition_id": str(row["condition_id"]),
        "trial": int(row["trial"]),
        "replicate_id": int(row["replicate_id"]),
        "harness": condition["agent"],
        "subject": condition["subject"],
        "model": settings.model,
        "status": status,
        "reason": (
            observed["stderr_tail"] or "Harbor did not produce a valid completed reward"
            if status == "INCOMPLETE"
            else None
        ),
        "diagnostic": {
            "stage": "harbor-execution" if status == "INCOMPLETE" else None,
            "reason_code": "harbor-operational-failure" if status == "INCOMPLETE" else None,
            "detail": observed["stderr_tail"] or None if status == "INCOMPLETE" else None,
        },
        "harbor": observed,
        "execution": {
            "campaign_id": campaign["campaign_id"],
            "launch_attempt": launch_attempt,
            "job_name": job_name,
            "mcp_treatment": (
                campaign.get("preflight", {})
                .get("hashmarks", {})
                .get("treatments", {})
                .get(subject)
            ),
            "ablation": (
                campaign.get("preflight", {})
                .get("hashmarks", {})
                .get("ablation")
            ),
            "factorial": (
                campaign.get("preflight", {})
                .get("hashmarks", {})
                .get("factorial")
            ),
        },
    }
    artifacts = {"harbor_result": ("harbor-result.json", canonical_json(observed))}
    if reward_bytes is not None:
        artifacts["reward"] = ("reward" + Path(str(observed["reward_path"])).suffix, reward_bytes)
    if trajectory_bytes is not None:
        artifacts["trajectory"] = ("trajectory.json", trajectory_bytes)
    if answer_bytes is not None:
        artifacts["answer"] = ("answer.json", answer_bytes)
    try:
        result_dir = publish_bundle(
            results_root=results_root,
            trial_id=trial_id,
            artifacts=artifacts,
            receipt=receipt,
        )
    except BundlePublicationError as exc:
        raise HarborBackendError(str(exc)) from exc
    valid, reason = verify_bundle(result_dir)
    if not valid:
        raise HarborBackendError(f"published Harbor receipt is invalid: {reason}")
    return TrialRunResult(
        trial_id=trial_id,
        definition_id=definition,
        status=status,
        result_dir=result_dir,
        reused=False,
        recovered=recovered,
        reason=observed["stderr_tail"] or None if status == "INCOMPLETE" else None,
        stage="harbor-execution" if status == "INCOMPLETE" else None,
        reason_code="harbor-operational-failure" if status == "INCOMPLETE" else None,
    )


def harbor_report(
    *,
    suite: SuiteDefinition,
    results_root: Path,
    selected_definitions: set[str],
) -> dict[str, Any]:
    status = campaign_status(
        suite=suite,
        results_root=results_root,
        selected_definitions=selected_definitions,
    )
    conditions = {value["id"]: value for value in suite.experiment["conditions"]}
    rows = []
    for item in status["rows"]:
        condition = conditions[str(item["condition_id"])]
        outcome = item["outcome"]
        rows.append({
            "task": item["task_id"],
            "harness": condition["agent"],
            "subject": condition["subject"],
            "attempt": int(item["trial"]) + 1,
            "status": "COMPLETE" if outcome in {"PASS", "FAIL"} else "INCOMPLETE",
            "success": outcome == "PASS",
        })
    report = build_harbor_report(rows)
    mechanism = build_mechanism_report(results_root)
    mechanism["campaign_qualified"] = status["qualified"]
    mechanism["interpretation_state"] = (
        "QUALIFIED" if status["qualified"] else "INSPECTION_ONLY"
    )
    ablation = build_ablation_report(results_root)
    factorial = build_factorial_report(results_root)
    factorial["campaign_qualified"] = status["qualified"]
    factorial["interpretation_state"] = (
        "QUALIFIED" if status["qualified"] else "INSPECTION_ONLY"
    )
    ablation["campaign_qualified"] = status["qualified"]
    ablation["interpretation_state"] = (
        "QUALIFIED" if status["qualified"] else "INSPECTION_ONLY"
    )
    report.update({
        "backend": "harbor",
        "expected_trials": status["expected_trials"],
        "completed_receipts": status["complete_trials"],
        "pending_trials": status["pending_trials"],
        "interrupted_trials": status["interrupted_trials"],
        "qualified": status["qualified"],
        "mechanism_attribution": mechanism,
        "component_ablation": ablation,
        "tool_factorial": factorial,
    })
    if not status["qualified"]:
        report["hashmarks_uplift"] = {
            harness: None for harness in report["hashmarks_uplift"]
        }
        report["harness_spread"] = {
            key: None for key in report["harness_spread"]
        }
    return report
