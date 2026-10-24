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


class _FunctionGlobalDeclarations(ast.NodeVisitor):
    """Collect a function's global declarations without borrowing nested scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.names.update(node.names)

    # These introduce different lexical namespaces; a global statement
    # inside one must not be attributed to the enclosing function.
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass


class _Declarations(ast.NodeVisitor):
    """Observe declaration spans and same-scope binding changes, never execute code."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.function_depth = 0
        self.guard_depth = 0
        self.class_stack: list[str] = []
        self.matches: list[tuple[int, int, tuple[str, ...], tuple[int, int]]] = []
        self.guarded_matches = 0
        self.local_matches = 0
        self.binding_changes: dict[tuple[str, ...], list[tuple[int, int]]] = {}
        self.wildcard_imports: list[tuple[int, int]] = []
        self.function_globals: list[tuple[set[str], int]] = []
        self.class_globals: list[tuple[set[str], int]] = []
        self.class_global_definitions = 0
        self.possible_global_rebinding = False

    def _mark_binding(
        self, name: str, *, node: ast.AST,
        scope: tuple[str, ...] | None = None,
    ) -> None:
        if name != self.symbol:
            return
        # A global declaration in a CLASS body directs name writes into
        # the MODULE namespace. Class decorators/bases execute in the
        # parent scope; function bodies inside the class are separate.
        if self.class_globals:
            global_names, class_body_function_depth = self.class_globals[-1]
            if (
                name in global_names
                and self.function_depth == class_body_function_depth
            ):
                if self.function_depth:
                    self.possible_global_rebinding = True
                else:
                    self.binding_changes.setdefault((), []).append(
                        (node.lineno, node.col_offset)
                    )
                return
        if self.function_depth:
            # A function-level 'global name' changes the module namespace
            # when that function runs, regardless of its lexical position.
            # Only count real writes, never a bare global declaration.
            if self.function_globals:
                global_names, class_depth = self.function_globals[-1]
                if name in global_names and len(self.class_stack) == class_depth:
                    self.possible_global_rebinding = True
            return
        owner = tuple(self.class_stack) if scope is None else scope
        self.binding_changes.setdefault(owner, []).append(
            (node.lineno, node.col_offset)
        )

    def _target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._mark_binding(target.id, node=target)
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
            self._mark_binding(
                target.attr, node=target, scope=(target.value.id,)
            )

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
            self._mark_binding(
                alias.asname or alias.name.split(".")[0], node=node
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                if self.function_depth == 0 and not self.class_stack:
                    # A wildcard import may bind any public module name.
                    # Its actual contents are outside the frozen source.
                    self.wildcard_imports.append((node.lineno, node.col_offset))
            else:
                self._mark_binding(alias.asname or alias.name, node=node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Decorators, bases and metaclass keywords evaluate in the
        # enclosing scope. A walrus binding there can replace a named
        # candidate; it is not a variable in the class being defined.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        self._mark_binding(node.name, node=node)
        globals_here = _FunctionGlobalDeclarations()
        for statement in node.body:
            globals_here.visit(statement)
        self.class_stack.append(node.name)
        self.class_globals.append(
            (globals_here.names, self.function_depth)
        )
        for statement in node.body:
            self.visit(statement)
        self.class_globals.pop()
        self.class_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # A nested definition also binds its name in the enclosing function.
        # If that enclosing function declares the name global, it may
        # replace a module-level candidate on a later invocation.
        class_global_definition = (
            node.name == self.symbol
            and bool(self.class_globals)
            and self.function_depth == self.class_globals[-1][1]
            and node.name in self.class_globals[-1][0]
        )
        if class_global_definition:
            # This syntax creates a MODULE binding, not a method in the
            # class suite. It cannot count as an ordinary class owner.
            self.class_global_definitions += 1
            self._mark_binding(node.name, node=node)
        elif self.function_depth and node.name == self.symbol:
            self._mark_binding(node.name, node=node)
        # The def statement binds its own name *after* evaluation of its
        # decorators, defaults and annotations. Those expressions may assign
        # to the very same name, but their writes are superseded by this def.
        # Preserve unrelated preceding/later source mutations as before.
        scope = tuple(self.class_stack)
        own_binding = (
            node.name == self.symbol
            and self.function_depth == 0
            and not class_global_definition
        )
        prior_writes = len(self.binding_changes.get(scope, ()))
        if node.name == self.symbol and not class_global_definition:
            if self.function_depth:
                self.local_matches += 1
            elif self.guard_depth:
                self.guarded_matches += 1
            else:
                start = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                self.matches.append((
                    start, node.end_lineno, tuple(self.class_stack),
                    (node.lineno, node.col_offset),
                ))
        # Definition-time decorators, parameter defaults and annotations
        # evaluate in the enclosing lexical namespace, NOT in the later
        # function call's locals. Only the function BODY introduces a local
        # scope. Do not hide definition-time walrus bindings as locals.
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        if own_binding:
            # A binding produced by THIS definition's header is earlier in
            # execution than the def's final name assignment, despite
            # appearing later than "def name" in source-column order.
            del self.binding_changes.setdefault(scope, [])[prior_writes:]
        global_declarations = _FunctionGlobalDeclarations()
        for statement in node.body:
            global_declarations.visit(statement)
        self.function_globals.append(
            (global_declarations.names, len(self.class_stack))
        )
        self.function_depth += 1
        for statement in node.body:
            self.visit(statement)
        self.function_depth -= 1
        self.function_globals.pop()

    def _visit_guarded(self, node: ast.AST) -> None:
        # A conditional binding is still a possible override. No attempt
        # is made to prove whether or when the branch executes.
        self.guard_depth += 1
        self.generic_visit(node)
        self.guard_depth -= 1

    visit_If = _visit_guarded
    visit_While = _visit_guarded
    visit_Try = _visit_guarded
    visit_TryStar = _visit_guarded
    visit_Match = _visit_guarded

    def visit_For(self, node: ast.For) -> None:
        self._visit_iteration_target(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_iteration_target(node)

    def _visit_iteration_target(self, node: ast.For | ast.AsyncFor) -> None:
        # For-loop targets bind in the enclosing module/class scope.
        # Unlike comprehension targets, these names may replace a def.
        self.guard_depth += 1
        self._target(node.target)
        self.generic_visit(node)
        self.guard_depth -= 1

    def visit_With(self, node: ast.With) -> None:
        self._visit_with_targets(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with_targets(node)

    def _visit_with_targets(self, node: ast.With | ast.AsyncWith) -> None:
        self.guard_depth += 1
        for item in node.items:
            if item.optional_vars is not None:
                self._target(item.optional_vars)
        self.generic_visit(node)
        self.guard_depth -= 1

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # Exception aliases are local to the enclosing block and are
        # commonly deleted after handling. Either behavior can change
        # a same-name binding, so do not infer runtime availability.
        if node.name:
            self._mark_binding(node.name, node=node)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._mark_binding(node.name, node=node)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._mark_binding(node.name, node=node)
        self.generic_visit(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._mark_binding(node.rest, node=node)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Lambda defaults evaluate in the parent scope at definition time,
        # but assignments in its body are lambda-local until invocation.
        self.visit(node.args)
        # A lambda body is another local scope; it cannot declare global.
        self.function_globals.append((set(), len(self.class_stack)))
        self.function_depth += 1
        self.visit(node.body)
        self.function_depth -= 1
        self.function_globals.pop()

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
            if declarations.class_global_definitions:
                return "CANDIDATE_CLASS_GLOBAL_DEFINITION", count
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
        start, end, scope, declaration_position = declarations.matches[0]
        # A later unconditional def overwrites an earlier import/assignment
        # in the same namespace. Only mutations after the observed def
        # could change the name's final binding. Python semantics beyond
        # source order (branch truth, dynamic execution) are not inferred.
        if any(
            position > declaration_position
            for position in declarations.binding_changes.get(scope, ())
        ):
            return "CANDIDATE_BINDING_MUTATION_OBSERVED", count
        if not scope and any(
            position > declaration_position
            for position in declarations.wildcard_imports
        ):
            return "CANDIDATE_WILDCARD_IMPORT_MAY_REBIND", count
        if not scope and declarations.possible_global_rebinding:
            # Whether a function with 'global symbol' actually executes
            # after this declaration cannot be decided from static source.
            return "CANDIDATE_DEFERRED_GLOBAL_REBINDING", count
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
