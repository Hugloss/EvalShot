from __future__ import annotations

from collections.abc import Mapping, Sequence

SCHEMA = "agentscookbook-working-evidence/v1"
SECTIONS = ("task", "known", "decisions", "remaining")
INVALIDATION_KINDS = frozenset(
    {
        "file-edit",
        "worktree-change",
        "repository-generation",
        "dependency-change",
        "environment-change",
        "process-restart",
        "session-end",
        "never-within-session",
    }
)


def new_working_evidence(*, goal: str) -> dict[str, object]:
    """Return the intentionally small, file-oriented task-state document."""
    return {
        "schema": SCHEMA,
        "task": {"goal": goal},
        "known": [],
        "decisions": [],
        "remaining": [],
    }


def validate_working_evidence(payload: object) -> list[str]:
    """Validate task-local evidence without creating a persistence service."""
    if not isinstance(payload, Mapping):
        return ["working evidence root must be an object"]

    errors: list[str] = []
    if payload.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r}")

    task = payload.get("task")
    if not isinstance(task, Mapping):
        errors.append("task must be an object")
    elif not isinstance(task.get("goal"), str) or not str(task.get("goal")).strip():
        errors.append("task.goal must be a non-empty string")

    for key in ("known", "decisions", "remaining"):
        value = payload.get(key)
        if not isinstance(value, Sequence) or isinstance(
            value, (str, bytes, bytearray)
        ):
            errors.append(f"{key} must be a list")

    forbidden = {
        "repository_graph",
        "ownership_graph",
        "test_graph",
        "repository_cache",
        "verification_cache",
        "repository_snapshot",
    }
    leaked = sorted(forbidden & set(payload))
    if leaked:
        errors.append(
            "working evidence must not duplicate repository intelligence: "
            + ", ".join(leaked)
        )
    return errors


def validate_invalidation(item: object) -> list[str]:
    """Validate explicit evidence lifetime without owning repository freshness."""
    if not isinstance(item, Mapping):
        return ["invalidation must be an object"]
    kind = item.get("kind")
    if not isinstance(kind, str) or kind not in INVALIDATION_KINDS:
        return ["invalidation.kind must be a supported explicit lifetime"]
    if kind == "repository-generation" and not isinstance(
        item.get("provider_reference"), str
    ):
        return [
            "repository-generation invalidation requires provider_reference; "
            "working evidence must not own repository generation"
        ]
    return []


def provider_reference(
    *,
    provider: str,
    evidence_identity: str,
    repository_identity: str | None = None,
    generation: str | int | None = None,
) -> dict[str, object]:
    """Build a small reference to reusable repository evidence, never a copy."""
    provider = provider.strip()
    evidence_identity = evidence_identity.strip()
    if not provider:
        raise ValueError("provider must be non-empty")
    if not evidence_identity:
        raise ValueError("evidence_identity must be non-empty")
    reference: dict[str, object] = {
        "provider": provider,
        "evidence_identity": evidence_identity,
    }
    if repository_identity is not None:
        reference["repository_identity"] = repository_identity
    if generation is not None:
        reference["generation"] = generation
    return reference


def validate_provider_reference(value: object) -> list[str]:
    """Validate a provider pointer without interpreting provider internals."""
    if not isinstance(value, Mapping):
        return ["provider reference must be an object"]
    errors: list[str] = []
    for field in ("provider", "evidence_identity"):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            errors.append(f"provider reference {field} must be a non-empty string")
    allowed = {"provider", "evidence_identity", "repository_identity", "generation"}
    extra = sorted(set(value) - allowed)
    if extra:
        errors.append(
            "provider reference must not embed repository intelligence: "
            + ", ".join(extra)
        )
    return errors


def action_fingerprint(
    *,
    action: str,
    inputs: Sequence[str],
    evidence_references: Sequence[Mapping[str, object]] = (),
) -> tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Canonicalize an acquisition attempt for anti-repeat comparison."""
    action = action.strip()
    if not action:
        raise ValueError("action must be non-empty")
    normalized_inputs = tuple(sorted({str(item) for item in inputs if str(item)}))
    references = tuple(
        sorted(
            (
                str(reference.get("provider") or ""),
                str(reference.get("evidence_identity") or ""),
            )
            for reference in evidence_references
        )
    )
    return action, normalized_inputs, references


def repeated_action_without_new_evidence(
    previous: Mapping[str, object],
    current: Mapping[str, object],
) -> bool:
    """Return a policy fact: same acquisition inputs and evidence means no progress."""
    previous_action = previous.get("action")
    current_action = current.get("action")
    previous_inputs = previous.get("inputs")
    current_inputs = current.get("inputs")
    previous_refs = previous.get("evidence_references", ())
    current_refs = current.get("evidence_references", ())
    if not isinstance(previous_action, str) or not isinstance(current_action, str):
        return False
    if not isinstance(previous_inputs, Sequence) or isinstance(
        previous_inputs, (str, bytes, bytearray)
    ):
        return False
    if not isinstance(current_inputs, Sequence) or isinstance(
        current_inputs, (str, bytes, bytearray)
    ):
        return False
    if not isinstance(previous_refs, Sequence) or isinstance(
        previous_refs, (str, bytes, bytearray)
    ):
        return False
    if not isinstance(current_refs, Sequence) or isinstance(
        current_refs, (str, bytes, bytearray)
    ):
        return False
    if not all(isinstance(item, Mapping) for item in (*previous_refs, *current_refs)):
        return False
    return action_fingerprint(
        action=previous_action,
        inputs=[str(item) for item in previous_inputs],
        evidence_references=previous_refs,
    ) == action_fingerprint(
        action=current_action,
        inputs=[str(item) for item in current_inputs],
        evidence_references=current_refs,
    )


def dirty_gate_delta_facts(
    observation_delta: Mapping[str, object],
) -> dict[str, object]:
    """Extract objective dirty-gate facts without parsing provider diagnostics."""
    diagnostics = observation_delta.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return {"usable": False, "reason": "missing-diagnostic-delta"}
    added = diagnostics.get("added")
    removed = diagnostics.get("removed")
    scoped = diagnostics.get("added_in_changed_scope")
    if not isinstance(added, Sequence) or isinstance(added, (str, bytes, bytearray)):
        return {"usable": False, "reason": "invalid-added-diagnostics"}
    if not isinstance(removed, Sequence) or isinstance(
        removed, (str, bytes, bytearray)
    ):
        return {"usable": False, "reason": "invalid-removed-diagnostics"}
    if not isinstance(scoped, Sequence) or isinstance(scoped, (str, bytes, bytearray)):
        return {"usable": False, "reason": "invalid-scoped-diagnostics"}
    return {
        "usable": True,
        "global_before_count": diagnostics.get("before_count"),
        "global_after_count": diagnostics.get("after_count"),
        "new_diagnostics": len(added),
        "removed_diagnostics": len(removed),
        "new_diagnostics_in_changed_scope": len(scoped),
        "unchanged_diagnostics": diagnostics.get("unchanged_count"),
    }


def provider_evidence_reuse_facts(
    freshness: Mapping[str, object],
) -> dict[str, object]:
    """Translate provider freshness into reusable policy facts without re-deriving it."""
    state = freshness.get("state")
    if state not in {"fresh", "stale"}:
        return {"usable": False, "reason": "unsupported-provider-freshness"}
    return {
        "usable": True,
        "fresh": state == "fresh",
        "provider_reason": freshness.get("reason"),
        "relevant_changes": list(freshness.get("intersection") or []),
    }


def evidence_stop_facts(
    *,
    risk_boundaries: Sequence[Mapping[str, object]],
    proofs: Sequence[Mapping[str, object]],
    scope_expansion_evidence: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Decide sufficiency from provider-bound proof relationships."""
    proof_by_boundary: dict[str, list[Mapping[str, object]]] = {}
    invalid_proofs: list[str] = []
    for proof in proofs:
        boundary = proof.get("boundary")
        reference = proof.get("provider_reference")
        freshness = proof.get("freshness")
        relationship = proof.get("relationship")
        if (
            not isinstance(boundary, str)
            or not boundary
            or not isinstance(reference, Mapping)
            or validate_provider_reference(reference)
            or not isinstance(freshness, Mapping)
            or freshness.get("state") not in {"fresh", "stale"}
            or not isinstance(relationship, Mapping)
            or not isinstance(relationship.get("classification"), str)
            or not isinstance(relationship.get("evidence_identity"), str)
            or not str(relationship.get("evidence_identity") or "").strip()
        ):
            invalid_proofs.append(str(boundary or "<invalid-proof>"))
            continue
        proof_by_boundary.setdefault(boundary, []).append(proof)

    uncovered: list[str] = []
    indirect_only: list[str] = []
    stale_only: list[str] = []
    unknown: list[str] = []
    for boundary in risk_boundaries:
        identity = boundary.get("identity")
        if not isinstance(identity, str) or not identity:
            unknown.append("<invalid-boundary>")
            continue
        candidates = proof_by_boundary.get(identity, [])
        if not candidates:
            uncovered.append(identity)
            continue
        fresh = [
            row
            for row in candidates
            if isinstance(row.get("freshness"), Mapping)
            and row["freshness"].get("state") == "fresh"
        ]
        if not fresh:
            stale_only.append(identity)
            continue
        direct = [
            row
            for row in fresh
            if isinstance(row.get("relationship"), Mapping)
            and row["relationship"].get("classification") == "direct"
        ]
        if not direct:
            indirect_only.append(identity)

    expansion_ids: list[str] = []
    invalid_expansion: list[str] = []
    for row in scope_expansion_evidence:
        identity = row.get("evidence_identity")
        provider = row.get("provider")
        boundary = row.get("boundary")
        if (
            not isinstance(identity, str)
            or not identity.strip()
            or not isinstance(provider, str)
            or not provider.strip()
            or not isinstance(boundary, str)
            or not boundary.strip()
        ):
            invalid_expansion.append("<invalid-scope-expansion>")
            continue
        expansion_ids.append(f"{provider}:{identity}:{boundary}")

    scope_expanded = bool(expansion_ids or invalid_expansion)
    sufficient = not (
        uncovered
        or indirect_only
        or stale_only
        or unknown
        or invalid_proofs
        or scope_expanded
    )
    return {
        "sufficient": sufficient,
        "stop_acquiring_evidence": sufficient,
        "scope_expanded": scope_expanded,
        "scope_expansion_evidence": sorted(expansion_ids),
        "invalid_scope_expansion_evidence": sorted(invalid_expansion),
        "invalid_proofs": sorted(invalid_proofs),
        "uncovered_boundaries": sorted(uncovered),
        "indirect_only_boundaries": sorted(indirect_only),
        "stale_only_boundaries": sorted(stale_only),
        "unknown_boundaries": sorted(unknown),
        "reason": (
            "all-declared-risk-boundaries-have-fresh-direct-provider-proof"
            if sufficient
            else "more-evidence-required"
        ),
    }
