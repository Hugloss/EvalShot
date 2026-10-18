from __future__ import annotations

import argparse
import json
from pathlib import Path

from .coupling_focus import CouplingFocusError, coupling_focus_audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bound historical Git co-change correlation for target paths.",
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--target-path",
        action="append",
        default=[],
        required=True,
        help="Repository-relative target path. Repeat as needed.",
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/coupling-focus.json"),
    )
    parser.add_argument("--history-max-commits", type=int, default=500)
    parser.add_argument("--max-files-per-commit", type=int, default=200)
    parser.add_argument("--min-shared-commits", type=int, default=2)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument(
        "--candidate-suffix",
        action="append",
        default=[],
        help="Restrict candidate files by suffix. Repeat as needed.",
    )
    parser.add_argument("--exclude-path", action="append", default=[])
    parser.add_argument("--no-default-excludes", action="store_true")
    parser.add_argument("--sample-commits-per-candidate", type=int, default=3)
    parser.add_argument("--history-max-bytes", type=int, default=8_000_000)
    parser.add_argument(
        "--all-parents",
        action="store_true",
        help="Scan all reachable history rather than first-parent history.",
    )
    parser.add_argument("--git-timeout-seconds", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        payload = coupling_focus_audit(
            repository_root=args.repository_root,
            target_paths=args.target_path,
            artifact_path=args.artifact_path,
            history_max_commits=args.history_max_commits,
            max_files_per_commit=args.max_files_per_commit,
            min_shared_commits=args.min_shared_commits,
            top_n=args.top_n,
            candidate_suffixes=tuple(args.candidate_suffix) or None,
            exclude_patterns=tuple(args.exclude_path),
            use_default_excludes=not args.no_default_excludes,
            sample_commits_per_candidate=args.sample_commits_per_candidate,
            first_parent=not args.all_parents,
            history_max_bytes=args.history_max_bytes,
            git_timeout_seconds=args.git_timeout_seconds,
        )
    except CouplingFocusError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
