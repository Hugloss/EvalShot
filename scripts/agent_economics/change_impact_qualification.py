from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .change_impact import ChangeImpactError, change_impact_audit
from .probe_contract import validate_probe_contract


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_repository(root: Path) -> Path:
    source_root = root / "src/pkg"
    source_root.mkdir(parents=True)
    _write(source_root / "__init__.py", "")
    _write(source_root / "a.py", "VALUE = 1\n")
    _write(source_root / "b.py", "import pkg.a\n")
    _write(source_root / "c.py", "import pkg.b\n")
    _write(source_root / "d.py", "from pkg import a\n")
    _write(source_root / "e.py", "import pkg.c\n")
    _write(source_root / "f.py", "import pkg.deleted\n")
    return source_root


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []
    observations: dict[str, object] = {}

    with (
        tempfile.TemporaryDirectory() as first,
        tempfile.TemporaryDirectory() as second,
    ):
        root_a = Path(first)
        root_b = Path(second)
        source_a = _make_repository(root_a)
        source_b = _make_repository(root_b)
        common = {
            "package_name": "pkg",
            "impact_max_depth": 3,
            "impact_max_sources": 20,
            "discovery_mode": "filesystem",
            "use_default_excludes": False,
        }

        payload = change_impact_audit(
            repository_root=root_a,
            source_root=source_a,
            changed_paths=["src/pkg/a.py"],
            **common,
        )
        targets = [candidate["target"] for candidate in payload["candidates"]]
        expected_targets = [
            "src/pkg/b.py",
            "src/pkg/d.py",
            "src/pkg/c.py",
            "src/pkg/e.py",
        ]
        if targets != expected_targets:
            failures.append(f"unexpected impact order: {targets!r}")
        depths = {
            candidate["target"]: candidate["facts"]["impact_depth"]
            for candidate in payload["candidates"]
        }
        expected_depths = {
            "src/pkg/b.py": 1,
            "src/pkg/d.py": 1,
            "src/pkg/c.py": 2,
            "src/pkg/e.py": 3,
        }
        if depths != expected_depths:
            failures.append(f"unexpected impact depths: {depths!r}")
        contract_errors = validate_probe_contract(payload)
        if contract_errors:
            failures.append(f"common contract invalid: {contract_errors}")

        depth_bounded = change_impact_audit(
            repository_root=root_a,
            source_root=source_a,
            changed_paths=["src/pkg/a.py"],
            **{**common, "impact_max_depth": 1},
        )
        if [candidate["target"] for candidate in depth_bounded["candidates"]] != [
            "src/pkg/b.py",
            "src/pkg/d.py",
        ]:
            failures.append("impact_max_depth did not stop transitive expansion")

        source_bounded = change_impact_audit(
            repository_root=root_a,
            source_root=source_a,
            changed_paths=["src/pkg/a.py"],
            **{**common, "impact_max_sources": 2},
        )
        if (
            len(source_bounded["candidates"]) != 2
            or not source_bounded["deferred_evidence"]
        ):
            failures.append("impact_max_sources did not preserve bounded omissions")

        deleted = change_impact_audit(
            repository_root=root_a,
            source_root=source_a,
            changed_paths=["src/pkg/deleted.py"],
            **common,
        )
        if [candidate["target"] for candidate in deleted["candidates"]] != [
            "src/pkg/f.py"
        ]:
            failures.append(
                "deleted-module lexical seed did not recover reverse importer"
            )
        if not any(
            item.get("code") == "changed_source_not_discovered"
            for item in deleted["uncertainty"]
        ):
            failures.append("deleted source did not publish discovery uncertainty")

        non_python = change_impact_audit(
            repository_root=root_a,
            source_root=source_a,
            changed_paths=["config/settings.yaml"],
            **common,
        )
        if non_python["candidates"] or not any(
            item.get("code") == "static_impact_unsupported"
            for item in non_python["uncertainty"]
        ):
            failures.append("non-Python change fabricated static impact")
        if (
            non_python["economics"].get("files_read") != 0
            or non_python["economics"].get("ast_parses") != 0
        ):
            failures.append(
                "unsupported-only change unnecessarily scanned Python source"
            )

        copied = change_impact_audit(
            repository_root=root_b,
            source_root=source_b,
            changed_paths=["src/pkg/a.py"],
            **common,
        )
        if payload["repository"]["identity"] != copied["repository"]["identity"]:
            failures.append("repository identity changed across checkout locations")
        _write(source_b / "a.py", "VALUE = 2\n")
        changed_copy = change_impact_audit(
            repository_root=root_b,
            source_root=source_b,
            changed_paths=["src/pkg/a.py"],
            **common,
        )
        if copied["repository"]["identity"] == changed_copy["repository"]["identity"]:
            failures.append(
                "repository identity did not invalidate on source-byte change"
            )

        try:
            change_impact_audit(
                repository_root=root_a,
                source_root=source_a,
                changed_paths=["../escape.py"],
                **common,
            )
        except ChangeImpactError:
            pass
        else:
            failures.append("escaping changed path did not fail closed")

        observations = {
            "targets": targets,
            "depths": depths,
            "bounded_selected": len(source_bounded["candidates"]),
            "bounded_deferred": len(source_bounded["deferred_evidence"]),
            "deleted_targets": [
                candidate["target"] for candidate in deleted["candidates"]
            ],
            "contract_errors": contract_errors,
        }

    result: dict[str, object] = {
        "probe": "change-impact",
        "qualification_phase": 8,
        "passed": not failures,
        "observations": observations,
        "failures": failures,
    }
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/change-impact-p8-qualification.json"),
    )
    args = parser.parse_args(argv)
    result = qualify(args.artifact_path)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
