"""Model-free audit of frozen EvalShot tasks using Hashmarks as the source repository.

The evaluator's task/oracle files own the expected answers. This command only
checks source identity and transcription consistency; it cannot certify an oracle
or infer a source owner. No agent, index, or network service is invoked.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

SOURCE_REPOSITORY = "github.com/hugloss/hashmarks"
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _read(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _source_name(url: object) -> str:
    if not isinstance(url, str):
        return ""
    return url.lower().removeprefix("https://").removeprefix("http://").rstrip("/").removesuffix(".git")


def _git(checkout: Path, *args: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "--no-optional-locks", *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return result.returncode == 0, result.stdout.strip()


def _safe_file(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in (".", "..") for part in value.split("/"))


def audit(suites: Path, *, checkout: Path | None = None, require_source: bool = False) -> dict[str, Any]:
    """Inventory real, frozen Hashmarks tasks without altering their authority."""
    if require_source and checkout is None:
        raise ValueError("--require-source requires --checkout")
    if not suites.is_dir():
        raise ValueError("suite root does not exist")

    records: list[dict[str, Any]] = []
    families: Counter[str] = Counter()
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    source_memo: dict[tuple[str, str, str], str] = {}

    for task_path in sorted(suites.glob("*/tasks/*.json")):
        task = _read(task_path)
        repo = task.get("repository")
        if not isinstance(repo, dict) or _source_name(repo.get("url")) != SOURCE_REPOSITORY:
            continue
        suite = task_path.parent.parent
        task_id = task.get("id")
        commit, tree = repo.get("commit"), repo.get("tree")
        expected = (task.get("oracle") or {}).get("configuration", {}).get("expected", {})
        if not isinstance(expected, dict):
            expected = {}
        owner_path = expected.get("path")
        owner_symbol = expected.get("symbol")
        problems: list[str] = []
        if not isinstance(task_id, str) or task_id != task_path.stem:
            problems.append("TASK_ID_MISMATCH")
        if not isinstance(commit, str) or not _SHA.fullmatch(commit):
            problems.append("INVALID_COMMIT")
        if not isinstance(tree, str) or not _SHA.fullmatch(tree):
            problems.append("INVALID_TREE")
        if owner_path is not None and not _safe_file(owner_path):
            problems.append("INVALID_ORACLE_PATH")

        review_path = suite / "qualification" / "oracle-reviews.json"
        review_status = "NO_REVIEW_FILE"
        if review_path.is_file():
            review = _read(review_path).get("tasks", {}).get(task_id)
            if review is None:
                review_status = "NO_TASK_REVIEW"
            else:
                review_status = "MATCH"
                if review.get("repository") != repo:
                    problems.append("REVIEW_SOURCE_MISMATCH")
                if owner_path is not None and review.get("owner") != {
                    "path": owner_path, "symbol": owner_symbol
                }:
                    problems.append("REVIEW_OWNER_MISMATCH")

        source_status = "NOT_CHECKED"
        if checkout is not None:
            if "INVALID_COMMIT" in problems or "INVALID_TREE" in problems:
                source_status = "INVALID_IDENTITY"
            else:
                source_key = (commit, tree, owner_path if isinstance(owner_path, str) else "")
                if source_key not in source_memo:
                    ok, found = _git(checkout, "rev-parse", "--verify", f"{commit}^{{tree}}")
                    if not ok:
                        source_memo[source_key] = "SOURCE_UNAVAILABLE"
                    elif found != tree:
                        source_memo[source_key] = "TREE_MISMATCH"
                    elif owner_path is not None:
                        exists, _ = _git(checkout, "cat-file", "-e", f"{commit}:{owner_path}")
                        source_memo[source_key] = "VERIFIED_PATH" if exists else "ORACLE_PATH_MISSING"
                    else:
                        source_memo[source_key] = "VERIFIED_COMMIT"
                source_status = source_memo[source_key]
                if source_status in {"TREE_MISMATCH", "ORACLE_PATH_MISSING"}:
                    problems.append(source_status)
                if require_source and source_status == "SOURCE_UNAVAILABLE":
                    problems.append("SOURCE_UNAVAILABLE")

        task_hash = hashlib.sha256(task_path.read_bytes()).hexdigest()
        if isinstance(commit, str) and isinstance(task_id, str):
            groups[(commit, task_id)].add(task_hash)
        family = str(task.get("family", "UNSPECIFIED"))
        families[family] += 1
        records.append({
            "suite": suite.name,
            "task": task_id,
            "family": family,
            "mode": task.get("mode"),
            "commit": commit,
            "tree": tree,
            "task_sha256": task_hash,
            "oracle_path": owner_path,
            "oracle_symbol": owner_symbol,
            "review": review_status,
            "source": source_status,
            "problems": sorted(problems),
        })

    if not records:
        raise ValueError("no Hashmarks-source tasks found under suite root")

    variants = [
        {"commit": commit, "task": task_id, "distinct_frozen_definitions": len(hashes)}
        for (commit, task_id), hashes in sorted(groups.items()) if len(hashes) > 1
    ]
    issues = sum(bool(row["problems"]) for row in records)
    return {
        "schema": "evalshot.hashmarks-dogfood-audit.v1",
        "repository": SOURCE_REPOSITORY,
        "qualification": "MODEL_FREE_ONLY",
        "summary": {
            "tasks": len(records), "distinct_commits": len({r["commit"] for r in records}),
            "families": dict(sorted(families.items())), "tasks_with_problems": issues,
            "unreviewed_tasks": sum(r["review"] != "MATCH" for r in records),
            "unchecked_sources": sum(r["source"] in {"NOT_CHECKED", "SOURCE_UNAVAILABLE"} for r in records),
        },
        "variants": variants,
        "tasks": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", type=Path, default=Path("benchmarks/suites/repository-intelligence"))
    parser.add_argument("--checkout", type=Path, help="existing local Hashmarks checkout with historical commits")
    parser.add_argument("--require-source", action="store_true", help="fail if any referenced commit is unavailable")
    parser.add_argument("--output", type=Path, help="write identical JSON report to this path")
    args = parser.parse_args(argv)
    try:
        result = audit(args.suite_root, checkout=args.checkout, require_source=args.require_source)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    report = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")
    return 1 if result["summary"]["tasks_with_problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
