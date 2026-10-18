from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from .bounded_process import ProcessLimits, run_bounded
from .workspace_state import (
    WorkspaceStateError,
    changed_tracked_paths,
    tracked_workspace_state,
)

SNAPSHOT_SCHEMA = "agentscookbook-refactor-locality-snapshot/v1"
COMPARISON_SCHEMA = "agentscookbook-refactor-locality-comparison/v1"
DECISION_SCHEMA = "agentscookbook-refactor-locality-decision/v1"
HASHMARKS_STRUCTURAL_LOCALITY_SCHEMA = "hashmarks.structural-locality.v1"
HASHMARKS_OBSERVATION_SCHEMA = "agentscookbook-hashmarks-locality-observation/v1"

KEEP_COHESIVE_AUTHORITY = "KEEP_COHESIVE_AUTHORITY"
DECOMPOSITION_JUSTIFIED = "DECOMPOSITION_JUSTIFIED"
DECOMPOSITION_LOCALITY_RISK = "DECOMPOSITION_LOCALITY_RISK"
INSUFFICIENT_LOCALITY_EVIDENCE = "INSUFFICIENT_LOCALITY_EVIDENCE"

LOCALITY_PRESERVED_OR_IMPROVED = "LOCALITY_PRESERVED_OR_IMPROVED"
LOCALITY_TRADEOFF_REVIEW_REQUIRED = "LOCALITY_TRADEOFF_REVIEW_REQUIRED"
LOCALITY_REGRESSED = "LOCALITY_REGRESSED"

TRADEOFF_EVIDENCE_KIND = "bounded_locality_tradeoff"

JUSTIFYING_EVIDENCE_KINDS = frozenset(
    {
        "mixed_responsibilities",
        "ownership_ambiguity",
        "verification_locality_failure",
        "unsafe_change_coupling",
        "duplicated_authority",
        "hidden_side_effects",
        "change_isolation_failure",
    }
)

# Every introduced symbol/layer must earn its existence with one of these
# semantic values. Merely lowering LOC/branches/nesting is intentionally absent.
STRUCTURAL_VALUE_KINDS = frozenset(
    {
        "semantic_responsibility_owner",
        "independent_policy_owner",
        "validation_boundary",
        "data_contract_owner",
        "side_effect_isolation",
        "resource_lifetime_owner",
        "change_isolation",
        "duplicated_authority_removed",
        "shared_reuse",
        "direct_test_seam",
        "stable_external_boundary",
        "compatibility_boundary",
        "protocol_adapter",
    }
)

# These shapes are especially easy for an agent to introduce just to satisfy a
# size/complexity target. They are not forbidden, but require explicit value.
EASY_PATH_STRUCTURE_KINDS = frozenset(
    {
        "wrapper",
        "shim",
        "adapter",
        "facade",
        "proxy",
        "delegate",
        "forwarding_helper",
        "reexport",
        "alias_module",
        "manager",
        "service",
    }
)

FORWARDING_BOUNDARY_VALUES = frozenset(
    {
        "stable_external_boundary",
        "compatibility_boundary",
        "protocol_adapter",
    }
)

LOCALITY_DIMENSIONS = (
    "symbol_count",
    "file_count",
    "max_navigation_depth",
    "forwarding_only_symbol_count",
    "context_lines",
    "verifier_file_count",
    "cross_file_symbol_count",
    "unresolved_call_count",
    "target_exact_caller_count",
)

HARD_REGRESSION_DIMENSIONS = frozenset(
    {
        "file_count",
        "context_lines",
        "cross_file_symbol_count",
        "unresolved_call_count",
    }
)


def _identity(payload: Mapping[str, object]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _hashmarks_identity(payload: Mapping[str, object]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )


def _bounded_command_identity(argv: Sequence[str], cwd: str = ".") -> str:
    raw = json.dumps(
        {"argv": list(argv), "cwd": cwd},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _artifact_identity_valid(payload: Mapping[str, object]) -> bool:
    identity = payload.get("evidence_identity")
    if not isinstance(identity, str) or not identity:
        return False
    semantic = {
        str(key): value for key, value in payload.items() if key != "evidence_identity"
    }
    return identity == _identity(semantic)


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalized_paths(values: Sequence[object]) -> list[str]:
    return sorted({str(value) for value in values if _nonempty(value)})


def _union_line_count(symbols: Sequence[Mapping[str, object]]) -> int:
    by_path: dict[str, list[tuple[int, int]]] = {}
    for row in symbols:
        by_path.setdefault(str(row["path"]), []).append(
            (int(row["line_start"]), int(row["line_end"]))
        )
    total = 0
    for spans in by_path.values():
        current_start: int | None = None
        current_end: int | None = None
        for start, end in sorted(spans):
            if current_start is None:
                current_start, current_end = start, end
                continue
            assert current_end is not None
            if start <= current_end + 1:
                current_end = max(current_end, end)
                continue
            total += current_end - current_start + 1
            current_start, current_end = start, end
        if current_start is not None and current_end is not None:
            total += current_end - current_start + 1
    return total


def locality_snapshot(
    *,
    repository_identity: str,
    target: str,
    target_path: str,
    source_identity: str,
    measurement_configuration_identity: str,
    provider: str,
    provider_evidence_identity: str,
    symbols: Sequence[Mapping[str, object]],
    verifier_paths: Sequence[str],
    responsibilities: Sequence[str] = (),
    unresolved_evidence: Sequence[str] = (),
    authority_lines: int | None = None,
    branch_points: int | None = None,
    nesting_depth: int | None = None,
    state_kind: str = "observed",
) -> dict[str, object]:
    """Build transparent locality evidence from an explicit symbol/navigation set."""
    required = (
        repository_identity,
        target,
        target_path,
        source_identity,
        measurement_configuration_identity,
        provider,
        provider_evidence_identity,
    )
    if any(not _nonempty(value) for value in required):
        raise ValueError(
            "snapshot identities, provider, and target fields must be non-empty"
        )
    if state_kind not in {"observed", "proposed"}:
        raise ValueError("state_kind must be observed or proposed")

    normalized_symbols: list[dict[str, object]] = []
    for row in symbols:
        path = str(row.get("path") or "")
        qualname = str(row.get("qualname") or "")
        line_start = row.get("line_start")
        line_end = row.get("line_end")
        navigation_depth = row.get("navigation_depth", 0)
        structure_kind = str(row.get("structure_kind") or "implementation")
        value_kind = str(row.get("value_kind") or "")
        value_evidence_identity = str(row.get("value_evidence_identity") or "")
        exact_caller_count = row.get("exact_caller_count", 0)
        exact_non_verifier_caller_count = row.get(
            "exact_non_verifier_caller_count", exact_caller_count
        )
        direct_verifier_count = row.get("direct_verifier_count", 0)
        caller_reference_bound_complete = bool(
            row.get("caller_reference_bound_complete", False)
        )
        value_evidence_provider = str(row.get("value_evidence_provider") or "")
        value_repository_identity = str(row.get("value_repository_identity") or "")
        if (
            not _nonempty(path)
            or not _nonempty(qualname)
            or not isinstance(line_start, int)
            or isinstance(line_start, bool)
            or not isinstance(line_end, int)
            or isinstance(line_end, bool)
            or line_start < 1
            or line_end < line_start
            or not isinstance(navigation_depth, int)
            or isinstance(navigation_depth, bool)
            or navigation_depth < 0
            or not _nonempty(structure_kind)
            or not isinstance(exact_caller_count, int)
            or isinstance(exact_caller_count, bool)
            or exact_caller_count < 0
            or not isinstance(exact_non_verifier_caller_count, int)
            or isinstance(exact_non_verifier_caller_count, bool)
            or exact_non_verifier_caller_count < 0
            or exact_non_verifier_caller_count > exact_caller_count
            or not isinstance(direct_verifier_count, int)
            or isinstance(direct_verifier_count, bool)
            or direct_verifier_count < 0
        ):
            raise ValueError(
                "symbols must contain valid path/qualname/span/depth/kind evidence"
            )
        value_fields = (
            bool(value_kind),
            bool(value_evidence_identity),
            bool(value_evidence_provider),
            bool(value_repository_identity),
        )
        if len(set(value_fields)) != 1:
            raise ValueError(
                "structural value kind, identity, provider, and repository identity "
                "must either all be set or all be empty"
            )
        if value_kind and value_kind not in STRUCTURAL_VALUE_KINDS:
            raise ValueError(f"unsupported structural value kind: {value_kind}")
        if value_kind and value_repository_identity != repository_identity:
            raise ValueError(
                "structural value evidence must bind the measured repository identity"
            )
        normalized_symbols.append(
            {
                "path": path,
                "qualname": qualname,
                "line_start": line_start,
                "line_end": line_end,
                "navigation_depth": navigation_depth,
                "structure_kind": structure_kind,
                "forwarding_only": bool(row.get("forwarding_only", False)),
                "edit_required": bool(row.get("edit_required", False)),
                "evidence_required": bool(row.get("evidence_required", True)),
                "value_kind": value_kind or None,
                "value_evidence_identity": value_evidence_identity or None,
                "exact_caller_count": exact_caller_count,
                "exact_non_verifier_caller_count": exact_non_verifier_caller_count,
                "direct_verifier_count": direct_verifier_count,
                "caller_reference_bound_complete": caller_reference_bound_complete,
                "value_evidence_provider": value_evidence_provider or None,
                "value_repository_identity": value_repository_identity or None,
            }
        )
    normalized_symbols.sort(key=lambda row: (str(row["path"]), str(row["qualname"])))
    if not normalized_symbols:
        raise ValueError("at least one symbol reference is required")
    symbol_identities = [
        (str(row["path"]), str(row["qualname"])) for row in normalized_symbols
    ]
    if len(symbol_identities) != len(set(symbol_identities)):
        raise ValueError("symbol references must be unique by path and qualname")
    if not any(
        row["path"] == target_path and row["navigation_depth"] == 0
        for row in normalized_symbols
    ):
        raise ValueError(
            "snapshot must include the target authority at navigation depth 0"
        )

    verifier_files = _normalized_paths(list(verifier_paths))
    responsibility_rows = sorted(
        {str(value) for value in responsibilities if _nonempty(value)}
    )
    unresolved = sorted(
        {str(value) for value in unresolved_evidence if _nonempty(value)}
    )
    symbol_files = {str(row["path"]) for row in normalized_symbols}
    edit_files = {
        str(row["path"]) for row in normalized_symbols if row["edit_required"]
    }
    evidence_files = {
        str(row["path"]) for row in normalized_symbols if row["evidence_required"]
    } | set(verifier_files)
    dimensions = {
        "symbol_count": len(normalized_symbols),
        "file_count": len(symbol_files),
        "max_navigation_depth": max(
            int(row["navigation_depth"]) for row in normalized_symbols
        ),
        "forwarding_only_symbol_count": sum(
            1 for row in normalized_symbols if row["forwarding_only"]
        ),
        "context_lines": _union_line_count(normalized_symbols),
        "verifier_file_count": len(verifier_files),
        "edit_file_count": len(edit_files),
        "evidence_file_count": len(evidence_files),
        "cross_file_symbol_count": sum(
            1 for row in normalized_symbols if row["path"] != target_path
        ),
        "unresolved_call_count": 0,
        "target_exact_caller_count": next(
            (
                int(row["exact_caller_count"])
                for row in normalized_symbols
                if row["path"] == target_path and row["navigation_depth"] == 0
            ),
            0,
        ),
    }
    structural_signals = {
        "authority_lines": authority_lines,
        "branch_points": branch_points,
        "nesting_depth": nesting_depth,
        "responsibility_count": len(responsibility_rows),
    }
    semantic = {
        "schema": SNAPSHOT_SCHEMA,
        "repository_identity": repository_identity,
        "target": target,
        "target_path": target_path,
        "source_identity": source_identity,
        "measurement_configuration_identity": measurement_configuration_identity,
        "provider": provider,
        "provider_evidence_identity": provider_evidence_identity,
        "state_kind": state_kind,
        "symbols": normalized_symbols,
        "verifier_paths": verifier_files,
        "responsibilities": responsibility_rows,
        "unresolved_evidence": unresolved,
        "dimensions": dimensions,
        "structural_signals": structural_signals,
    }
    claims = {
        "composite_score_used": False,
        "size_alone_justifies_decomposition": False,
        "edit_authorized": False,
        "independent_structural_provider": False,
    }
    payload = {**semantic, "claims": claims}
    return {**payload, "evidence_identity": _identity(payload)}


def _hashmarks_packet_validation(
    packet: Mapping[str, object],
) -> tuple[list[str], list[str]]:
    """Return integrity errors and incompleteness from one Hashmarks packet."""
    errors: list[str] = []
    incomplete: list[str] = []
    if packet.get("schema") != HASHMARKS_STRUCTURAL_LOCALITY_SCHEMA:
        errors.append("hashmarks-schema")
    if packet.get("provider") != "hashmarks":
        errors.append("hashmarks-provider")
    semantic = {
        str(key): value for key, value in packet.items() if key != "evidence_identity"
    }
    expected_identity = _hashmarks_identity(semantic)
    if packet.get("evidence_identity") != expected_identity:
        errors.append("hashmarks-evidence-identity")
    for key in (
        "repository_identity",
        "source_identity",
        "measurement_configuration_identity",
        "target",
        "target_symbol_id",
        "provider_version",
        "provider_implementation_identity",
    ):
        if not _nonempty(packet.get(key)):
            errors.append(f"hashmarks-{key.replace('_', '-')}")
    freshness = packet.get("freshness")
    if not isinstance(freshness, Mapping):
        errors.append("hashmarks-freshness")
    elif freshness.get("state") != "current":
        incomplete.append("hashmarks-current-freshness")
    claims = packet.get("claims")
    if not isinstance(claims, Mapping):
        errors.append("hashmarks-claims")
    else:
        forbidden_true = (
            "refactor_recommendation",
            "semantic_responsibility_inferred",
            "ambiguous_calls_promoted_to_exact",
            "execution_authority",
        )
        if any(claims.get(key) is not False for key in forbidden_true):
            errors.append("hashmarks-observer-boundary")
    nodes = packet.get("nodes")
    if (
        not isinstance(nodes, Sequence)
        or isinstance(nodes, (str, bytes, bytearray))
        or not nodes
        or any(not isinstance(row, Mapping) for row in nodes)
    ):
        errors.append("hashmarks-nodes")
        nodes = []
    target_symbol_id = packet.get("target_symbol_id")
    if nodes and not any(
        row.get("symbol_id") == target_symbol_id
        for row in nodes
        if isinstance(row, Mapping)
    ):
        errors.append("hashmarks-target-node")
    for row in nodes:
        if (
            isinstance(row, Mapping)
            and row.get("caller_reference_bound_complete") is False
        ):
            symbol_id = str(row.get("symbol_id") or "<unknown>")
            incomplete.append(f"hashmarks-caller-reference-bound:{symbol_id}")
    dimensions = packet.get("dimensions")
    if not isinstance(dimensions, Mapping):
        errors.append("hashmarks-dimensions")
    else:
        for dimension in LOCALITY_DIMENSIONS:
            value = dimensions.get(dimension)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"hashmarks-dimension:{dimension}")
    unresolved = packet.get("unresolved_calls")
    if isinstance(unresolved, Sequence) and not isinstance(
        unresolved, (str, bytes, bytearray)
    ):
        if unresolved:
            incomplete.append("hashmarks-unresolved-calls")
    else:
        errors.append("hashmarks-unresolved-calls-shape")
    return sorted(set(errors)), sorted(set(incomplete))


def _observation_semantic(
    *,
    target: str,
    executable: str,
    argv: Sequence[str],
    command_identity: str,
    packet: Mapping[str, object] | None,
    status: str,
    classification: str,
    workspace_before_identity: str | None,
    workspace_after_identity: str | None,
    changed_paths: Sequence[str],
    stdout_sha256: str,
    stderr_sha256: str,
    return_code: int | None,
    timed_out: bool,
    executable_missing: bool,
    output_truncated: bool,
) -> dict[str, object]:
    return {
        "schema": HASHMARKS_OBSERVATION_SCHEMA,
        "target": target,
        "executable": executable,
        "argv": list(argv),
        "command_identity": command_identity,
        "packet_evidence_identity": None
        if packet is None
        else packet.get("evidence_identity"),
        "packet_repository_identity": None
        if packet is None
        else packet.get("repository_identity"),
        "provider_implementation_identity": None
        if packet is None
        else packet.get("provider_implementation_identity"),
        "status": status,
        "classification": classification,
        "workspace_before_identity": workspace_before_identity,
        "workspace_after_identity": workspace_after_identity,
        "changed_tracked_paths": list(changed_paths),
        "stdout_sha256": stdout_sha256,
        "stderr_sha256": stderr_sha256,
        "return_code": return_code,
        "timed_out": timed_out,
        "executable_missing": executable_missing,
        "output_truncated": output_truncated,
    }


def observe_hashmarks_locality(
    *,
    repository_root: Path,
    target: str,
    hashmarks_executable: str = "hashmarks",
    max_depth: int = 2,
    call_limit: int = 64,
    ref_limit: int = 256,
    timeout_seconds: float = 60.0,
    max_stdout_bytes: int = 2_000_000,
    max_stderr_bytes: int = 500_000,
) -> dict[str, object]:
    """Execute Hashmarks directly and bind its packet to observed repository bytes."""
    root = repository_root.resolve()
    if not root.is_dir():
        raise ValueError(f"repository root does not exist: {root}")
    if not target.strip():
        raise ValueError("target must be non-empty")
    if shutil.which(hashmarks_executable) is None:
        raise ValueError(f"Hashmarks executable not found: {hashmarks_executable}")

    try:
        before = tracked_workspace_state(root)
    except WorkspaceStateError as exc:
        raise ValueError(
            "authoritative Hashmarks observation requires bounded Git tracked-byte identity"
        ) from exc

    argv = (
        hashmarks_executable,
        "--workspace",
        ".",
        "structural-locality",
        target,
        "--max-depth",
        str(max_depth),
        "--call-limit",
        str(call_limit),
        "--ref-limit",
        str(ref_limit),
    )
    result = run_bounded(
        repository_root=root,
        argv=argv,
        limits=ProcessLimits(
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=max_stdout_bytes,
            max_stderr_bytes=max_stderr_bytes,
        ),
    )
    try:
        after = tracked_workspace_state(root)
    except WorkspaceStateError as exc:
        raise ValueError(
            "cannot establish post-observation tracked-byte identity"
        ) from exc
    changed = changed_tracked_paths(before, after)
    stdout_sha = hashlib.sha256(result.stdout).hexdigest()
    stderr_sha = hashlib.sha256(result.stderr).hexdigest()
    output_truncated = result.stdout_truncated or result.stderr_truncated

    packet: Mapping[str, object] | None = None
    classification = "pass"
    if result.executable_missing:
        classification = "executable_missing"
    elif result.timed_out:
        classification = "timeout"
    elif output_truncated:
        classification = "output_limit_exceeded"
    elif result.return_code != 0:
        classification = "hashmarks_failed"
    elif changed:
        classification = "tracked_workspace_mutation"
    else:
        try:
            parsed = json.loads(result.stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            classification = "invalid_hashmarks_json"
        else:
            if not isinstance(parsed, Mapping):
                classification = "invalid_hashmarks_packet"
            else:
                errors, _ = _hashmarks_packet_validation(parsed)
                if errors:
                    classification = "invalid_hashmarks_packet"
                else:
                    packet = parsed

    status = "PASS" if classification == "pass" and packet is not None else "FAIL"
    semantic = _observation_semantic(
        target=target,
        executable=hashmarks_executable,
        argv=argv,
        command_identity=result.command_identity,
        packet=packet,
        status=status,
        classification=classification,
        workspace_before_identity=str(before["identity"]),
        workspace_after_identity=str(after["identity"]),
        changed_paths=changed,
        stdout_sha256=stdout_sha,
        stderr_sha256=stderr_sha,
        return_code=result.return_code,
        timed_out=result.timed_out,
        executable_missing=result.executable_missing,
        output_truncated=output_truncated,
    )
    receipt = {**semantic, "evidence_identity": _identity(semantic)}
    return {
        "packet": None if packet is None else dict(packet),
        "observation_receipt": receipt,
        "execution": result.metrics(),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
    }


def _observation_receipt_matches(
    receipt: Mapping[str, object] | None,
    *,
    packet: Mapping[str, object],
) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    if receipt.get("schema") != HASHMARKS_OBSERVATION_SCHEMA:
        return False
    semantic = {
        str(key): value for key, value in receipt.items() if key != "evidence_identity"
    }
    if receipt.get("evidence_identity") != _identity(semantic):
        return False
    executable = receipt.get("executable")
    bounds = packet.get("bounds")
    argv = receipt.get("argv")
    if (
        not _nonempty(executable)
        or not isinstance(bounds, Mapping)
        or not isinstance(argv, Sequence)
        or isinstance(argv, (str, bytes, bytearray))
    ):
        return False
    expected_argv = [
        str(executable),
        "--workspace",
        ".",
        "structural-locality",
        str(packet.get("target") or ""),
        "--max-depth",
        str(bounds.get("max_depth")),
        "--call-limit",
        str(bounds.get("call_limit_per_symbol")),
        "--ref-limit",
        str(bounds.get("ref_limit_per_symbol")),
    ]
    observed_argv = [str(value) for value in argv]
    if observed_argv != expected_argv:
        return False
    if receipt.get("command_identity") != _bounded_command_identity(observed_argv):
        return False
    return (
        receipt.get("status") == "PASS"
        and receipt.get("classification") == "pass"
        and receipt.get("packet_evidence_identity") == packet.get("evidence_identity")
        and receipt.get("packet_repository_identity")
        == packet.get("repository_identity")
        and receipt.get("provider_implementation_identity")
        == packet.get("provider_implementation_identity")
        and receipt.get("target") == packet.get("target")
        and receipt.get("workspace_before_identity")
        == receipt.get("workspace_after_identity")
        and receipt.get("changed_tracked_paths") == []
        and receipt.get("return_code") == 0
        and receipt.get("timed_out") is False
        and receipt.get("executable_missing") is False
        and receipt.get("output_truncated") is False
    )


def locality_snapshot_from_hashmarks(
    *,
    packet: Mapping[str, object],
    observation_receipt: Mapping[str, object] | None = None,
    structural_values: Sequence[Mapping[str, object]] = (),
    _provider_execution_observed: bool = False,
    responsibilities: Sequence[str] = (),
    authority_lines: int | None = None,
    branch_points: int | None = None,
    nesting_depth: int | None = None,
    state_kind: str = "observed",
) -> dict[str, object]:
    """Build refactor-locality evidence from independently observed Hashmarks facts."""
    errors, incomplete = _hashmarks_packet_validation(packet)
    if errors:
        raise ValueError(
            "invalid Hashmarks structural-locality evidence: " + ", ".join(errors)
        )
    observed_execution = _provider_execution_observed and _observation_receipt_matches(
        observation_receipt, packet=packet
    )
    if not observed_execution:
        incomplete.append("provider-execution-binding")
    repository_identity = str(packet["repository_identity"])
    value_rows: dict[str, Mapping[str, object]] = {}
    for row in structural_values:
        symbol_id = str(row.get("symbol_id") or "")
        if not _nonempty(symbol_id):
            raise ValueError("structural value evidence requires symbol_id")
        if symbol_id in value_rows:
            raise ValueError(f"duplicate structural value evidence: {symbol_id}")
        if str(row.get("repository_identity") or "") != repository_identity:
            raise ValueError(
                f"structural value evidence for {symbol_id} is not bound to the measured repository"
            )
        if not _nonempty(row.get("evidence_provider")) or not _nonempty(
            row.get("evidence_identity")
        ):
            raise ValueError(
                f"structural value evidence for {symbol_id} requires provider and identity"
            )
        value_kind = str(row.get("value_kind") or "")
        if value_kind not in STRUCTURAL_VALUE_KINDS:
            raise ValueError(f"unsupported structural value kind: {value_kind}")
        value_rows[symbol_id] = row

    verifier_paths = packet.get("verification_paths")
    if not isinstance(verifier_paths, Sequence) or isinstance(
        verifier_paths, (str, bytes, bytearray)
    ):
        raise ValueError("Hashmarks verification_paths is malformed")
    verifier_file_set = {str(value) for value in verifier_paths if _nonempty(value)}

    raw_nodes = packet["nodes"]
    assert isinstance(raw_nodes, Sequence)
    symbols: list[dict[str, object]] = []
    known_symbol_ids: set[str] = set()
    target_symbol_id = str(packet["target_symbol_id"])
    target_path = target_symbol_id.split("::", 1)[0]
    for raw in raw_nodes:
        assert isinstance(raw, Mapping)
        symbol_id = str(raw.get("symbol_id") or "")
        path = str(raw.get("path") or "")
        qualname = str(raw.get("qualname") or "")
        lines = raw.get("lines")
        if (
            not _nonempty(symbol_id)
            or not _nonempty(path)
            or not _nonempty(qualname)
            or not isinstance(lines, Sequence)
            or isinstance(lines, (str, bytes, bytearray))
            or len(lines) != 2
        ):
            raise ValueError("Hashmarks symbol row is malformed")
        known_symbol_ids.add(symbol_id)
        value = value_rows.get(symbol_id)
        forwarding = raw.get("forwarding_only")
        symbol_kind = str(raw.get("kind") or "")
        if forwarding is None:
            if symbol_kind != "class":
                incomplete.append(f"forwarding-shape:{symbol_id}")
        elif not isinstance(forwarding, bool):
            raise ValueError(f"invalid forwarding fact for {symbol_id}")
        caller_count = raw.get("exact_caller_count")
        caller_complete = raw.get("caller_reference_bound_complete")
        exact_callers = raw.get("exact_callers")
        if (
            not isinstance(caller_count, int)
            or isinstance(caller_count, bool)
            or caller_count < 0
            or not isinstance(caller_complete, bool)
            or not isinstance(exact_callers, Sequence)
            or isinstance(exact_callers, (str, bytes, bytearray))
            or any(not isinstance(caller, Mapping) for caller in exact_callers)
        ):
            raise ValueError(f"invalid caller facts for {symbol_id}")
        caller_rows = [
            caller for caller in exact_callers if isinstance(caller, Mapping)
        ]
        if len(caller_rows) != caller_count:
            raise ValueError(
                f"exact caller count does not match caller rows for {symbol_id}"
            )
        if any(not _nonempty(caller.get("path")) for caller in caller_rows):
            raise ValueError(f"exact caller rows require paths for {symbol_id}")
        exact_non_verifier_caller_count = sum(
            1
            for caller in caller_rows
            if str(caller.get("path") or "") not in verifier_file_set
        )
        structure_kind = (
            str(value.get("structure_kind") or "") if value is not None else ""
        ) or ("forwarding_helper" if forwarding is True else "implementation")
        symbols.append(
            {
                "path": path,
                "qualname": qualname,
                "line_start": int(lines[0]),
                "line_end": int(lines[1]),
                "navigation_depth": int(raw.get("navigation_depth", 0)),
                "structure_kind": structure_kind,
                "forwarding_only": forwarding is True,
                "edit_required": bool(value.get("edit_required", False))
                if value is not None
                else False,
                "evidence_required": True,
                "value_kind": None
                if value is None
                else str(value.get("value_kind") or ""),
                "value_evidence_identity": None
                if value is None
                else str(value.get("evidence_identity") or ""),
                "value_evidence_provider": None
                if value is None
                else str(value.get("evidence_provider") or ""),
                "value_repository_identity": None
                if value is None
                else repository_identity,
                "exact_caller_count": caller_count,
                "exact_non_verifier_caller_count": exact_non_verifier_caller_count,
                "caller_reference_bound_complete": caller_complete,
                # Hashmarks v1 exposes related verifier paths, not exact
                # symbol-to-test seam ownership. Do not manufacture direct seams.
                "direct_verifier_count": 0,
                "provider_symbol_evidence_identity": raw.get(
                    "symbol_evidence_identity"
                ),
            }
        )
    unknown_values = sorted(set(value_rows) - known_symbol_ids)
    if unknown_values:
        raise ValueError(
            "structural value evidence references symbols absent from Hashmarks: "
            + ", ".join(unknown_values)
        )

    normalized = locality_snapshot(
        repository_identity=repository_identity,
        target=str(packet["target"]),
        target_path=target_path,
        source_identity=str(packet["source_identity"]),
        measurement_configuration_identity=str(
            packet["measurement_configuration_identity"]
        ),
        provider="hashmarks",
        provider_evidence_identity=str(packet["evidence_identity"]),
        symbols=symbols,
        verifier_paths=[str(value) for value in verifier_paths],
        responsibilities=responsibilities,
        unresolved_evidence=sorted(set(incomplete)),
        authority_lines=authority_lines,
        branch_points=branch_points,
        nesting_depth=nesting_depth,
        state_kind=state_kind,
    )
    provider_dimensions = packet.get("dimensions")
    assert isinstance(provider_dimensions, Mapping)
    normalized["dimensions"] = {
        dimension: int(provider_dimensions[dimension])
        for dimension in LOCALITY_DIMENSIONS
    }
    normalized["provider_packet_schema"] = HASHMARKS_STRUCTURAL_LOCALITY_SCHEMA
    normalized["provider_packet_identity"] = packet["evidence_identity"]
    normalized["provider_observation_identity"] = (
        None
        if observation_receipt is None
        else observation_receipt.get("evidence_identity")
    )
    claims = dict(normalized["claims"])
    claims["independent_structural_provider"] = observed_execution
    claims["caller_counts_from_provider"] = True
    claims["forwarding_shape_from_provider"] = True
    claims["direct_test_seam_inferred"] = False
    claims["provider_execution_observed"] = observed_execution
    normalized["claims"] = claims

    semantic = {
        key: value for key, value in normalized.items() if key != "evidence_identity"
    }
    normalized["evidence_identity"] = _identity(semantic)
    return normalized


def _locality_snapshot_from_observed_hashmarks(
    *,
    packet: Mapping[str, object],
    observation_receipt: Mapping[str, object],
    structural_values: Sequence[Mapping[str, object]] = (),
    responsibilities: Sequence[str] = (),
    authority_lines: int | None = None,
    branch_points: int | None = None,
    nesting_depth: int | None = None,
    state_kind: str = "observed",
) -> dict[str, object]:
    """Internal promotion path used only after this process executed Hashmarks."""
    return locality_snapshot_from_hashmarks(
        packet=packet,
        observation_receipt=observation_receipt,
        structural_values=structural_values,
        responsibilities=responsibilities,
        authority_lines=authority_lines,
        branch_points=branch_points,
        nesting_depth=nesting_depth,
        state_kind=state_kind,
        _provider_execution_observed=True,
    )


def _symbol_map(
    snapshot: Mapping[str, object],
) -> dict[tuple[str, str], Mapping[str, object]]:
    raw = snapshot.get("symbols")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return {}
    result: dict[tuple[str, str], Mapping[str, object]] = {}
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        key = (str(row.get("path") or ""), str(row.get("qualname") or ""))
        if all(key):
            result[key] = row
    return result


def _structural_value_is_credible(row: Mapping[str, object]) -> bool:
    value_kind = row.get("value_kind")
    value_identity = row.get("value_evidence_identity")
    structure_kind = str(row.get("structure_kind") or "implementation")
    forwarding_only = bool(row.get("forwarding_only", False))
    caller_count = row.get("exact_caller_count", 0)
    non_verifier_caller_count = row.get("exact_non_verifier_caller_count", caller_count)
    verifier_count = row.get("direct_verifier_count", 0)
    value_provider = row.get("value_evidence_provider")
    value_repository_identity = row.get("value_repository_identity")
    if (
        not isinstance(value_kind, str)
        or value_kind not in STRUCTURAL_VALUE_KINDS
        or not _nonempty(value_identity)
        or not _nonempty(value_provider)
        or not _nonempty(value_repository_identity)
    ):
        return False
    if forwarding_only and value_kind not in FORWARDING_BOUNDARY_VALUES:
        return False
    if value_kind == "shared_reuse" and (
        not isinstance(non_verifier_caller_count, int)
        or isinstance(non_verifier_caller_count, bool)
        or non_verifier_caller_count < 2
    ):
        return False
    if value_kind == "direct_test_seam" and (
        not isinstance(verifier_count, int)
        or isinstance(verifier_count, bool)
        or verifier_count < 1
    ):
        return False
    if (
        structure_kind
        in {
            "shim",
            "proxy",
            "delegate",
            "forwarding_helper",
            "reexport",
            "alias_module",
        }
        and value_kind not in FORWARDING_BOUNDARY_VALUES
    ):
        return False
    if structure_kind == "adapter" and value_kind not in {
        "protocol_adapter",
        "stable_external_boundary",
        "compatibility_boundary",
    }:
        return False
    return True


def _introduced_structure_evidence(
    pre_snapshot: Mapping[str, object],
    post_snapshot: Mapping[str, object],
) -> tuple[list[dict[str, object]], list[str], list[str], list[dict[str, str]]]:
    pre_symbols = _symbol_map(pre_snapshot)
    post_symbols = _symbol_map(post_snapshot)
    introduced: list[dict[str, object]] = []
    unjustified: list[str] = []
    easy_path: list[str] = []
    anti_patterns: list[dict[str, str]] = []
    for key in sorted(set(post_symbols) - set(pre_symbols)):
        row = post_symbols[key]
        path, qualname = key
        structure_kind = str(row.get("structure_kind") or "implementation")
        value_kind = row.get("value_kind")
        value_identity = row.get("value_evidence_identity")
        forwarding_only = bool(row.get("forwarding_only", False))
        earned = _structural_value_is_credible(row)
        identity = f"{path}::{qualname}"
        if not earned:
            unjustified.append(identity)
        if structure_kind in EASY_PATH_STRUCTURE_KINDS or forwarding_only:
            easy_path.append(identity)
        if not earned and structure_kind in EASY_PATH_STRUCTURE_KINDS:
            anti_patterns.append(
                {
                    "symbol": identity,
                    "code": "unearned-easy-path-layer",
                }
            )
        if forwarding_only and not earned:
            anti_patterns.append(
                {
                    "symbol": identity,
                    "code": "forwarder-without-external-boundary",
                }
            )
        if structure_kind == "implementation" and not earned:
            anti_patterns.append(
                {
                    "symbol": identity,
                    "code": "extraction-without-evidence-bound-semantic-value",
                }
            )
        introduced.append(
            {
                "path": path,
                "qualname": qualname,
                "structure_kind": structure_kind,
                "forwarding_only": forwarding_only,
                "value_kind": value_kind,
                "value_evidence_identity": value_identity,
                "exact_caller_count": row.get("exact_caller_count", 0),
                "exact_non_verifier_caller_count": row.get(
                    "exact_non_verifier_caller_count",
                    row.get("exact_caller_count", 0),
                ),
                "direct_verifier_count": row.get("direct_verifier_count", 0),
                "caller_reference_bound_complete": row.get(
                    "caller_reference_bound_complete", False
                ),
                "value_evidence_provider": row.get("value_evidence_provider"),
                "value_repository_identity": row.get("value_repository_identity"),
                "earned_structural_value": earned,
            }
        )
    return (
        introduced,
        sorted(unjustified),
        sorted(easy_path),
        sorted(anti_patterns, key=lambda row: (row["symbol"], row["code"])),
    )


def compare_locality(
    pre_snapshot: Mapping[str, object],
    post_snapshot: Mapping[str, object],
) -> dict[str, object]:
    unresolved: list[str] = []
    if not _artifact_identity_valid(pre_snapshot):
        unresolved.append("pre-snapshot-integrity")
    if not _artifact_identity_valid(post_snapshot):
        unresolved.append("post-snapshot-integrity")
    if (
        pre_snapshot.get("schema") != SNAPSHOT_SCHEMA
        or post_snapshot.get("schema") != SNAPSHOT_SCHEMA
    ):
        unresolved.append("valid-locality-snapshots")
    if pre_snapshot.get("target") != post_snapshot.get("target"):
        unresolved.append("same-semantic-target")
    if pre_snapshot.get("measurement_configuration_identity") != post_snapshot.get(
        "measurement_configuration_identity"
    ):
        unresolved.append("comparable-measurement-configuration")
    if pre_snapshot.get("provider") != post_snapshot.get("provider"):
        unresolved.append("same-measurement-provider")
    pre_claims = pre_snapshot.get("claims")
    post_claims = post_snapshot.get("claims")
    pre_independent = (
        isinstance(pre_claims, Mapping)
        and pre_claims.get("independent_structural_provider") is True
    )
    post_independent = (
        isinstance(post_claims, Mapping)
        and post_claims.get("independent_structural_provider") is True
    )
    if pre_independent != post_independent:
        unresolved.append("same-structural-evidence-authority")
    same_repository = pre_snapshot.get("repository_identity") == post_snapshot.get(
        "repository_identity"
    )
    proposed_post = post_snapshot.get("state_kind") == "proposed"
    distinct_source = pre_snapshot.get("source_identity") != post_snapshot.get(
        "source_identity"
    )
    if same_repository and not (proposed_post and distinct_source):
        unresolved.append("distinct-observed-state-or-proposal")
    for label, snapshot in (("pre", pre_snapshot), ("post", post_snapshot)):
        if snapshot.get("unresolved_evidence"):
            unresolved.append(f"{label}-snapshot-completeness")
        if not _nonempty(snapshot.get("evidence_identity")):
            unresolved.append(f"{label}-snapshot-identity")
        if not _nonempty(snapshot.get("provider_evidence_identity")):
            unresolved.append(f"{label}-provider-evidence")

    pre_dimensions = pre_snapshot.get("dimensions")
    post_dimensions = post_snapshot.get("dimensions")
    if not isinstance(pre_dimensions, Mapping) or not isinstance(
        post_dimensions, Mapping
    ):
        unresolved.append("locality-dimensions")
        pre_dimensions = {}
        post_dimensions = {}

    deltas: dict[str, int] = {}
    worsened: list[str] = []
    improved: list[str] = []
    hard_regressions: list[str] = []
    for dimension in LOCALITY_DIMENSIONS:
        pre_value = pre_dimensions.get(dimension)
        post_value = post_dimensions.get(dimension)
        if (
            not isinstance(pre_value, int)
            or isinstance(pre_value, bool)
            or not isinstance(post_value, int)
            or isinstance(post_value, bool)
        ):
            unresolved.append(f"dimension:{dimension}")
            continue
        delta = post_value - pre_value
        deltas[dimension] = delta
        if delta > 0:
            worsened.append(dimension)
            if dimension in HARD_REGRESSION_DIMENSIONS:
                hard_regressions.append(dimension)
        elif delta < 0:
            improved.append(dimension)

    introduced, unjustified, easy_path, anti_patterns = _introduced_structure_evidence(
        pre_snapshot, post_snapshot
    )
    limited_observed_reuse = sorted(
        f"{row['path']}::{row['qualname']}"
        for row in introduced
        if isinstance(row.get("exact_caller_count"), int)
        and not isinstance(row.get("exact_caller_count"), bool)
        and int(row["exact_caller_count"]) <= 1
    )
    if unjustified:
        hard_regressions.append("unjustified-new-structure")

    unresolved = sorted(set(unresolved))
    hard_regressions = sorted(set(hard_regressions))
    if unresolved:
        status = INSUFFICIENT_LOCALITY_EVIDENCE
    elif hard_regressions:
        status = LOCALITY_REGRESSED
    elif worsened or easy_path:
        status = LOCALITY_TRADEOFF_REVIEW_REQUIRED
    else:
        status = LOCALITY_PRESERVED_OR_IMPROVED

    semantic = {
        "schema": COMPARISON_SCHEMA,
        "target": pre_snapshot.get("target"),
        "pre_repository_identity": pre_snapshot.get("repository_identity"),
        "post_repository_identity": post_snapshot.get("repository_identity"),
        "pre_snapshot_identity": pre_snapshot.get("evidence_identity"),
        "post_snapshot_identity": post_snapshot.get("evidence_identity"),
        "measurement_configuration_identity": pre_snapshot.get(
            "measurement_configuration_identity"
        ),
        "provider": pre_snapshot.get("provider"),
        "deltas": deltas,
        "worsened_dimensions": sorted(worsened),
        "improved_dimensions": sorted(improved),
        "hard_regressions": hard_regressions,
        "introduced_structures": introduced,
        "unjustified_new_structures": unjustified,
        "easy_path_structures": easy_path,
        "limited_observed_reuse_structures": limited_observed_reuse,
        "structural_anti_patterns": anti_patterns,
        "status": status,
        "unresolved_evidence": unresolved,
    }
    claims = {
        "composite_score_used": False,
        "lower_entrypoint_loc_is_improvement_proof": False,
        "locality_preserved": status == LOCALITY_PRESERVED_OR_IMPROVED,
        "all_new_structure_earned_value": not unjustified,
        "limited_observed_reuse_proves_single_use": False,
        "wrappers_shims_are_default_solution": False,
    }
    payload = {**semantic, "claims": claims}
    return {**payload, "evidence_identity": _identity(payload)}


def decomposition_decision(
    *,
    pre_snapshot: Mapping[str, object],
    post_snapshot: Mapping[str, object],
    comparison: Mapping[str, object],
    decomposition_evidence: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    normalized_evidence = sorted(
        [
            {
                "kind": str(row.get("kind") or ""),
                "evidence_identity": str(row.get("evidence_identity") or ""),
                "summary": str(row.get("summary") or ""),
            }
            for row in decomposition_evidence
        ],
        key=lambda row: (row["kind"], row["evidence_identity"], row["summary"]),
    )
    invalid_evidence = [
        row
        for row in normalized_evidence
        if not _nonempty(row["kind"]) or not _nonempty(row["evidence_identity"])
    ]
    justifying = [
        row for row in normalized_evidence if row["kind"] in JUSTIFYING_EVIDENCE_KINDS
    ]
    tradeoff_bound = any(
        row["kind"] == TRADEOFF_EVIDENCE_KIND for row in normalized_evidence
    )
    size_only = bool(normalized_evidence) and not justifying
    comparison_status = comparison.get("status")
    comparison_bound = (
        _artifact_identity_valid(pre_snapshot)
        and _artifact_identity_valid(post_snapshot)
        and _artifact_identity_valid(comparison)
        and comparison.get("pre_snapshot_identity")
        == pre_snapshot.get("evidence_identity")
        and comparison.get("post_snapshot_identity")
        == post_snapshot.get("evidence_identity")
    )
    structural_value_complete = not bool(comparison.get("unjustified_new_structures"))
    pre_claims = pre_snapshot.get("claims")
    post_claims = post_snapshot.get("claims")
    independent_structural_evidence = (
        isinstance(pre_claims, Mapping)
        and isinstance(post_claims, Mapping)
        and pre_claims.get("independent_structural_provider") is True
        and post_claims.get("independent_structural_provider") is True
    )

    if (
        invalid_evidence
        or not comparison_bound
        or comparison_status == INSUFFICIENT_LOCALITY_EVIDENCE
    ):
        status = INSUFFICIENT_LOCALITY_EVIDENCE
    elif not structural_value_complete or comparison_status == LOCALITY_REGRESSED:
        status = DECOMPOSITION_LOCALITY_RISK
    elif not justifying:
        status = KEEP_COHESIVE_AUTHORITY
    elif not independent_structural_evidence:
        status = INSUFFICIENT_LOCALITY_EVIDENCE
    elif comparison_status == LOCALITY_PRESERVED_OR_IMPROVED:
        status = DECOMPOSITION_JUSTIFIED
    elif comparison_status == LOCALITY_TRADEOFF_REVIEW_REQUIRED and tradeoff_bound:
        status = DECOMPOSITION_JUSTIFIED
    else:
        status = INSUFFICIENT_LOCALITY_EVIDENCE

    introduced_structures = comparison.get("introduced_structures")
    introduced_structure_count = (
        len(introduced_structures)
        if isinstance(introduced_structures, Sequence)
        and not isinstance(introduced_structures, (str, bytes, bytearray))
        else 0
    )
    limited_observed_reuse = comparison.get("limited_observed_reuse_structures")
    limited_observed_reuse_rows = (
        sorted(str(value) for value in limited_observed_reuse)
        if isinstance(limited_observed_reuse, Sequence)
        and not isinstance(limited_observed_reuse, (str, bytes, bytearray))
        else []
    )
    semantic = {
        "schema": DECISION_SCHEMA,
        "target": pre_snapshot.get("target"),
        "pre_snapshot_identity": pre_snapshot.get("evidence_identity"),
        "post_snapshot_identity": post_snapshot.get("evidence_identity"),
        "comparison_identity": comparison.get("evidence_identity"),
        "comparison_status": comparison_status,
        "introduced_structure_count": introduced_structure_count,
        "limited_observed_reuse_structures": limited_observed_reuse_rows,
        "decomposition_evidence": normalized_evidence,
        "status": status,
    }
    claims = {
        "size_only_signal": size_only,
        "size_alone_justifies_decomposition": False,
        "all_new_structure_earned_value": structural_value_complete,
        "edit_authorized": False,
        "merge_authorized": False,
        "composite_score_used": False,
        "prefer_deletion_or_consolidation_before_new_layers": True,
        "independent_structural_evidence": independent_structural_evidence,
    }
    required_next_evidence = (
        [{"kind": "resolve-locality-evidence"}]
        if status == INSUFFICIENT_LOCALITY_EVIDENCE
        else (
            [
                {
                    "kind": "revise-decomposition-to-remove-unearned-structure",
                    "symbols": comparison.get("unjustified_new_structures", []),
                }
            ]
            if status == DECOMPOSITION_LOCALITY_RISK
            else []
        )
    )
    payload = {
        **semantic,
        "claims": claims,
        "required_next_evidence": required_next_evidence,
    }
    return {**payload, "evidence_identity": _identity(payload)}


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _load_structural_values(path: Path | None) -> list[Mapping[str, object]]:
    if path is None:
        return []
    raw_values = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw_values, Mapping):
        raw_values = raw_values.get("structural_values", [])
    if not isinstance(raw_values, list) or any(
        not isinstance(row, Mapping) for row in raw_values
    ):
        raise ValueError(
            "structural values must contain a JSON array or structural_values array"
        )
    return list(raw_values)


def _write(path: Path | None, payload: Mapping[str, object]) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(rendered, end="")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare refactor locality without a score and require every new "
            "structural layer to earn its existence."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = sub.add_parser(
        "snapshot", help="Canonicalize explicit locality facts."
    )
    snapshot_parser.add_argument("input", type=Path)
    snapshot_parser.add_argument("--artifact", type=Path)
    hashmarks_parser = sub.add_parser(
        "from-hashmarks",
        help="Build locality evidence from a fresh Hashmarks structural-locality packet.",
    )
    hashmarks_parser.add_argument("input", type=Path)
    hashmarks_parser.add_argument(
        "--values",
        type=Path,
        help="Optional JSON array/object of repository-bound structural value evidence.",
    )
    hashmarks_parser.add_argument("--artifact", type=Path)

    observe_parser = sub.add_parser(
        "observe-hashmarks",
        help="Execute Hashmarks under bounded process/workspace controls and capture an observation bundle.",
    )
    observe_parser.add_argument("target")
    observe_parser.add_argument("--repository-root", type=Path, default=Path("."))
    observe_parser.add_argument("--hashmarks-executable", default="hashmarks")
    observe_parser.add_argument(
        "--values",
        type=Path,
        help="Optional repository-bound structural value evidence for the live observation.",
    )
    observe_parser.add_argument("--max-depth", type=int, default=2)
    observe_parser.add_argument("--call-limit", type=int, default=64)
    observe_parser.add_argument("--ref-limit", type=int, default=256)
    observe_parser.add_argument("--timeout-seconds", type=float, default=60.0)
    observe_parser.add_argument("--artifact", type=Path)

    compare_parser = sub.add_parser(
        "compare", help="Compare pre/post locality snapshots."
    )
    compare_parser.add_argument("pre", type=Path)
    compare_parser.add_argument("post", type=Path)
    compare_parser.add_argument(
        "--evidence",
        type=Path,
        help="Optional decomposition evidence JSON array/object.",
    )
    compare_parser.add_argument("--artifact", type=Path)
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        raw = _load(args.input)
        payload = locality_snapshot(**raw)  # type: ignore[arg-type]
        _write(args.artifact, payload)
        return

    if args.command == "from-hashmarks":
        raw = _load(args.input)
        bundled_packet = raw.get("packet")
        bundled_receipt = raw.get("observation_receipt")
        packet = bundled_packet if isinstance(bundled_packet, Mapping) else raw
        # Saved packet/receipt input is intentionally diagnostic. A caller-supplied
        # receipt cannot promote itself to independent observation authority.
        _ = bundled_receipt
        payload = locality_snapshot_from_hashmarks(
            packet=packet,
            structural_values=_load_structural_values(args.values),
        )
        _write(args.artifact, payload)
        return

    if args.command == "observe-hashmarks":
        observed = observe_hashmarks_locality(
            repository_root=args.repository_root,
            target=args.target,
            hashmarks_executable=args.hashmarks_executable,
            max_depth=args.max_depth,
            call_limit=args.call_limit,
            ref_limit=args.ref_limit,
            timeout_seconds=args.timeout_seconds,
        )
        packet = observed.get("packet")
        receipt = observed.get("observation_receipt")
        snapshot = None
        if isinstance(packet, Mapping) and isinstance(receipt, Mapping):
            snapshot = _locality_snapshot_from_observed_hashmarks(
                packet=packet,
                observation_receipt=receipt,
                structural_values=_load_structural_values(args.values),
            )
        payload = {
            "schema": "agentscookbook-observed-hashmarks-locality/v1",
            **observed,
            "snapshot": snapshot,
        }
        _write(args.artifact, payload)
        if snapshot is None:
            raise SystemExit(1)
        return

    pre = _load(args.pre)
    post = _load(args.post)
    comparison = compare_locality(pre, post)
    if args.evidence is None:
        _write(args.artifact, comparison)
        return
    raw_evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    if isinstance(raw_evidence, dict):
        rows = raw_evidence.get("decomposition_evidence", [])
    else:
        rows = raw_evidence
    if not isinstance(rows, list):
        raise ValueError("decomposition evidence must be a JSON array")
    decision = decomposition_decision(
        pre_snapshot=pre,
        post_snapshot=post,
        comparison=comparison,
        decomposition_evidence=[row for row in rows if isinstance(row, dict)],
    )
    _write(args.artifact, {"comparison": comparison, "decision": decision})


if __name__ == "__main__":
    main()
