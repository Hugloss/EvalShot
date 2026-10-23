"""Single, portable source-reference existence check for provisional reviews.

Operates on bounded source bytes provided by the *caller*. Git caller owns
historical commit/tree authentication; standalone kit caller owns its
manifest-verified snapshot. Existence never proves semantics or independence.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from typing import Any

MAX_REFERENCES = 60
MAX_SOURCE_BYTES = 32 * 1024 * 1024


def check_references(
    response: Mapping[str, Any],
    source_bytes: Callable[[str], bytes | None],
) -> tuple[str, int]:
    """Return an answer-free status plus number of inspected references."""
    cache: dict[str, bytes | None] = {}
    count = 0

    def source(path: str) -> bytes | None:
        if path not in cache:
            content = source_bytes(path)
            cache[path] = (
                content if isinstance(content, bytes) and
                len(content) <= MAX_SOURCE_BYTES else None
            )
        return cache[path]

    locations, evidence = response["candidate_locations"], response["source_evidence"]
    if len(locations) + len(evidence) > MAX_REFERENCES:
        raise ValueError("bounded first-pass source references exceeded")
    for location in locations:
        path, symbol = location["path"], location["symbol"]
        content = source(path)
        count += 1
        if content is None:
            return "PINNED_CANDIDATE_FILE_UNAVAILABLE", count
        if path.endswith(".py"):
            try:
                syntax = ast.parse(content.decode("utf-8"), filename=path)
            except (SyntaxError, UnicodeError, ValueError, RecursionError):
                return "CANDIDATE_PYTHON_NOT_PARSEABLE", count
            if not any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == symbol
                for node in ast.walk(syntax)
            ):
                return "CANDIDATE_DECLARATION_NOT_OBSERVED", count

    for observation in evidence:
        content = source(observation["path"])
        count += 1
        if content is None:
            return "PINNED_EVIDENCE_FILE_UNAVAILABLE", count
        if observation["line"] > len(content.splitlines()):
            return "PINNED_EVIDENCE_LINE_ABSENT", count
    return "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF", count
