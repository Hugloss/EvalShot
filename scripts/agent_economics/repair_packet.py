from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable


_LOCATION = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+):(?P<line>[1-9][0-9]*)"
)
_ALLOWED_PROBE_SCHEMAS = {
    "agent-economics-context-focus",
    "agent-economics-test-focus",
    "agent-economics-change-impact",
    "agent-economics-quality-debt",
    "context-focus",
    "test-focus",
    "change-impact",
    "quality-debt",
}


def _probe_summary(
    path: Path, *, max_bytes: int, max_candidates: int
) -> dict[str, object]:
    if path.stat().st_size > max_bytes:
        raise ValueError(f"probe artifact exceeds byte bound: {path.name}")
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("probe artifact must contain an object")
    schema = data.get("schema")
    schema_name = schema.get("name") if isinstance(schema, dict) else None
    if schema_name not in _ALLOWED_PROBE_SCHEMAS:
        raise ValueError(f"unsupported probe artifact schema: {schema_name!r}")
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    compact: list[dict[str, object]] = []
    for item in candidates[:max_candidates]:
        if isinstance(item, dict):
            compact.append(
                {
                    key: item.get(key)
                    for key in (
                        "target",
                        "facts",
                        "uncertainty",
                        "required_next_evidence",
                        "verification_suggestions",
                    )
                    if key in item
                }
            )
    repository = data.get("repository")
    return {
        "schema": schema,
        "tool": data.get("tool"),
        "repository_identity": repository.get("identity")
        if isinstance(repository, dict)
        else None,
        "configuration_identity": data.get("configuration", {}).get("identity")
        if isinstance(data.get("configuration"), dict)
        else None,
        "candidates": compact,
        "uncertainty": data.get("uncertainty", []),
        "required_next_evidence": data.get("required_next_evidence", []),
        "verification_suggestions": data.get("verification_suggestions", []),
    }


def build_repair_packet(
    *,
    repository_root: Path,
    command_result: dict[str, object],
    changed_paths: list[str] | None = None,
    probe_artifacts: Iterable[Path] = (),
    max_excerpt_chars: int = 12_000,
    max_anchors: int = 20,
    max_probe_bytes: int = 1_000_000,
    max_probe_candidates: int = 20,
) -> dict[str, object]:
    if min(max_excerpt_chars, max_anchors, max_probe_bytes, max_probe_candidates) < 1:
        raise ValueError("repair packet bounds must be positive")
    root = repository_root.resolve()
    stdout, stderr = (
        str(command_result.get("stdout", "")),
        str(command_result.get("stderr", "")),
    )
    diagnostic = (stderr + "\n" + stdout).strip()
    excerpt = diagnostic[:max_excerpt_chars]
    anchors: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    for match in _LOCATION.finditer(excerpt):
        item = (match.group("path"), int(match.group("line")))
        if item not in seen:
            seen.add(item)
            anchors.append({"path": item[0], "line": item[1]})
        if len(anchors) >= max_anchors:
            break
    evidence: list[dict[str, object]] = []
    evidence_bytes = 0
    for raw in probe_artifacts:
        target = raw if raw.is_absolute() else root / raw
        resolved = target.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("probe artifact escapes repository root") from exc
        evidence_bytes += resolved.stat().st_size
        evidence.append(
            _probe_summary(
                resolved, max_bytes=max_probe_bytes, max_candidates=max_probe_candidates
            )
        )
    execution = command_result.get("execution")
    stage = (
        command_result.get("command", {}).get("stage")
        if isinstance(command_result.get("command"), dict)
        else None
    )
    next_stage = {
        "focused": "affected",
        "affected": "component",
        "component": "repository",
        "repository": None,
    }.get(str(stage))
    core = {
        "schema": {"name": "agent-economics-repair-packet", "version": 2},
        "classification": command_result.get("classification"),
        "failure_identity": command_result.get("failure_identity"),
        "command": command_result.get("command"),
        "changed_paths": sorted(changed_paths or []),
        "anchors": anchors,
        "diagnostic_excerpt": excerpt,
        "diagnostic_truncated": len(diagnostic) > len(excerpt),
        "probe_evidence": evidence,
        "economics": {
            "probe_files": len(evidence),
            "probe_bytes": evidence_bytes,
            "diagnostic_chars": len(excerpt),
            "estimated_context_tokens": (len(excerpt) + 3) // 4,
        },
        "uncertainty": [{"code": "probe_evidence_not_supplied"}]
        if not evidence
        else [],
        "authority": {
            "repair_performed": False,
            "verification_complete": False,
            "ci_status": "NOT_RUN",
        },
        "required_next_evidence": [
            {"kind": "inspect_failure_anchors_and_changed_contracts"}
        ],
        "suggested_next_verification_stage": next_stage,
    }
    semantic = {k: v for k, v in core.items() if k != "economics"}
    core["evidence_identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return core
