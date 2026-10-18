from __future__ import annotations

import argparse
import json
from pathlib import Path

from .quality_debt import baseline_document, quality_debt_audit


def _limits(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        rule, sep, raw = value.partition("=")
        if not sep:
            raise argparse.ArgumentTypeError("--limit requires RULE=INTEGER")
        result[rule] = int(raw)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Measure configured static-analysis debt without making policy decisions."
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--limit", action="append", required=True)
    parser.add_argument("--max-file-lines", type=int)
    parser.add_argument("--file-line-root", action="append")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--artifact", "--artifact-path", dest="artifact", type=Path)
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-stdout-bytes", type=int, default=2_000_000)
    parser.add_argument("--max-stderr-bytes", type=int, default=200_000)
    args = parser.parse_args(argv)
    payload = quality_debt_audit(
        repository_root=args.repository_root,
        roots=tuple(args.root),
        limits=_limits(args.limit),
        max_file_lines=args.max_file_lines,
        file_line_roots=tuple(args.file_line_root) if args.file_line_root else None,
        excludes=tuple(args.exclude),
        baseline_path=args.baseline,
        artifact_path=args.artifact,
        timeout_seconds=args.timeout_seconds,
        max_stdout_bytes=args.max_stdout_bytes,
        max_stderr_bytes=args.max_stderr_bytes,
    )
    if args.write_baseline:
        root = args.repository_root.resolve()
        target = (
            args.write_baseline
            if args.write_baseline.is_absolute()
            else root / args.write_baseline
        )
        resolved = target.resolve()
        resolved.relative_to(root)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        temp = resolved.with_suffix(resolved.suffix + ".tmp")
        temp.write_text(
            json.dumps(baseline_document(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp.replace(resolved)
    if args.quiet:
        return
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        summary = payload["derived"]["summary"]
        print(
            f"artifact={args.artifact.as_posix() if args.artifact else '-'} "
            f"files={len(summary['files'])} excess={summary['excess']} "
            f"locations={summary['locations']} rule_findings={summary['rule_findings']}"
        )


if __name__ == "__main__":
    main()
