from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_NAME = "agent-economics-probe"
SCHEMA_VERSION = "1.0"

COMMON_TOP_LEVEL_KEYS = (
    "schema",
    "tool",
    "generated_at",
    "repository",
    "configuration",
    "evidence",
    "derived",
    "interpretation",
    "uncertainty",
    "warnings",
    "candidates",
    "required_next_evidence",
    "deferred_evidence",
    "verification_suggestions",
    "economics",
)

COMMON_CANDIDATE_KEYS = (
    "target",
    "facts",
    "evidence",
    "derived",
    "interpretation",
    "recommendations",
    "uncertainty",
    "required_next_evidence",
    "verification_suggestions",
)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_identity(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def analyzed_input_identity(entries: Sequence[Mapping[str, str]]) -> str:
    normalized = sorted(
        (
            {"path": str(item["path"]), "sha256": str(item["sha256"])}
            for item in entries
        ),
        key=lambda item: item["path"],
    )
    return sha256_identity(normalized)


def configuration_identity(values: Mapping[str, object]) -> str:
    return sha256_identity(dict(values))


def build_probe_contract(
    *,
    tool_name: str,
    tool_version: str,
    generated_at: str,
    repository: Mapping[str, object],
    configuration_values: Mapping[str, object],
    evidence: Mapping[str, object],
    derived: Mapping[str, object],
    interpretation: Mapping[str, object],
    uncertainty: Sequence[Mapping[str, object]],
    warnings: Sequence[Mapping[str, object]],
    candidates: Sequence[Mapping[str, object]],
    required_next_evidence: Sequence[Mapping[str, object]],
    deferred_evidence: Sequence[Mapping[str, object]],
    verification_suggestions: Sequence[Mapping[str, object]],
    economics: Mapping[str, object],
) -> dict[str, object]:
    config_values = dict(configuration_values)
    return {
        "schema": {"name": SCHEMA_NAME, "version": SCHEMA_VERSION},
        "tool": {"name": tool_name, "version": tool_version},
        "generated_at": generated_at,
        "repository": dict(repository),
        "configuration": {
            "identity": configuration_identity(config_values),
            "values": config_values,
        },
        "evidence": dict(evidence),
        "derived": dict(derived),
        "interpretation": dict(interpretation),
        "uncertainty": [dict(item) for item in uncertainty],
        "warnings": [dict(item) for item in warnings],
        "candidates": [dict(item) for item in candidates],
        "required_next_evidence": [dict(item) for item in required_next_evidence],
        "deferred_evidence": [dict(item) for item in deferred_evidence],
        "verification_suggestions": [dict(item) for item in verification_suggestions],
        "economics": dict(economics),
    }


def validate_probe_contract(payload: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["contract root must be an object"]

    for key in COMMON_TOP_LEVEL_KEYS:
        if key not in payload:
            errors.append(f"missing top-level field: {key}")

    schema = payload.get("schema")
    if not isinstance(schema, dict):
        errors.append("schema must be an object")
    else:
        if schema.get("name") != SCHEMA_NAME:
            errors.append(f"schema.name must be {SCHEMA_NAME!r}")
        if schema.get("version") != SCHEMA_VERSION:
            errors.append(f"schema.version must be {SCHEMA_VERSION!r}")

    tool = payload.get("tool")
    if (
        not isinstance(tool, dict)
        or not isinstance(tool.get("name"), str)
        or not isinstance(tool.get("version"), str)
    ):
        errors.append("tool must contain string name and version")

    repository = payload.get("repository")
    if not isinstance(repository, dict):
        errors.append("repository must be an object")
    else:
        identity = repository.get("identity")
        if not isinstance(identity, str) or not identity.startswith("sha256:"):
            errors.append("repository.identity must be a sha256 identity")
        if repository.get("root") != ".":
            errors.append("repository.root must be portable '.'")

    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        errors.append("configuration must be an object")
    else:
        values = configuration.get("values")
        identity = configuration.get("identity")
        if not isinstance(values, dict):
            errors.append("configuration.values must be an object")
        elif identity != configuration_identity(values):
            errors.append("configuration.identity does not match configuration.values")

    object_fields = ("evidence", "derived", "interpretation", "economics")
    for key in object_fields:
        if not isinstance(payload.get(key), dict):
            errors.append(f"{key} must be an object")

    list_fields = (
        "uncertainty",
        "warnings",
        "candidates",
        "required_next_evidence",
        "deferred_evidence",
        "verification_suggestions",
    )
    for key in list_fields:
        if not isinstance(payload.get(key), list):
            errors.append(f"{key} must be a list")

    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                errors.append(f"candidate {index} must be an object")
                continue
            for key in COMMON_CANDIDATE_KEYS:
                if key not in candidate:
                    errors.append(f"candidate {index} missing field: {key}")
            if not isinstance(candidate.get("target"), str):
                errors.append(f"candidate {index} target must be a string")
            for key in (
                "facts",
                "evidence",
                "derived",
                "interpretation",
                "recommendations",
            ):
                if not isinstance(candidate.get(key), dict):
                    errors.append(f"candidate {index} {key} must be an object")
            for key in (
                "uncertainty",
                "required_next_evidence",
                "verification_suggestions",
            ):
                if not isinstance(candidate.get(key), list):
                    errors.append(f"candidate {index} {key} must be a list")
            facts = candidate.get("facts")
            if isinstance(facts, dict):
                forbidden = {
                    "risk_score",
                    "recommended_test_action",
                    "recommended_strategy",
                    "test_action",
                    "strategy",
                }
                leaked = sorted(forbidden & set(facts))
                if leaked:
                    errors.append(
                        f"candidate {index} facts contain recommendation/interpretation fields: {leaked}"
                    )

    return errors
