import ast
from collections import defaultdict
from pathlib import Path

from .refactor_focus_analysis import AnalysisCache
from .refactor_focus_paths import module_path_for_file, source_module_path


def is_first_party_module(module: str, package_names: set[str]) -> bool:
    return any(
        module == name or module.startswith(f"{name}.") for name in package_names
    )


def parse_internal_imported_modules(
    *,
    path: Path,
    root: Path,
    current_package_name: str,
    package_names: set[str],
    analysis_cache: AnalysisCache,
) -> set[str]:
    tree = analysis_cache.get(path).tree
    if tree is None:
        return set()

    try:
        current_module_parts = module_path_for_file(
            path=path,
            root=root,
            package_name=current_package_name,
        ).split(".")
    except ValueError:
        current_module_parts = []

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.strip()
                if is_first_party_module(name, package_names):
                    modules.add(name)
        elif isinstance(node, ast.ImportFrom):
            base_module = resolve_import_from_module(
                node=node,
                current_module_parts=current_module_parts,
                current_is_package=path.name == "__init__.py",
            )
            if not base_module or not is_first_party_module(base_module, package_names):
                continue
            modules.add(base_module)
            for alias in node.names:
                name = alias.name.strip()
                if name and name != "*":
                    modules.add(f"{base_module}.{name}")
    return modules


def resolve_import_from_module(
    *,
    node: ast.ImportFrom,
    current_module_parts: list[str],
    current_is_package: bool,
) -> str:
    module = (node.module or "").strip()
    level = getattr(node, "level", 0)
    if level <= 0:
        return module
    if not current_module_parts:
        return module

    package_parts = (
        current_module_parts if current_is_package else current_module_parts[:-1]
    )
    ascend = level - 1
    if ascend > len(package_parts):
        return ""
    base_parts = (
        package_parts[: len(package_parts) - ascend] if ascend else package_parts
    )
    if module:
        return ".".join((*base_parts, module))
    return ".".join(base_parts)


def build_import_index(
    *,
    files: list[Path],
    root: Path,
    current_package_name: str,
    package_names: set[str],
    analysis_cache: AnalysisCache,
) -> dict[str, set[Path]]:
    index: dict[str, set[Path]] = defaultdict(set)
    for file_path in files:
        for module in parse_internal_imported_modules(
            path=file_path,
            root=root,
            current_package_name=current_package_name,
            package_names=package_names,
            analysis_cache=analysis_cache,
        ):
            index[module].add(file_path)
    return dict(index)


def internal_imports_for_file(
    *,
    path: Path,
    root: Path,
    current_package_name: str,
    package_names: set[str],
    analysis_cache: AnalysisCache,
) -> set[str]:
    return parse_internal_imported_modules(
        path=path,
        root=root,
        current_package_name=current_package_name,
        package_names=package_names,
        analysis_cache=analysis_cache,
    )


def parse_dynamic_loaded_source_module_evidence(
    *,
    path: Path,
    source_root: Path,
    package_name: str,
    analysis_cache: AnalysisCache,
) -> dict[str, set[str]]:
    """Return literal dynamic source-module loads grouped by provenance kind.

    Only statically recoverable literal relationships are admitted. Runtime-built
    module names remain unsupported/unknown instead of being guessed.
    """

    tree = analysis_cache.get(path).tree
    if tree is None:
        return {}

    env: dict[str, Path] = {}
    evidence: dict[str, set[str]] = defaultdict(set)
    current_file = path.resolve()

    for node in getattr(tree, "body", []):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            evaluated = evaluate_path_expression(
                node.value,
                current_file=current_file,
                env=env,
            )
            if evaluated is not None:
                env[node.targets[0].id] = evaluated

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if is_spec_from_file_location_call(node) and len(node.args) >= 2:
            loaded_path = evaluate_path_expression(
                node.args[1],
                current_file=current_file,
                env=env,
            )
            if loaded_path is not None:
                try:
                    module = source_module_path(
                        source_path=loaded_path.resolve(),
                        source_root=source_root.resolve(),
                        package_name=package_name,
                    )
                except ValueError:
                    pass
                else:
                    evidence[module].add("spec_from_file_location")
            continue

        module = literal_dynamic_module_name_for_call(node)
        if module and is_first_party_module(module, {package_name}):
            evidence[module].add(dynamic_call_kind(node))

    return dict(evidence)


def parse_dynamic_loaded_source_modules(
    *,
    path: Path,
    source_root: Path,
    package_name: str,
    analysis_cache: AnalysisCache,
) -> set[str]:
    return set(
        parse_dynamic_loaded_source_module_evidence(
            path=path,
            source_root=source_root,
            package_name=package_name,
            analysis_cache=analysis_cache,
        )
    )


def literal_dynamic_module_name_for_call(node: ast.Call) -> str | None:
    if not node.args:
        return None
    if not isinstance(node.args[0], ast.Constant) or not isinstance(
        node.args[0].value, str
    ):
        return None
    value = node.args[0].value.strip()
    if not value or value.startswith("."):
        return None
    call_kind = dynamic_call_kind(node)
    if call_kind not in {"importlib.import_module", "import_module", "__import__"}:
        return None
    return value


def dynamic_call_kind(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        prefix = attribute_name(func.value)
        return f"{prefix}.{func.attr}" if prefix else func.attr
    return "unknown"


def attribute_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = attribute_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def is_spec_from_file_location_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "spec_from_file_location"


def evaluate_path_expression(
    node: ast.AST,
    *,
    current_file: Path,
    env: dict[str, Path],
) -> Path | None:
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return current_file
        return env.get(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return Path(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = evaluate_path_expression(node.left, current_file=current_file, env=env)
        right = evaluate_path_expression(node.right, current_file=current_file, env=env)
        if left is None or right is None:
            return None
        return left / right
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        value = evaluate_path_expression(node.value, current_file=current_file, env=env)
        if value is None:
            return None
        return value.parent
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "Path"
            and len(node.args) == 1
        ):
            return evaluate_path_expression(
                node.args[0],
                current_file=current_file,
                env=env,
            )
        if isinstance(node.func, ast.Attribute) and node.func.attr == "resolve":
            value = evaluate_path_expression(
                node.func.value,
                current_file=current_file,
                env=env,
            )
            if value is None:
                return None
            return value.resolve()
    return None
