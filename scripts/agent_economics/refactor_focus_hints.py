from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeclaredOwnershipHint:
    source_path: Path
    test_path: Path
    provenance: str


@dataclass(frozen=True)
class OwnershipHintsLoad:
    relationships: tuple[DeclaredOwnershipHint, ...]
    bytes_read: int
    content_sha256: str


class OwnershipHintsError(ValueError):
    pass


def load_declared_ownership_hints(
    *,
    hints_path: Path,
    repository_root: Path,
    source_files: set[Path],
    test_files: set[Path],
) -> OwnershipHintsLoad:
    """Load explicit source/test ownership relationships from portable JSON.

    Schema::

        {
          "version": 1,
          "relationships": [
            {
              "source": "src/pkg/foo.py",
              "test": "tests/test_foo.py",
              "reason": "optional human explanation"
            }
          ]
        }

    Paths must be repository-relative and must resolve to discovered source/test
    files. Stale or out-of-repository declarations fail closed.
    """

    try:
        raw = hints_path.read_bytes()
        text = raw.decode("utf-8")
        payload = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipHintsError(f"cannot read ownership hints: {exc}") from exc
    if not isinstance(payload, dict):
        raise OwnershipHintsError("ownership hints root must be an object")
    if payload.get("version") != 1:
        raise OwnershipHintsError("ownership hints version must be 1")
    relationships = payload.get("relationships")
    if not isinstance(relationships, list):
        raise OwnershipHintsError("ownership hints relationships must be a list")

    root = repository_root.resolve()
    hints_resolved = hints_path.resolve()
    try:
        hints_label = hints_resolved.relative_to(root).as_posix()
    except ValueError:
        hints_label = f"<external>/{hints_resolved.name}"
    results: list[DeclaredOwnershipHint] = []
    seen: set[tuple[Path, Path]] = set()
    for index, item in enumerate(relationships):
        if not isinstance(item, dict):
            raise OwnershipHintsError(f"relationship {index} must be an object")
        source_value = item.get("source")
        test_value = item.get("test")
        reason_value = item.get("reason", "repository declaration")
        if not isinstance(source_value, str) or not source_value.strip():
            raise OwnershipHintsError(
                f"relationship {index} source must be a path string"
            )
        if not isinstance(test_value, str) or not test_value.strip():
            raise OwnershipHintsError(
                f"relationship {index} test must be a path string"
            )
        if not isinstance(reason_value, str):
            raise OwnershipHintsError(f"relationship {index} reason must be a string")

        source = _resolve_repository_relative(
            root, source_value, index=index, field="source"
        )
        test = _resolve_repository_relative(root, test_value, index=index, field="test")
        if source not in source_files:
            raise OwnershipHintsError(
                f"relationship {index} source is not a discovered source file: {source_value}"
            )
        if test not in test_files:
            raise OwnershipHintsError(
                f"relationship {index} test is not a discovered test file: {test_value}"
            )
        key = (source, test)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            DeclaredOwnershipHint(
                source_path=source,
                test_path=test,
                provenance=(
                    f"declared_owner:file={hints_label}:"
                    f"relationship={index}:reason={reason_value.strip() or 'repository declaration'}"
                ),
            )
        )
    return OwnershipHintsLoad(
        relationships=tuple(results),
        bytes_read=len(raw),
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _resolve_repository_relative(
    root: Path,
    value: str,
    *,
    index: int,
    field: str,
) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise OwnershipHintsError(
            f"relationship {index} {field} must be repository-relative"
        )
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise OwnershipHintsError(
            f"relationship {index} {field} escapes repository root"
        ) from exc
    return resolved
