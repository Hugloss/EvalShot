from __future__ import annotations

import argparse
import json
from pathlib import Path

from .hotspot_focus import DEFAULT_RANKING, hotspot_focus_audit


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Rank bounded repository hotspots for investigation."
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--tests-root", type=Path)
    parser.add_argument(
        "--package-name",
        default=None,
        help="Import package name. Default: source-root basename.",
    )
    parser.add_argument("--tests-package-name", default="tests")
    parser.add_argument("--artifact", "--artifact-path", dest="artifact", type=Path)
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--rank-by", default=",".join(DEFAULT_RANKING))
    parser.add_argument("--history-max-commits", type=int, default=500)
    parser.add_argument("--history-max-bytes", type=int, default=8_000_000)
    parser.add_argument("--git-timeout-seconds", type=float, default=10.0)
    parser.add_argument(
        "--history-policy", choices=("auto", "required", "disabled"), default="auto"
    )
    parser.add_argument(
        "--discovery-mode", choices=("auto", "git", "filesystem"), default="auto"
    )
    args = parser.parse_args(argv)
    payload = hotspot_focus_audit(
        repository_root=args.repository_root,
        source_root=args.source_root,
        tests_root=args.tests_root,
        package_name=args.package_name,
        tests_package_name=args.tests_package_name,
        artifact_path=args.artifact,
        top_n=args.top,
        ranking_dimensions=tuple(
            item.strip() for item in args.rank_by.split(",") if item.strip()
        ),
        history_max_commits=args.history_max_commits,
        history_max_bytes=args.history_max_bytes,
        git_timeout_seconds=args.git_timeout_seconds,
        history_policy=args.history_policy,
        discovery_mode=args.discovery_mode,
    )
    if args.quiet:
        return
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for index, candidate in enumerate(payload["candidates"], 1):
        facts = candidate["facts"]
        print(
            f"{index}. {candidate['target']} "
            f"churn={facts['churn_commits']} fan-in={facts['fan_in']} "
            f"branches={facts['branch_points']} lines={facts['source_lines']} "
            f"tests={facts['confirmed_tests']}"
        )


if __name__ == "__main__":
    main()
