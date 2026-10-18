from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .refactor_focus_p2_qualification import qualify as qualify_p2
from .refactor_focus_qualification import contract_candidate_as_legacy_row
from .refactor_focus_workflow import refactor_focus_audit

Expectation = Literal["confirmed", "not_confirmed"]


@dataclass(frozen=True)
class P3Case:
    name: str
    source_path: str
    test_path: str
    expectation: Expectation
    match_type: str | None = None
    provenance_contains: str | None = None


CASES = (
    P3Case(
        "explicit_conftest_fixture",
        "src/samplepkg/fixture_target.py",
        "tests/test_fixture_use.py",
        "confirmed",
        "conftest_fixture",
        "pytest_fixture:",
    ),
    P3Case(
        "autouse_conftest_fixture",
        "src/samplepkg/autouse_target.py",
        "tests/test_autouse_behavior.py",
        "confirmed",
        "conftest_fixture",
        "autouse_target",
    ),
    P3Case(
        "fixture_dependency_chain",
        "src/samplepkg/fixture_dependency_target.py",
        "tests/test_fixture_dependency.py",
        "confirmed",
        "conftest_fixture",
        "dependency_depth=1",
    ),
    P3Case(
        "request_getfixturevalue",
        "src/samplepkg/fixture_target.py",
        "tests/test_getfixturevalue.py",
        "confirmed",
        "conftest_fixture",
        "fixture=lookup_fixture",
    ),
    P3Case(
        "unused_fixture_does_not_own_test",
        "src/samplepkg/unused_fixture_target.py",
        "tests/test_unused_fixture.py",
        "not_confirmed",
    ),
    P3Case(
        "pytest_plugin_direct",
        "src/samplepkg/plugin_target.py",
        "tests/test_plugin_behavior.py",
        "confirmed",
        "pytest_plugin",
        "plugin=checks.plugins.owner",
    ),
    P3Case(
        "pytest_plugin_nested",
        "src/samplepkg/plugin_nested_target.py",
        "tests/test_plugin_behavior.py",
        "confirmed",
        "pytest_plugin",
        "depth=2",
    ),
    P3Case(
        "pytest_plugin_depth_bound",
        "src/samplepkg/plugin_too_deep_target.py",
        "tests/test_plugin_behavior.py",
        "not_confirmed",
    ),
    P3Case(
        "pytest_plugin_fixture_used",
        "src/samplepkg/plugin_fixture_target.py",
        "tests/test_plugin_fixture_use.py",
        "confirmed",
        "pytest_plugin_fixture",
        "fixture=plugin_owned",
    ),
    P3Case(
        "pytest_plugin_fixture_unused",
        "src/samplepkg/plugin_unused_fixture_target.py",
        "tests/test_plugin_fixture_unused.py",
        "not_confirmed",
    ),
    P3Case(
        "helper_to_helper_chain",
        "src/samplepkg/helper_chain_target.py",
        "tests/test_helper_chain.py",
        "confirmed",
        "support_loader",
        "depth=2",
    ),
    P3Case(
        "helper_depth_bound",
        "src/samplepkg/helper_too_deep_target.py",
        "tests/test_helper_too_deep.py",
        "not_confirmed",
    ),
    P3Case(
        "literal_importlib",
        "src/samplepkg/dynamic_target.py",
        "tests/test_dynamic_import.py",
        "confirmed",
        "dynamic_import_literal",
        "importlib.import_module",
    ),
    P3Case(
        "literal_dunder_import",
        "src/samplepkg/dunder_target.py",
        "tests/test_dunder_import.py",
        "confirmed",
        "dynamic_import_literal",
        "__import__",
    ),
    P3Case(
        "computed_dynamic_name_unknown",
        "src/samplepkg/dynamic_computed_target.py",
        "tests/test_dynamic_computed.py",
        "not_confirmed",
    ),
    P3Case(
        "declared_repository_owner",
        "src/samplepkg/declared_target.py",
        "tests/test_declared_behavior.py",
        "confirmed",
        "declared_owner",
        "reason=generated contract owner",
    ),
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _oversized(value: int) -> str:
    lines = [f"VALUE = {value}"]
    while len(lines) < 25:
        lines.append(f"# p3 qualification padding {len(lines) + 1}")
    return "\n".join(lines) + "\n"


def materialize_p3_corpus(root: Path) -> Path:
    _write(root / "src/samplepkg/__init__.py", "# package\n")
    source_names = [
        "fixture_target",
        "autouse_target",
        "fixture_dependency_target",
        "unused_fixture_target",
        "plugin_target",
        "plugin_nested_target",
        "plugin_too_deep_target",
        "plugin_fixture_target",
        "plugin_unused_fixture_target",
        "helper_chain_target",
        "helper_too_deep_target",
        "dynamic_target",
        "dunder_target",
        "dynamic_computed_target",
        "declared_target",
    ]
    for index, name in enumerate(source_names, start=1):
        _write(root / f"src/samplepkg/{name}.py", _oversized(index))

    _write(
        root / "tests/conftest.py",
        "\n".join(
            [
                "import pytest",
                "import samplepkg.fixture_target as fixture_target",
                "import samplepkg.autouse_target as autouse_target",
                "import samplepkg.fixture_dependency_target as fixture_dependency_target",
                "import samplepkg.unused_fixture_target as unused_fixture_target",
                'pytest_plugins = ["checks.plugins.owner"]',
                "",
                "@pytest.fixture",
                "def owned_fixture():",
                "    return fixture_target.VALUE",
                "",
                "@pytest.fixture",
                "def lookup_fixture():",
                "    return fixture_target.VALUE",
                "",
                "@pytest.fixture(autouse=True)",
                "def automatic_fixture():",
                "    return autouse_target.VALUE",
                "",
                "@pytest.fixture",
                "def dependency_leaf():",
                "    return fixture_dependency_target.VALUE",
                "",
                "@pytest.fixture",
                "def dependency_root(dependency_leaf):",
                "    return dependency_leaf",
                "",
                "@pytest.fixture",
                "def unused_fixture():",
                "    return unused_fixture_target.VALUE",
                "",
            ]
        ),
    )
    _write(
        root / "tests/test_fixture_use.py",
        "def test_owned(owned_fixture):\n    assert owned_fixture\n",
    )
    _write(
        root / "tests/test_getfixturevalue.py",
        'def test_lookup(request):\n    assert request.getfixturevalue("lookup_fixture")\n',
    )
    _write(
        root / "tests/test_autouse_behavior.py", "def test_auto():\n    assert True\n"
    )
    _write(
        root / "tests/test_fixture_dependency.py",
        "def test_dependency(dependency_root):\n    assert dependency_root\n",
    )
    _write(
        root / "tests/test_unused_fixture.py", "def test_unused():\n    assert True\n"
    )

    _write(
        root / "tests/plugins/owner.py",
        "\n".join(
            [
                "import pytest",
                "import samplepkg.plugin_target",
                'pytest_plugins = ["checks.plugins.nested"]',
                "",
                "@pytest.fixture",
                "def plugin_owned():",
                "    import samplepkg.plugin_fixture_target as target",
                "    return target.VALUE",
                "",
                "@pytest.fixture",
                "def plugin_unused():",
                "    import samplepkg.plugin_unused_fixture_target as target",
                "    return target.VALUE",
                "",
            ]
        ),
    )
    _write(
        root / "tests/plugins/nested.py",
        'import samplepkg.plugin_nested_target\npytest_plugins = ["checks.plugins.too_deep"]\n',
    )
    _write(
        root / "tests/plugins/too_deep.py", "import samplepkg.plugin_too_deep_target\n"
    )
    _write(
        root / "tests/test_plugin_behavior.py", "def test_plugin():\n    assert True\n"
    )
    _write(
        root / "tests/test_plugin_fixture_use.py",
        "def test_plugin_fixture(plugin_owned):\n    assert plugin_owned\n",
    )
    _write(
        root / "tests/test_plugin_fixture_unused.py",
        "def test_plugin_unused():\n    assert True\n",
    )

    _write(root / "tests/helpers/first.py", "import checks.helpers.second\n")
    _write(root / "tests/helpers/second.py", "import samplepkg.helper_chain_target\n")
    _write(root / "tests/test_helper_chain.py", "import checks.helpers.first\n")

    _write(root / "tests/helpers/deep_a.py", "import checks.helpers.deep_b\n")
    _write(root / "tests/helpers/deep_b.py", "import checks.helpers.deep_c\n")
    _write(
        root / "tests/helpers/deep_c.py", "import samplepkg.helper_too_deep_target\n"
    )
    _write(root / "tests/test_helper_too_deep.py", "import checks.helpers.deep_a\n")

    _write(
        root / "tests/test_dynamic_import.py",
        'import importlib\n\ndef test_dynamic():\n    assert importlib.import_module("samplepkg.dynamic_target")\n',
    )
    _write(
        root / "tests/test_dunder_import.py",
        'def test_dynamic():\n    assert __import__("samplepkg.dunder_target")\n',
    )
    _write(
        root / "tests/test_dynamic_computed.py",
        'import importlib\n\ndef test_dynamic():\n    name = "samplepkg." + "dynamic_computed_target"\n    assert importlib.import_module(name)\n',
    )
    _write(
        root / "tests/test_declared_behavior.py",
        "def test_declared():\n    assert True\n",
    )

    hints = root / "ownership.json"
    hints.write_text(
        json.dumps(
            {
                "version": 1,
                "relationships": [
                    {
                        "source": "src/samplepkg/declared_target.py",
                        "test": "tests/test_declared_behavior.py",
                        "reason": "generated contract owner",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return hints


def _run_probe(root: Path, hints: Path) -> dict[str, object]:
    artifact = root / "p3.json"
    exit_codes: list[int] = []

    def emit(_level: str, _event: str, **_payload: object) -> None:
        return None

    def exit_code(code: int) -> None:
        exit_codes.append(code)

    refactor_focus_audit(
        emit=emit,
        exit_code=exit_code,
        source_root=root / "src/samplepkg",
        tests_root=root / "tests",
        repository_root=root,
        artifact_path=artifact,
        package_name="samplepkg",
        tests_package_name="checks",
        file_line_threshold=20,
        function_line_threshold=10,
        top_n=100,
        transitive_max_depth=2,
        helper_max_depth=2,
        pytest_max_depth=2,
        ownership_hints_path=hints,
    )
    if exit_codes != [0]:
        raise RuntimeError(f"unexpected probe exit codes: {exit_codes!r}")
    return json.loads(artifact.read_text(encoding="utf-8"))


def _invalid_hint_cases_fail_closed(root: Path) -> dict[str, bool]:
    cases = {
        "stale_source": "src/samplepkg/does_not_exist.py",
        "absolute_source": (root / "src/samplepkg/declared_target.py")
        .resolve()
        .as_posix(),
        "escaping_source": "../outside.py",
    }
    results: dict[str, bool] = {}
    for name, source_value in cases.items():
        invalid = root / f"invalid-{name}.json"
        invalid.write_text(
            json.dumps(
                {
                    "version": 1,
                    "relationships": [
                        {
                            "source": source_value,
                            "test": "tests/test_declared_behavior.py",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        artifact = root / f"invalid-{name}-artifact.json"
        exits: list[int] = []

        def emit(_level: str, _event: str, **_payload: object) -> None:
            return None

        def exit_code(code: int) -> None:
            exits.append(code)

        refactor_focus_audit(
            emit=emit,
            exit_code=exit_code,
            source_root=root / "src/samplepkg",
            tests_root=root / "tests",
            repository_root=root,
            artifact_path=artifact,
            package_name="samplepkg",
            tests_package_name="checks",
            file_line_threshold=20,
            top_n=100,
            helper_max_depth=2,
            pytest_max_depth=2,
            ownership_hints_path=invalid,
        )
        results[name] = exits == [2] and not artifact.exists()
    return results


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []
    p2 = qualify_p2(None)
    if not p2.get("passed"):
        failures.append("P2 parse-once/evidence-authority qualification regressed")

    with tempfile.TemporaryDirectory(
        prefix="agent-economics-refactor-focus-p3-"
    ) as tmp:
        root = Path(tmp)
        hints = materialize_p3_corpus(root)
        payload = _run_probe(root, hints)
        candidates = payload.get("candidates", [])
        assert isinstance(candidates, list)
        rows = [
            contract_candidate_as_legacy_row(item)
            for item in candidates
            if isinstance(item, dict)
        ]
        rows_by_source = {
            row.get("source_path"): row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("source_path"), str)
        }

        results: list[dict[str, object]] = []
        confirmed_expected = 0
        confirmed_observed = 0
        false_authority = 0
        for case in CASES:
            row = rows_by_source.get(case.source_path)
            if not isinstance(row, dict):
                failures.append(f"{case.name}: source row missing")
                continue
            matches = row.get("matches", [])
            assert isinstance(matches, list)
            match = next(
                (
                    item
                    for item in matches
                    if isinstance(item, dict)
                    and item.get("test_path") == case.test_path
                ),
                None,
            )
            authority = (
                match.get("evidence_authority") if isinstance(match, dict) else None
            )
            if case.expectation == "confirmed":
                confirmed_expected += 1
                if authority == "confirmed":
                    confirmed_observed += 1
                else:
                    failures.append(
                        f"{case.name}: expected confirmed evidence, got {authority!r}"
                    )
                if (
                    isinstance(match, dict)
                    and case.match_type
                    and match.get("match_type") != case.match_type
                ):
                    failures.append(
                        f"{case.name}: expected match type {case.match_type}, "
                        f"got {match.get('match_type')}"
                    )
                if isinstance(match, dict):
                    provenance = match.get("provenance")
                    if not isinstance(provenance, str) or not provenance:
                        failures.append(f"{case.name}: provenance missing")
                    elif (
                        case.provenance_contains
                        and case.provenance_contains not in provenance
                    ):
                        failures.append(
                            f"{case.name}: provenance missing {case.provenance_contains!r}"
                        )
            else:
                if authority == "confirmed":
                    false_authority += 1
                    failures.append(
                        f"{case.name}: bounded/unused relationship became confirmed"
                    )
            results.append(
                {
                    "name": case.name,
                    "expectation": case.expectation,
                    "observed_match": match,
                    "correspondence_status": row.get("correspondence_status"),
                }
            )

        invalid_hint_cases = _invalid_hint_cases_fail_closed(root)
        invalid_hints_fail_closed = all(invalid_hint_cases.values())
        for name, passed in invalid_hint_cases.items():
            if not passed:
                failures.append(
                    f"invalid ownership hint case did not fail closed: {name}"
                )

        economics = payload.get("economics", {})
        if not isinstance(economics, dict):
            economics = {}
            failures.append("economics block missing")
        if economics.get("helper_max_depth") != 2:
            failures.append("helper depth bound missing from economics")
        if economics.get("pytest_max_depth") != 2:
            failures.append("pytest depth bound missing from economics")
        if economics.get("ownership_hints_loaded") != 1:
            failures.append("ownership hint count missing or incorrect")
        expected_hint_bytes = hints.stat().st_size
        if economics.get("auxiliary_files_read") != 1:
            failures.append("ownership hint auxiliary file read count incorrect")
        if economics.get("auxiliary_bytes_read") != expected_hint_bytes:
            failures.append("ownership hint auxiliary byte accounting incorrect")
        if economics.get("total_files_read") != int(economics.get("files_read", 0)) + 1:
            failures.append(
                "total file-read accounting does not include ownership hints"
            )
        if (
            economics.get("total_bytes_read")
            != int(economics.get("bytes_read", 0)) + expected_hint_bytes
        ):
            failures.append("total byte accounting does not include ownership hints")

        result: dict[str, object] = {
            "probe": "refactor-focus",
            "qualification_phase": 3,
            "passed": not failures,
            "confirmed_expected": confirmed_expected,
            "confirmed_observed": confirmed_observed,
            "false_authority": false_authority,
            "invalid_hints_fail_closed": invalid_hints_fail_closed,
            "invalid_hint_cases": invalid_hint_cases,
            "p2_passed": p2.get("passed"),
            "economics": economics,
            "cases": results,
            "failures": failures,
        }

    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run P3 Python/pytest ownership qualification for refactor-focus.",
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/refactor-focus-p3-qualification.json"),
        help="Qualification JSON destination.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = qualify(args.artifact_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
