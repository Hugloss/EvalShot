from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .refactor_focus_analysis import AnalysisCache
from .refactor_focus_imports import is_first_party_module, resolve_import_from_module
from .refactor_focus_paths import module_path_for_file, report_path


@dataclass(frozen=True)
class InheritedMethodOwnershipEvidence:
    source_path: Path
    test_path: Path
    match_type: str
    provenance: str


@dataclass(frozen=True)
class _ClassInfo:
    key: str
    path: Path
    module: str
    node: ast.ClassDef
    bases: tuple[str, ...]


def _lazy_getattr_export_name(node: ast.AST, argument: str) -> str | None:
    if (
        not isinstance(node, ast.Compare)
        or len(node.ops) != 1
        or len(node.comparators) != 1
    ):
        return None
    if not isinstance(node.ops[0], ast.Eq):
        return None
    left, right = node.left, node.comparators[0]
    if isinstance(left, ast.Name) and left.id == argument:
        value = right
    elif isinstance(right, ast.Name) and right.id == argument:
        value = left
    else:
        return None
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _lazy_getattr_aliases(
    *,
    tree: ast.AST,
    current_module_parts: list[str],
    current_is_package: bool,
    package_name: str,
) -> dict[str, str]:
    """Recover simple module-level __getattr__ lazy re-exports.

    Only a literal name comparison, a first-party import in that branch, and a
    return of that exact imported local are admitted. Dynamic or ambiguous
    export logic remains unresolved.
    """

    exports: dict[str, str] = {}
    for function in getattr(tree, "body", []):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if function.name != "__getattr__":
            continue
        positional = [*function.args.posonlyargs, *function.args.args]
        if len(positional) != 1:
            continue
        argument = positional[0].arg
        for branch in function.body:
            if not isinstance(branch, ast.If):
                continue
            export_name = _lazy_getattr_export_name(branch.test, argument)
            if not export_name:
                continue
            imported: dict[str, str] = {}
            returned: set[str] = set()
            for statement in branch.body:
                if isinstance(statement, ast.ImportFrom):
                    base = resolve_import_from_module(
                        node=statement,
                        current_module_parts=current_module_parts,
                        current_is_package=current_is_package,
                    )
                    if not base or not is_first_party_module(base, {package_name}):
                        continue
                    for alias in statement.names:
                        if alias.name == "*":
                            continue
                        imported[alias.asname or alias.name] = f"{base}.{alias.name}"
                elif isinstance(statement, ast.Import):
                    for alias in statement.names:
                        target = alias.name.strip()
                        if not is_first_party_module(target, {package_name}):
                            continue
                        imported[alias.asname or target.split(".")[0]] = target
                elif isinstance(statement, ast.Return) and isinstance(
                    statement.value, ast.Name
                ):
                    returned.add(statement.value.id)
            targets = {imported[name] for name in returned if name in imported}
            if len(targets) == 1:
                exports[export_name] = next(iter(targets))
    return exports


def _module_aliases(
    *,
    path: Path,
    root: Path,
    current_package_name: str,
    package_name: str,
    analysis_cache: AnalysisCache,
) -> dict[str, str]:
    tree = analysis_cache.get(path).tree
    if tree is None:
        return {}
    try:
        current_module_parts = module_path_for_file(
            path=path,
            root=root,
            package_name=current_package_name,
        ).split(".")
    except ValueError:
        current_module_parts = []

    aliases: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name.strip()
                if not is_first_party_module(target, {package_name}):
                    continue
                local = alias.asname or target.split(".")[0]
                aliases[local] = target if alias.asname else target.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = resolve_import_from_module(
                node=node,
                current_module_parts=current_module_parts,
                current_is_package=path.name == "__init__.py",
            )
            if not base or not is_first_party_module(base, {package_name}):
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{base}.{alias.name}"
    for local, target in _lazy_getattr_aliases(
        tree=tree,
        current_module_parts=current_module_parts,
        current_is_package=path.name == "__init__.py",
        package_name=package_name,
    ).items():
        aliases.setdefault(local, target)
    return aliases


def _expr_symbol(
    node: ast.AST,
    *,
    module: str,
    aliases: dict[str, str],
    local_classes: set[str],
) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in local_classes:
            return f"{module}.{node.id}"
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        prefix = _expr_symbol(
            node.value,
            module=module,
            aliases=aliases,
            local_classes=local_classes,
        )
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _resolve_class_symbol(
    symbol: str | None,
    *,
    classes: dict[str, _ClassInfo],
    aliases_by_module: dict[str, dict[str, str]],
    module_paths: dict[str, Path],
    seen: frozenset[str] = frozenset(),
) -> str | None:
    if not symbol or symbol in seen:
        return None
    if symbol in classes:
        return symbol
    parts = symbol.split(".")
    for index in range(len(parts) - 1, 0, -1):
        module = ".".join(parts[:index])
        tail = parts[index:]
        if module not in module_paths or not tail:
            continue
        target = aliases_by_module.get(module, {}).get(tail[0])
        if target is None:
            continue
        candidate = ".".join((target, *tail[1:]))
        resolved = _resolve_class_symbol(
            candidate,
            classes=classes,
            aliases_by_module=aliases_by_module,
            module_paths=module_paths,
            seen=seen | {symbol},
        )
        if resolved is not None:
            return resolved
    return None


def _class_index(
    *,
    source_files: list[Path],
    source_root: Path,
    package_name: str,
    analysis_cache: AnalysisCache,
) -> tuple[dict[str, _ClassInfo], dict[str, dict[str, str]], dict[str, Path]]:
    module_by_path = {
        path: module_path_for_file(
            path=path,
            root=source_root,
            package_name=package_name,
        )
        for path in source_files
    }
    module_paths = {module: path for path, module in module_by_path.items()}
    aliases_by_module = {
        module: _module_aliases(
            path=path,
            root=source_root,
            current_package_name=package_name,
            package_name=package_name,
            analysis_cache=analysis_cache,
        )
        for path, module in module_by_path.items()
    }

    raw: dict[str, tuple[Path, str, ast.ClassDef, tuple[str, ...]]] = {}
    for path, module in module_by_path.items():
        tree = analysis_cache.get(path).tree
        if tree is None:
            continue
        local_classes = {
            node.name
            for node in getattr(tree, "body", [])
            if isinstance(node, ast.ClassDef)
        }
        aliases = aliases_by_module[module]
        for node in getattr(tree, "body", []):
            if not isinstance(node, ast.ClassDef):
                continue
            raw_bases = tuple(
                symbol
                for base in node.bases
                if (
                    symbol := _expr_symbol(
                        base,
                        module=module,
                        aliases=aliases,
                        local_classes=local_classes,
                    )
                )
                is not None
            )
            raw[f"{module}.{node.name}"] = (path, module, node, raw_bases)

    provisional = {
        key: _ClassInfo(key, path, module, node, ())
        for key, (path, module, node, _) in raw.items()
    }
    classes: dict[str, _ClassInfo] = {}
    for key, (path, module, node, raw_bases) in raw.items():
        resolved_bases = tuple(
            resolved
            for symbol in raw_bases
            if (
                resolved := _resolve_class_symbol(
                    symbol,
                    classes=provisional,
                    aliases_by_module=aliases_by_module,
                    module_paths=module_paths,
                )
            )
            is not None
        )
        classes[key] = _ClassInfo(key, path, module, node, resolved_bases)
    return classes, aliases_by_module, module_paths


def _method_node(
    info: _ClassInfo, method: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in info.node.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method
        ):
            return node
    return None


def _method_owner(
    class_key: str,
    method: str,
    *,
    classes: dict[str, _ClassInfo],
    seen: frozenset[str] = frozenset(),
) -> str | None:
    if class_key in seen:
        return None
    info = classes.get(class_key)
    if info is None:
        return None
    if _method_node(info, method) is not None:
        return class_key

    owners = {
        owner
        for base in info.bases
        if (
            owner := _method_owner(
                base,
                method,
                classes=classes,
                seen=seen | {class_key},
            )
        )
        is not None
    }
    return next(iter(owners)) if len(owners) == 1 else None


def _context_manager_returns_self(
    class_key: str,
    *,
    classes: dict[str, _ClassInfo],
) -> bool:
    owner = _method_owner(class_key, "__enter__", classes=classes)
    if owner is None:
        return False
    method = _method_node(classes[owner], "__enter__")
    if method is None:
        return False
    returns = [node for node in ast.walk(method) if isinstance(node, ast.Return)]
    return bool(returns) and all(
        isinstance(node.value, ast.Name) and node.value.id == "self" for node in returns
    )


def _test_import_aliases(
    *,
    path: Path,
    tests_root: Path,
    tests_package_name: str,
    package_name: str,
    analysis_cache: AnalysisCache,
) -> dict[str, str]:
    return _module_aliases(
        path=path,
        root=tests_root,
        current_package_name=tests_package_name,
        package_name=package_name,
        analysis_cache=analysis_cache,
    )


def _constructor_class(
    node: ast.AST,
    *,
    aliases: dict[str, str],
    classes: dict[str, _ClassInfo],
    aliases_by_module: dict[str, dict[str, str]],
    module_paths: dict[str, Path],
) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "__new__"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "object"
        and node.args
    ):
        symbol = _expr_symbol(
            node.args[0],
            module="",
            aliases=aliases,
            local_classes=set(),
        )
    else:
        symbol = _expr_symbol(
            node.func,
            module="",
            aliases=aliases,
            local_classes=set(),
        )
    return _resolve_class_symbol(
        symbol,
        classes=classes,
        aliases_by_module=aliases_by_module,
        module_paths=module_paths,
    )


def _scope_instance_bindings(
    scope: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    aliases: dict[str, str],
    classes: dict[str, _ClassInfo],
    aliases_by_module: dict[str, dict[str, str]],
    module_paths: dict[str, Path],
) -> dict[str, str]:
    candidates: dict[str, set[str | None]] = {}

    def record(name: str, class_key: str | None) -> None:
        candidates.setdefault(name, set()).add(class_key)

    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            class_key = _constructor_class(
                node.value,
                aliases=aliases,
                classes=classes,
                aliases_by_module=aliases_by_module,
                module_paths=module_paths,
            )
            for target in node.targets:
                if isinstance(target, ast.Name):
                    record(target.id, class_key)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            class_key = (
                _constructor_class(
                    node.value,
                    aliases=aliases,
                    classes=classes,
                    aliases_by_module=aliases_by_module,
                    module_paths=module_paths,
                )
                if node.value is not None
                else None
            )
            record(node.target.id, class_key)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if not isinstance(item.optional_vars, ast.Name):
                    continue
                class_key = _constructor_class(
                    item.context_expr,
                    aliases=aliases,
                    classes=classes,
                    aliases_by_module=aliases_by_module,
                    module_paths=module_paths,
                )
                if class_key is None or not _context_manager_returns_self(
                    class_key, classes=classes
                ):
                    record(item.optional_vars.id, None)
                else:
                    record(item.optional_vars.id, class_key)

    return {
        name: next(iter(values))
        for name, values in candidates.items()
        if len(values) == 1 and None not in values
    }


def _receiver_class(
    node: ast.AST,
    *,
    bindings: dict[str, str],
    aliases: dict[str, str],
    classes: dict[str, _ClassInfo],
    aliases_by_module: dict[str, dict[str, str]],
    module_paths: dict[str, Path],
) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    return _constructor_class(
        node,
        aliases=aliases,
        classes=classes,
        aliases_by_module=aliases_by_module,
        module_paths=module_paths,
    )


def build_inherited_method_ownership_evidence(
    *,
    source_files: list[Path],
    test_files: list[Path],
    source_root: Path,
    tests_root: Path,
    package_name: str,
    tests_package_name: str,
    analysis_cache: AnalysisCache,
    repository_root: Path,
) -> list[InheritedMethodOwnershipEvidence]:
    """Recover exact test ownership for methods supplied through source-class inheritance.

    A relationship is confirmed only when the test's imported constructor resolves to
    one repository class, the receiver resolves to that class, and the called method
    has exactly one static owner in the repository inheritance graph.
    """

    classes, aliases_by_module, module_paths = _class_index(
        source_files=source_files,
        source_root=source_root,
        package_name=package_name,
        analysis_cache=analysis_cache,
    )
    evidence: dict[tuple[Path, Path], set[str]] = {}

    for test_file in test_files:
        tree = analysis_cache.get(test_file).tree
        if tree is None:
            continue
        aliases = _test_import_aliases(
            path=test_file,
            tests_root=tests_root,
            tests_package_name=tests_package_name,
            package_name=package_name,
            analysis_cache=analysis_cache,
        )
        test_label = report_path(path=test_file, anchor=repository_root)
        scopes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test")
        ]
        for scope in scopes:
            bindings = _scope_instance_bindings(
                scope,
                aliases=aliases,
                classes=classes,
                aliases_by_module=aliases_by_module,
                module_paths=module_paths,
            )
            for call in ast.walk(scope):
                if not isinstance(call, ast.Call) or not isinstance(
                    call.func, ast.Attribute
                ):
                    continue
                receiver = _receiver_class(
                    call.func.value,
                    bindings=bindings,
                    aliases=aliases,
                    classes=classes,
                    aliases_by_module=aliases_by_module,
                    module_paths=module_paths,
                )
                if receiver is None:
                    continue
                owner = _method_owner(receiver, call.func.attr, classes=classes)
                if owner is None or owner == receiver:
                    continue
                owner_info = classes[owner]
                key = (owner_info.path, test_file)
                evidence.setdefault(key, set()).add(
                    "inherited_method_call:"
                    f"test={test_label}:"
                    f"facade={receiver}:"
                    f"member={call.func.attr}:"
                    f"owner={owner}"
                )

    return [
        InheritedMethodOwnershipEvidence(
            source_path=source_path,
            test_path=test_path,
            match_type="inherited_method_call",
            provenance=" | ".join(sorted(provenance)),
        )
        for (source_path, test_path), provenance in sorted(
            evidence.items(),
            key=lambda item: (item[0][0].as_posix(), item[0][1].as_posix()),
        )
    ]
