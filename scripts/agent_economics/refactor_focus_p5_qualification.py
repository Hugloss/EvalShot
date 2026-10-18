from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .refactor_focus_discovery import (
    DEFAULT_EXCLUDE_PATTERNS,
    DiscoveryConfig,
    DiscoveryError,
    discover_python_roots,
    is_excluded_repo_path,
    normalize_exclude_patterns,
)
from .refactor_focus_workflow import refactor_focus_audit


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _relative(paths: tuple[Path, ...], root: Path) -> set[str]:
    return {path.absolute().relative_to(root).as_posix() for path in paths}


def _expect_error(failures: list[str], label: str, fn: object) -> None:
    try:
        fn()  # type: ignore[operator]
    except DiscoveryError:
        return
    failures.append(f"{label}: expected DiscoveryError")


def _run_audit(
    *,
    root: Path,
    artifact: Path,
    untracked_policy: str = "include",
) -> dict[str, object]:
    exit_codes: list[int] = []

    def emit(*_args: object, **_kwargs: object) -> None:
        return None

    def exit_code(code: int) -> None:
        exit_codes.append(code)

    refactor_focus_audit(
        emit=emit,
        exit_code=exit_code,
        source_root=root / "src" / "samplepkg",
        tests_root=root / "tests",
        repository_root=root,
        artifact_path=artifact,
        package_name="samplepkg",
        tests_package_name="tests",
        file_line_threshold=2,
        function_line_threshold=2,
        top_n=5,
        discovery_mode="auto",
        untracked_policy=untracked_policy,
        ignored_policy="exclude",
        symlink_policy="exclude",
        exclude_patterns=("src/samplepkg/generated/**",),
        use_default_excludes=True,
        git_timeout_seconds=5.0,
    )
    if exit_codes != [0]:
        raise AssertionError(f"unexpected audit exit codes: {exit_codes}")
    return json.loads(artifact.read_text(encoding="utf-8"))


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []
    observations: dict[str, object] = {}

    with tempfile.TemporaryDirectory(prefix="agent-economics-p5-") as temp:
        root = Path(temp) / "repo"
        root.mkdir()
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "p5@example.invalid")
        _git(root, "config", "user.name", "P5 Qualification")

        _write(root / ".gitignore", "src/samplepkg/ignored.py\ntests/test_ignored.py\n")
        _write(root / "src/samplepkg/__init__.py", "")
        _write(
            root / "src/samplepkg/tracked.py",
            "def tracked():\n    value = 1\n    return value\n",
        )
        _write(
            root / "src/samplepkg/generated/generated.py",
            "def generated():\n    value = 2\n    return value\n",
        )
        _write(
            root / "tests/test_tracked.py",
            "import samplepkg.tracked\n\ndef test_tracked():\n    assert True\n",
        )
        _write(root / "docs/_build/should_not_scan.py", "x = 1\n")
        _git(
            root,
            "add",
            ".gitignore",
            "src",
            "tests/test_tracked.py",
            "docs/_build/should_not_scan.py",
        )
        _git(root, "commit", "-qm", "fixture")

        _write(
            root / "src/samplepkg/untracked.py",
            "def untracked():\n    value = 3\n    return value\n",
        )
        _write(
            root / "src/samplepkg/ignored.py",
            "def ignored():\n    value = 4\n    return value\n",
        )
        _write(root / "tests/test_ignored.py", "assert True\n")

        internal_link_supported = True
        try:
            os.symlink("tracked.py", root / "src/samplepkg/link.py")
        except (OSError, NotImplementedError):
            internal_link_supported = False

        default_result = discover_python_roots(
            roots={"source": root / "src/samplepkg", "tests": root / "tests"},
            repository_root=root,
            config=DiscoveryConfig(),
        )
        default_source = _relative(default_result.files_for("source"), root)
        default_tests = _relative(default_result.files_for("tests"), root)
        expected_default = {
            "src/samplepkg/__init__.py",
            "src/samplepkg/tracked.py",
            "src/samplepkg/generated/generated.py",
            "src/samplepkg/untracked.py",
        }
        if default_source != expected_default:
            failures.append(f"default Git discovery mismatch: {sorted(default_source)}")
        if default_tests != {"tests/test_tracked.py"}:
            failures.append(f"default test discovery mismatch: {sorted(default_tests)}")
        if default_result.backend != "git" or default_result.git_commands != 3:
            failures.append(
                f"auto discovery did not use one bounded Git listing: {default_result.metrics()}"
            )
        if internal_link_supported and default_result.symlinks_excluded != 1:
            failures.append("default symlink exclusion was not reported")

        tracked_only = discover_python_roots(
            roots={"source": root / "src/samplepkg", "tests": root / "tests"},
            repository_root=root,
            config=DiscoveryConfig(untracked_policy="exclude"),
        )
        tracked_source = _relative(tracked_only.files_for("source"), root)
        if "src/samplepkg/untracked.py" in tracked_source:
            failures.append("untracked_policy=exclude admitted an untracked source")
        if tracked_only.git_commands != 2:
            failures.append("tracked-only Git discovery executed unexpected commands")

        include_ignored = discover_python_roots(
            roots={"source": root / "src/samplepkg", "tests": root / "tests"},
            repository_root=root,
            config=DiscoveryConfig(ignored_policy="include"),
        )
        if "src/samplepkg/ignored.py" not in _relative(
            include_ignored.files_for("source"), root
        ):
            failures.append("ignored_policy=include did not admit ignored source")
        if "tests/test_ignored.py" not in _relative(
            include_ignored.files_for("tests"), root
        ):
            failures.append("ignored_policy=include did not admit ignored test")
        if include_ignored.git_commands != 4:
            failures.append("ignored-inclusive discovery executed unexpected commands")

        custom_excludes = normalize_exclude_patterns(
            [*DEFAULT_EXCLUDE_PATTERNS, "src/samplepkg/generated/**"]
        )
        excluded = discover_python_roots(
            roots={"source": root / "src/samplepkg", "tests": root / "tests"},
            repository_root=root,
            config=DiscoveryConfig(exclude_patterns=custom_excludes),
        )
        if "src/samplepkg/generated/generated.py" in _relative(
            excluded.files_for("source"), root
        ):
            failures.append("normalized repository-relative exclusion was ignored")
        if not is_excluded_repo_path(
            "docs/_build/should_not_scan.py", DEFAULT_EXCLUDE_PATTERNS
        ):
            failures.append("default docs/_build exclusion is ineffective")
        if is_excluded_repo_path(
            "src/docs/_build/should_not_scan.py", DEFAULT_EXCLUDE_PATTERNS
        ):
            failures.append("repo-root docs/_build exclusion leaked into nested paths")
        anchored = normalize_exclude_patterns(["src/*.py"])
        if is_excluded_repo_path("src/pkg/deep.py", anchored):
            failures.append(
                "single-segment exclusion wildcard crossed a directory boundary"
            )
        if not is_excluded_repo_path("src/direct.py", anchored):
            failures.append("anchored exclusion wildcard failed direct match")
        if normalize_exclude_patterns(["src\\generated\\**"]) != ("src/generated/**",):
            failures.append("backslash exclusion normalization failed")

        _expect_error(
            failures,
            "absolute exclusion",
            lambda: normalize_exclude_patterns(["/tmp/**"]),
        )
        _expect_error(
            failures,
            "escaping exclusion",
            lambda: normalize_exclude_patterns(["src/../outside/**"]),
        )
        _expect_error(
            failures,
            "ignored without untracked",
            lambda: DiscoveryConfig(
                untracked_policy="exclude", ignored_policy="include"
            ),
        )

        if internal_link_supported:
            _expect_error(
                failures,
                "symlink reject",
                lambda: discover_python_roots(
                    roots={"source": root / "src/samplepkg"},
                    repository_root=root,
                    config=DiscoveryConfig(symlink_policy="reject"),
                ),
            )
            internal_only = discover_python_roots(
                roots={"source": root / "src/samplepkg"},
                repository_root=root,
                config=DiscoveryConfig(symlink_policy="within-repo"),
            )
            if "src/samplepkg/link.py" not in _relative(
                internal_only.files_for("source"), root
            ):
                failures.append(
                    "within-repo symlink policy did not admit internal file symlink"
                )

            external = Path(temp) / "external.py"
            _write(external, "x = 1\n")
            try:
                os.symlink(str(external), root / "src/samplepkg/external_link.py")
                _expect_error(
                    failures,
                    "external symlink escape",
                    lambda: discover_python_roots(
                        roots={"source": root / "src/samplepkg"},
                        repository_root=root,
                        config=DiscoveryConfig(symlink_policy="within-repo"),
                    ),
                )
            except (OSError, NotImplementedError):
                pass

        outside = Path(temp) / "outside"
        outside.mkdir()
        _expect_error(
            failures,
            "out-of-repository root",
            lambda: discover_python_roots(
                roots={"outside": outside},
                repository_root=root,
                config=DiscoveryConfig(),
            ),
        )
        _expect_error(
            failures,
            "nested repository root",
            lambda: discover_python_roots(
                roots={"source": root / "src/samplepkg"},
                repository_root=root / "src",
                config=DiscoveryConfig(),
            ),
        )

        nongit = Path(temp) / "nongit"
        _write(nongit / "src/pkg/a.py", "x = 1\n")
        _write(nongit / "src/pkg/generated/b.py", "x = 2\n")
        _expect_error(
            failures,
            "explicit Git mode outside Git repository",
            lambda: discover_python_roots(
                roots={"source": nongit / "src/pkg"},
                repository_root=nongit,
                config=DiscoveryConfig(mode="git"),
            ),
        )
        fallback = discover_python_roots(
            roots={"source": nongit / "src/pkg"},
            repository_root=nongit,
            config=DiscoveryConfig(
                mode="auto",
                exclude_patterns=normalize_exclude_patterns(["src/pkg/generated/**"]),
            ),
        )
        if fallback.backend != "filesystem":
            failures.append("auto mode did not fall back to filesystem without Git")
        if not any("fallback" in warning for warning in fallback.warnings):
            failures.append("filesystem fallback was not made explicit")
        if _relative(fallback.files_for("source"), nongit) != {"src/pkg/a.py"}:
            failures.append("filesystem fallback exclusion semantics are incorrect")

        contract_include = _run_audit(
            root=root,
            artifact=Path(temp) / "include.json",
            untracked_policy="include",
        )
        contract_exclude = _run_audit(
            root=root,
            artifact=Path(temp) / "exclude.json",
            untracked_policy="exclude",
        )
        include_config = contract_include.get("configuration", {})
        exclude_config = contract_exclude.get("configuration", {})
        include_repo = contract_include.get("repository", {})
        exclude_repo = contract_exclude.get("repository", {})
        include_evidence = contract_include.get("evidence", {})
        if not isinstance(include_config, dict) or not isinstance(
            include_config.get("values"), dict
        ):
            failures.append("P5 discovery configuration missing from probe contract")
        else:
            values = include_config["values"]
            if values.get("discovery_mode") != "auto":
                failures.append("contract did not record discovery mode")
            if values.get("untracked_policy") != "include":
                failures.append("contract did not record untracked policy")
            if "src/samplepkg/generated/**" not in values.get("exclude_patterns", []):
                failures.append("contract did not record effective exclusion patterns")
        if not isinstance(include_evidence, dict) or not isinstance(
            include_evidence.get("discovery"), dict
        ):
            failures.append("contract did not publish discovery evidence")
        elif include_evidence["discovery"].get("backend") != "git":
            failures.append("contract discovery evidence did not record Git backend")
        if isinstance(include_config, dict) and isinstance(exclude_config, dict):
            if include_config.get("identity") == exclude_config.get("identity"):
                failures.append(
                    "discovery policy change did not alter configuration identity"
                )
        if isinstance(include_repo, dict) and isinstance(exclude_repo, dict):
            if include_repo.get("identity") == exclude_repo.get("identity"):
                failures.append(
                    "untracked policy change did not alter analyzed repository identity"
                )

        observations = {
            "default_backend": default_result.backend,
            "default_git_commands": default_result.git_commands,
            "default_source_files": sorted(default_source),
            "default_test_files": sorted(default_tests),
            "tracked_only_source_files": sorted(tracked_source),
            "ignored_inclusive_source_count": len(include_ignored.files_for("source")),
            "custom_excluded_count": excluded.excluded_by_pattern,
            "symlinks_excluded": default_result.symlinks_excluded,
            "filesystem_fallback_warnings": list(fallback.warnings),
            "contract_discovery_backend": (
                include_evidence.get("discovery", {}).get("backend")
                if isinstance(include_evidence, dict)
                and isinstance(include_evidence.get("discovery"), dict)
                else None
            ),
            "contract_config_identity_changes": (
                include_config.get("identity") != exclude_config.get("identity")
                if isinstance(include_config, dict) and isinstance(exclude_config, dict)
                else None
            ),
            "contract_repository_identity_changes": (
                include_repo.get("identity") != exclude_repo.get("identity")
                if isinstance(include_repo, dict) and isinstance(exclude_repo, dict)
                else None
            ),
        }

    result: dict[str, object] = {
        "probe": "refactor-focus",
        "qualification_phase": 5,
        "passed": not failures,
        "observations": observations,
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
        description="Qualify Agent Economics P5 repository discovery semantics."
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/refactor-focus-p5-qualification.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = qualify(args.artifact_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
