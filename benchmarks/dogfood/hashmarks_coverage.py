"""Read-only coverage and candidate triage derived from a Hashmarks dogfood audit.

This consumer never creates evaluation tasks, guesses source owners, approves an
oracle, or qualifies experimental outcomes. Candidates are review prompts only.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Mapping

from benchmarks.harness.identity import digest

SOURCE_SCHEMA = "evalshot.hashmarks-dogfood-audit.v1"
COVERAGE_SCHEMA = "evalshot.hashmarks-dogfood-coverage.v1"
_REPOSITORY = "github.com/hugloss/hashmarks"
_MEMBERSHIP = frozenset({"ASSIGNED", "UNASSIGNED", "UNKNOWN"})
_SOURCE_VERIFIED = frozenset({"VERIFIED_PATH", "VERIFIED_COMMIT"})
_MAX_INPUT_BYTES = 4_000_000


class CoverageError(ValueError):
    """Malformed source audit: do not derive a misleading coverage report."""


def _required_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoverageError(f"invalid {field}")
    return value


def _rows(document: Mapping[str, object]) -> list[dict[str, Any]]:
    if document.get("schema") != SOURCE_SCHEMA:
        raise CoverageError("unsupported source audit schema")
    if document.get("repository") != _REPOSITORY:
        raise CoverageError("source audit does not describe Hashmarks")
    if document.get("qualification") != "MODEL_FREE_ONLY":
        raise CoverageError("unexpected source audit qualification")
    rows = document.get("tasks")
    summary = document.get("summary")
    if not isinstance(rows, list) or not rows or not isinstance(summary, Mapping):
        raise CoverageError("missing source audit tasks or summary")
    if type(summary.get("tasks")) is not int or summary["tasks"] != len(rows):
        raise CoverageError("source audit task count disagrees with records")

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise CoverageError(f"tasks[{index}] is not an object")
        row = {}
        for key in ("suite", "task", "family", "mode", "commit", "task_sha256",
                    "experiment_membership", "source", "review"):
            row[key] = _required_str(raw.get(key), f"tasks[{index}].{key}")
        if row["experiment_membership"] not in _MEMBERSHIP:
            raise CoverageError(f"tasks[{index}] has unknown experiment membership")
        key = (row["suite"], row["task"])
        if key in seen:
            raise CoverageError("duplicate suite/task inventory entry")
        seen.add(key)
        problems = raw.get("problems")
        if not isinstance(problems, list) or any(
            not isinstance(problem, str) or not problem.strip() for problem in problems
        ):
            raise CoverageError(f"tasks[{index}] has invalid problem list")
        row["problems"] = sorted(set(problems))
        row["oracle_path"] = raw.get("oracle_path")
        if row["oracle_path"] is not None and not isinstance(row["oracle_path"], str):
            raise CoverageError(f"tasks[{index}] has invalid oracle path")
        result.append(row)
    return result


def build_coverage(document: Mapping[str, object]) -> dict[str, Any]:
    """Describe observed coverage only, never promise missing oracles are valid."""
    rows = _rows(document)
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        families[row["family"]].append(row)

    matrix: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    def nominate(kind: str, *, family: str, suite: str | None = None,
                 task: str | None = None, commit: str | None = None,
                 reason: str, next_action: str) -> None:
        basis = {"kind": kind, "family": family, "suite": suite,
                 "task": task, "commit": commit}
        candidates.append({
            "id": "sha256:" + digest(basis),
            "kind": kind,
            "family": family,
            "suite": suite,
            "task": task,
            "commit": commit,
            "basis": reason,
            "next_action": next_action,
            "review_status": "PROPOSAL_ONLY",
            "oracle_status": "NOT_ESTABLISHED",
        })

    for family in sorted(families):
        group = families[family]
        membership = Counter(row["experiment_membership"] for row in group)
        assigned = [row for row in group if row["experiment_membership"] == "ASSIGNED"]
        assigned_cases = {(row["commit"], row["task"]) for row in assigned}
        unique_cases = {(row["commit"], row["task"]) for row in group}
        matrix.append({
            "family": family,
            "task_entries": len(group),
            "distinct_task_identities": len(unique_cases),
            "assigned_entries": membership["ASSIGNED"],
            "unassigned_entries": membership["UNASSIGNED"],
            "unknown_membership_entries": membership["UNKNOWN"],
            "distinct_assigned_task_identities": len(assigned_cases),
            "modes": dict(sorted(Counter(row["mode"] for row in group).items())),
            "verified_source_entries": sum(
                row["source"] in _SOURCE_VERIFIED for row in group
            ),
        })
        if not assigned_cases and membership["UNASSIGNED"] and not membership["UNKNOWN"]:
            nominate(
                "FAMILY_WITHOUT_ASSIGNED_TASK",
                family=family,
                reason="observed in inventory but absent from known assigned Hashmarks tasks",
                next_action="Review scope and propose a separately frozen, independently reviewed case if useful",
            )
        elif len(assigned_cases) == 1 and not membership["UNKNOWN"]:
            nominate(
                "SINGLE_ASSIGNED_TASK_FAMILY",
                family=family,
                reason="only one distinct assigned commit/task identity observed",
                next_action="Investigate whether a different behavior boundary merits independent task design",
            )

    for row in sorted(rows, key=lambda x: (x["suite"], x["task"])):
        if row["experiment_membership"] != "UNASSIGNED":
            continue
        if row["problems"]:
            nominate(
                "DORMANT_TASK_NEEDS_REVIEW",
                family=row["family"], suite=row["suite"], task=row["task"],
                commit=row["commit"],
                reason="unassigned task has source-audit problems: " + ", ".join(row["problems"]),
                next_action="Independently review the frozen source mismatch; do not rewrite historical evidence",
            )
        else:
            nominate(
                "UNASSIGNED_CASE_FOR_TRIAGE",
                family=row["family"], suite=row["suite"], task=row["task"],
                commit=row["commit"],
                reason=(
                    "source identity checked" if row["source"] in _SOURCE_VERIFIED
                    else "source identity has not been established"
                ),
                next_action="Review eligibility, independent oracle, and experimental headroom before freezing a new case",
            )

    candidates.sort(key=lambda row: (row["kind"], row["family"],
                                      row["suite"] or "", row["task"] or ""))
    return {
        "schema": COVERAGE_SCHEMA,
        "qualification": "DESCRIPTIVE_ONLY",
        "source_audit_identity": "sha256:" + digest(document),
        "source_audit_passed": (
            document.get("requirements", {}).get("audit_passed")
            if isinstance(document.get("requirements"), Mapping) else None
        ),
        "summary": {
            "task_entries": len(rows),
            "observed_families": len(families),
            "assigned_entries": sum(row["experiment_membership"] == "ASSIGNED" for row in rows),
            "unassigned_entries": sum(row["experiment_membership"] == "UNASSIGNED" for row in rows),
            "unknown_membership_entries": sum(row["experiment_membership"] == "UNKNOWN" for row in rows),
            "candidate_review_items": len(candidates),
        },
        "family_matrix": matrix,
        "candidate_review_queue": candidates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True, type=Path, help="existing source audit JSON")
    parser.add_argument("--output", type=Path, help="new review-only coverage JSON")
    args = parser.parse_args(argv)
    try:
        if args.output is not None and args.output.resolve() == args.audit.resolve():
            raise CoverageError("coverage output must not replace its source audit")
        if args.audit.stat().st_size > _MAX_INPUT_BYTES:
            raise CoverageError("source audit exceeds 4,000,000 byte limit")
        document = json.loads(args.audit.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise CoverageError("source audit root must be an object")
        coverage = build_coverage(document)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        parser.error(str(exc))
    raw = json.dumps(coverage, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
