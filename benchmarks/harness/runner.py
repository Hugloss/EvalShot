"""Executable trial lifecycle and atomic evidence-bundle publication."""

from __future__ import annotations

import hashlib
import json
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from benchmarks.harness.admission import TrialAdmissionError, admit_trial
from benchmarks.harness.bundle import verify_bundle
from benchmarks.harness.bundle_writer import BundlePublicationError, publish_bundle
from benchmarks.harness.campaign_authority import (
    CampaignAuthorityError,
    campaign_trial_id,
    claim_launch,
    launch_state,
    record_interrupted_attempt,
    retire_completed_events,
    start_attempt_events,
    verify_trial_authority,
)
from benchmarks.harness.contamination import classify_contamination
from benchmarks.harness.events import append_event, seal_events
from benchmarks.harness.identity import (
    EXECUTION_EVIDENCE_CONTRACT,
    execution_evidence_id,
    score_projection_id,
    digest,
)
from benchmarks.adapters.oracles import RepositoryLocationOracle
from benchmarks.harness.model import Observation, TrialStatus
from benchmarks.harness.schema_validation import (
    SchemaValidationError,
    validate_instance,
)
from benchmarks.harness.suite import SuiteDefinition, effective_prompt
from benchmarks.harness.workspace import snapshot


class TrialRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrialRunResult:
    trial_id: str
    definition_id: str
    status: str
    result_dir: Path
    reused: bool
    reason: str | None = None
    stage: str | None = None
    reason_code: str | None = None
    diagnostic: str | None = None
    recovered: bool = False


def result_from_receipt(
    receipt: dict[str, Any],
    *,
    result_dir: Path,
    reused: bool,
    recovered: bool = False,
    expected_trial_id: str | None = None,
    expected_definition_id: str | None = None,
) -> TrialRunResult:
    """Project one canonical TrialRunResult from receipt-owned fields."""

    def identity(name: str, expected: str | None) -> str:
        value = receipt.get(name)
        if value is None:
            if expected is None:
                raise TrialRunnerError(
                    f"persisted result receipt has no {name}"
                )
            value = expected
        if not isinstance(value, str) or not value:
            raise TrialRunnerError(
                f"persisted result receipt {name} is not text"
            )
        if expected is not None and value != expected:
            raise TrialRunnerError(
                f"persisted result receipt {name} differs from runner result"
            )
        return value

    trial_id = identity("trial_id", expected_trial_id)
    definition_id = identity("definition_id", expected_definition_id)

    status = receipt.get("status")
    if not isinstance(status, str) or not status:
        raise TrialRunnerError("persisted result receipt has no status")

    reason = receipt.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise TrialRunnerError("persisted result receipt reason is not text")

    diagnostic = receipt.get("diagnostic")
    if diagnostic is None:
        diagnostic = {}
    if not isinstance(diagnostic, dict):
        raise TrialRunnerError(
            "persisted result receipt diagnostic is not an object"
        )

    def optional_text(name: str) -> str | None:
        value = diagnostic.get(name)
        if value is not None and not isinstance(value, str):
            raise TrialRunnerError(
                f"persisted result receipt diagnostic.{name} is not text"
            )
        return value

    return TrialRunResult(
        trial_id=trial_id,
        definition_id=definition_id,
        status=status,
        result_dir=result_dir,
        reused=reused,
        reason=reason,
        stage=optional_text("stage"),
        reason_code=optional_text("reason_code"),
        diagnostic=optional_text("detail"),
        recovered=recovered,
    )


def bind_result_to_receipt(
    result: TrialRunResult,
    receipt: dict[str, Any],
) -> TrialRunResult:
    """Rebind downstream run state through the canonical receipt projector."""
    return result_from_receipt(
        receipt,
        result_dir=result.result_dir,
        reused=result.reused,
        recovered=result.recovered,
        expected_trial_id=result.trial_id,
        expected_definition_id=result.definition_id,
    )


def reuse_completed_trial(
    *,
    results_root: Path,
    definition_id: str,
    trial_id: str,
) -> TrialRunResult:
    """Reuse one verified durable result without participant admission."""
    final_dir = results_root / trial_id
    valid, invalid_reason = verify_bundle(final_dir)
    if not valid:
        raise TrialRunnerError(
            f"existing trial bundle is invalid: {final_dir}: {invalid_reason}"
        )
    existing = json.loads(
        (final_dir / "result.json").read_text(encoding="utf-8")
    )
    return result_from_receipt(
        existing,
        result_dir=final_dir,
        reused=True,
        expected_trial_id=trial_id,
        expected_definition_id=definition_id,
    )


def _validate_result_receipt(receipt: dict[str, Any]) -> None:
    schema_path = Path(__file__).resolve().parents[1] / "schema" / "result.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        validate_instance(receipt, schema)
    except (OSError, json.JSONDecodeError, SchemaValidationError) as exc:
        raise TrialRunnerError(
            f"generated result receipt violates result schema: {exc}"
        ) from exc


def _publish_bundle(
    *,
    results_root: Path,
    trial_id: str,
    event_path: Path,
    event_seal_path: Path,
    agent_trace: str,
    receipt: dict[str, Any],
) -> Path:
    try:
        return publish_bundle(
            results_root=results_root,
            trial_id=trial_id,
            artifacts={
                "events": ("events.jsonl", event_path.read_bytes()),
                "events_seal": ("events.jsonl.seal.json", event_seal_path.read_bytes()),
                "agent_trace": ("agent-trace.jsonl", agent_trace.encode("utf-8")),
            },
            receipt=receipt,
            validate=_validate_result_receipt,
        )
    except BundlePublicationError as exc:
        raise TrialRunnerError(str(exc)) from exc


def _bounded_preview(value: Any, *, limit: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = value.strip()
    if not rendered:
        return None
    if len(rendered) > limit:
        rendered = rendered[:limit] + "…"
    return repr(rendered)


def _reason_for_oracle_failure(grade: Observation) -> str:
    reason = grade.payload.get("reason")
    rendered = str(reason) if reason else "independent oracle rejected outcome"
    actual = _bounded_preview(grade.payload.get("actual_text"))
    if actual is not None:
        return f"{rendered}; actual={actual}"
    return rendered


def _oracle_failure_diagnostic(grade: Observation) -> tuple[str, str]:
    payload = grade.payload
    gradeable = payload.get("semantic_gradeable")
    format_compliant = payload.get("format_compliant")
    semantic_status = payload.get("semantic_status")
    if gradeable is False:
        reason_code = (
            "output-contract-ungradeable"
            if format_compliant is False
            else "semantic-ungradeable"
        )
    elif semantic_status == "INCORRECT":
        reason_code = "oracle-mismatch"
    else:
        reason_code = "oracle-rejected"

    observed = payload.get("normalized_actual")
    if observed is None:
        observed = payload.get("actual")
    detail = {
        "semantic_status": semantic_status,
        "semantic_gradeable": gradeable,
        "format_compliant": format_compliant,
        "answer_shape": payload.get("answer_shape"),
        "expected": payload.get("expected"),
        "observed": observed,
        "reason": payload.get("reason"),
    }
    actual_text = _bounded_preview(payload.get("actual_text"))
    if actual_text is not None:
        detail["actual_text"] = actual_text
    return reason_code, json.dumps(
        detail,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _agent_failure(observation: Observation) -> tuple[str, str] | None:
    revalidation = observation.payload.get("authority_revalidation")
    if isinstance(revalidation, dict) and revalidation.get("status") == "failed":
        reason_code = revalidation.get("reason_code")
        if not isinstance(reason_code, str) or not reason_code:
            reason_code = "execution-authority-drift"
        reason = revalidation.get("reason")
        if not isinstance(reason, str) or not reason:
            reason = "participant execution authority changed during inference"
        return reason_code, reason

    process = observation.payload.get("process")
    if isinstance(process, dict):
        if process.get("executable_missing"):
            return "agent-executable-unavailable", "agent executable unavailable"
        if process.get("timed_out"):
            return "agent-timeout", "agent execution timed out"
        if process.get("stdout_truncated") or process.get("stderr_truncated"):
            return (
                "agent-evidence-truncated",
                "agent execution evidence exceeded configured bounds",
            )
        if process.get("return_code") not in (None, 0):
            return (
                "agent-process-nonzero",
                f"agent process exited with status {process['return_code']}",
            )
    if observation.payload.get("jsonl_parse_errors"):
        return "agent-jsonl-malformed", "agent JSONL evidence is malformed"
    terminal = observation.payload.get("terminal_event")
    if not isinstance(terminal, dict):
        return "agent-terminal-missing", "agent emitted no terminal event"
    if terminal.get("type") != "turn.completed":
        reason = terminal.get("reason")
        rendered = f"agent terminal event was {terminal.get('type')}"
        if isinstance(reason, str) and reason:
            rendered += f": {reason}"
        return "agent-terminal-failed", rendered
    answer = observation.payload.get("final_message")
    if not isinstance(answer, str) or not answer.strip():
        return (
            "agent-final-answer-missing",
            "agent completed without a final assistant answer",
        )
    return None


def _reason_for_agent(observation: Observation) -> str | None:
    failure = _agent_failure(observation)
    return failure[1] if failure is not None else None


def _bounded_diagnostic(value: str | None, *, limit: int = 8_000) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = value.strip()
    if not rendered:
        return None
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + "…"


def _agent_failure_diagnostic(
    observation: Observation,
    *,
    reason: str,
    exception_traceback: str | None = None,
) -> str | None:
    """Prefer preserved adapter/runtime evidence over the one-line reason."""
    candidates = (
        exception_traceback,
        observation.payload.get("failure_diagnostic"),
        reason,
    )
    for candidate in candidates:
        diagnostic = _bounded_diagnostic(candidate)
        if diagnostic is not None:
            return diagnostic
    return None


def run_trial(
    *,
    suite: SuiteDefinition,
    task_id: str,
    condition_id: str,
    trial_index: int,
    harness_root: Path,
    cache_root: Path,
    results_root: Path,
    work_root: Path,
    local_source: Path | None = None,
    codex_auth: Path | None = None,
    source: Mapping[str, str] | None = None,
    campaign: dict[str, Any] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> TrialRunResult:
    def emit_stage(value: str) -> None:
        if on_progress is not None:
            on_progress(value)

    try:
        emit_stage("admission")
        admission_context = admit_trial(
            suite=suite,
            task_id=task_id,
            condition_id=condition_id,
            trial_index=trial_index,
            harness_root=harness_root,
            cache_root=cache_root,
            work_root=work_root,
            local_source=local_source,
            codex_auth=codex_auth,
            source=source,
        )
        with admission_context as admission:
            task = admission.task
            expanded_condition = admission.expanded_condition
            replicate_id = admission.replicate_id
            identity_field = (
                {"seed": replicate_id}
                if admission.legacy_seed
                else {"replicate_id": replicate_id}
            )
            definition = admission.definition_id
            context = admission.context
            mutation = admission.mutation
            admitted_state = admission.admitted_state
            subject = admission.subject
            agent = admission.agent
            oracle = admission.oracle
            subject_prepare = admission.subject_prepare
            agent_prepare = admission.agent_prepare
            oracle_health = admission.oracle_health
            subject_authority = admission.subject_authority
            agent_authority = admission.agent_authority
            oracle_authority = admission.oracle_authority
            harness_authority = admission.harness_authority
            environment_authority = admission.environment_authority
            mutation_authority = admission.mutation_authority
            auth_mode = admission.auth_mode
            recovered_interruption_attempt: int | None = None
            recovered_interruption_reason: str | None = None
            authority_epoch_binding = None
            if not admission.legacy_seed:
                if campaign is None:
                    raise TrialRunnerError(
                        "replicate trial requires campaign authority"
                    )
                authority_epoch_binding = verify_trial_authority(
                    results_root=results_root,
                    campaign=campaign,
                    admission=admission,
                )
                trial_id = campaign_trial_id(
                    campaign=campaign,
                    admission=admission,
                    condition_authority_binding=authority_epoch_binding.authority,
                )
                final_dir = results_root / trial_id
                state = launch_state(
                    results_root=results_root,
                    campaign=campaign,
                    definition_id=definition,
                    trial_id=trial_id,
                )
                if state in {"INTERRUPTED", "STALE_INTERRUPTED"}:
                    emit_stage("recovery")
                    recovered_interruption_attempt = record_interrupted_attempt(
                        results_root=results_root,
                        campaign=campaign,
                        definition_id=definition,
                        trial_id=(
                            None if state == "STALE_INTERRUPTED" else trial_id
                        ),
                    )
                    recovered_interruption_reason = (
                        "authority-epoch-transition"
                        if state == "STALE_INTERRUPTED"
                        else "interrupted-launch"
                    )
                if state == "CORRUPT":
                    raise TrialRunnerError(f"existing trial bundle is corrupt: {final_dir}")
                if final_dir.exists() and state != "COMPLETE":
                    raise TrialRunnerError(
                        "result exists without a matching launch claim"
                    )
            else:
                trial_id = admission.trial_id
                final_dir = results_root / trial_id
            if final_dir.exists():
                emit_stage("reuse")
                valid, invalid_reason = verify_bundle(final_dir)
                if not valid:
                    raise TrialRunnerError(
                        f"existing trial bundle is invalid: {final_dir}: "
                        f"{invalid_reason}"
                    )
                existing = json.loads(
                    (final_dir / "result.json").read_text(encoding="utf-8")
                )
                return result_from_receipt(
                    existing,
                    result_dir=final_dir,
                    reused=True,
                    expected_trial_id=trial_id,
                    expected_definition_id=definition,
                )

            run_root = context.workspace.parent
            event_path = run_root / "events.jsonl"
            sequence = 0

            def emit(kind: str, payload: dict[str, Any]) -> None:
                nonlocal sequence
                append_event(
                    event_path,
                    trial_id=trial_id,
                    sequence=sequence,
                    kind=kind,
                    payload=payload,
                )
                sequence += 1

            emit(
                "trial.materialized",
                {
                    "repository": task["repository"],
                    "definition_id": definition,
                    **identity_field,
                },
            )
            emit("trial.mutation", mutation.payload)
            emit("subject.prepared", subject_prepare.payload)
            emit(
                "agent.prepared",
                {**agent_prepare.payload, "auth_mode": auth_mode},
            )
            emit("oracle.health", oracle_health.payload)
            if authority_epoch_binding is not None:
                emit(
                    "authority.epoch",
                    {
                        "epoch": authority_epoch_binding.epoch,
                        "epoch_id": authority_epoch_binding.epoch_id,
                        "transitioned": authority_epoch_binding.transitioned,
                        "changed_components": list(
                            authority_epoch_binding.changed_components
                        ),
                        "changed_fields": list(
                            authority_epoch_binding.changed_fields
                        ),
                    },
                )
            if recovered_interruption_attempt is not None:
                emit(
                    "trial.recovered_interruption",
                    {
                        "definition_id": definition,
                        "attempt": recovered_interruption_attempt,
                        "reason_code": (
                            recovered_interruption_reason or "interrupted-launch"
                        ),
                    },
                )

            agent_observation = Observation(
                {
                    "terminal_complete": False,
                    "final_message": None,
                    "process": {},
                },
                "",
                {},
            )
            grade = Observation(
                {"passed": False, "reason": "not graded"},
                "",
                {},
            )
            location_observation: dict[str, Any] | None = None
            stage: str | None = None
            reason_code: str | None = None
            diagnostic_detail: str | None = None
            agent_exception_traceback: str | None = None
            launch_attempt: int | None = None
            status, reason = admission.initial_outcome()
            if status is not None:
                stage = "admission"
                reason_code = "admission-initial-outcome"
            if not admission.legacy_seed and status is not None:
                raise TrialRunnerError(f"pre-model admission failed: {reason}")

            if status is None:
                resolved_prompt = effective_prompt(task, expanded_condition)
                if campaign is not None:
                    launch_attempt = claim_launch(
                        results_root=results_root,
                        campaign=campaign,
                        definition_id=definition,
                        trial_id=trial_id,
                    )
                    event_path = start_attempt_events(
                        results_root,
                        definition,
                        launch_attempt,
                        event_path.read_bytes(),
                    )
                emit(
                    "agent.started",
                    {
                        "tool_available": (
                            agent_prepare.payload.get("mcp_exposure") is not None
                        ),
                        "mode": task["mode"],
                        "prompt_sha256": hashlib.sha256(
                            resolved_prompt.encode("utf-8")
                        ).hexdigest(),
                        "prompt_context": expanded_condition.get("context"),
                        **(
                            {"attempt": launch_attempt}
                            if launch_attempt is not None
                            else {}
                        ),
                    },
                )
                emit_stage("agent-execution")
                try:
                    agent_observation = agent.run(context, resolved_prompt, subject)
                except Exception as exc:
                    agent_exception_traceback = traceback.format_exc()
                    agent_observation = Observation(
                        {
                            "terminal_event": {
                                "type": "turn.failed",
                                "reason": str(exc),
                            },
                            "terminal_complete": False,
                            "final_message": None,
                            "process": {},
                        },
                        repr(exc),
                    )
                emit(
                    "agent.completed",
                    {
                        "payload": agent_observation.payload,
                        "measurements": agent_observation.measurements,
                    },
                )
                emit_stage("grading")
                try:
                    if isinstance(oracle, RepositoryLocationOracle):
                        location_observation = oracle.observe(
                            context, agent_observation
                        )
                    budget_violation = agent_observation.payload.get("budget_violation")
                    agent_failure = _agent_failure(agent_observation)
                    if isinstance(budget_violation, str) and budget_violation:
                        status = TrialStatus.INVALID
                        reason = budget_violation
                        stage = "agent-execution"
                        reason_code = "agent-budget-violation"
                        diagnostic_detail = _bounded_diagnostic(budget_violation)
                    elif agent_failure is not None:
                        status = TrialStatus.INCOMPLETE
                        reason_code, reason = agent_failure
                        stage = "agent-execution"
                        diagnostic_detail = _agent_failure_diagnostic(
                            agent_observation,
                            reason=reason,
                            exception_traceback=agent_exception_traceback,
                        )
                    else:
                        grade = (
                            oracle.grade_observed(location_observation)
                            if isinstance(oracle, RepositoryLocationOracle)
                            and location_observation is not None
                            else oracle.grade(context, agent_observation)
                        )
                        emit(
                            "oracle.graded",
                            {
                                "payload": grade.payload,
                                "measurements": grade.measurements,
                            },
                        )
                        if grade.payload.get("valid") is False:
                            status = TrialStatus.INVALID
                            reason = "independent oracle could not complete grading"
                            stage = "oracle-grading"
                            reason_code = "oracle-invalid"
                            diagnostic_detail = _bounded_diagnostic(
                                str(grade.payload.get("reason") or reason)
                            )
                        else:
                            status = (
                                TrialStatus.PASS
                                if grade.payload.get("passed") is True
                                else TrialStatus.FAIL
                            )
                            if status is TrialStatus.FAIL:
                                reason = _reason_for_oracle_failure(grade)
                                stage = "oracle-grading"
                                reason_code, diagnostic_detail = (
                                    _oracle_failure_diagnostic(grade)
                                )
                except Exception as exc:
                    status = TrialStatus.INCOMPLETE
                    reason = f"post-execution observation failed: {exc}"
                    stage = "post-execution-observation"
                    reason_code = "post-execution-observation-exception"
                    diagnostic_detail = _bounded_diagnostic(traceback.format_exc())
                    emit("trial.observation_failed", {"reason": reason})

            emit_stage("verification")
            try:
                observed_state = snapshot(context.workspace)
                contamination_config = task["contamination"]
                allowed_generated = tuple(
                    sorted(
                        set(contamination_config["allowed_generated_globs"])
                        | set(admission.generated_globs())
                    )
                )
                contamination = classify_contamination(
                    before=admitted_state,
                    after=observed_state,
                    allowed_change_globs=tuple(
                        contamination_config["allowed_change_globs"]
                    ),
                    allowed_generated_globs=allowed_generated,
                )
            except Exception as exc:
                contamination = {
                    "contaminated": None,
                    "diff": {"added": [], "removed": [], "changed": []},
                    "observation_error": str(exc),
                }
                status = TrialStatus.INCOMPLETE
                reason = f"workspace observation failed: {exc}"
                stage = "workspace-observation"
                reason_code = "workspace-observation-exception"
                diagnostic_detail = _bounded_diagnostic(traceback.format_exc())
            emit("trial.contamination", contamination)
            if contamination["contaminated"]:
                status = TrialStatus.CONTAMINATED
                reason = "workspace changed outside frozen contamination allowances"
                stage = "contamination"
                reason_code = "workspace-contamination"
                diagnostic_detail = _bounded_diagnostic(reason)

            changed_paths = tuple(
                sorted(
                    set(contamination["diff"]["added"])
                    | set(contamination["diff"]["removed"])
                    | set(contamination["diff"]["changed"])
                )
            )
            if changed_paths:
                try:
                    post_change = admission.post_change(changed_paths)
                    emit(
                        "subject.post_change",
                        {
                            "payload": post_change.payload,
                            "measurements": post_change.measurements,
                        },
                    )
                except Exception as exc:
                    status = TrialStatus.INCOMPLETE
                    reason = f"subject post-change failed: {exc}"
                    stage = "subject-post-change"
                    reason_code = "subject-post-change-exception"
                    diagnostic_detail = _bounded_diagnostic(traceback.format_exc())
                    emit("subject.post_change_failed", {"reason": reason})
            try:
                cleanup = admission.cleanup_subject()
                emit("subject.cleanup", cleanup.payload)
            except Exception as exc:
                status = TrialStatus.INCOMPLETE
                reason = f"subject cleanup failed: {exc}"
                stage = "subject-cleanup"
                reason_code = "subject-cleanup-exception"
                diagnostic_detail = _bounded_diagnostic(traceback.format_exc())
                emit("subject.cleanup_failed", {"reason": reason})

            event_evidence = seal_events(event_path, trial_id=trial_id)
            event_seal_path = event_path.with_name(event_path.name + ".seal.json")

            agent_answer = agent_observation.payload.get("final_message")
            workspace_root = str(context.workspace.resolve())
            trace_sha256 = hashlib.sha256(
                agent_observation.raw.encode("utf-8")
            ).hexdigest()
            execution_evidence = execution_evidence_id(
                task=task,
                condition=expanded_condition,
                trial=trial_index,
                **identity_field,
                subject_identity=subject_authority,
                agent_identity=agent_authority,
                harness_identity=harness_authority,
                environment_identity=environment_authority,
                mutation_identity=mutation_authority,
                agent_answer=agent_answer,
                workspace_root=workspace_root,
                location_observation=location_observation,
                agent_trace_sha256=trace_sha256,
                **(
                    {
                        "campaign_id": campaign["campaign_id"],
                        "admitted_state_sha256": digest(admitted_state),
                    }
                    if campaign is not None and not admission.legacy_seed
                    else {}
                ),
            )
            score_projection = score_projection_id(
                execution_evidence=execution_evidence,
                oracle_identity=oracle_authority["declared"],
                **(
                    {"contract": "benchmark-score-projection.v3"}
                    if not admission.legacy_seed
                    else {}
                ),
            )

            receipt: dict[str, Any] = {
                "definition_id": definition,
                "trial_id": trial_id,
                "experiment": suite.experiment,
                "task": task,
                "condition": expanded_condition,
                "status": status.value,
                "authority": {
                    "subject": subject_authority,
                    "agent": agent_authority,
                    "oracle": oracle_authority,
                    "harness": harness_authority,
                    "environment": environment_authority,
                    "mutation": mutation_authority,
                },
                "execution": {
                    **identity_field,
                    **(
                        {"campaign_id": campaign["campaign_id"]}
                        if campaign is not None and not admission.legacy_seed
                        else {}
                    ),
                    "trial_index": trial_index,
                    **(
                        {
                            "authority_epoch": {
                                "epoch": authority_epoch_binding.epoch,
                                "epoch_id": authority_epoch_binding.epoch_id,
                                "transitioned": authority_epoch_binding.transitioned,
                                "changed_components": list(
                                    authority_epoch_binding.changed_components
                                ),
                                "changed_fields": list(
                                    authority_epoch_binding.changed_fields
                                ),
                            }
                        }
                        if authority_epoch_binding is not None
                        else {}
                    ),
                    "events": event_evidence,
                    "agent_terminal": agent_observation.payload.get("terminal_event"),
                    "agent_answer": agent_answer,
                    "workspace_root": workspace_root,
                    "admitted_state_sha256": digest(admitted_state),
                    "location_observation": location_observation,
                    "evidence_contract": (
                        EXECUTION_EVIDENCE_CONTRACT
                        if admission.legacy_seed
                        else "benchmark-execution-evidence.v3"
                    ),
                    "evidence_identity": execution_evidence,
                },
                "scoring": {
                    "projection_identity": score_projection,
                    "oracle_grade": grade.payload,
                },
                "diagnostic": {
                    "stage": stage,
                    "reason_code": reason_code,
                    "detail": diagnostic_detail,
                },
                "measurements": {
                    "subject_prepare": subject_prepare.measurements,
                    "agent_prepare": agent_prepare.measurements,
                    "agent": agent_observation.measurements,
                    "oracle_health": oracle_health.measurements,
                    "oracle_grade": grade.measurements,
                    "contamination": contamination,
                },
                "reason": reason,
            }
            emit_stage("publication")
            result_dir = _publish_bundle(
                results_root=results_root,
                trial_id=trial_id,
                event_path=event_path,
                event_seal_path=event_seal_path,
                agent_trace=agent_observation.raw,
                receipt=receipt,
            )
            if launch_attempt is not None:
                try:
                    retire_completed_events(results_root, definition, launch_attempt)
                except OSError:
                    pass  # The verified bundle already contains the sealed stream.
            persisted = json.loads(
                (result_dir / "result.json").read_text(encoding="utf-8")
            )
            return result_from_receipt(
                persisted,
                result_dir=result_dir,
                reused=False,
                recovered=recovered_interruption_attempt is not None,
                expected_trial_id=trial_id,
                expected_definition_id=definition,
            )
    except (TrialAdmissionError, CampaignAuthorityError) as exc:
        raise TrialRunnerError(str(exc)) from exc
