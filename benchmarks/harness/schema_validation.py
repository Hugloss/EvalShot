"""Small standard-library validator for the benchmark JSON Schema subset.

The benchmark schemas intentionally use a compact Draft 2020-12 subset so suite
validation does not depend on a network install or an ambient jsonschema package.
"""

from __future__ import annotations

import json
import re
from typing import Any


class SchemaValidationError(ValueError):
    pass


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    raise SchemaValidationError(f"unsupported schema type: {expected}")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_instance(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str = "$",
) -> None:
    if "oneOf" in schema:
        successes = 0
        errors: list[str] = []
        for branch in schema["oneOf"]:
            try:
                validate_instance(value, branch, path=path)
            except SchemaValidationError as exc:
                errors.append(str(exc))
            else:
                successes += 1
        if successes != 1:
            raise SchemaValidationError(
                f"{path}: expected exactly one oneOf branch, matched {successes}; "
                + " | ".join(errors[:3])
            )
        return

    expected = schema.get("type")
    if expected is not None:
        allowed = [expected] if isinstance(expected, str) else list(expected)
        if not any(_type_matches(value, item) for item in allowed):
            raise SchemaValidationError(
                f"{path}: expected type {allowed}, got {type(value).__name__}"
            )

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(
            f"{path}: {value!r} is not one of {schema['enum']!r}"
        )

    if isinstance(value, str):
        minimum = schema.get("minLength")
        if minimum is not None and len(value) < int(minimum):
            raise SchemaValidationError(
                f"{path}: string shorter than minLength {minimum}"
            )
        pattern = schema.get("pattern")
        if pattern is not None and re.search(str(pattern), value) is None:
            raise SchemaValidationError(
                f"{path}: string does not match pattern {pattern!r}"
            )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            raise SchemaValidationError(
                f"{path}: value {value} is below minimum {minimum}"
            )

    if isinstance(value, list):
        minimum = schema.get("minItems")
        if minimum is not None and len(value) < int(minimum):
            raise SchemaValidationError(
                f"{path}: array shorter than minItems {minimum}"
            )
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for index, item in enumerate(value):
                identity = _canonical(item)
                if identity in seen:
                    raise SchemaValidationError(
                        f"{path}[{index}]: duplicate item violates uniqueItems"
                    )
                seen.add(identity)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_instance(item, item_schema, path=f"{path}[{index}]")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaValidationError(
                    f"{path}: missing required property {key!r}"
                )
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, child in properties.items():
                if key in value and isinstance(child, dict):
                    validate_instance(value[key], child, path=f"{path}.{key}")
        additional = schema.get("additionalProperties", True)
        if additional is False and isinstance(properties, dict):
            extras = sorted(set(value) - set(properties))
            if extras:
                raise SchemaValidationError(
                    f"{path}: additional properties are not allowed: {extras}"
                )
        elif isinstance(additional, dict) and isinstance(properties, dict):
            for key in set(value) - set(properties):
                validate_instance(
                    value[key],
                    additional,
                    path=f"{path}.{key}",
                )
