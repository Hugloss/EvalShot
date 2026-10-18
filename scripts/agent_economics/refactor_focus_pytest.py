from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .refactor_focus_analysis import AnalysisCache
from .refactor_focus_imports import (
    dynamic_call_kind,
    internal_imports_for_file,
    is_first_party_module,
    literal_dynamic_module_name_for_call,
    parse_dynamic_loaded_source_module_evidence,
    resolve_import_from_module,
)
from .refactor_focus_paths import module_path_for_file, report_path


@dataclass(frozen=True)
class PytestOwnershipEvidence:
    source_path: Path
    test_path: Path
    match_type: str
    provenance: str


@dataclass(frozen=True)
class FixtureInfo:
    name: str
    autouse: bool
    dependencies: frozenset[str]
    source_evidence: tuple[tuple[str, str], ...]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _literal_strings(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: set[str] = set()
        for item in node.elts:
            values.update(_literal_strings(item))
        return values
    return set()


def parse_pytest_plugins(path: Path, *, analysis_cache: AnalysisCache) -> set[str]:
    tree = analysis_cache.get(path).tree
    if tree is None:
        return set()
    plugins: set[str] = set()
    for node in getattr(tree, "body", []):
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins"
            for target in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "pytest_plugins"
        ):
            value = node.value
        if value is not None:
            plugins.update(_literal_strings(value))
    return {plugin.strip() for plugin in plugins if plugin.strip()}


def _fixture_decorator_info(
    decorators: list[ast.expr],
    *,
    default_name: str,
) -> tuple[str, bool] | None:
    for decorator in decorators:
        call = decorator if isinstance(decorator, ast.Call) else None
        target = call.func if call is not None else decorator
        name = _call_name(target)
        if name not in {"fixture", "pytest.fixture"}:
            continue
        fixture_name = default_name
        autouse = False
        if call is not None:
            for keyword in call.keywords:
                if (
                    keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    fixture_name = keyword.value.value
                elif keyword.arg == "autouse" and isinstance(
                    keyword.value, ast.Constant
                ):
                    autouse = keyword.value.value is True
        return fixture_name, autouse
    return None


def _global_first_party_aliases(
    *,
    path: Path,
    root: Path,
    current_package_name: str,
    package_name: str,
    analysis_cache: AnalysisCache,
) -> dict[str, set[str]]:
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

    aliases: dict[str, set[str]] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.strip()
                if not is_first_party_module(module, {package_name}):
                    continue
                local = alias.asname or module.split(".")[0]
                aliases.setdefault(local, set()).add(module)
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
                local = alias.asname or alias.name
                # Keep both possibilities. module_to_path later discards the one
                # that is not a real repository module.
                aliases.setdefault(local, set()).update({base, f"{base}.{alias.name}"})
    return aliases


def _static_modules_inside_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    path: Path,
    root: Path,
    current_package_name: str,
    package_name: str,
) -> set[str]:
    try:
        current_module_parts = module_path_for_file(
            path=path,
            root=root,
            package_name=current_package_name,
        ).split(".")
    except ValueError:
        current_module_parts = []
    modules: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            for alias in child.names:
                module = alias.name.strip()
                if is_first_party_module(module, {package_name}):
                    modules.add(module)
        elif isinstance(child, ast.ImportFrom):
            base = resolve_import_from_module(
                node=child,
                current_module_parts=current_module_parts,
                current_is_package=path.name == "__init__.py",
            )
            if base and is_first_party_module(base, {package_name}):
                modules.add(base)
                for alias in child.names:
                    if alias.name != "*":
                        modules.add(f"{base}.{alias.name}")
    return modules


def _dynamic_modules_inside_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    package_name: str,
) -> dict[str, set[str]]:
    evidence: dict[str, set[str]] = {}
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        module = literal_dynamic_module_name_for_call(child)
        if not module or not is_first_party_module(module, {package_name}):
            continue
        evidence.setdefault(module, set()).add(dynamic_call_kind(child))
    return evidence


def fixture_infos_for_file(
    *,
    path: Path,
    root: Path,
    current_package_name: str,
    package_name: str,
    analysis_cache: AnalysisCache,
) -> dict[str, FixtureInfo]:
    tree = analysis_cache.get(path).tree
    if tree is None:
        return {}
    aliases = _global_first_party_aliases(
        path=path,
        root=root,
        current_package_name=current_package_name,
        package_name=package_name,
        analysis_cache=analysis_cache,
    )
    fixtures: dict[str, FixtureInfo] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorator_info = _fixture_decorator_info(
            node.decorator_list,
            default_name=node.name,
        )
        if decorator_info is None:
            continue
        fixture_name, autouse = decorator_info
        referenced_names = {
            child.id for child in ast.walk(node) if isinstance(child, ast.Name)
        }
        source_evidence: set[tuple[str, str]] = set()
        for local in referenced_names:
            for module in aliases.get(local, set()):
                source_evidence.add((module, f"global_alias:{local}"))
        for module in _static_modules_inside_function(
            node,
            path=path,
            root=root,
            current_package_name=current_package_name,
            package_name=package_name,
        ):
            source_evidence.add((module, "fixture_local_static_import"))
        for module, kinds in _dynamic_modules_inside_function(
            node,
            package_name=package_name,
        ).items():
            source_evidence.add(
                (module, f"fixture_literal_dynamic_import:{','.join(sorted(kinds))}")
            )
        dependencies = {
            arg.arg
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            if arg.arg not in {"self", "cls", "request"}
        }
        fixtures[fixture_name] = FixtureInfo(
            name=fixture_name,
            autouse=autouse,
            dependencies=frozenset(dependencies),
            source_evidence=tuple(sorted(source_evidence)),
        )
    return fixtures


def used_fixture_names(path: Path, *, analysis_cache: AnalysisCache) -> set[str]:
    tree = analysis_cache.get(path).tree
    if tree is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test"):
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if arg.arg not in {"self", "cls"}:
                    names.add(arg.arg)
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name.endswith("usefixtures") or call_name.endswith(
                "getfixturevalue"
            ):
                for arg in node.args:
                    names.update(_literal_strings(arg))
    return names


def _ancestor_conftests(
    test_file: Path,
    *,
    conftest_files: list[Path],
) -> list[Path]:
    ancestors: list[Path] = []
    for conftest in conftest_files:
        try:
            test_file.parent.relative_to(conftest.parent)
        except ValueError:
            continue
        ancestors.append(conftest)
    return sorted(ancestors, key=lambda path: (len(path.parts), path.as_posix()))


def _resolve_fixture_modules(
    fixture_name: str,
    *,
    fixtures: dict[str, tuple[FixtureInfo, Path]],
    module_to_path: dict[str, Path],
    max_depth: int,
) -> list[tuple[str, Path, str, int]]:
    resolved: list[tuple[str, Path, str, int]] = []
    queue: list[tuple[str, int]] = [(fixture_name, 0)]
    seen: set[str] = set()
    while queue:
        current, depth = queue.pop(0)
        if current in seen or depth > max_depth:
            continue
        seen.add(current)
        owned = fixtures.get(current)
        if owned is None:
            continue
        info, defined_in = owned
        for module, reason in info.source_evidence:
            if module in module_to_path:
                resolved.append((module, defined_in, reason, depth))
        if depth >= max_depth:
            continue
        for dependency in sorted(info.dependencies):
            queue.append((dependency, depth + 1))
    return resolved


def _module_scope_source_evidence(
    *,
    path: Path,
    root: Path,
    current_package_name: str,
    package_name: str,
    analysis_cache: AnalysisCache,
) -> dict[str, set[str]]:
    """Return source modules actually loaded while importing a pytest plugin.

    Imports nested inside fixture/helper functions are deliberately excluded: loading
    a plugin module does not execute those function bodies. This prevents an unused
    plugin fixture from becoming ownership authority for every test that loads the
    plugin.
    """

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

    evidence: dict[str, set[str]] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.strip()
                if is_first_party_module(module, {package_name}):
                    evidence.setdefault(module, set()).add("module_scope_static_import")
            continue
        if isinstance(node, ast.ImportFrom):
            base = resolve_import_from_module(
                node=node,
                current_module_parts=current_module_parts,
                current_is_package=path.name == "__init__.py",
            )
            if not base or not is_first_party_module(base, {package_name}):
                continue
            evidence.setdefault(base, set()).add("module_scope_static_import")
            for alias in node.names:
                if alias.name != "*":
                    evidence.setdefault(f"{base}.{alias.name}", set()).add(
                        "module_scope_static_import"
                    )
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            module = literal_dynamic_module_name_for_call(child)
            if module and is_first_party_module(module, {package_name}):
                evidence.setdefault(module, set()).add(
                    f"module_scope_{dynamic_call_kind(child)}"
                )
    return evidence


def _loaded_pytest_plugins_for_test(
    *,
    test_file: Path,
    ancestors: list[Path],
    test_module_to_path: dict[str, Path],
    analysis_cache: AnalysisCache,
    max_depth: int,
) -> list[tuple[str, Path, Path, int]]:
    """Resolve repository-local pytest_plugins declarations within a depth bound.

    Returns ``(module, plugin_path, declared_by, depth)`` tuples.
    """

    declarations: list[tuple[str, Path]] = []
    for origin in [test_file, *ancestors]:
        for plugin in sorted(
            parse_pytest_plugins(origin, analysis_cache=analysis_cache)
        ):
            declarations.append((plugin, origin))

    loaded: list[tuple[str, Path, Path, int]] = []
    queue: list[tuple[str, Path, int]] = [
        (plugin, origin, 1) for plugin, origin in declarations
    ]
    seen: set[str] = set()
    while queue:
        module, declared_by, depth = queue.pop(0)
        if module in seen or depth > max_depth:
            continue
        seen.add(module)
        plugin_path = test_module_to_path.get(module)
        if plugin_path is None:
            continue
        loaded.append((module, plugin_path, declared_by, depth))
        if depth >= max_depth:
            continue
        for child in sorted(
            parse_pytest_plugins(plugin_path, analysis_cache=analysis_cache)
        ):
            queue.append((child, plugin_path, depth + 1))
    return loaded


def build_pytest_ownership_evidence(
    *,
    test_files: list[Path],
    all_test_python_files: list[Path],
    module_to_path: dict[str, Path],
    source_root: Path,
    tests_root: Path,
    package_name: str,
    tests_package_name: str,
    analysis_cache: AnalysisCache,
    repository_root: Path,
    pytest_max_depth: int = 2,
) -> list[PytestOwnershipEvidence]:
    conftest_files = [
        path for path in all_test_python_files if path.name == "conftest.py"
    ]
    test_module_to_path = {
        module_path_for_file(
            path=path,
            root=tests_root,
            package_name=tests_package_name,
        ): path
        for path in all_test_python_files
    }
    fixture_infos = {
        path: fixture_infos_for_file(
            path=path,
            root=tests_root,
            current_package_name=tests_package_name,
            package_name=package_name,
            analysis_cache=analysis_cache,
        )
        for path in all_test_python_files
    }
    plugin_module_scope_evidence = {
        path: _module_scope_source_evidence(
            path=path,
            root=tests_root,
            current_package_name=tests_package_name,
            package_name=package_name,
            analysis_cache=analysis_cache,
        )
        for path in all_test_python_files
    }

    evidence: list[PytestOwnershipEvidence] = []
    seen: set[tuple[Path, Path, str, str]] = set()

    def append(
        *,
        source_module: str,
        test_file: Path,
        match_type: str,
        provenance: str,
    ) -> None:
        source_path = module_to_path.get(source_module)
        if source_path is None:
            return
        key = (source_path, test_file, match_type, provenance)
        if key in seen:
            return
        seen.add(key)
        evidence.append(
            PytestOwnershipEvidence(
                source_path=source_path,
                test_path=test_file,
                match_type=match_type,
                provenance=provenance,
            )
        )

    for test_file in test_files:
        test_report = report_path(path=test_file, anchor=repository_root)
        ancestors = _ancestor_conftests(test_file, conftest_files=conftest_files)
        loaded_plugins = _loaded_pytest_plugins_for_test(
            test_file=test_file,
            ancestors=ancestors,
            test_module_to_path=test_module_to_path,
            analysis_cache=analysis_cache,
            max_depth=pytest_max_depth,
        )
        loaded_plugin_paths = {entry[1] for entry in loaded_plugins}

        # Plugin fixtures are visible, but duplicate plugin fixture names are
        # ambiguous unless a nearer conftest/test definition overrides them.
        plugin_fixture_candidates: dict[str, list[tuple[FixtureInfo, Path]]] = {}
        for _module, plugin_path, _declared_by, _depth in loaded_plugins:
            for name, info in fixture_infos.get(plugin_path, {}).items():
                plugin_fixture_candidates.setdefault(name, []).append(
                    (info, plugin_path)
                )
        visible_fixtures: dict[str, tuple[FixtureInfo, Path]] = {
            name: definitions[0]
            for name, definitions in plugin_fixture_candidates.items()
            if len(definitions) == 1
        }
        # Root-to-nearest conftest order gives nearest definitions the final word.
        for owner in [*ancestors, test_file]:
            for name, info in fixture_infos.get(owner, {}).items():
                visible_fixtures[name] = (info, owner)

        used_fixtures = used_fixture_names(test_file, analysis_cache=analysis_cache)
        active_fixtures = set(used_fixtures)
        active_fixtures.update(
            name for name, (info, _owner) in visible_fixtures.items() if info.autouse
        )
        for fixture_name in sorted(active_fixtures):
            for source_module, defined_in, reason, depth in _resolve_fixture_modules(
                fixture_name,
                fixtures=visible_fixtures,
                module_to_path=module_to_path,
                max_depth=pytest_max_depth,
            ):
                if defined_in in loaded_plugin_paths:
                    match_type = "pytest_plugin_fixture"
                    prefix = "pytest_plugin_fixture"
                elif defined_in.name == "conftest.py":
                    match_type = "conftest_fixture"
                    prefix = "pytest_fixture"
                else:
                    match_type = "pytest_fixture"
                    prefix = "pytest_fixture"
                append(
                    source_module=source_module,
                    test_file=test_file,
                    match_type=match_type,
                    provenance=(
                        f"{prefix}:test={test_report}:fixture={fixture_name}:"
                        f"defined_in={report_path(path=defined_in, anchor=repository_root)}:"
                        f"dependency_depth={depth}:source={source_module}:reason={reason}"
                    ),
                )

        # Loading a plugin executes only module-scope imports/calls. Imports inside
        # fixture bodies are represented above only when the fixture is active.
        for plugin_module, plugin_path, declared_by, depth in loaded_plugins:
            for source_module, kinds in sorted(
                plugin_module_scope_evidence.get(plugin_path, {}).items()
            ):
                append(
                    source_module=source_module,
                    test_file=test_file,
                    match_type="pytest_plugin",
                    provenance=(
                        f"pytest_plugin:test={test_report}:"
                        f"declared_by={report_path(path=declared_by, anchor=repository_root)}:"
                        f"plugin={plugin_module}:depth={depth}:source={source_module}:"
                        f"kind={','.join(sorted(kinds))}"
                    ),
                )

    return evidence
