from __future__ import annotations

import argparse
import json
from pathlib import Path

from .test_focus import GateSpec, TestFocusError, test_focus_audit


def _gate(value: str) -> GateSpec:
    if "=" not in value:
        raise argparse.ArgumentTypeError("gate must be NAME=COMMAND")
    name, command = value.split("=", 1)
    if not name.strip() or not command.strip():
        raise argparse.ArgumentTypeError("gate must contain non-empty NAME and COMMAND")
    return GateSpec(name=name.strip(), command=command.strip())


def _changed_paths(
    values: list[Path], path_file: Path | None, repository_root: Path
) -> list[Path]:
    result = list(values)
    if path_file is not None:
        source = path_file if path_file.is_absolute() else repository_root / path_file
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise TestFocusError(f"cannot read changed-paths file: {exc}") from exc
        if len(lines) > 10_000:
            raise TestFocusError("changed-paths file exceeds 10,000-line bound")
        for raw in lines:
            value = raw.strip()
            if not value or value.startswith("#"):
                continue
            result.append(Path(value))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a bounded staged verification ladder for changed repository paths.",
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--tests-root", type=Path, default=Path("tests"))
    parser.add_argument("--changed-path", action="append", type=Path, default=[])
    parser.add_argument("--changed-paths-file", type=Path, default=None)
    parser.add_argument("--package-name", default=None)
    parser.add_argument("--tests-package-name", default=None)
    parser.add_argument(
        "--artifact",
        "--artifact-path",
        dest="artifact",
        type=Path,
        default=Path(".agent-artifacts/test-focus.json"),
    )
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--ownership-hints-path", type=Path, default=None)
    parser.add_argument("--helper-max-depth", type=int, default=2)
    parser.add_argument("--pytest-max-depth", type=int, default=2)
    parser.add_argument("--impact-max-depth", type=int, default=1)
    parser.add_argument("--impact-max-sources", type=int, default=100)
    parser.add_argument("--max-tests-per-stage", type=int, default=50)
    parser.add_argument("--max-changed-identity-bytes", type=int, default=1_048_576)
    parser.add_argument(
        "--gate",
        action="append",
        type=_gate,
        default=[],
        metavar="NAME=COMMAND",
        help="Repository-supplied broader verification command. Repeat as needed.",
    )
    parser.add_argument(
        "--discovery-mode", choices=("auto", "git", "filesystem"), default="auto"
    )
    parser.add_argument(
        "--untracked-policy", choices=("include", "exclude"), default="include"
    )
    parser.add_argument(
        "--ignored-policy", choices=("exclude", "include"), default="exclude"
    )
    parser.add_argument(
        "--symlink-policy",
        choices=("exclude", "reject", "within-repo"),
        default="exclude",
    )
    parser.add_argument("--exclude-path", action="append", default=[])
    parser.add_argument("--no-default-excludes", action="store_true")
    parser.add_argument("--git-timeout-seconds", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    try:
        changed = _changed_paths(
            args.changed_path, args.changed_paths_file, repository_root
        )
        payload = test_focus_audit(
            repository_root=repository_root,
            source_root=args.source_root,
            tests_root=args.tests_root,
            changed_paths=changed,
            package_name=args.package_name,
            tests_package_name=args.tests_package_name,
            artifact_path=args.artifact,
            helper_max_depth=args.helper_max_depth,
            pytest_max_depth=args.pytest_max_depth,
            impact_max_depth=args.impact_max_depth,
            impact_max_sources=args.impact_max_sources,
            max_tests_per_stage=args.max_tests_per_stage,
            max_changed_identity_bytes=args.max_changed_identity_bytes,
            ownership_hints_path=args.ownership_hints_path,
            gates=tuple(args.gate),
            discovery_mode=args.discovery_mode,
            untracked_policy=args.untracked_policy,
            ignored_policy=args.ignored_policy,
            symlink_policy=args.symlink_policy,
            exclude_patterns=tuple(args.exclude_path),
            use_default_excludes=not args.no_default_excludes,
            git_timeout_seconds=args.git_timeout_seconds,
        )
    except TestFocusError as exc:
        raise SystemExit(f"test-focus: {exc}") from exc
    if args.quiet:
        return
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"artifact={args.artifact.as_posix()} candidates={len(payload['candidates'])} "
            f"verification={len(payload['verification_suggestions'])} "
            f"uncertainty={len(payload['uncertainty'])}"
        )


if __name__ == "__main__":
    main()
