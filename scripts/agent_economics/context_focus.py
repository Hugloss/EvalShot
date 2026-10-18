from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .probe_contract import (
    analyzed_input_identity,
    build_probe_contract,
    sha256_identity,
)
from .refactor_focus_discovery import (
    DEFAULT_EXCLUDE_PATTERNS,
    DiscoveryConfig,
    DiscoveryError,
    DiscoveryResult,
    discover_repository_roots,
    normalize_exclude_patterns,
    normalize_suffixes,
)

TOOL_NAME = "context-focus"
TOOL_VERSION = "0.1.0"

DEFAULT_CONTEXT_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".cs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".sh",
    ".bash",
    ".sql",
    ".graphql",
    ".proto",
    ".md",
    ".rst",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SYMBOL_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    ".py": (re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)"),),
    ".pyi": (re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)"),),
    ".js": (
        re.compile(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)"
        ),
        re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
    ),
    ".jsx": (),
    ".ts": (
        re.compile(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)"
        ),
        re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
    ),
    ".tsx": (),
    ".go": (re.compile(r"^\s*(?:func|type)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),),
    ".rs": (
        re.compile(
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait|type)\s+([A-Za-z_]\w*)"
        ),
    ),
}
_SYMBOL_PATTERNS[".jsx"] = _SYMBOL_PATTERNS[".js"]
_SYMBOL_PATTERNS[".mjs"] = _SYMBOL_PATTERNS[".js"]
_SYMBOL_PATTERNS[".cjs"] = _SYMBOL_PATTERNS[".js"]
_SYMBOL_PATTERNS[".tsx"] = _SYMBOL_PATTERNS[".ts"]


class ContextFocusError(ValueError):
    pass


@dataclass(frozen=True)
class ContextBudget:
    max_files: int = 8
    max_lines: int = 3000
    max_bytes: int = 250_000
    max_tokens: int = 50_000

    def __post_init__(self) -> None:
        for name, value in (
            ("max_files", self.max_files),
            ("max_lines", self.max_lines),
            ("max_bytes", self.max_bytes),
            ("max_tokens", self.max_tokens),
        ):
            if value < 1:
                raise ContextFocusError(f"{name} must be greater than zero")


@dataclass(frozen=True)
class ScanBudget:
    max_files: int = 1000
    max_bytes: int = 25_000_000
    max_file_bytes: int = 1_000_000
    max_anchors_per_file: int = 8

    def __post_init__(self) -> None:
        for name, value in (
            ("max_files", self.max_files),
            ("max_bytes", self.max_bytes),
            ("max_file_bytes", self.max_file_bytes),
            ("max_anchors_per_file", self.max_anchors_per_file),
        ):
            if value < 1:
                raise ContextFocusError(f"scan {name} must be greater than zero")


@dataclass(frozen=True)
class ExternalHint:
    path: str
    score: float
    reasons: tuple[str, ...]
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class ExternalIntelligence:
    provider: str
    content_sha256: str
    hints: dict[str, ExternalHint]
    bytes_read: int


@dataclass(frozen=True)
class ScannedFile:
    path: Path
    relative_path: str
    byte_count: int
    line_count: int
    estimated_tokens: int
    content_sha256: str
    path_tokens: tuple[str, ...]
    basename_tokens: tuple[str, ...]
    content_tokens: tuple[str, ...]
    content_occurrences: int
    anchors: tuple[dict[str, object], ...]
    symbol_hints: tuple[dict[str, object], ...]
    external_hint: ExternalHint | None
    score: float

    @property
    def covered_tokens(self) -> tuple[str, ...]:
        values = (
            set(self.path_tokens) | set(self.basename_tokens) | set(self.content_tokens)
        )
        return tuple(sorted(values))


def _tokens(text: str) -> tuple[str, ...]:
    expanded = _CAMEL_BOUNDARY.sub(" ", text)
    values = {item.lower() for item in _IDENTIFIER.findall(expanded) if len(item) > 1}
    return tuple(sorted(values))


def _portable_relative(path: Path, repository_root: Path) -> str:
    try:
        return path.absolute().relative_to(repository_root.resolve()).as_posix()
    except ValueError as exc:
        raise ContextFocusError(f"path must be inside repository_root: {path}") from exc


def _safe_relative(raw: str) -> str:
    normalized = raw.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    candidate = Path(normalized)
    if (
        not normalized
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ContextFocusError(
            f"invalid repository-relative intelligence path: {raw!r}"
        )
    return candidate.as_posix()


def load_external_intelligence(
    path: Path, *, max_bytes: int = 5_000_000
) -> ExternalIntelligence:
    if max_bytes < 1:
        raise ContextFocusError(
            "repository intelligence max_bytes must be greater than zero"
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ContextFocusError(
            f"cannot stat repository intelligence: {type(exc).__name__}"
        ) from exc
    if size > max_bytes:
        raise ContextFocusError(
            f"repository intelligence exceeds max_bytes: {size} > {max_bytes}"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContextFocusError(
            f"cannot read repository intelligence: {type(exc).__name__}"
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextFocusError("repository intelligence must be UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ContextFocusError("repository intelligence version must be 1")
    provider = payload.get("provider", "external")
    if not isinstance(provider, str) or not provider.strip():
        raise ContextFocusError(
            "repository intelligence provider must be a non-empty string"
        )
    files = payload.get("files")
    if not isinstance(files, list):
        raise ContextFocusError("repository intelligence files must be a list")
    hints: dict[str, ExternalHint] = {}
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ContextFocusError(
                f"repository intelligence file {index} must be an object"
            )
        relative = _safe_relative(str(item.get("path", "")))
        score = item.get("score", 0)
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
        ):
            raise ContextFocusError(
                f"repository intelligence score for {relative} must be finite"
            )
        score_value = float(score)
        if not 0 <= score_value <= 100:
            raise ContextFocusError(
                f"repository intelligence score for {relative} must be within 0..100"
            )
        reasons_raw = item.get("reasons", [])
        symbols_raw = item.get("symbols", [])
        if not isinstance(reasons_raw, list) or not all(
            isinstance(v, str) for v in reasons_raw
        ):
            raise ContextFocusError(
                f"repository intelligence reasons for {relative} must be strings"
            )
        if not isinstance(symbols_raw, list) or not all(
            isinstance(v, str) for v in symbols_raw
        ):
            raise ContextFocusError(
                f"repository intelligence symbols for {relative} must be strings"
            )
        if relative in hints:
            raise ContextFocusError(
                f"duplicate repository intelligence path: {relative}"
            )
        hints[relative] = ExternalHint(
            path=relative,
            score=score_value,
            reasons=tuple(reasons_raw),
            symbols=tuple(symbols_raw),
        )
    return ExternalIntelligence(
        provider=provider.strip(),
        content_sha256=hashlib.sha256(raw).hexdigest(),
        hints=hints,
        bytes_read=len(raw),
    )


def _symbol_hints(
    *,
    suffix: str,
    lines: list[str],
    query_tokens: set[str],
    max_items: int,
) -> tuple[dict[str, object], ...]:
    patterns = _SYMBOL_PATTERNS.get(suffix.lower(), ())
    if not patterns:
        return ()
    output: list[dict[str, object]] = []
    for line_no, line in enumerate(lines, start=1):
        for pattern in patterns:
            match = pattern.match(line)
            if match is None:
                continue
            name = match.group(1)
            matched = sorted(query_tokens & set(_tokens(name)))
            if not matched:
                continue
            output.append(
                {"name": name, "line": line_no, "matched_query_tokens": matched}
            )
            if len(output) >= max_items:
                return tuple(output)
            break
    return tuple(output)


def _scan_file(
    *,
    path: Path,
    relative_path: str,
    query_tokens: set[str],
    external_hint: ExternalHint | None,
    max_anchors: int,
) -> ScannedFile:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    path_token_set = set(_tokens(relative_path)) & query_tokens
    basename_token_set = set(_tokens(path.stem)) & query_tokens
    content_matched: set[str] = set()
    occurrences = 0
    anchors: list[dict[str, object]] = []
    for line_no, line in enumerate(lines, start=1):
        line_tokens = _tokens(line)
        matches = sorted(query_tokens & set(line_tokens))
        if not matches:
            continue
        content_matched.update(matches)
        occurrences += sum(1 for token in line_tokens if token in query_tokens)
        if len(anchors) < max_anchors:
            anchors.append(
                {
                    "line": line_no,
                    "matched_query_tokens": matches,
                }
            )
    symbols = _symbol_hints(
        suffix=path.suffix,
        lines=lines,
        query_tokens=query_tokens,
        max_items=max_anchors,
    )
    external_score = external_hint.score if external_hint is not None else 0.0
    score = (
        12.0 * len(path_token_set)
        + 8.0 * len(basename_token_set)
        + 6.0 * len(content_matched)
        + float(min(occurrences, 20))
        + external_score
    )
    return ScannedFile(
        path=path,
        relative_path=relative_path,
        byte_count=len(raw),
        line_count=len(lines),
        estimated_tokens=math.ceil(len(text) / 4),
        content_sha256=hashlib.sha256(raw).hexdigest(),
        path_tokens=tuple(sorted(path_token_set)),
        basename_tokens=tuple(sorted(basename_token_set)),
        content_tokens=tuple(sorted(content_matched)),
        content_occurrences=occurrences,
        anchors=tuple(anchors),
        symbol_hints=symbols,
        external_hint=external_hint,
        score=score,
    )


def _candidate_record(item: ScannedFile, rank: int) -> dict[str, object]:
    external = item.external_hint
    evidence: dict[str, object] = {
        "path_query_tokens": list(item.path_tokens),
        "basename_query_tokens": list(item.basename_tokens),
        "content_query_tokens": list(item.content_tokens),
        "content_occurrences": item.content_occurrences,
        "anchors": [dict(anchor) for anchor in item.anchors],
        "symbol_hints": [dict(symbol) for symbol in item.symbol_hints],
    }
    if external is not None:
        evidence["external_intelligence"] = {
            "score": external.score,
            "reasons": list(external.reasons),
            "symbols": list(external.symbols),
        }
    recommendation: dict[str, object] = {
        "action": "inspect_file",
        "path": item.relative_path,
    }
    recommended_symbols = [str(symbol["name"]) for symbol in item.symbol_hints]
    if external is not None:
        recommended_symbols.extend(external.symbols)
    recommended_symbols = list(dict.fromkeys(recommended_symbols))
    if recommended_symbols:
        recommendation["inspect_symbols_first"] = recommended_symbols
    elif item.anchors:
        recommendation["inspect_lines_first"] = [
            anchor["line"] for anchor in item.anchors
        ]
    return {
        "target": item.relative_path,
        "facts": {
            "byte_count": item.byte_count,
            "line_count": item.line_count,
            "estimated_tokens": item.estimated_tokens,
            "suffix": item.path.suffix.lower(),
        },
        "evidence": evidence,
        "derived": {"query_tokens_covered": list(item.covered_tokens)},
        "interpretation": {"rank": rank, "context_value_score": item.score},
        "recommendations": recommendation,
        "uncertainty": [],
        "required_next_evidence": [
            {
                "kind": "inspect_file",
                "path": item.relative_path,
                "reason": "selected by bounded context-focus ranking",
            }
        ],
        "verification_suggestions": [],
    }


def context_focus_audit(
    *,
    task: str,
    repository_root: Path,
    roots: tuple[Path, ...] | list[Path] | None = None,
    suffixes: tuple[str, ...] | list[str] = DEFAULT_CONTEXT_SUFFIXES,
    artifact_path: Path | None = None,
    context_budget: ContextBudget = ContextBudget(),
    scan_budget: ScanBudget = ScanBudget(),
    discovery_config: DiscoveryConfig = DiscoveryConfig(),
    repository_intelligence_path: Path | None = None,
    repository_intelligence_max_bytes: int = 5_000_000,
) -> dict[str, object]:
    started = time.perf_counter()
    task = task.strip()
    query_tokens = set(_tokens(task))
    if not task or not query_tokens:
        raise ContextFocusError("task must contain at least one searchable token")
    repository_root = repository_root.resolve()
    if not repository_root.is_dir():
        raise ContextFocusError("repository_root must be an existing directory")
    normalized_suffixes = normalize_suffixes(tuple(suffixes))
    raw_roots = tuple(roots or (repository_root,))
    requested_roots = tuple(
        root.resolve() if root.is_absolute() else (repository_root / root).resolve()
        for root in raw_roots
    )
    root_map: dict[str, Path] = {}
    for index, root in enumerate(requested_roots):
        key = f"root_{index}"
        root_map[key] = root

    try:
        discovery = discover_repository_roots(
            roots=root_map,
            repository_root=repository_root,
            config=discovery_config,
            suffixes=normalized_suffixes,
        )
    except DiscoveryError as exc:
        raise ContextFocusError(str(exc)) from exc

    raw_discovered_paths = sorted(
        {path for label in root_map for path in discovery.files_for(label)},
        key=lambda path: _portable_relative(path, repository_root),
    )
    auxiliary_paths: set[Path] = set()
    for auxiliary in (artifact_path, repository_intelligence_path):
        if auxiliary is None:
            continue
        try:
            auxiliary_paths.add(auxiliary.resolve())
        except OSError:
            auxiliary_paths.add(auxiliary.absolute())
    discovered_paths = [
        path for path in raw_discovered_paths if path.resolve() not in auxiliary_paths
    ]
    auxiliary_inputs_excluded = len(raw_discovered_paths) - len(discovered_paths)
    discovered_relatives = {
        _portable_relative(path, repository_root) for path in discovered_paths
    }

    external: ExternalIntelligence | None = None
    external_stale: list[str] = []
    if repository_intelligence_path is not None:
        external = load_external_intelligence(
            repository_intelligence_path, max_bytes=repository_intelligence_max_bytes
        )
        external_stale = sorted(set(external.hints) - discovered_relatives)

    stat_info: list[tuple[Path, str, int, int, float]] = []
    stat_failures = 0
    for path in discovered_paths:
        relative = _portable_relative(path, repository_root)
        try:
            size = path.stat().st_size
        except OSError:
            stat_failures += 1
            continue
        path_overlap = len(set(_tokens(relative)) & query_tokens)
        basename_overlap = len(set(_tokens(path.stem)) & query_tokens)
        external_score = (
            external.hints[relative].score
            if external and relative in external.hints
            else 0.0
        )
        pre_score = 12.0 * path_overlap + 8.0 * basename_overlap + external_score
        stat_info.append(
            (path, relative, size, path_overlap + basename_overlap, pre_score)
        )
    stat_info.sort(key=lambda item: (-item[4], item[2], item[1]))

    scanned: list[ScannedFile] = []
    scan_bytes = 0
    read_failures = 0
    skipped_large = 0
    scan_budget_deferred: list[dict[str, object]] = []
    for path, relative, size, _path_hits, _pre_score in stat_info:
        if len(scanned) >= scan_budget.max_files:
            scan_budget_deferred.append(
                {"target": relative, "reason": "scan max_files budget exhausted"}
            )
            continue
        if size > scan_budget.max_file_bytes:
            skipped_large += 1
            scan_budget_deferred.append(
                {"target": relative, "reason": "file exceeds scan max_file_bytes"}
            )
            continue
        if scan_bytes + size > scan_budget.max_bytes:
            scan_budget_deferred.append(
                {"target": relative, "reason": "scan max_bytes budget exhausted"}
            )
            continue
        try:
            item = _scan_file(
                path=path,
                relative_path=relative,
                query_tokens=query_tokens,
                external_hint=(external.hints.get(relative) if external else None),
                max_anchors=scan_budget.max_anchors_per_file,
            )
        except OSError:
            read_failures += 1
            continue
        scanned.append(item)
        scan_bytes += item.byte_count

    relevant = [item for item in scanned if item.score > 0]
    relevant.sort(
        key=lambda item: (-item.score, item.estimated_tokens, item.relative_path)
    )

    selected: list[ScannedFile] = []
    selected_lines = 0
    selected_bytes = 0
    selected_tokens = 0
    deferred: list[dict[str, object]] = list(scan_budget_deferred)
    for item in relevant:
        if len(selected) >= context_budget.max_files:
            deferred.append(
                {
                    "target": item.relative_path,
                    "reason": "context max_files budget exhausted",
                }
            )
            continue
        if selected_lines + item.line_count > context_budget.max_lines:
            deferred.append(
                {
                    "target": item.relative_path,
                    "reason": "context max_lines budget exhausted",
                }
            )
            continue
        if selected_bytes + item.byte_count > context_budget.max_bytes:
            deferred.append(
                {
                    "target": item.relative_path,
                    "reason": "context max_bytes budget exhausted",
                }
            )
            continue
        if selected_tokens + item.estimated_tokens > context_budget.max_tokens:
            deferred.append(
                {
                    "target": item.relative_path,
                    "reason": "context max_tokens budget exhausted",
                }
            )
            continue
        selected.append(item)
        selected_lines += item.line_count
        selected_bytes += item.byte_count
        selected_tokens += item.estimated_tokens

    analyzed_entries = [
        {"path": item.relative_path, "sha256": item.content_sha256} for item in scanned
    ]
    path_set_identity = sha256_identity(sorted(discovered_relatives))
    analysis_identity = analyzed_input_identity(analyzed_entries)
    external_identity = external.content_sha256 if external is not None else None
    root_values = [
        _portable_relative(path, repository_root) if path != repository_root else "."
        for path in requested_roots
    ]

    warnings: list[dict[str, object]] = [
        {"code": "discovery_warning", "message": message}
        for message in discovery.warnings
    ]
    if stat_failures:
        warnings.append({"code": "stat_failures", "count": stat_failures})
    if read_failures:
        warnings.append({"code": "read_failures", "count": read_failures})
    if skipped_large:
        warnings.append(
            {"code": "files_exceed_scan_max_file_bytes", "count": skipped_large}
        )
    if external_stale:
        warnings.append(
            {"code": "stale_external_intelligence_paths", "paths": external_stale}
        )
    if scan_budget_deferred:
        warnings.append(
            {"code": "scan_budget_truncated", "count": len(scan_budget_deferred)}
        )

    uncertainty: list[dict[str, object]] = []
    if scan_budget_deferred:
        uncertainty.append(
            {
                "code": "repository_not_fully_content_scanned",
                "message": "Some discovered files were not content-scanned within the configured scan budget.",
            }
        )
    if discovery.backend == "filesystem":
        uncertainty.append(
            {
                "code": "filesystem_discovery_authority",
                "message": "Filesystem discovery cannot prove Git tracked/ignored semantics.",
            }
        )
    if not selected:
        uncertainty.append(
            {
                "code": "no_context_candidate_selected",
                "message": "No relevant file fit the configured context budget.",
            }
        )

    candidates = [
        _candidate_record(item, rank) for rank, item in enumerate(selected, start=1)
    ]
    required_next_evidence = [
        {
            "target": item.relative_path,
            "kind": "inspect_file",
            "reason": "selected by context-focus",
        }
        for item in selected
    ]
    discovered_count = len(discovered_paths)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    economics: dict[str, object] = {
        "discovered_files": discovered_count,
        "auxiliary_inputs_excluded_from_context": auxiliary_inputs_excluded,
        "stat_calls": len(discovered_paths),
        "stat_failures": stat_failures,
        "files_read": len(scanned),
        "bytes_read": scan_bytes,
        "read_failures": read_failures,
        "relevant_files": len(relevant),
        "selected_files": len(selected),
        "selected_lines": selected_lines,
        "selected_bytes": selected_bytes,
        "selected_estimated_tokens": selected_tokens,
        "token_estimator": "ceil(decoded_characters/4)",
        "scan_budget": {
            "max_files": scan_budget.max_files,
            "max_bytes": scan_budget.max_bytes,
            "max_file_bytes": scan_budget.max_file_bytes,
            "max_anchors_per_file": scan_budget.max_anchors_per_file,
        },
        "context_budget": {
            "max_files": context_budget.max_files,
            "max_lines": context_budget.max_lines,
            "max_bytes": context_budget.max_bytes,
            "max_tokens": context_budget.max_tokens,
        },
        "discovery": discovery.metrics(),
        "external_intelligence_files_read": 1 if external else 0,
        "external_intelligence_bytes_read": external.bytes_read if external else 0,
        "elapsed_ms": elapsed_ms,
    }

    payload = build_probe_contract(
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        generated_at=_iso_utc_now(),
        repository={
            "root": ".",
            "identity": path_set_identity,
            "identity_kind": "discovered-context-path-set-sha256",
            "input_count": discovered_count,
        },
        configuration_values={
            "task": task,
            "roots": root_values,
            "suffixes": list(normalized_suffixes),
            "discovery_mode": discovery_config.mode,
            "untracked_policy": discovery_config.untracked_policy,
            "ignored_policy": discovery_config.ignored_policy,
            "symlink_policy": discovery_config.symlink_policy,
            "exclude_patterns": list(discovery_config.exclude_patterns),
            "git_timeout_seconds": discovery_config.git_timeout_seconds,
            "context_budget": economics["context_budget"],
            "scan_budget": economics["scan_budget"],
            "repository_intelligence_path": (
                _portable_relative(repository_intelligence_path, repository_root)
                if repository_intelligence_path is not None
                and repository_intelligence_path.absolute().is_relative_to(
                    repository_root
                )
                else (
                    f"<external>/{repository_intelligence_path.name}"
                    if repository_intelligence_path
                    else None
                )
            ),
            "repository_intelligence_sha256": external_identity,
            "repository_intelligence_max_bytes": repository_intelligence_max_bytes,
        },
        evidence={
            "query_tokens": sorted(query_tokens),
            "discovery": discovery.metrics(),
            "discovered_path_set_identity": path_set_identity,
            "analyzed_input_identity": analysis_identity,
            "ranking_formula": {
                "path_query_token": 12,
                "basename_query_token": 8,
                "content_query_token": 6,
                "content_occurrence_cap": 20,
                "external_intelligence_score": "0..100 additive",
            },
            "external_intelligence": (
                {
                    "provider": external.provider,
                    "hints_loaded": len(external.hints),
                    "content_sha256": external.content_sha256,
                }
                if external
                else None
            ),
        },
        derived={
            "discovered_files": discovered_count,
            "scanned_files": len(scanned),
            "relevant_files": len(relevant),
            "selected_count": len(selected),
            "query_tokens_covered": sorted(
                {token for item in selected for token in item.covered_tokens}
            ),
        },
        interpretation={
            "ranking_policy": [
                "context_value_score_desc",
                "estimated_tokens_asc",
                "path",
            ],
            "selected_targets": [item.relative_path for item in selected],
            "external_intelligence_is_ranking_support_not_repository_authority": True,
        },
        uncertainty=uncertainty,
        warnings=warnings,
        candidates=candidates,
        required_next_evidence=required_next_evidence,
        deferred_evidence=deferred,
        verification_suggestions=[],
        economics=economics,
    )
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return payload


def _iso_utc_now() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
