import argparse
import json
import sys
from pathlib import Path

from .refactor_focus_workflow import refactor_focus_audit


def emit(level: str, event: str, **payload: object) -> None:
    print(
        json.dumps(
            {
                "level": level,
                "event": event,
                **payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )


def exit_code(code: int) -> None:
    raise SystemExit(code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Find bounded Python refactoring candidates and the evidence an agent "
            "should inspect before editing."
        ),
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="Python package source root, for example src/my_package.",
    )
    parser.add_argument(
        "--tests-root",
        type=Path,
        default=Path("tests"),
        help="Test tree root. Default: tests.",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=None,
        help=(
            "Repository/reporting root. If omitted, the common ancestor of "
            "source-root and tests-root is used."
        ),
    )
    parser.add_argument(
        "--package-name",
        default=None,
        help="Import package name. If omitted, source-root basename is used.",
    )
    parser.add_argument(
        "--tests-package-name",
        default=None,
        help="Tests import package name. If omitted, tests-root basename is used.",
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/refactor-focus.json"),
        help="JSON report destination.",
    )
    parser.add_argument("--file-line-threshold", type=int, default=800)
    parser.add_argument("--function-line-threshold", type=int, default=80)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument(
        "--transitive-max-depth",
        type=int,
        default=2,
        help="Bound reverse source-dependency ownership traversal. Default: 2.",
    )
    parser.add_argument(
        "--helper-max-depth",
        type=int,
        default=2,
        help="Bound test helper-to-helper import traversal. Default: 2.",
    )
    parser.add_argument(
        "--pytest-max-depth",
        type=int,
        default=2,
        help="Bound pytest fixture dependency and pytest_plugins traversal. Default: 2.",
    )
    parser.add_argument(
        "--ownership-hints-path",
        type=Path,
        default=None,
        help=(
            "Optional JSON file with explicit repository-relative source/test "
            "ownership relationships. Invalid or stale hints fail closed."
        ),
    )
    parser.add_argument(
        "--discovery-mode",
        choices=("auto", "git", "filesystem"),
        default="auto",
        help="Discovery backend. auto prefers Git and falls back to filesystem.",
    )
    parser.add_argument(
        "--untracked-policy",
        choices=("include", "exclude"),
        default="include",
        help="Whether Git discovery includes untracked, non-ignored files.",
    )
    parser.add_argument(
        "--ignored-policy",
        choices=("exclude", "include"),
        default="exclude",
        help="Whether Git discovery includes ignored untracked files.",
    )
    parser.add_argument(
        "--symlink-policy",
        choices=("exclude", "reject", "within-repo"),
        default="exclude",
        help="How discovered Python file symlinks are handled.",
    )
    parser.add_argument(
        "--exclude-path",
        action="append",
        default=[],
        help="Additional repository-relative exclusion glob. Repeat as needed.",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="Disable the portable default exclusion patterns.",
    )
    parser.add_argument(
        "--git-timeout-seconds",
        type=float,
        default=5.0,
        help="Timeout for each bounded Git discovery command. Default: 5 seconds.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    refactor_focus_audit(
        emit=emit,
        exit_code=exit_code,
        source_root=args.source_root,
        tests_root=args.tests_root,
        repository_root=args.repository_root,
        artifact_path=args.artifact_path,
        package_name=args.package_name,
        tests_package_name=args.tests_package_name,
        file_line_threshold=args.file_line_threshold,
        function_line_threshold=args.function_line_threshold,
        top_n=args.top_n,
        transitive_max_depth=args.transitive_max_depth,
        helper_max_depth=args.helper_max_depth,
        pytest_max_depth=args.pytest_max_depth,
        ownership_hints_path=args.ownership_hints_path,
        discovery_mode=args.discovery_mode,
        untracked_policy=args.untracked_policy,
        ignored_policy=args.ignored_policy,
        symlink_policy=args.symlink_policy,
        exclude_patterns=tuple(args.exclude_path),
        use_default_excludes=not args.no_default_excludes,
        git_timeout_seconds=args.git_timeout_seconds,
    )


if __name__ == "__main__":
    main()
