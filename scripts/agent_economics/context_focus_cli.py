from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .context_focus import (
    DEFAULT_CONTEXT_SUFFIXES,
    ContextBudget,
    ContextFocusError,
    ScanBudget,
    context_focus_audit,
)
from .refactor_focus_discovery import (
    DEFAULT_EXCLUDE_PATTERNS,
    DiscoveryConfig,
    DiscoveryError,
    normalize_exclude_patterns,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select a bounded, evidence-backed repository context for an agent task."
    )
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--task", help="Task/query text used only for evidence ranking.")
    task.add_argument(
        "--task-file", type=Path, help="UTF-8 file containing the task/query text."
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        default=[],
        help="Repository subtree to consider. Repeatable. Default: repository root.",
    )
    parser.add_argument(
        "--include-suffix",
        action="append",
        default=[],
        help="File suffix to include, e.g. .py or ts. Repeatable. Defaults to common code/text suffixes.",
    )
    parser.add_argument(
        "--artifact",
        "--artifact-path",
        dest="artifact",
        type=Path,
        default=Path(".agent-artifacts/context-focus.json"),
    )
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--max-files", type=int, default=8)
    parser.add_argument("--max-lines", type=int, default=3000)
    parser.add_argument("--max-bytes", type=int, default=250_000)
    parser.add_argument("--max-tokens", type=int, default=50_000)
    parser.add_argument("--scan-max-files", type=int, default=1000)
    parser.add_argument("--scan-max-bytes", type=int, default=25_000_000)
    parser.add_argument("--scan-max-file-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-anchors-per-file", type=int, default=8)
    parser.add_argument(
        "--repository-intelligence-path",
        type=Path,
        default=None,
        help="Optional provider-neutral JSON ranking hints (version 1). Relative paths are repository-relative.",
    )
    parser.add_argument(
        "--repository-intelligence-max-bytes", type=int, default=5_000_000
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
    parser.add_argument(
        "--exclude-path",
        action="append",
        default=[],
        help="Repository-relative exclusion glob.",
    )
    parser.add_argument("--no-default-excludes", action="store_true")
    parser.add_argument("--git-timeout-seconds", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.task_file is not None:
            task = args.task_file.read_text(encoding="utf-8")
        else:
            task = args.task
        repository_root = args.repository_root.resolve()
        roots = tuple(args.root) if args.root else (Path("."),)
        suffixes = (
            tuple(args.include_suffix)
            if args.include_suffix
            else DEFAULT_CONTEXT_SUFFIXES
        )
        artifact_path = (
            args.artifact
            if args.artifact.is_absolute()
            else repository_root / args.artifact
        )
        intelligence_path = args.repository_intelligence_path
        if intelligence_path is not None and not intelligence_path.is_absolute():
            intelligence_path = repository_root / intelligence_path
        exclusions = list(
            DEFAULT_EXCLUDE_PATTERNS if not args.no_default_excludes else ()
        )
        exclusions.extend(args.exclude_path)
        discovery = DiscoveryConfig(
            mode=args.discovery_mode,
            untracked_policy=args.untracked_policy,
            ignored_policy=args.ignored_policy,
            symlink_policy=args.symlink_policy,
            exclude_patterns=normalize_exclude_patterns(exclusions),
            git_timeout_seconds=args.git_timeout_seconds,
        )
        payload = context_focus_audit(
            task=task,
            repository_root=repository_root,
            roots=roots,
            suffixes=suffixes,
            artifact_path=artifact_path,
            context_budget=ContextBudget(
                max_files=args.max_files,
                max_lines=args.max_lines,
                max_bytes=args.max_bytes,
                max_tokens=args.max_tokens,
            ),
            scan_budget=ScanBudget(
                max_files=args.scan_max_files,
                max_bytes=args.scan_max_bytes,
                max_file_bytes=args.scan_max_file_bytes,
                max_anchors_per_file=args.max_anchors_per_file,
            ),
            discovery_config=discovery,
            repository_intelligence_path=intelligence_path,
            repository_intelligence_max_bytes=args.repository_intelligence_max_bytes,
        )
    except (ContextFocusError, DiscoveryError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc

    if args.quiet:
        return
    summary = {
        "artifact": artifact_path.as_posix(),
        "selected": payload["interpretation"]["selected_targets"],
        "selected_count": payload["derived"]["selected_count"],
        "files_read": payload["economics"]["files_read"],
        "bytes_read": payload["economics"]["bytes_read"],
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
