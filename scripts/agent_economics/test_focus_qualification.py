from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from .probe_contract import validate_probe_contract
from .test_focus import GateSpec, TestFocusError, test_focus_audit


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_repo(root: Path) -> None:
    _write(
        root / "src/samplepkg/__init__.py",
        "from .locality import structural_locality_delta\n\n"
        "__all__ = ['Facade', 'structural_locality_delta']\n\n"
        "def __getattr__(name):\n"
        "    if name == 'Facade':\n"
        "        from .facade import Facade\n"
        "        return Facade\n"
        "    raise AttributeError(name)\n",
    )
    _write(
        root / "src/samplepkg/feature_mixin.py",
        "class FeatureMixin:\n    def feature(self, value):\n        return value\n",
    )
    _write(
        root / "src/samplepkg/facade.py",
        "from samplepkg.feature_mixin import FeatureMixin\n\n"
        "class Facade(FeatureMixin):\n"
        "    def __enter__(self):\n"
        "        return self\n\n"
        "    def __exit__(self, exc_type, exc, tb):\n"
        "        return None\n\n"
        "    def facade_only(self):\n"
        "        return 'facade'\n",
    )
    _write(root / "src/samplepkg/core.py", "def normalize(x):\n    return x.strip()\n")
    _write(
        root / "src/samplepkg/locality.py",
        "def structural_locality_delta(before, after):\n    return after - before\n",
    )
    _write(root / "src/samplepkg/ambiguous_a.py", "def shared():\n    return 'a'\n")
    _write(root / "src/samplepkg/ambiguous_b.py", "def shared():\n    return 'b'\n")
    _write(
        root / "src/samplepkg/ambiguous_api.py",
        "from .ambiguous_a import shared\nfrom .ambiguous_b import shared\n",
    )
    _write(
        root / "src/samplepkg/service.py",
        "from samplepkg.core import normalize\n\ndef serve(x):\n    return normalize(x)\n",
    )
    _write(root / "src/samplepkg/mirror.py", "def mirror(x):\n    return x\n")
    _write(root / "src/samplepkg/unrelated.py", "def unrelated():\n    return 1\n")
    _write(root / "tests/__init__.py", "")
    _write(
        root / "tests/test_static_reexport.py",
        "from samplepkg import structural_locality_delta\n\n"
        "def test_static_reexport():\n"
        "    assert structural_locality_delta(1, 3) == 2\n",
    )
    _write(
        root / "tests/test_ambiguous_reexport.py",
        "from samplepkg.ambiguous_api import shared\n\n"
        "def test_ambiguous_reexport():\n"
        "    assert shared() in {'a', 'b'}\n",
    )
    _write(
        root / "tests/test_core.py",
        "from samplepkg.core import normalize\n\ndef test_core():\n    assert normalize(' x ') == 'x'\n",
    )
    _write(
        root / "tests/test_core_extra.py",
        "from samplepkg.core import normalize\n\ndef test_core_extra():\n    assert normalize('y') == 'y'\n",
    )
    _write(
        root / "tests/test_service.py",
        "from samplepkg.service import serve\n\ndef test_service():\n    assert serve(' z ') == 'z'\n",
    )
    _write(root / "tests/test_mirror.py", "def test_name_only():\n    assert True\n")
    _write(
        root / "tests/test_feature_facade.py",
        "from samplepkg import Facade\n\n"
        "def test_feature_through_facade():\n"
        "    with Facade() as facade:\n"
        "        assert facade.feature('x') == 'x'\n",
    )
    _write(
        root / "tests/test_facade_only.py",
        "from samplepkg import Facade\n\n"
        "def test_facade_only():\n"
        "    with Facade() as facade:\n"
        "        assert facade.facade_only() == 'facade'\n",
    )
    _write(
        root / "tests/test_feature_uninitialized.py",
        "from samplepkg import Facade\n\n"
        "def test_feature_without_initialization():\n"
        "    facade = object.__new__(Facade)\n"
        "    assert facade.feature('x') == 'x'\n",
    )
    _write(root / "config/settings.yaml", "mode: safe\n")


def _run(root: Path, changed: list[str], **kwargs: object) -> dict[str, object]:
    return test_focus_audit(
        repository_root=root,
        source_root=Path("src/samplepkg"),
        tests_root=Path("tests"),
        changed_paths=[Path(item) for item in changed],
        package_name="samplepkg",
        tests_package_name="tests",
        discovery_mode="filesystem",
        gates=(GateSpec("full", "uv run pytest"),),
        **kwargs,
    )


def _paths(payload: dict[str, object], stage: str) -> list[str]:
    suggestions = payload.get("verification_suggestions", [])
    assert isinstance(suggestions, list)
    return sorted(
        str(item.get("path"))
        for item in suggestions
        if isinstance(item, dict)
        and item.get("stage") == stage
        and item.get("kind") == "test_file"
    )


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []
    observations: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="agent-econ-p7-") as temp:
        root = Path(temp) / "repo-a"
        _build_repo(root)

        core = _run(root, ["src/samplepkg/core.py"])
        contract_errors = validate_probe_contract(core)
        if contract_errors:
            failures.append(f"common contract invalid: {contract_errors}")
        direct = _paths(core, "direct")
        affected = _paths(core, "affected")
        if direct != ["tests/test_core.py", "tests/test_core_extra.py"]:
            failures.append(f"direct owning tests mismatch: {direct}")
        if affected != ["tests/test_service.py"]:
            failures.append(f"affected dependent tests mismatch: {affected}")

        static_reexport = _run(root, ["src/samplepkg/locality.py"])
        static_reexport_direct = _paths(static_reexport, "direct")
        if static_reexport_direct != ["tests/test_static_reexport.py"]:
            failures.append(
                f"static re-export owning tests mismatch: {static_reexport_direct}"
            )
        static_candidates = static_reexport.get("candidates", [])
        if not isinstance(static_candidates, list) or not static_candidates:
            failures.append("static re-export candidate missing")
        else:
            static_evidence = (
                static_candidates[0].get("evidence", {})
                if isinstance(static_candidates[0], dict)
                else {}
            )
            static_confirmed = (
                static_evidence.get("confirmed", [])
                if isinstance(static_evidence, dict)
                else []
            )
            if not any(
                isinstance(item, dict)
                and item.get("test_path") == "tests/test_static_reexport.py"
                and item.get("match_type") == "import_exact"
                and "static_reexport:" in str(item.get("provenance") or "")
                for item in static_confirmed
            ):
                failures.append("static re-export ownership provenance missing")

        ambiguous_a = _run(root, ["src/samplepkg/ambiguous_a.py"])
        if "tests/test_ambiguous_reexport.py" in _paths(ambiguous_a, "direct"):
            failures.append(
                "ambiguous static re-export became direct ownership authority"
            )

        inherited = _run(root, ["src/samplepkg/feature_mixin.py"])
        inherited_direct = _paths(inherited, "direct")
        if inherited_direct != [
            "tests/test_feature_facade.py",
            "tests/test_feature_uninitialized.py",
        ]:
            failures.append(
                f"inherited method owning tests mismatch: {inherited_direct}"
            )
        if "tests/test_facade_only.py" in inherited_direct:
            failures.append(
                "facade-only call became inherited method ownership authority"
            )
        inherited_candidates = inherited.get("candidates", [])
        if not isinstance(inherited_candidates, list) or not inherited_candidates:
            failures.append("inherited method candidate missing")
        else:
            inherited_evidence = (
                inherited_candidates[0].get("evidence", {})
                if isinstance(inherited_candidates[0], dict)
                else {}
            )
            inherited_confirmed = (
                inherited_evidence.get("confirmed", [])
                if isinstance(inherited_evidence, dict)
                else []
            )
            if not any(
                isinstance(item, dict)
                and item.get("test_path") == "tests/test_feature_facade.py"
                and item.get("match_type") == "inherited_method_call"
                for item in inherited_confirmed
            ):
                failures.append("inherited method ownership provenance missing")
            if not any(
                isinstance(item, dict)
                and item.get("test_path") == "tests/test_feature_uninitialized.py"
                and item.get("match_type") == "inherited_method_call"
                for item in inherited_confirmed
            ):
                failures.append("object.__new__ inherited ownership provenance missing")
        interpretation = core.get("interpretation", {})
        if (
            not isinstance(interpretation, dict)
            or interpretation.get("sufficiency_rule")
            != "focused suggestions never prove broader verification unnecessary"
        ):
            failures.append("focused verification sufficiency boundary missing")
        gates = [
            item
            for item in core.get("verification_suggestions", [])
            if isinstance(item, dict) and item.get("stage") == "broader_gate"
        ]
        if len(gates) != 1 or gates[0].get("command") != "uv run pytest":
            failures.append("repository-supplied broader gate missing")

        no_impact = _run(root, ["src/samplepkg/core.py"], impact_max_depth=0)
        if _paths(no_impact, "affected"):
            failures.append("impact_max_depth=0 still emitted affected tests")

        mirror = _run(root, ["src/samplepkg/mirror.py"])
        if _paths(mirror, "direct"):
            failures.append(
                "name/mirrored convention became direct ownership authority"
            )
        mirror_candidates = mirror.get("candidates", [])
        if not isinstance(mirror_candidates, list) or not mirror_candidates:
            failures.append("mirror candidate missing")
        else:
            evidence = (
                mirror_candidates[0].get("evidence", {})
                if isinstance(mirror_candidates[0], dict)
                else {}
            )
            supporting = (
                evidence.get("supporting", []) if isinstance(evidence, dict) else []
            )
            if not any(
                isinstance(item, dict)
                and item.get("test_path") == "tests/test_mirror.py"
                for item in supporting
            ):
                failures.append("name/mirrored supporting evidence missing")
            if not mirror_candidates[0].get("required_next_evidence"):
                failures.append("unconfirmed source has no required next evidence")

        changed_test = _run(root, ["tests/test_service.py"])
        if _paths(changed_test, "direct") != ["tests/test_service.py"]:
            failures.append("changed test file was not first-stage verification")

        config = _run(root, ["config/settings.yaml"])
        if _paths(config, "direct") or _paths(config, "affected"):
            failures.append("unsupported non-Python change fabricated focused tests")
        uncertainty = config.get("uncertainty", [])
        if not any(
            isinstance(item, dict)
            and item.get("code") == "focused_test_mapping_unsupported"
            for item in uncertainty
        ):
            failures.append("unsupported non-Python change did not publish uncertainty")

        missing = _run(root, ["src/samplepkg/deleted.py"])
        if not any(
            isinstance(item, dict)
            and item.get("code")
            in {"changed_python_not_discovered", "changed_path_missing"}
            for item in missing.get("uncertainty", [])
        ):
            failures.append("missing/deleted path did not publish uncertainty")

        bounded = _run(root, ["src/samplepkg/core.py"], max_tests_per_stage=1)
        if len(_paths(bounded, "direct")) != 1:
            failures.append("max_tests_per_stage did not bound direct tests")
        deferred = bounded.get("deferred_evidence", [])
        if not any(
            isinstance(item, dict) and item.get("stage") == "direct"
            for item in deferred
        ):
            failures.append("bounded direct test omission was not deferred explicitly")

        no_gates = test_focus_audit(
            repository_root=root,
            source_root=Path("src/samplepkg"),
            tests_root=Path("tests"),
            changed_paths=[Path("src/samplepkg/core.py")],
            package_name="samplepkg",
            tests_package_name="tests",
            discovery_mode="filesystem",
        )
        if not any(
            isinstance(item, dict)
            and item.get("code") == "broader_verification_unspecified"
            for item in no_gates.get("uncertainty", [])
        ):
            failures.append("missing broader gates did not publish uncertainty")

        root_b = Path(temp) / "repo-b"
        shutil.copytree(root, root_b)
        core_b = _run(root_b, ["src/samplepkg/core.py"])
        repo_a = core.get("repository", {})
        repo_b = core_b.get("repository", {})
        if (
            not isinstance(repo_a, dict)
            or not isinstance(repo_b, dict)
            or repo_a.get("identity") != repo_b.get("identity")
        ):
            failures.append("repository identity depends on checkout location")
        _write(
            root_b / "src/samplepkg/core.py",
            "def normalize(x):\n    return x.strip().lower()\n",
        )
        core_b_changed = _run(root_b, ["src/samplepkg/core.py"])
        repo_b_changed = core_b_changed.get("repository", {})
        if (
            isinstance(repo_a, dict)
            and isinstance(repo_b_changed, dict)
            and repo_a.get("identity") == repo_b_changed.get("identity")
        ):
            failures.append(
                "repository identity did not change after analyzed source changed"
            )

        try:
            _run(root, ["../escape.py"])
        except TestFocusError:
            pass
        else:
            failures.append("changed path escaping repository_root did not fail closed")

        economics = core.get("economics", {})
        observations = {
            "direct_tests": direct,
            "affected_tests": affected,
            "inherited_direct_tests": inherited_direct,
            "static_reexport_direct_tests": static_reexport_direct,
            "bounded_direct": _paths(bounded, "direct"),
            "deferred_count": len(deferred) if isinstance(deferred, list) else None,
            "contract_errors": contract_errors,
            "repository_identity": repo_a.get("identity")
            if isinstance(repo_a, dict)
            else None,
            "economics": economics,
        }

    result: dict[str, object] = {
        "probe": "test-focus",
        "qualification_phase": 7,
        "passed": not failures,
        "observations": observations,
        "failures": failures,
    }
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Qualify P7 test-focus verification planning."
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/test-focus-p7-qualification.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = qualify(args.artifact_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
