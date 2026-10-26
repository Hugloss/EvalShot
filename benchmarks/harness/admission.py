"""Shared benchmark trial admission used by preflight and execution."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import platform
import shutil
from collections.abc import Mapping
import tempfile
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from benchmarks.adapters.codex import seed_codex_auth
from benchmarks.adapters.registry import build_agent, build_oracle, build_subject
from benchmarks.harness.contamination import classify_contamination
from benchmarks.harness.identity import definition_id, digest, execution_id
from benchmarks.harness.model import (
    Observation,
    SubjectLifecycleMode,
    TrialContext,
    TrialStatus,
)
from benchmarks.harness.mutation import apply_mutation
from benchmarks.harness.runtime_authority import (
    oh_goon_temporary_parent,
    transport_native_codex_environment,
    native_host_paths,
    transport_runtime_authority,
)
from benchmarks.harness.source import materialize_repository
from benchmarks.harness.suite import SuiteDefinition
from benchmarks.harness.workspace import (
    initialize_git_subdirectory,
    isolated_environment,
    snapshot,
)
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


class TrialAdmissionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrialAdmission:
    suite: SuiteDefinition
    task: dict[str, Any]
    condition: dict[str, Any]
    expanded_condition: dict[str, Any]
    trial_index: int
    replicate_id: int
    legacy_seed: bool
    definition_id: str
    context: TrialContext
    mutation: Observation
    admitted_state: dict[str, Any]
    subject: Any
    agent: Any
    oracle: Any
    subject_prepare: Observation
    agent_prepare: Observation
    oracle_health: Observation
    subject_authority: dict[str, Any]
    agent_authority: dict[str, Any]
    oracle_authority: dict[str, Any]
    harness_authority: dict[str, Any]
    environment_authority: dict[str, Any]
    mutation_authority: Any
    auth_mode: str
    subject_lifecycle_mode: SubjectLifecycleMode
    admission_timings_ms: dict[str, int]

    def generated_globs(self) -> tuple[str, ...]:
        # Output ownership is independent of who invokes the subject. A native
        # agent may invoke the subject's MCP tools inside the trial workspace.
        return self.subject.generated_globs()

    def prework_contamination(self) -> dict[str, Any]:
        """Check setup effects before inference against the original source seal.

        Task edit allowances apply to agent work, never participant preparation.
        Only the subject's declared disposable generated outputs are excluded.
        Both preflight and run consume this one classification.
        """
        return classify_contamination(
            before=self.admitted_state,
            after=snapshot(self.context.workspace),
            allowed_change_globs=(),
            allowed_generated_globs=tuple(sorted(set(self.generated_globs()))),
        )

    def post_change(
        self,
        changed_paths: tuple[str, ...],
    ) -> Observation:
        if self.subject_lifecycle_mode is SubjectLifecycleMode.ADAPTER:
            return self.subject.post_change(self.context, changed_paths)
        return Observation(
            {
                "changed_paths": list(changed_paths),
                "lifecycle_owner": "agent-native",
            },
            "",
        )

    def cleanup_subject(self) -> Observation:
        if self.subject_lifecycle_mode is SubjectLifecycleMode.ADAPTER:
            return self.subject.cleanup(self.context)
        return Observation({"lifecycle_owner": "agent-native"}, "")

    @property
    def trial_id(self) -> str:
        return execution_id(
            definition=self.definition_id,
            subject_identity=self.subject_authority,
            agent_identity=self.agent_authority,
            oracle_identity=self.oracle_authority,
            harness_identity=self.harness_authority,
            environment_identity=self.environment_authority,
            mutation_identity=self.mutation_authority,
        )

    def initial_outcome(self) -> tuple[TrialStatus | None, str | None]:
        if not self.subject_authority["available"]:
            reason = self.subject_prepare.payload.get("reason")
            return (
                TrialStatus.INCOMPLETE,
                str(reason)
                if isinstance(reason, str) and reason
                else "subject unavailable or preparation failed",
            )
        if not self.agent_authority["available"]:
            reason = self.agent_prepare.payload.get("reason")
            return (
                TrialStatus.INCOMPLETE,
                str(reason)
                if isinstance(reason, str) and reason
                else "agent unavailable or preparation failed",
            )
        if not self.oracle_authority["healthy"]:
            return (
                TrialStatus.INVALID,
                "independent oracle healthcheck failed",
            )
        return None, None


def _git_bytes(root: Path, argv: tuple[str, ...]) -> bytes:
    result = run_bounded(
        repository_root=root,
        argv=argv,
        limits=ProcessLimits(
            timeout_seconds=30.0,
            max_stdout_bytes=50_000_000,
            max_stderr_bytes=1_000_000,
        ),
    )
    if (
        result.executable_missing
        or result.timed_out
        or result.return_code != 0
        or result.stdout_truncated
        or result.stderr_truncated
    ):
        raise TrialAdmissionError(
            f"cannot establish harness git authority: {' '.join(argv)}"
        )
    return result.stdout


def _git_value(root: Path, argv: tuple[str, ...]) -> str:
    return _git_bytes(root, argv).decode("utf-8", errors="strict").strip()


def harness_identity(root: Path, *, repository: str = "Hugloss/EvalShot") -> dict[str, Any]:
    root = root.resolve()
    commit = _git_value(root, ("git", "rev-parse", "HEAD"))
    tree = _git_value(root, ("git", "rev-parse", f"{commit}^{{tree}}"))
    diff = _git_bytes(root, ("git", "diff", "--binary", commit))
    untracked_raw = _git_bytes(
        root,
        ("git", "ls-files", "--others", "--exclude-standard", "-z"),
    )
    untracked = [path for path in untracked_raw.split(b"\0") if path]
    untracked_payloads: list[tuple[bytes, bytes]] = []
    fingerprint = hashlib.sha256(diff)
    try:
        for path in untracked:
            payload = (root / os.fsdecode(path)).read_bytes()
            untracked_payloads.append((path, payload))
            fingerprint.update(path)
            fingerprint.update(payload)
    except OSError as exc:
        raise TrialAdmissionError(
            "cannot fingerprint untracked benchmark files"
        ) from exc

    if _git_value(root, ("git", "rev-parse", "HEAD")) != commit:
        raise TrialAdmissionError(
            "harness git authority changed during identity observation"
        )
    if _git_bytes(root, ("git", "diff", "--binary", commit)) != diff:
        raise TrialAdmissionError(
            "harness git authority changed during identity observation"
        )
    if (
        _git_bytes(
            root,
            ("git", "ls-files", "--others", "--exclude-standard", "-z"),
        )
        != untracked_raw
    ):
        raise TrialAdmissionError(
            "harness git authority changed during identity observation"
        )
    try:
        for path, payload in untracked_payloads:
            if (root / os.fsdecode(path)).read_bytes() != payload:
                raise TrialAdmissionError(
                    "harness git authority changed during identity observation"
                )
    except OSError as exc:
        raise TrialAdmissionError(
            "harness git authority changed during identity observation"
        ) from exc

    return {
        "repository": repository,
        "commit": commit,
        "tree": tree,
        "working_copy_sha256": fingerprint.hexdigest(),
        "working_copy_clean": not diff and not untracked,
        "contract": "generic-benchmark-runner-v1",
    }

def runtime_environment_identity(
    environment: dict[str, str],
    *,
    control_root: Path,
    temporary_root: Path | None = None,
) -> dict[str, Any]:
    control_root = control_root.resolve()
    roots = [(control_root, "$CONTROL_ROOT/")]
    if temporary_root is not None:
        roots.insert(0, (temporary_root.resolve(), "$TRIAL_TEMP/"))

    def semantic_value(value: str) -> str:
        candidate = Path(value)
        if not candidate.is_absolute():
            return value
        for root, token in roots:
            try:
                relative = candidate.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            return token + relative.as_posix()
        return value

    semantic = {
        key: semantic_value(value) for key, value in sorted(environment.items())
    }
    digest = hashlib.sha256()
    for key, value in semantic.items():
        digest.update(key.encode())
        digest.update(b"\0")
        digest.update(value.encode())
        digest.update(b"\0")
    return {
        "isolation_contract": ("explicit-benchmark-environment-v4"),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "environment_sha256": digest.hexdigest(),
        "environment_variables": sorted(semantic),
    }


def _declared(identity) -> dict[str, Any]:
    return dataclasses.asdict(identity)


def _subject_authority(subject, prepared: Observation) -> dict[str, Any]:
    return {
        "declared": _declared(subject.identity()),
        "available": bool(prepared.payload.get("available")),
        "observed": prepared.payload.get("observed_identity"),
    }


def _native_subject_preparation(
    subject,
    agent_prepare: Observation,
) -> Observation:
    identity = subject.identity()
    if identity.kind == "control" or identity.participant_id == "none":
        return Observation(
            {
                "available": True,
                "lifecycle_owner": "agent-native",
                "observed_identity": {
                    "source": "agent-native-control",
                },
            },
            "",
        )

    observed = agent_prepare.payload.get("observed_identity")
    stable_binding = (
        observed.get("workspace_binding") if isinstance(observed, dict) else None
    )
    exposure = agent_prepare.payload.get("mcp_exposure")
    native_identity = agent_prepare.payload.get("native_subject_identity")
    native_identity_verified = (
        isinstance(native_identity, dict)
        and native_identity.get("verified") is True
        and native_identity.get("subject") == identity.participant_id
        and isinstance(native_identity.get("executable_sha256"), str)
    )
    available = (
        bool(agent_prepare.payload.get("available"))
        and isinstance(exposure, dict)
        and exposure.get("name") == identity.participant_id
        and native_identity_verified
    )
    return Observation(
        {
            "available": available,
            "reason": (
                None
                if available
                else agent_prepare.payload.get("reason")
                or "native agent subject admission failed"
            ),
            "lifecycle_owner": "agent-native",
            "observed_identity": {
                "source": (
                    exposure.get("source")
                    if isinstance(exposure, dict) and exposure.get("source")
                    else "native-agent-runtime"
                ),
                "subject": identity.participant_id,
                "mcp_exposure": exposure,
                "native_config_sha256": agent_prepare.payload.get(
                    "native_config_sha256"
                ),
                "native_subject_identity": native_identity,
                "workspace_binding": stable_binding,
            },
        },
        "",
    )


def _agent_authority(agent, prepared: Observation) -> dict[str, Any]:
    return {
        "declared": _declared(agent.identity()),
        "available": bool(prepared.payload.get("available")),
        "observed": {
            "version": prepared.payload.get("version"),
            "executable_sha256": prepared.payload.get("executable_sha256"),
            "mcp_exposure": prepared.payload.get("mcp_exposure"),
            "auth_mode": prepared.payload.get("auth_mode"),
            "model": prepared.payload.get("model"),
            "reasoning_effort": prepared.payload.get("reasoning_effort"),
            "provider": prepared.payload.get("provider"),
            "execution_runtime_identity": prepared.payload.get("execution_runtime_identity"),
            "native_config_sha256": prepared.payload.get("native_config_sha256"),
            "native_config_observation": prepared.payload.get("native_config_observation"),
            "native_state_isolation": prepared.payload.get("native_state_isolation"),
            "native_plugins_observation": prepared.payload.get("native_plugins_observation"),
            "native_namespace_observation": prepared.payload.get("native_namespace_observation"),
            "native_mcp_servers": prepared.payload.get("native_mcp_servers"),
        },
    }


def _oracle_authority(oracle, health: Observation) -> dict[str, Any]:
    return {
        "declared": _declared(oracle.identity()),
        "healthy": bool(health.payload.get("healthy")),
    }


def _resolve_condition(
    suite: SuiteDefinition,
    condition_id: str,
) -> dict[str, Any]:
    condition = next(
        (
            value
            for value in suite.experiment["conditions"]
            if value["id"] == condition_id
        ),
        None,
    )
    if condition is None:
        raise TrialAdmissionError(f"unknown condition: {condition_id}")
    return condition


def _materialize_fixtures(
    suite_root: Path,
    workspace: Path,
    fixtures: list[dict[str, str]],
) -> None:
    for fixture in fixtures:
        source = (suite_root / fixture["artifact"]).resolve()
        try:
            source.relative_to(suite_root.resolve())
        except ValueError as exc:
            raise TrialAdmissionError("fixture source escapes suite") from exc
        if hashlib.sha256(source.read_bytes()).hexdigest() != fixture["sha256"]:
            raise TrialAdmissionError("fixture changed after suite validation")
        target = (workspace / fixture["target"]).resolve()
        try:
            target.relative_to(workspace.resolve())
        except ValueError as exc:
            raise TrialAdmissionError("fixture target escapes trial workspace") from exc
        if target.exists() or target.is_symlink():
            raise TrialAdmissionError(
                f"fixture target already exists: {fixture['target']}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


@contextmanager
def admit_trial(
    *,
    suite: SuiteDefinition,
    task_id: str,
    condition_id: str,
    trial_index: int,
    harness_root: Path,
    cache_root: Path,
    work_root: Path,
    local_source: Path | None = None,
    codex_auth: Path | None = None,
    source: Mapping[str, str] | None = None,
    harness_authority: dict[str, Any] | None = None,
) -> Iterator[TrialAdmission]:
    source = os.environ if source is None else source
    task = suite.tasks[task_id]
    condition = _resolve_condition(suite, condition_id)
    if trial_index < 0 or trial_index >= int(condition["trials"]):
        raise TrialAdmissionError(
            f"trial index {trial_index} outside condition trial range"
        )

    expanded_condition = suite.expanded_condition(condition)
    legacy_seed = "replicate_ids" not in condition
    replicate_id = (
        int(condition["seed"]) + trial_index
        if legacy_seed
        else int(condition["replicate_ids"][trial_index])
    )
    definition = definition_id(
        experiment=suite.experiment,
        task=task,
        condition=expanded_condition,
        trial=trial_index,
        **({"seed": replicate_id} if legacy_seed else {"replicate_id": replicate_id}),
    )

    agent_definition = suite.agents[str(condition["agent"])]
    temporary_parent = None
    if agent_definition["adapter"] == "oh-goon":
        try:
            temporary_parent = oh_goon_temporary_parent(source)
        except (OSError, ValueError) as exc:
            raise TrialAdmissionError(str(exc)) from exc

    admission_started = time.monotonic()
    timings_ms: dict[str, int] = {}

    stage_started = time.monotonic()
    effective_harness_authority = (
        dict(harness_authority)
        if harness_authority is not None
        else harness_identity(harness_root)
    )
    timings_ms["harness_identity"] = int(
        round((time.monotonic() - stage_started) * 1000)
    )

    work_root.mkdir(parents=True, exist_ok=True)
    with (
        ExitStack() as resources,
        tempfile.TemporaryDirectory(
            prefix=f"{definition[:12]}-",
            dir=work_root,
        ) as temporary,
    ):
        run_root = Path(temporary)
        workspace = run_root / "workspace"

        stage_started = time.monotonic()
        materialize_repository(
            repository=task["repository"],
            destination=workspace,
            cache_root=cache_root,
            local_source=local_source,
        )
        timings_ms["materialize"] = int(
            round((time.monotonic() - stage_started) * 1000)
        )

        stage_started = time.monotonic()
        control_root = run_root / "control"
        temporary_root = None
        if temporary_parent is not None:
            temporary_root = Path(
                resources.enter_context(
                    tempfile.TemporaryDirectory(prefix="e-", dir=temporary_parent)
                )
            )
        environment = isolated_environment(
            control_root, source=source, temporary_root=temporary_root
        )
        try:
            transport_runtime_authority(source, environment)
        except ValueError as exc:
            raise TrialAdmissionError(str(exc)) from exc
        context = TrialContext(
            workspace=workspace,
            control_root=control_root,
            environment=environment,
            temporary_root=temporary_root,
        )
        timings_ms["environment"] = int(
            round((time.monotonic() - stage_started) * 1000)
        )

        stage_started = time.monotonic()
        mutation = apply_mutation(
            context,
            suite_root=suite.root,
            mutation=task["mutation"],
        )
        _materialize_fixtures(suite.root, workspace, task.get("fixtures", []))
        agent = build_agent(
            agent_definition, budgets=task["budgets"], suite_root=suite.root
        )
        if agent_definition["adapter"] == "oh-goon":
            initialize_git_subdirectory(
                workspace=workspace,
                subdirectory=agent.workspace_subdirectory,
                control_root=control_root,
                environment=context.environment,
            )
        timings_ms["mutation_fixtures"] = int(
            round((time.monotonic() - stage_started) * 1000)
        )

        stage_started = time.monotonic()
        admitted_state = snapshot(workspace)
        timings_ms["snapshot"] = int(
            round((time.monotonic() - stage_started) * 1000)
        )
        context = dataclasses.replace(
            context,
            admission_scope=digest(
                {
                    "task_id": task_id,
                    "workspace": admitted_state,
                    "mutation": mutation.payload.get("identity"),
                }
            ),
        )

        subject = build_subject(suite.subjects[str(condition["subject"])])
        native_codex = (
            agent_definition["adapter"] == "codex"
            and agent_definition.get("configuration", {}).get("native_host") is True
        )
        native_opencode = agent_definition["adapter"] == "opencode-native"
        if native_codex or native_opencode:
            native_paths = native_host_paths(source)
            if native_codex:
                transport_native_codex_environment(source, context.environment)
                context.environment["BENCHMARK_NATIVE_CODEX_HOME"] = native_paths["codex_home"]
                context.environment["BENCHMARK_NATIVE_HOME"] = native_paths["home"]
            if native_opencode:
                context.environment["BENCHMARK_NATIVE_HOME"] = native_paths["home"]
                context.environment["BENCHMARK_NATIVE_XDG_CONFIG_HOME"] = native_paths[
                    "xdg_config_home"
                ]
        auth_mode = (
            "native-host"
            if native_codex
            else seed_codex_auth(context, codex_auth)
            if agent_definition["adapter"] == "codex"
            else "native-opencode"
            if agent_definition["adapter"] == "opencode-native"
            else "not-applicable"
        )
        if agent_definition["adapter"] == "codex":
            context.environment["BENCHMARK_CODEX_AUTH_MODE"] = auth_mode

        environment_authority = runtime_environment_identity(
            context.environment,
            control_root=context.control_root,
            temporary_root=context.temporary_root,
        )

        oracle = build_oracle(
            task["oracle"],
            timeout_seconds=int(task["budgets"]["timeout_seconds"]),
            suite_root=suite.root,
        )

        lifecycle_mode = agent.subject_lifecycle_mode()
        stage_started = time.monotonic()
        if lifecycle_mode is SubjectLifecycleMode.ADAPTER:
            subject_prepare = subject.prepare(context)
            agent_prepare = agent.prepare(context, subject)
        elif lifecycle_mode is SubjectLifecycleMode.AGENT_NATIVE:
            agent_prepare = agent.prepare(context, subject)
            subject_prepare = _native_subject_preparation(
                subject,
                agent_prepare,
            )
        else:
            raise TrialAdmissionError(
                f"unsupported subject lifecycle mode: {lifecycle_mode}"
            )
        timings_ms["participant_prepare"] = int(
            round((time.monotonic() - stage_started) * 1000)
        )

        stage_started = time.monotonic()
        oracle_health = oracle.healthcheck(context)
        timings_ms["oracle_health"] = int(
            round((time.monotonic() - stage_started) * 1000)
        )
        timings_ms["total"] = int(
            round((time.monotonic() - admission_started) * 1000)
        )

        yield TrialAdmission(
            suite=suite,
            task=task,
            condition=condition,
            expanded_condition=expanded_condition,
            trial_index=trial_index,
            replicate_id=replicate_id,
            legacy_seed=legacy_seed,
            definition_id=definition,
            context=context,
            mutation=mutation,
            admitted_state=admitted_state,
            subject=subject,
            agent=agent,
            oracle=oracle,
            subject_prepare=subject_prepare,
            agent_prepare=agent_prepare,
            oracle_health=oracle_health,
            subject_authority=_subject_authority(subject, subject_prepare),
            agent_authority=_agent_authority(agent, agent_prepare),
            oracle_authority=_oracle_authority(oracle, oracle_health),
            harness_authority=effective_harness_authority,
            environment_authority=environment_authority,
            mutation_authority=mutation.payload.get("identity"),
            auth_mode=auth_mode,
            subject_lifecycle_mode=lifecycle_mode,
            admission_timings_ms=dict(timings_ms),
        )
