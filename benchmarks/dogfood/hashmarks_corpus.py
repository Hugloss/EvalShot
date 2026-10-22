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
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

from benchmarks.harness.identity import digest

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
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return result.returncode == 0, result.stdout.strip()


def _safe_file(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    # Git pathspecs interpret *, ?, and [] as matches, not literal paths.
    # Control characters are not valid audit input; Git would escape them
    # in diagnostics, confusing a proof about one exact source filename.
    if any(c in "*?[]" or ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in (".", "..") for part in value.split("/"))


def _canonical_review_ready(suite_root: Path) -> bool:
    """Delegate the whole-suite review decision to EvalShot's existing owners."""
    from benchmarks.harness.oracle_reviews import validate_oracle_reviews
    from benchmarks.harness.suite import load_suite

    try:
        suite = load_suite(suite_root)
        validate_oracle_reviews(suite, require_complete=True)
    except (ValueError, OSError):
        return False
    return True


def audit(
    suites: Path,
    *,
    checkout: Path | None = None,
    require_source: bool = False,
    require_reviewed: bool = False,
    gate_assigned_only: bool = False,
) -> dict[str, Any]:
    """Inventory real, frozen Hashmarks tasks without altering their authority."""
    if require_source and checkout is None:
        raise ValueError("--require-source requires --checkout")
    if not suites.is_dir():
        raise ValueError("suite root does not exist")
    if checkout is not None:
        if not checkout.is_dir():
            raise ValueError("--checkout must name an existing directory")
        usable, inside = _git(checkout, "rev-parse", "--is-inside-work-tree")
        if not usable or inside != "true":
            raise ValueError("--checkout must be a readable Git working tree")

    records: list[dict[str, Any]] = []
    families: Counter[str] = Counter()
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    semantic_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    source_memo: dict[tuple[str, str, str], str] = {}
    assignment_memo: dict[Path, set[str] | None] = {}

    for task_path in sorted(suites.glob("*/tasks/*.json")):
        task = _read(task_path)
        repo = task.get("repository")
        if not isinstance(repo, dict) or _source_name(repo.get("url")) != SOURCE_REPOSITORY:
            continue
        suite = task_path.parent.parent
        task_id = task.get("id")
        if suite not in assignment_memo:
            manifest = suite / "experiment.json"
            if manifest.is_file():
                selection = _read(manifest).get("tasks")
                assignment_memo[suite] = (
                    set(selection)
                    if isinstance(selection, list) and all(isinstance(x, str) for x in selection)
                    else None
                )
            else:
                assignment_memo[suite] = None
        assignments = assignment_memo[suite]
        membership = (
            "UNKNOWN" if assignments is None else
            "ASSIGNED" if task_id in assignments else "UNASSIGNED"
        )
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
                before = len(problems)
                if review.get("repository") != repo:
                    problems.append("REVIEW_SOURCE_MISMATCH")
                if owner_path is not None and review.get("owner") != {
                    "path": owner_path, "symbol": owner_symbol
                }:
                    problems.append("REVIEW_OWNER_MISMATCH")
                review_status = "MISMATCH" if len(problems) != before else "MATCH"

        source_status = "NOT_CHECKED"
        if checkout is not None:
            if "INVALID_COMMIT" in problems or "INVALID_TREE" in problems:
                source_status = "INVALID_IDENTITY"
            elif "INVALID_ORACLE_PATH" in problems:
                source_status = "INVALID_PATH"
            else:
                source_key = (commit, tree, owner_path if isinstance(owner_path, str) else "")
                if source_key not in source_memo:
                    # A 40-hex tree ID can satisfy `^{tree}`, so check the
                    # object kind before admitting a claimed Git commit.
                    typed, object_type = _git(checkout, "cat-file", "-t", commit)
                    if not typed:
                        source_memo[source_key] = "SOURCE_UNAVAILABLE"
                    elif object_type != "commit":
                        source_memo[source_key] = "SOURCE_NOT_COMMIT"
                    else:
                        ok, found = _git(checkout, "rev-parse", "--verify", f"{commit}^{{tree}}")
                        if not ok:
                            source_memo[source_key] = "SOURCE_UNAVAILABLE"
                        elif found != tree:
                            source_memo[source_key] = "TREE_MISMATCH"
                        elif owner_path is not None:
                            # ls-tree preserves modes and emits a NUL-delimited
                            # exact path. Merely matching a Git pathspec is NOT
                            # evidence that the requested filename exists.
                            found_path, listing = _git(
                                checkout, "ls-tree", "--full-tree", "-z", commit, "--", owner_path
                            )
                            if not found_path or not listing:
                                source_memo[source_key] = "ORACLE_PATH_MISSING"
                            else:
                                entries = [entry for entry in listing.split("\0") if entry]
                                parsed = [entry.split("\t", 1) for entry in entries]
                                if (
                                    len(parsed) != 1 or len(parsed[0]) != 2
                                    or parsed[0][1] != owner_path
                                ):
                                    source_memo[source_key] = "ORACLE_PATH_MISMATCH"
                                else:
                                    mode_and_type = parsed[0][0].split(" ", 2)[:2]
                                    source_memo[source_key] = (
                                        "VERIFIED_PATH"
                                        if mode_and_type in (["100644", "blob"], ["100755", "blob"])
                                        else "ORACLE_PATH_NOT_FILE"
                                    )
                        else:
                            source_memo[source_key] = "VERIFIED_COMMIT"
                source_status = source_memo[source_key]
                if source_status in {
                    "SOURCE_NOT_COMMIT", "TREE_MISMATCH", "ORACLE_PATH_MISSING",
                    "ORACLE_PATH_NOT_FILE", "ORACLE_PATH_MISMATCH",
                }:
                    problems.append(source_status)
                if require_source and source_status == "SOURCE_UNAVAILABLE":
                    problems.append("SOURCE_UNAVAILABLE")

        task_hash = hashlib.sha256(task_path.read_bytes()).hexdigest()
        if isinstance(commit, str) and isinstance(task_id, str):
            key = (commit, task_id)
            groups[key].add(task_hash)
            # Use EvalShot's canonical task identity, not a second JSON
            # normalization invented by the dogfood inventory.
            semantic_groups[key].add(digest(task))
        family = str(task.get("family", "UNSPECIFIED"))
        families[family] += 1
        records.append({
            "suite": suite.name,
            "task": task_id,
            "family": family,
            "mode": task.get("mode"),
            "experiment_membership": membership,
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

    # The existing EvalShot suite loader and oracle-review validator alone own
    # review admission. The dogfood inventory must never duplicate or relax that
    # decision by equating a matched transcript with approved independent review.
    canonical_reviews: dict[str, str] = {}
    if require_reviewed:
        for suite_name in sorted({str(row["suite"]) for row in records}):
            approved = _canonical_review_ready(suites / suite_name)
            canonical_reviews[suite_name] = "APPROVED" if approved else "BLOCKED"
            if not approved:
                for row in records:
                    if row["suite"] == suite_name and row["experiment_membership"] != "UNASSIGNED":
                        row["problems"].append("CANONICAL_REVIEW_NOT_APPROVED")

    variants = [
        {
            "commit": commit,
            "task": task_id,
            "distinct_frozen_definitions": len(hashes),
            "distinct_semantic_definitions": len(semantic_groups[(commit, task_id)]),
            "kind": (
                "SEMANTIC_VARIANT"
                if len(semantic_groups[(commit, task_id)]) > 1
                else "BYTE_ONLY_VARIANT"
            ),
        }
        for (commit, task_id), hashes in sorted(groups.items()) if len(hashes) > 1
    ]
    issues = sum(bool(row["problems"]) for row in records)
    gated = [
        row for row in records
        if not gate_assigned_only or row["experiment_membership"] != "UNASSIGNED"
    ]
    gated_issues = sum(bool(row["problems"]) for row in gated)
    blocked_reviews = sorted(
        suite_name for suite_name, state in canonical_reviews.items()
        if state != "APPROVED"
    )
    # Review qualification belongs to EvalShot's suite validator, not to
    # the subset of Hashmarks-source tasks in this inventory. A mixed-source
    # suite may have a rejected oracle elsewhere, or only dormant Hashmarks
    # tasks, yet its canonical review gate still blocks this strict audit.
    audit_passed = gated_issues == 0 and not blocked_reviews
    return {
        "schema": "evalshot.hashmarks-dogfood-audit.v1",
        "repository": SOURCE_REPOSITORY,
        "qualification": "MODEL_FREE_ONLY",
        "summary": {
            "tasks": len(records), "distinct_commits": len({r["commit"] for r in records}),
            "families": dict(sorted(families.items())), "tasks_with_problems": issues,
            "gated_tasks_with_problems": gated_issues,
            "ungated_tasks_with_problems": issues - gated_issues,
            "unreviewed_tasks": sum(r["review"] in {"NO_REVIEW_FILE", "NO_TASK_REVIEW"} for r in records),
            "unreviewed_assigned_tasks": sum(
                r["experiment_membership"] == "ASSIGNED"
                and r["review"] in {"NO_REVIEW_FILE", "NO_TASK_REVIEW"}
                for r in records
            ),
            "unassigned_tasks": sum(r["experiment_membership"] == "UNASSIGNED" for r in records),
            "unknown_assignment_tasks": sum(r["experiment_membership"] == "UNKNOWN" for r in records),
            "unchecked_sources": sum(r["source"] in {"NOT_CHECKED", "SOURCE_UNAVAILABLE"} for r in records),
            "blocked_review_suites": len(blocked_reviews),
        },
        "requirements": {
            "source_required": require_source,
            "canonical_review_required": require_reviewed,
            "gate_assigned_only": gate_assigned_only,
            "audit_passed": audit_passed,
            "blocked_review_suites": blocked_reviews,
        },
        "canonical_review_qualification": canonical_reviews if require_reviewed else "NOT_CHECKED",
        "variants": variants,
        "tasks": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", type=Path, default=Path("benchmarks/suites/repository-intelligence"))
    parser.add_argument("--checkout", type=Path, help="existing local Hashmarks checkout with historical commits")
    parser.add_argument("--require-source", action="store_true", help="fail if any referenced commit is unavailable")
    parser.add_argument("--require-reviewed", action="store_true", help="delegate review readiness to the canonical EvalShot validator")
    parser.add_argument(
        "--gate-assigned-only", action="store_true",
        help="gate assigned and unknown-membership tasks; keep unassigned failures diagnostic",
    )
    parser.add_argument("--output", type=Path, help="write identical JSON report to this path")
    args = parser.parse_args(argv)
    try:
        result = audit(
            args.suite_root, checkout=args.checkout, require_source=args.require_source,
            require_reviewed=args.require_reviewed,
            gate_assigned_only=args.gate_assigned_only,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    report = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")
    return 0 if result["requirements"]["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
