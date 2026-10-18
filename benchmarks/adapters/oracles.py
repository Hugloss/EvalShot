"""Independent deterministic benchmark oracles."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.harness.identity import canonical_json
from benchmarks.harness.model import Observation, ParticipantIdentity, TrialContext
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


def _text(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")


REPOSITORY_LOCATION_NORMALIZATION_POLICY = "repository-location-normalization.v3"
REPOSITORY_LOCATION_SCORING_POLICY = "repository-location-score.v2"


class DuplicateLocationKeyError(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateLocationKeyError(key)
        result[key] = value
    return result


def _normalize_repository_location(
    actual: dict[str, Any],
    *,
    workspace: Path,
) -> tuple[dict[str, str] | None, list[str], str | None]:
    normalizations: list[str] = []
    if set(actual) != {"path", "symbol"}:
        return (
            None,
            normalizations,
            "repository location must contain exactly path and symbol",
        )
    raw_path = actual.get("path")
    raw_symbol = actual.get("symbol")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return (
            None,
            normalizations,
            "repository location path must be a non-empty string",
        )
    if not isinstance(raw_symbol, str) or not raw_symbol.strip():
        return (
            None,
            normalizations,
            "repository location symbol must be a non-empty string",
        )

    workspace_root = workspace.resolve()
    candidate = Path(raw_path.strip())
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (workspace_root / candidate).resolve()
    )
    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError:
        return (
            None,
            normalizations,
            "repository location path escapes the trial workspace",
        )
    if relative == Path("."):
        return (
            None,
            normalizations,
            "repository location path must identify a repository file",
        )
    normalized_path = relative.as_posix()
    if candidate.is_absolute():
        normalizations.append("workspace-absolute-path-to-relative")
    elif raw_path.strip() != normalized_path:
        normalizations.append("repository-relative-path-canonicalized")

    raw_symbol = raw_symbol.strip()
    symbol = raw_symbol.rsplit(".", 1)[-1]
    if not symbol:
        return (
            None,
            normalizations,
            "repository location symbol is empty after qualification",
        )
    if symbol != raw_symbol:
        normalizations.append("qualified-symbol-to-terminal")

    return {"path": normalized_path, "symbol": symbol}, normalizations, None


def observe_repository_location(
    final_message: Any,
    *,
    workspace: Path,
) -> dict[str, Any]:
    """Parse and normalize execution evidence without deciding correctness."""
    observed: dict[str, Any] = {
        "answer_shape": "MISSING",
        "semantic_gradeable": False,
        "format_compliant": False,
        "actual": None,
        "normalized_actual": None,
        "normalizations": [],
        "reason": "agent final_message is missing",
        "normalization_policy": REPOSITORY_LOCATION_NORMALIZATION_POLICY,
    }
    if not isinstance(final_message, str):
        return observed

    stripped = final_message.strip()
    payload = stripped
    observed["answer_shape"] = "BARE_JSON"
    observed["format_compliant"] = True

    fence_pattern = re.compile(
        r"```json[ \t]*\r?\n(?P<payload>.*?)\r?\n```",
        re.DOTALL,
    )
    fence_matches = list(fence_pattern.finditer(stripped))
    fence_tokens = stripped.count("```")
    if fence_matches:
        if len(fence_matches) != 1 or fence_tokens != 2:
            observed["answer_shape"] = "PROSE_OR_MALFORMED"
            observed["format_compliant"] = False
            observed["reason"] = "answer contains nested or multiple code fences"
            observed["actual_text"] = final_message
            return observed
        match = fence_matches[0]
        prefix = stripped[: match.start()].strip()
        suffix = stripped[match.end() :].strip()
        payload = match.group("payload").strip()
        observed["format_compliant"] = False
        if prefix or suffix:
            observed["answer_shape"] = "PROSE_WITH_JSON_FENCE"
            observed["normalizations"].append("embedded-json-fence-extracted")
        else:
            observed["answer_shape"] = "JSON_FENCE"
            observed["normalizations"].append("json-fence-unwrapped")
    elif stripped.startswith("```") or "```" in stripped:
        observed["answer_shape"] = "PROSE_OR_MALFORMED"
        observed["format_compliant"] = False
        observed["reason"] = "answer is not one bare JSON object or one json fence"
        observed["actual_text"] = final_message
        return observed

    try:
        actual = json.loads(payload, object_pairs_hook=_unique_json_object)
    except DuplicateLocationKeyError as exc:
        observed["format_compliant"] = False
        observed["reason"] = f"agent final_message JSON has duplicate key: {exc}"
        observed["actual_text"] = final_message
        return observed
    except json.JSONDecodeError as exc:
        observed["answer_shape"] = (
            "PROSE_OR_MALFORMED"
            if observed["answer_shape"] == "BARE_JSON"
            else observed["answer_shape"]
        )
        observed["format_compliant"] = False
        observed["reason"] = f"agent final_message is not JSON: {exc.msg}"
        observed["actual_text"] = final_message
        return observed
    if not isinstance(actual, dict):
        observed["format_compliant"] = False
        observed["reason"] = "agent final_message JSON is not an object"
        observed["actual_text"] = final_message
        return observed

    observed["actual"] = actual
    structure_compliant = (
        set(actual) == {"path", "symbol"}
        and isinstance(actual.get("path"), str)
        and bool(actual["path"].strip())
        and isinstance(actual.get("symbol"), str)
        and bool(actual["symbol"].strip())
    )
    observed["format_compliant"] = observed["format_compliant"] and structure_compliant
    normalized, normalizations, reason = _normalize_repository_location(
        actual,
        workspace=workspace,
    )
    observed["normalizations"].extend(normalizations)
    observed["normalized_actual"] = normalized
    observed["semantic_gradeable"] = normalized is not None
    observed["reason"] = reason
    return observed


def score_repository_location(
    observed: dict[str, Any],
    *,
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Apply deterministic repository-location scoring to one observation."""
    gradeable = observed.get("semantic_gradeable") is True
    normalized = observed.get("normalized_actual")
    if not gradeable:
        semantic_status = "UNSCORABLE"
        semantic_success = False
        reason = observed.get("reason") or "repository location is unscorable"
    elif normalized == expected:
        semantic_status = "CORRECT"
        semantic_success = True
        reason = None
    else:
        semantic_status = "INCORRECT"
        semantic_success = False
        reason = "repository location differs from frozen oracle"

    grade = {
        "passed": semantic_success,
        "valid": True,
        "oracle_truth_status": "VALID",
        "semantic_status": semantic_status,
        "semantic_success": semantic_success,
        "semantic_gradeable": gradeable,
        "format_compliant": observed.get("format_compliant") is True,
        "answer_shape": observed.get("answer_shape"),
        "expected": expected,
        "actual": observed.get("actual"),
        "normalized_actual": normalized,
        "normalizations": list(observed.get("normalizations", [])),
        "normalization_policy": REPOSITORY_LOCATION_NORMALIZATION_POLICY,
        "scoring_policy": REPOSITORY_LOCATION_SCORING_POLICY,
        "reason": reason,
    }
    actual_text = observed.get("actual_text")
    if isinstance(actual_text, str):
        grade["actual_text"] = actual_text
    return grade


@dataclass(frozen=True)
class RepositoryLocationOracle:
    participant_id: str
    version: str
    expected: dict[str, Any]

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            self.participant_id,
            "oracle",
            self.version,
            {
                "kind": "repository-location-json",
                "expected": self.expected,
                "normalization_policy": REPOSITORY_LOCATION_NORMALIZATION_POLICY,
                "scoring_policy": REPOSITORY_LOCATION_SCORING_POLICY,
            },
        )

    def healthcheck(self, context: TrialContext) -> Observation:
        normalized, _normalizations, reason = _normalize_repository_location(
            self.expected,
            workspace=context.workspace,
        )
        healthy = normalized == self.expected and reason is None
        return Observation(
            {
                "healthy": healthy,
                "oracle_truth_status": "VALID" if healthy else "INVALID",
                "reason": (
                    None
                    if healthy
                    else reason or "expected repository location is not canonical"
                ),
                "normalization_policy": REPOSITORY_LOCATION_NORMALIZATION_POLICY,
                "scoring_policy": REPOSITORY_LOCATION_SCORING_POLICY,
            },
            "",
        )

    def observe(
        self, context: TrialContext, observation: Observation
    ) -> dict[str, Any]:
        return observe_repository_location(
            observation.payload.get("final_message"),
            workspace=context.workspace,
        )

    def grade_observed(self, observed: dict[str, Any]) -> Observation:
        grade = score_repository_location(observed, expected=self.expected)
        raw = grade.get("normalized_actual") or grade.get("actual") or {}
        return Observation(grade, json.dumps(raw, sort_keys=True))

    def grade(self, context: TrialContext, observation: Observation) -> Observation:
        return self.grade_observed(self.observe(context, observation))


@dataclass(frozen=True)
class ExpectedJsonOracle:
    participant_id: str
    version: str
    expected: dict[str, Any]

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            self.participant_id,
            "oracle",
            self.version,
            {"kind": "expected-json", "expected": self.expected},
        )

    def healthcheck(self, context: TrialContext) -> Observation:
        try:
            canonical_json(self.expected)
        except (TypeError, ValueError) as exc:
            return Observation(
                {"healthy": False, "reason": f"invalid expected JSON: {exc}"},
                "",
            )
        return Observation({"healthy": True}, "")

    def grade(self, context: TrialContext, observation: Observation) -> Observation:
        final_message = observation.payload.get("final_message")
        if not isinstance(final_message, str):
            return Observation(
                {
                    "passed": False,
                    "valid": True,
                    "reason": "agent final_message is missing",
                },
                "",
            )
        try:
            actual = json.loads(final_message)
        except json.JSONDecodeError as exc:
            return Observation(
                {
                    "passed": False,
                    "valid": True,
                    "reason": f"agent final_message is not JSON: {exc.msg}",
                    "actual_text": final_message,
                },
                "",
            )
        passed = actual == self.expected
        return Observation(
            {
                "passed": passed,
                "valid": True,
                "expected": self.expected,
                "actual": actual,
                "reason": None if passed else "JSON answer differs from frozen oracle",
            },
            json.dumps(actual, sort_keys=True),
        )


@dataclass(frozen=True)
class CommandOracle:
    participant_id: str
    version: str
    health_argv: tuple[str, ...]
    grade_argv: tuple[str, ...]
    timeout_seconds: int = 60
    valid_exit_codes: tuple[int, ...] | None = None
    result_format: str = "text"

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            self.participant_id,
            "oracle",
            self.version,
            {
                "health_argv": self.health_argv,
                "grade_argv": self.grade_argv,
                "result_format": self.result_format,
            },
        )

    def _run(
        self,
        context: TrialContext,
        argv: tuple[str, ...],
        *,
        environment: dict[str, str] | None = None,
    ):
        effective_environment = dict(context.environment)
        if environment:
            effective_environment.update(environment)
        return run_bounded(
            repository_root=context.workspace,
            argv=argv,
            environment=effective_environment,
            limits=ProcessLimits(
                timeout_seconds=self.timeout_seconds,
                max_stdout_bytes=5_000_000,
                max_stderr_bytes=2_000_000,
            ),
            inherit_environment=False,
        )

    def healthcheck(self, context: TrialContext) -> Observation:
        result = self._run(context, self.health_argv)
        healthy = (
            not result.executable_missing
            and not result.timed_out
            and result.return_code == 0
            and not result.stdout_truncated
            and not result.stderr_truncated
        )
        return Observation(
            {
                "healthy": healthy,
                "process": result.metrics(),
                "stderr": _text(result.stderr),
            },
            _text(result.stdout),
            {"duration_ms": result.elapsed_ms},
        )

    def grade(self, context: TrialContext, observation: Observation) -> Observation:
        with tempfile.TemporaryDirectory(
            prefix="benchmark-oracle-",
            dir=context.environment.get("TMPDIR"),
        ) as tmp:
            observation_path = Path(tmp) / "observation.json"
            observation_path.write_bytes(
                canonical_json(
                    {
                        "payload": observation.payload,
                        "raw": observation.raw,
                        "measurements": observation.measurements,
                    }
                )
            )
            result = self._run(
                context,
                self.grade_argv,
                environment={"BENCHMARK_OBSERVATION_PATH": str(observation_path)},
            )
        valid = (
            not result.executable_missing
            and not result.timed_out
            and not result.stdout_truncated
            and not result.stderr_truncated
            and result.return_code is not None
            and (
                self.valid_exit_codes is None
                or result.return_code in self.valid_exit_codes
            )
        )
        passed = valid and result.return_code == 0
        detail: dict[str, Any] = {}
        if valid and self.result_format == "lexigram-v1":
            try:
                value = json.loads(_text(result.stdout))
                if (
                    not isinstance(value, dict)
                    or value.get("schema") != "agents-cookbook-lexigram-oracle.v1"
                    or not isinstance(value.get("passed"), bool)
                    or value["passed"] != passed
                    or not isinstance(value.get("rubric"), dict)
                    or not isinstance(value.get("reason"), str)
                ):
                    raise ValueError("invalid structured oracle result")
                detail = {"rubric": value["rubric"], "reason": value["reason"]}
            except (ValueError, json.JSONDecodeError):
                valid = False
                passed = False
        return Observation(
            {
                "passed": passed,
                "valid": valid,
                **detail,
                "process": result.metrics(),
                "stderr": _text(result.stderr),
            },
            _text(result.stdout),
            {"duration_ms": result.elapsed_ms},
        )
