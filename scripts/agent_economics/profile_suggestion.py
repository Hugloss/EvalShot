from __future__ import annotations

from typing import Any


def profile_suggestion(doctor_payload: dict[str, Any]) -> dict[str, Any]:
    suggestions = doctor_payload.get("suggestions")
    if not isinstance(suggestions, dict):
        raise ValueError("doctor artifact has no suggestions object")
    ambiguity = doctor_payload.get("ambiguity")
    if not isinstance(ambiguity, list):
        raise ValueError("doctor artifact has no ambiguity list")
    environment = doctor_payload.get("environment")
    if not isinstance(environment, dict):
        raise ValueError("doctor artifact has no environment object")
    ruff_environment = environment.get("ruff")
    if not isinstance(ruff_environment, dict):
        raise ValueError("doctor artifact has no Ruff environment evidence")

    package_roots = list(suggestions.get("source_roots") or [])
    test_roots = list(suggestions.get("tests_roots") or [])
    package_root_evidence = suggestions.get("source_root_evidence")
    if not isinstance(package_root_evidence, list):
        package_root_evidence = []
    test_root_evidence = suggestions.get("test_root_evidence")
    if not isinstance(test_root_evidence, list):
        test_root_evidence = []
    quality_roots = list(suggestions.get("quality_analysis_roots") or [])
    quality_root_evidence = suggestions.get("quality_analysis_root_evidence")
    if not isinstance(quality_root_evidence, list):
        quality_root_evidence = []
    ruff = suggestions.get("ruff")
    if not isinstance(ruff, dict):
        ruff = {}

    unresolved: list[str] = []
    if "source_root" in ambiguity:
        unresolved.append("package_roots")
    if "tests_root" in ambiguity:
        unresolved.append("test_roots")
    if not quality_roots:
        unresolved.append("quality_analysis_roots")
    if not ruff.get("limits"):
        unresolved.append("quality_debt_limits")
    if not ruff_environment.get("available"):
        unresolved.append("ruff_supply")

    return {
        "schema": {"name": "agent-economics-profile-suggestion", "version": 1},
        "status": "REVIEW_REQUIRED",
        "repository": {
            "package_roots": package_roots,
            "package_root_evidence": package_root_evidence,
            "test_roots": test_roots,
            "test_root_evidence": test_root_evidence,
        },
        "quality_debt": {
            "analysis_roots": quality_roots,
            "analysis_root_evidence": quality_root_evidence,
            "limits": dict(ruff.get("limits") or {}),
            "exclude": list(ruff.get("extend_exclude") or []),
        },
        "environment": {
            "ruff": {
                "available": bool(ruff_environment.get("available")),
                "resolved_executable": ruff_environment.get("resolved_executable"),
                "version": ruff_environment.get("version"),
            }
        },
        "unresolved": unresolved,
        "interpretation": {
            "review_required": True,
            "writes_repository_configuration": False,
            "suggestion_is_not_repository_authority": True,
            "proposed_roots_require_review_before_persistent_use": True,
            "environment_availability_is_not_persisted_authority": True,
        },
    }
