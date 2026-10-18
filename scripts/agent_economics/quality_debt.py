from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from .bounded_process import ProcessLimits, run_bounded
from .probe_contract import (
    analyzed_input_identity,
    build_probe_contract,
    configuration_identity,
)
from .refactor_focus_paths import iso_utc_now

TOOL_NAME = "quality-debt"
TOOL_VERSION = "0.15.0"
_OBSERVED_LIMIT = re.compile(r"\((\d+)\s*(?:>|/)\s*(\d+)\)$")
_RUFF_LIMIT_CONFIG = {
    "C901": "lint.mccabe.max-complexity",
    "PLR0911": "lint.pylint.max-returns",
    "PLR0912": "lint.pylint.max-branches",
    "PLR0913": "lint.pylint.max-args",
    "PLR0914": "lint.pylint.max-locals",
    "PLR0915": "lint.pylint.max-statements",
    "PLR0916": "lint.pylint.max-bool-expr",
}


class QualityDebtError(ValueError):
    pass


def _inside(root: Path, raw: str) -> tuple[Path, str]:
    path = Path(raw)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise QualityDebtError(f"analyzer path escapes repository root: {raw}") from exc
    return resolved, relative.as_posix()


def _normalize_scope_path(root: Path, raw: str, *, kind: str) -> str:
    if not raw:
        raise QualityDebtError(f"{kind} must not be empty")
    resolved, relative = _inside(root, raw)
    if relative in ("", "."):
        raise QualityDebtError(f"{kind} must not resolve to repository root: {raw}")
    return relative.rstrip("/")


def _excluded(relative: str, excludes: tuple[str, ...]) -> bool:
    return any(relative == item or relative.startswith(item + "/") for item in excludes)


def _validate_scope(
    root: Path, roots: tuple[str, ...], excludes: tuple[str, ...]
) -> list[dict[str, str]]:
    resolved_roots: list[tuple[str, Path]] = []
    seen: dict[Path, str] = {}
    for raw in roots:
        resolved, relative = _inside(root, raw)
        if not resolved.exists():
            raise QualityDebtError(f"configured root does not exist: {raw}")
        if resolved in seen:
            raise QualityDebtError(
                f"duplicate configured root: {raw} resolves to {seen[resolved]}"
            )
        for previous_raw, previous in resolved_roots:
            if resolved in previous.parents or previous in resolved.parents:
                raise QualityDebtError(
                    f"overlapping configured roots: {previous_raw} and {raw}"
                )
        seen[resolved] = relative
        resolved_roots.append((raw, resolved))

    diagnostics: list[dict[str, str]] = []
    for raw, resolved in resolved_roots:
        relative = resolved.relative_to(root).as_posix()
        if _excluded(relative, excludes):
            diagnostics.append(
                {
                    "code": "configured_root_fully_excluded",
                    "root": raw,
                    "message": f"configured root is fully excluded: {raw}",
                }
            )
    return diagnostics


def _source_identity(
    root: Path,
    roots: tuple[str, ...],
    suffixes: tuple[str, ...],
    excludes: tuple[str, ...],
) -> tuple[str, int, int, dict[str, object]]:
    entries: list[dict[str, str]] = []
    total = 0
    root_counts: dict[str, int] = {}
    excluded_counts: dict[str, int] = {item: 0 for item in excludes}
    for raw_root in roots:
        base, _ = _inside(root, raw_root)
        if not base.exists():
            raise QualityDebtError(f"configured root does not exist: {raw_root}")
        root_counts[raw_root] = 0
        paths = (
            [base]
            if base.is_file()
            else sorted(p for p in base.rglob("*") if p.is_file())
        )
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if path.suffix not in suffixes:
                continue
            matching_excludes = [
                item
                for item in excludes
                if relative == item.rstrip("/")
                or relative.startswith(item.rstrip("/") + "/")
            ]
            if matching_excludes:
                for item in matching_excludes:
                    excluded_counts[item] += 1
                continue
            root_counts[raw_root] += 1
            data = path.read_bytes()
            total += len(data)
            entries.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    return (
        analyzed_input_identity(entries),
        len(entries),
        total,
        {
            "configured_roots": list(roots),
            "root_file_counts": root_counts,
            "excludes": list(excludes),
            "excluded_python_files_by_rule": excluded_counts,
            "analyzed_python_files": len(entries),
        },
    )


def _analyzer_version(root: Path, executable: str, timeout_seconds: float) -> str:
    result = run_bounded(
        repository_root=root,
        argv=(executable, "--version"),
        limits=ProcessLimits(timeout_seconds, 16_384, 16_384),
    )
    if result.executable_missing:
        raise QualityDebtError(f"analyzer executable unavailable: {executable}")
    if (
        result.timed_out
        or result.stdout_truncated
        or result.stderr_truncated
        or result.return_code != 0
    ):
        raise QualityDebtError(
            "analyzer version command did not complete within bounds"
        )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _run_ruff(
    root: Path,
    *,
    executable: str,
    roots: tuple[str, ...],
    limits: dict[str, int],
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    excludes: tuple[str, ...],
) -> tuple[list[dict[str, object]], dict[str, object], tuple[str, ...]]:
    unsupported = sorted(set(limits) - set(_RUFF_LIMIT_CONFIG))
    if unsupported:
        raise QualityDebtError(
            "unsupported Ruff debt limit rules: " + ", ".join(unsupported)
        )
    exclude_args = ("--exclude", ",".join(excludes)) if excludes else ()
    limit_config_args = tuple(
        item
        for rule in sorted(limits)
        for item in ("--config", f"{_RUFF_LIMIT_CONFIG[rule]} = {limits[rule]}")
    )
    argv = (
        executable,
        "check",
        *roots,
        "--preview",
        "--select",
        ",".join(sorted(limits)),
        "--isolated",
        "--config",
        "lint.per-file-ignores = {}",
        *limit_config_args,
        *exclude_args,
        "--output-format",
        "json",
    )
    result = run_bounded(
        repository_root=root,
        argv=argv,
        limits=ProcessLimits(timeout_seconds, max_stdout_bytes, max_stderr_bytes),
    )
    if result.executable_missing:
        raise QualityDebtError(f"analyzer executable unavailable: {executable}")
    if result.timed_out:
        raise QualityDebtError("analyzer timed out")
    if result.stdout_truncated or result.stderr_truncated:
        raise QualityDebtError("analyzer output exceeded configured byte bound")
    if result.return_code not in (0, 1):
        raise QualityDebtError(
            "analyzer failed: " + result.stderr.decode("utf-8", errors="replace")[:1000]
        )
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualityDebtError("analyzer did not return valid UTF-8 JSON") from exc
    if not isinstance(value, list):
        raise QualityDebtError("analyzer JSON root must be a list")
    return value, result.metrics(), argv


def _findings(
    root: Path,
    diagnostics: list[dict[str, object]],
    limits: dict[str, int],
    excludes: tuple[str, ...],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, int], dict[str, object]] = {}
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            raise QualityDebtError("analyzer diagnostic must be an object")
        rule = diagnostic.get("code")
        filename = diagnostic.get("filename")
        location = diagnostic.get("location")
        message = diagnostic.get("message")
        if (
            rule not in limits
            or not isinstance(filename, str)
            or not isinstance(location, dict)
            or not isinstance(message, str)
        ):
            raise QualityDebtError("unrecognized analyzer diagnostic shape")
        line = location.get("row")
        if not isinstance(line, int) or line < 1:
            raise QualityDebtError("analyzer diagnostic line must be positive")
        _, relative = _inside(root, filename)
        if _excluded(relative, excludes):
            continue
        match = _OBSERVED_LIMIT.search(message)
        if match is None or int(match.group(2)) != limits[str(rule)]:
            raise QualityDebtError(
                f"unrecognized analyzer {rule} diagnostic: {message}"
            )
        key = (relative, line)
        row = grouped.setdefault(
            key, {"path": relative, "line": line, "violations": {}}
        )
        violations = row["violations"]
        assert isinstance(violations, dict)
        violations[str(rule)] = int(match.group(1))
    return [grouped[key] for key in sorted(grouped)]


def _detailed_findings(
    findings: list[dict[str, object]], limits: dict[str, int]
) -> list[dict[str, object]]:
    detailed: list[dict[str, object]] = []
    for finding in findings:
        path = str(finding["path"])
        line = int(finding["line"])
        for rule, raw_observed in sorted(dict(finding["violations"]).items()):
            observed = int(raw_observed)
            limit = limits[rule]
            detailed.append(
                {
                    "path": path,
                    "line": line,
                    "rule": rule,
                    "observed": observed,
                    "limit": limit,
                    "excess": observed - limit,
                }
            )
    return detailed


def _file_lengths(
    root: Path,
    roots: tuple[str, ...],
    max_file_lines: int | None,
    excludes: tuple[str, ...],
) -> dict[str, int]:
    if max_file_lines is None:
        return {}
    oversized: dict[str, int] = {}
    for raw_root in roots:
        base, _ = _inside(root, raw_root)
        paths = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if path.is_file() and not _excluded(relative, excludes):
                lines = len(path.read_text(encoding="utf-8").splitlines())
                if lines > max_file_lines:
                    oversized[relative] = lines
    return oversized


def _summary(
    findings: list[dict[str, object]], limits: dict[str, int], oversized: dict[str, int]
) -> dict[str, object]:
    files: dict[str, dict[str, int]] = {}
    for finding in findings:
        path = str(finding["path"])
        row = files.setdefault(path, {"locations": 0, "rule_findings": 0, "excess": 0})
        row["locations"] += 1
        violations = dict(finding["violations"])
        row["rule_findings"] += len(violations)
        row["excess"] += sum(
            int(value) - limits[rule] for rule, value in violations.items()
        )
    return {
        "locations": len(findings),
        "rule_findings": sum(len(dict(item["violations"])) for item in findings),
        "excess": sum(
            int(value) - limits[rule]
            for item in findings
            for rule, value in dict(item["violations"]).items()
        ),
        "oversized_files": oversized,
        "files": dict(sorted(files.items())),
    }


def _comparison(
    current: dict[str, object],
    baseline: dict[str, object] | None,
    comparable_identity: str,
    comparable_values: dict[str, object],
) -> dict[str, object]:
    if baseline is None:
        return {"state": "NO_BASELINE", "files": {}, "delta_excess": None}
    if baseline.get("schema") != "agent-economics-quality-debt-baseline.v1":
        return {
            "state": "INCOMPARABLE_BASELINE",
            "reason": "schema_mismatch",
            "files": {},
            "delta_excess": None,
        }
    if baseline.get("comparable_identity") != comparable_identity:
        previous_values = baseline.get("comparable_values")
        if not isinstance(previous_values, dict):
            return {
                "state": "INCOMPARABLE_BASELINE",
                "reason": "comparable_values_unavailable",
                "changed_fields": None,
                "files": {},
                "delta_excess": None,
            }
        # v1 baselines written before executable-path portability included the
        # resolved analyzer location in semantic comparability. Preserve those
        # baselines when every actual measurement input still agrees.
        normalized_previous = dict(previous_values)
        normalized_previous.pop("analyzer_executable", None)
        # Pre-isolation v1 baselines were measured with ambient Ruff config
        # discovery. They cannot be proven semantically comparable to the
        # explicit isolated analyzer mode.
        if "analyzer_configuration_mode" not in normalized_previous:
            return {
                "state": "INCOMPARABLE_BASELINE",
                "reason": "analyzer_configuration_mode_unavailable",
                "changed_fields": ["analyzer_configuration_mode"],
                "files": {},
                "delta_excess": None,
            }
        if configuration_identity(normalized_previous) != comparable_identity:
            changed_fields = sorted(
                key
                for key in set(normalized_previous) | set(comparable_values)
                if normalized_previous.get(key) != comparable_values.get(key)
            )
            return {
                "state": "INCOMPARABLE_BASELINE",
                "reason": "configuration_or_analyzer_mismatch",
                "changed_fields": changed_fields,
                "files": {},
                "delta_excess": None,
            }
    previous = baseline.get("summary")
    if not isinstance(previous, dict) or not isinstance(previous.get("files"), dict):
        return {
            "state": "INCOMPARABLE_BASELINE",
            "reason": "invalid_summary",
            "files": {},
            "delta_excess": None,
        }
    old_files, new_files = dict(previous["files"]), dict(current["files"])
    states: dict[str, dict[str, object]] = {}
    for path in sorted(set(old_files) | set(new_files)):
        before = old_files.get(path)
        after = new_files.get(path)
        if before is None:
            state = "NEW"
        elif after is None:
            state = "RESOLVED"
        else:
            delta = int(after["excess"]) - int(before["excess"])
            state = (
                "INCREASED" if delta > 0 else ("REDUCED" if delta < 0 else "UNCHANGED")
            )
        states[path] = {
            "state": state,
            "previous_excess": int(before["excess"])
            if isinstance(before, dict)
            else None,
            "current_excess": int(after["excess"]) if isinstance(after, dict) else None,
        }
    delta = int(current["excess"]) - int(previous.get("excess", 0))
    overall = "INCREASED" if delta > 0 else ("REDUCED" if delta < 0 else "UNCHANGED")
    return {"state": overall, "files": states, "delta_excess": delta}


def quality_debt_audit(
    *,
    repository_root: Path,
    roots: tuple[str, ...],
    limits: dict[str, int],
    analyzer: str = "ruff",
    max_file_lines: int | None = None,
    file_line_roots: tuple[str, ...] | None = None,
    excludes: tuple[str, ...] = (),
    baseline_path: Path | None = None,
    artifact_path: Path | None = None,
    timeout_seconds: float = 30.0,
    max_stdout_bytes: int = 2_000_000,
    max_stderr_bytes: int = 200_000,
) -> dict[str, object]:
    started = time.perf_counter()
    root = repository_root.resolve()
    if not roots or not limits or any(not k or v < 1 for k, v in limits.items()):
        raise QualityDebtError("roots and positive rule limits are required")
    if max_file_lines is not None and max_file_lines < 1:
        raise QualityDebtError("max_file_lines must be positive")
    if analyzer != "ruff":
        raise QualityDebtError("only the ruff analyzer adapter is currently supported")
    roots = tuple(
        _normalize_scope_path(root, item, kind="configured root") for item in roots
    )
    excludes = tuple(
        _normalize_scope_path(root, item, kind="exclude") for item in excludes
    )
    if len(set(excludes)) != len(excludes):
        raise QualityDebtError(
            "duplicate excludes after repository-relative normalization"
        )
    line_roots_raw = roots if file_line_roots is None else file_line_roots
    line_roots = tuple(
        _normalize_scope_path(root, item, kind="file-line root")
        for item in line_roots_raw
    )
    scope_diagnostics = _validate_scope(root, roots, excludes)
    _validate_scope(root, line_roots, excludes)
    executable = shutil.which(analyzer) or analyzer
    version = _analyzer_version(root, executable, timeout_seconds)
    source_identity, files_read, source_bytes, source_universe = _source_identity(
        root, roots, (".py",), excludes
    )
    diagnostics, execution, analyzer_argv = _run_ruff(
        root,
        executable=executable,
        roots=roots,
        limits=limits,
        timeout_seconds=timeout_seconds,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes,
        excludes=excludes,
    )
    findings = _findings(root, diagnostics, limits, excludes)
    detailed_findings = _detailed_findings(findings, limits)
    oversized = _file_lengths(root, line_roots, max_file_lines, excludes)
    summary = _summary(findings, limits, oversized)
    comparable_values = {
        "analyzer": analyzer,
        "analyzer_version": version,
        "roots": list(roots),
        "excludes": list(excludes),
        "file_line_roots": list(line_roots),
        "limits": dict(sorted(limits.items())),
        "max_file_lines": max_file_lines,
        "analyzer_configuration_mode": "isolated_explicit",
    }
    comparable_identity = configuration_identity(comparable_values)
    baseline = None
    if baseline_path is not None:
        target = baseline_path if baseline_path.is_absolute() else root / baseline_path
        try:
            target.resolve().relative_to(root)
        except ValueError as exc:
            raise QualityDebtError("baseline path escapes repository root") from exc
        baseline = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(baseline, dict):
            raise QualityDebtError("baseline must be a JSON object")
    comparison = _comparison(summary, baseline, comparable_identity, comparable_values)
    candidates = []
    for path, row in summary["files"].items():
        candidates.append(
            {
                "target": path,
                "facts": dict(row),
                "evidence": {"analyzer": analyzer},
                "derived": {"excess": row["excess"]},
                "interpretation": {
                    "baseline_state": comparison.get("files", {})
                    .get(path, {})
                    .get("state")
                    if isinstance(comparison.get("files"), dict)
                    else None
                },
                "recommendations": {},
                "uncertainty": [],
                "required_next_evidence": [
                    {
                        "kind": "test_focus",
                        "target": path,
                        "reason": (
                            "quality-debt magnitude does not establish edit safety; "
                            "recover confirmed/supporting test ownership and affected "
                            "verification before selecting this target for an edit"
                        ),
                    }
                ],
                "verification_suggestions": [],
            }
        )
    payload = build_probe_contract(
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        generated_at=iso_utc_now(),
        repository={
            "root": ".",
            "identity": source_identity,
            "identity_kind": "analyzed-source-content-sha256",
        },
        configuration_values={
            **comparable_values,
            "timeout_seconds": timeout_seconds,
            "max_stdout_bytes": max_stdout_bytes,
            "max_stderr_bytes": max_stderr_bytes,
        },
        evidence={
            "analyzer": {
                "name": analyzer,
                "version": version,
                "resolved_executable": executable,
                "version_argv": [executable, "--version"],
                "analysis_argv": list(analyzer_argv),
                "working_directory": ".",
            },
            "source_universe": {**source_universe, "diagnostics": scope_diagnostics},
            "findings": findings,
            "detailed_findings": detailed_findings,
            "oversized_files": oversized,
            "comparable_identity": comparable_identity,
            "comparable_values": comparable_values,
        },
        derived={"summary": summary, "baseline_comparison": comparison},
        interpretation={
            "measurement_not_policy_authority": True,
            "baseline_growth_is_evidence_not_verdict": True,
        },
        uncertainty=[]
        if comparison["state"] != "INCOMPARABLE_BASELINE"
        else [{"code": "baseline_incomparable", "reason": comparison.get("reason")}],
        warnings=[
            {
                "code": "quality_debt_not_edit_authority",
                "message": "Quality-debt measurements do not authorize source edits or certification.",
            },
            *scope_diagnostics,
        ],
        candidates=sorted(
            candidates, key=lambda x: (-int(x["facts"]["excess"]), x["target"])
        ),
        required_next_evidence=[],
        deferred_evidence=[],
        verification_suggestions=[],
        economics={
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "files_read": files_read,
            "source_bytes": source_bytes,
            "analyzer_stdout_bytes": execution["stdout_bytes"],
            "analyzer_stderr_bytes": execution["stderr_bytes"],
            "analyzer_elapsed_ms": execution["elapsed_ms"],
        },
    )
    if artifact_path is not None:
        target = artifact_path if artifact_path.is_absolute() else root / artifact_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return payload


def baseline_document(payload: dict[str, object]) -> dict[str, object]:
    evidence = payload["evidence"]
    derived = payload["derived"]
    assert isinstance(evidence, dict) and isinstance(derived, dict)
    return {
        "schema": "agent-economics-quality-debt-baseline.v1",
        "comparable_identity": evidence["comparable_identity"],
        "comparable_values": evidence["comparable_values"],
        "summary": derived["summary"],
    }
