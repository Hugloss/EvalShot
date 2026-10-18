from scripts.agent_economics.working_evidence import (
    SCHEMA,
    dirty_gate_delta_facts,
    evidence_stop_facts,
    provider_evidence_reuse_facts,
    provider_reference,
    repeated_action_without_new_evidence,
    validate_provider_reference,
    new_working_evidence,
    validate_invalidation,
    validate_working_evidence,
)


def test_working_evidence_is_small_file_oriented_task_state() -> None:
    payload = new_working_evidence(goal="reduce repeated verification discovery")

    assert payload == {
        "schema": SCHEMA,
        "task": {"goal": "reduce repeated verification discovery"},
        "known": [],
        "decisions": [],
        "remaining": [],
    }
    assert validate_working_evidence(payload) == []


def test_working_evidence_rejects_repository_intelligence_copies() -> None:
    payload = new_working_evidence(goal="keep ownership singular")
    payload["ownership_graph"] = {"copied": True}

    errors = validate_working_evidence(payload)

    assert any(
        "must not duplicate repository intelligence" in error for error in errors
    )


def test_repository_generation_invalidation_requires_provider_reference() -> None:
    errors = validate_invalidation({"kind": "repository-generation"})

    assert errors == [
        "repository-generation invalidation requires provider_reference; "
        "working evidence must not own repository generation"
    ]
    assert (
        validate_invalidation(
            {
                "kind": "repository-generation",
                "provider_reference": "hashmarks:repository-generation",
            }
        )
        == []
    )


def test_environment_lifetime_is_task_local_and_does_not_need_repository_owner() -> (
    None
):
    assert validate_invalidation({"kind": "environment-change"}) == []


def test_provider_reference_points_to_repository_evidence_without_copying_it() -> None:
    reference = provider_reference(
        provider="hashmarks",
        evidence_identity="sha256:evidence",
        repository_identity="sha256:repository",
        generation=42,
    )

    assert reference == {
        "provider": "hashmarks",
        "evidence_identity": "sha256:evidence",
        "repository_identity": "sha256:repository",
        "generation": 42,
    }
    assert validate_provider_reference(reference) == []


def test_provider_reference_rejects_embedded_repository_facts() -> None:
    errors = validate_provider_reference(
        {
            "provider": "hashmarks",
            "evidence_identity": "sha256:evidence",
            "ownership_graph": {"copied": True},
        }
    )

    assert errors == [
        "provider reference must not embed repository intelligence: ownership_graph"
    ]


def test_identical_acquisition_without_new_evidence_is_detected() -> None:
    reference = provider_reference(
        provider="hashmarks",
        evidence_identity="sha256:delta-a",
        generation=42,
    )
    previous = {
        "action": "discover-related-tests",
        "inputs": ["src/b.py", "src/a.py"],
        "evidence_references": [reference],
    }
    current = {
        "action": "discover-related-tests",
        "inputs": ["src/a.py", "src/b.py"],
        "evidence_references": [reference],
    }

    assert repeated_action_without_new_evidence(previous, current) is True


def test_new_provider_evidence_allows_same_acquisition_to_be_reconsidered() -> None:
    previous = {
        "action": "repository-typecheck",
        "inputs": ["pyproject.toml"],
        "evidence_references": [
            {"provider": "hashmarks", "evidence_identity": "sha256:generation-41"}
        ],
    }
    current = {
        "action": "repository-typecheck",
        "inputs": ["pyproject.toml"],
        "evidence_references": [
            {"provider": "hashmarks", "evidence_identity": "sha256:generation-42"}
        ],
    }

    assert repeated_action_without_new_evidence(previous, current) is False


def test_dirty_gate_facts_do_not_treat_same_count_as_same_diagnostics() -> None:
    facts = dirty_gate_delta_facts(
        {
            "diagnostics": {
                "before_count": 1841,
                "after_count": 1841,
                "added": [{"identity": "new-a"}, {"identity": "new-b"}],
                "removed": [{"identity": "old-a"}, {"identity": "old-b"}],
                "unchanged_count": 1839,
                "added_in_changed_scope": [],
            }
        }
    )

    assert facts == {
        "usable": True,
        "global_before_count": 1841,
        "global_after_count": 1841,
        "new_diagnostics": 2,
        "removed_diagnostics": 2,
        "new_diagnostics_in_changed_scope": 0,
        "unchanged_diagnostics": 1839,
    }


def test_dirty_gate_facts_require_provider_delta_shape() -> None:
    assert dirty_gate_delta_facts({"diagnostics": {"before_count": 10}}) == {
        "usable": False,
        "reason": "invalid-added-diagnostics",
    }


def test_provider_freshness_allows_reuse_after_proven_unrelated_edit() -> None:
    facts = provider_evidence_reuse_facts(
        {
            "state": "fresh",
            "reason": "changed-paths-proven-outside-observation-scope",
            "intersection": [],
        }
    )

    assert facts == {
        "usable": True,
        "fresh": True,
        "provider_reason": "changed-paths-proven-outside-observation-scope",
        "relevant_changes": [],
    }


def test_provider_relevant_change_blocks_evidence_reuse() -> None:
    facts = provider_evidence_reuse_facts(
        {
            "state": "stale",
            "reason": "relevant-repository-evidence-changed",
            "intersection": ["src/owner.py"],
        }
    )

    assert facts["usable"] is True
    assert facts["fresh"] is False
    assert facts["relevant_changes"] == ["src/owner.py"]


def test_unknown_provider_freshness_fails_closed() -> None:
    assert provider_evidence_reuse_facts({"state": "unknown"}) == {
        "usable": False,
        "reason": "unsupported-provider-freshness",
    }


def _proof(
    boundary: str, *, fresh: bool = True, direct: bool = True
) -> dict[str, object]:
    return {
        "boundary": boundary,
        "provider_reference": {
            "provider": "hashmarks",
            "evidence_identity": f"sha256:proof-{boundary}",
        },
        "freshness": {
            "state": "fresh" if fresh else "stale",
            "reason": "provider-owned",
        },
        "relationship": {
            "classification": "direct" if direct else "related",
            "evidence_identity": f"sha256:relationship-{boundary}",
        },
    }


def test_stop_rule_closes_when_every_boundary_has_fresh_direct_proof() -> None:
    result = evidence_stop_facts(
        risk_boundaries=[
            {"identity": "behavior"},
            {"identity": "typing"},
            {"identity": "format"},
        ],
        proofs=[_proof("behavior"), _proof("typing"), _proof("format")],
    )

    assert result["sufficient"] is True
    assert result["stop_acquiring_evidence"] is True
    assert result["reason"] == (
        "all-declared-risk-boundaries-have-fresh-direct-provider-proof"
    )


def test_stop_rule_rejects_related_but_indirect_test_evidence() -> None:
    result = evidence_stop_facts(
        risk_boundaries=[{"identity": "behavior"}],
        proofs=[_proof("behavior", direct=False)],
    )

    assert result["sufficient"] is False
    assert result["indirect_only_boundaries"] == ["behavior"]


def test_stop_rule_rejects_stale_direct_proof() -> None:
    result = evidence_stop_facts(
        risk_boundaries=[{"identity": "behavior"}],
        proofs=[_proof("behavior", fresh=False)],
    )

    assert result["sufficient"] is False
    assert result["stale_only_boundaries"] == ["behavior"]


def test_scope_expansion_reopens_evidence_acquisition() -> None:
    result = evidence_stop_facts(
        risk_boundaries=[{"identity": "behavior"}],
        proofs=[_proof("behavior")],
        scope_expansion_evidence=[
            {
                "provider": "hashmarks",
                "evidence_identity": "sha256:impact-delta",
                "boundary": "public-api-consumers",
            }
        ],
    )

    assert result["sufficient"] is False
    assert result["scope_expanded"] is True


def test_unbound_agent_authored_booleans_cannot_satisfy_boundary() -> None:
    result = evidence_stop_facts(
        risk_boundaries=[{"identity": "behavior"}],
        proofs=[{"boundary": "behavior", "fresh": True, "direct": True}],
    )

    assert result["sufficient"] is False
    assert result["invalid_proofs"] == ["behavior"]
    assert result["uncovered_boundaries"] == ["behavior"]


def test_proof_relationship_requires_stable_evidence_identity() -> None:
    proof = _proof("behavior")
    proof["relationship"] = {"classification": "direct"}

    result = evidence_stop_facts(
        risk_boundaries=[{"identity": "behavior"}],
        proofs=[proof],
    )

    assert result["sufficient"] is False
    assert result["invalid_proofs"] == ["behavior"]


def test_scope_expansion_cannot_be_asserted_without_provider_evidence() -> None:
    result = evidence_stop_facts(
        risk_boundaries=[{"identity": "behavior"}],
        proofs=[_proof("behavior")],
        scope_expansion_evidence=[],
    )

    assert result["sufficient"] is True
    assert result["scope_expanded"] is False


def test_malformed_scope_expansion_fails_closed() -> None:
    result = evidence_stop_facts(
        risk_boundaries=[{"identity": "behavior"}],
        proofs=[_proof("behavior")],
        scope_expansion_evidence=[
            {"provider": "hashmarks", "boundary": "new-consumer"}
        ],
    )

    assert result["sufficient"] is False
    assert result["scope_expanded"] is True
    assert result["invalid_scope_expansion_evidence"] == ["<invalid-scope-expansion>"]
