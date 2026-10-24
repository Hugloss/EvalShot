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


class _Declarations(ast.NodeVisitor):
    """Observe functions/methods, not local helper definitions inside functions."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.function_depth = 0
        self.guard_depth = 0
        self.matches: list[tuple[int, int]] = []
        self.guarded_matches = 0
        self.local_matches = 0

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name == self.symbol:
            if self.function_depth:
                self.local_matches += 1
            elif self.guard_depth:
                # A declaration inside an if/try/match/loop/with block
                # might not be bound on every import/execution path.
                # Observing its syntax must not qualify availability.
                self.guarded_matches += 1
            else:
                start = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                self.matches.append((start, node.end_lineno))
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def _visit_guarded(self, node: ast.AST) -> None:
        # Do not interpret conditions or execute the source. Even an
        # apparently constant predicate could hide another declaration.
        self.guard_depth += 1
        self.generic_visit(node)
        self.guard_depth -= 1

    # Python executes these bodies conditionally, or in a managed scope.
    # A syntactic declaration there is not proof of an unconditional owner.
    visit_If = _visit_guarded
    visit_For = _visit_guarded
    visit_AsyncFor = _visit_guarded
    visit_While = _visit_guarded
    visit_Try = _visit_guarded
    visit_TryStar = _visit_guarded
    visit_With = _visit_guarded
    visit_AsyncWith = _visit_guarded
    visit_Match = _visit_guarded

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


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
    declaration_spans: dict[tuple[str, str], tuple[int, int]] = {}
    if response["decision"] == "unable_to_review":
        return "UNABLE_TO_REVIEW_NO_SOURCE_CHECK", 0
    if len(locations) + len(evidence) > MAX_REFERENCES:
        raise ValueError("bounded first-pass source references exceeded")
    for location in locations:
        path, symbol = location["path"], location["symbol"]
        content = source(path)
        count += 1
        if content is None:
            return "PINNED_CANDIDATE_FILE_UNAVAILABLE", count
        # This historical cohort asks for Python symbol definitions. A
        # README or another non-Python file may exist in the source tree,
        # but this checker has no authority to establish its symbol owner.
        if not path.endswith(".py"):
            return "UNSUPPORTED_NON_PYTHON_CANDIDATE", count
        try:
            syntax = ast.parse(content.decode("utf-8"), filename=path)
            declarations = _Declarations(symbol)
            declarations.visit(syntax)
        except (SyntaxError, UnicodeError, ValueError, RecursionError):
            return "CANDIDATE_PYTHON_NOT_PARSEABLE", count
        if not declarations.matches:
            if declarations.guarded_matches:
                return "CANDIDATE_ONLY_GUARDED_DECLARATION", count
            return (
                "CANDIDATE_ONLY_LOCAL_DECLARATION"
                if declarations.local_matches else "CANDIDATE_DECLARATION_NOT_OBSERVED"
            ), count
        if len(declarations.matches) != 1:
            return "CANDIDATE_MULTIPLE_DECLARATIONS_OBSERVED", count
        if declarations.guarded_matches:
            return "CANDIDATE_GUARDED_ALTERNATIVE", count
        declaration_spans[(path, symbol)] = declarations.matches[0]

    for observation in evidence:
        content = source(observation["path"])
        count += 1
        if content is None:
            return "PINNED_EVIDENCE_FILE_UNAVAILABLE", count
        if observation["line"] > len(content.splitlines()):
            return "PINNED_EVIDENCE_LINE_ABSENT", count
    # Merely citing a different file does not source-anchor a claimed
    # definition. Require at least one real cited line *in each* proposed
    # owner file, while still allowing additional callers/tests as context.
    cited_paths = {observation["path"] for observation in evidence}
    if any(location["path"] not in cited_paths for location in locations):
        return "CANDIDATE_WITHOUT_CITED_SOURCE_EVIDENCE", count
    # A real line in the right file is still insufficient if it has no
    # relationship to the named declaration. This is only AST syntax, not a
    # semantic assessment of ownership, delegation or reviewer independence.
    for location in locations:
        path, symbol = location["path"], location["symbol"]
        start, end = declaration_spans[(path, symbol)]
        if not any(
            fact["path"] == path and start <= fact["line"] <= end
            for fact in evidence
        ):
            return "CANDIDATE_WITHOUT_DECLARATION_SPAN_CITATION", count
    return "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF", count
