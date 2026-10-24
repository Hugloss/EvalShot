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
    """Observe declaration spans and same-scope binding changes, never execute code."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.function_depth = 0
        self.guard_depth = 0
        self.class_stack: list[str] = []
        self.matches: list[tuple[int, int, tuple[str, ...]]] = []
        self.guarded_matches = 0
        self.local_matches = 0
        self.binding_changes: set[tuple[str, ...]] = set()

    def _mark_binding(self, name: str, *, scope: tuple[str, ...] | None = None) -> None:
        if self.function_depth == 0 and name == self.symbol:
            self.binding_changes.add(
                tuple(self.class_stack) if scope is None else scope
            )

    def _target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._mark_binding(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._target(item)
        elif (
            isinstance(target, ast.Attribute)
            and self.function_depth == 0
            and not self.class_stack
            and isinstance(target.value, ast.Name)
        ):
            # Module-level ClassName.method = replacement is a possible
            # monkeypatch of a named class method, but not of every other
            # class with the same method name.
            self._mark_binding(target.attr, scope=(target.value.id,))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._target(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._target(node.target)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._target(node.target)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._target(target)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._mark_binding(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self._mark_binding(alias.asname or alias.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._mark_binding(node.name)
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name == self.symbol:
            if self.function_depth:
                self.local_matches += 1
            elif self.guard_depth:
                self.guarded_matches += 1
            else:
                start = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                self.matches.append((start, node.end_lineno, tuple(self.class_stack)))
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def _visit_guarded(self, node: ast.AST) -> None:
        # A conditional binding is still a possible override. No attempt
        # is made to prove whether or when the branch executes.
        self.guard_depth += 1
        self.generic_visit(node)
        self.guard_depth -= 1

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
        start, end, scope = declarations.matches[0]
        if scope in declarations.binding_changes:
            return "CANDIDATE_BINDING_MUTATION_OBSERVED", count
        declaration_spans[(path, symbol)] = (start, end)

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
