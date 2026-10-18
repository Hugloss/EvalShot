from __future__ import annotations

import argparse
import json
from pathlib import Path

from .change_impact import ChangeImpactError, change_impact_audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bound static Python reverse-impact evidence for changed paths.",
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--changed-path",
        action="append",
        default=[],
        required=True,
        help="Repository-relative changed path. Repeat as needed.",
    )
    parser.add_argument("--package-name", default=None)
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/change-impact.json"),
    )
    parser.add_argument("--impact-max-depth", type=int, default=3)
    parser.add_argument("--impact-max-sources", type=int, default=100)
    parser.add_argument(
        "--discovery-mode",
        choices=("auto", "git", "filesystem"),
        default="auto",
    )
    parser.add_argument(
        "--untracked-policy",
        choices=("include", "exclude"),
        default="include",
    )
    parser.add_argument(
        "--ignored-policy",
        choices=("exclude", "include"),
        default="exclude",
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
    try:
        payload = change_impact_audit(
            repository_root=args.repository_root,
            source_root=args.source_root,
            changed_paths=args.changed_path,
            package_name=args.package_name,
            artifact_path=args.artifact_path,
            impact_max_depth=args.impact_max_depth,
            impact_max_sources=args.impact_max_sources,
            discovery_mode=args.discovery_mode,
            untracked_policy=args.untracked_policy,
            ignored_policy=args.ignored_policy,
            symlink_policy=args.symlink_policy,
            exclude_patterns=tuple(args.exclude_path),
            use_default_excludes=not args.no_default_excludes,
            git_timeout_seconds=args.git_timeout_seconds,
        )
    except ChangeImpactError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
