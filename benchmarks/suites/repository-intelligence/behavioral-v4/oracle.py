"""Independent, case-atom oracle for behavioral v4 agent outcomes."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CASES = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))["cases"]


def grade_answer(case_id: str, actual: object, workspace: Path) -> dict[str, Any]:
    case = CASES[case_id]
    expected = case["expected"]
    if not isinstance(actual, dict):
        actual = {}
    correct = sorted(
        key for key, value in expected.items() if key in actual and actual[key] == value
    )
    missing = sorted(key for key in expected if key not in actual)
    incorrect = sorted(
        key for key, value in expected.items() if key in actual and actual[key] != value
    )
    extra = sorted(set(actual) - set(expected))
    false_authority = sorted(set(incorrect) & set(case["authority_fields"]))
    failed_files = []
    for check in case["file_checks"]:
        path = (workspace / check["path"]).resolve()
        try:
            path.relative_to(workspace.resolve())
            if check["contains"] not in path.read_text(encoding="utf-8"):
                failed_files.append(check["path"])
        except (OSError, ValueError):
            failed_files.append(check["path"])
    authority = (
        "FALSE_AUTHORITY" if false_authority else "UNSUPPORTED" if extra else "BOUNDED"
    )
    resolution = (
        "COMPLETE"
        if len(correct) == len(expected) and not extra and not failed_files
        else "PARTIAL"
        if correct
        else "WRONG"
    )
    provenance = case["provenance_fields"]
    evidence = (
        "NOT_APPLICABLE"
        if not provenance
        else "PRESERVED"
        if all(key in correct for key in provenance)
        else "THIN"
    )
    passed = authority == "BOUNDED" and resolution == "COMPLETE"
    reason = (
        "all frozen claims and edited bytes verified"
        if passed
        else "missing="
        + ",".join(missing)
        + " incorrect="
        + ",".join(incorrect)
        + " extra="
        + ",".join(extra)
        + " failed_files="
        + ",".join(failed_files)
    )
    return {
        "schema": "agents-cookbook-lexigram-oracle.v1",
        "passed": passed,
        "reason": reason,
        "rubric": {
            "authority": authority,
            "resolution": resolution,
            "evidence": evidence,
            "correct_fields": correct,
            "missing_fields": missing,
            "incorrect_fields": incorrect,
            "false_authority_fields": false_authority,
            "failed_file_checks": failed_files,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in {"health", "grade"} or args[1] not in CASES:
        print("usage: oracle.py {health|grade} CASE", file=sys.stderr)
        return 2
    if args[0] == "health":
        return 0
    try:
        observation = json.loads(
            Path(os.environ["BENCHMARK_OBSERVATION_PATH"]).read_text(encoding="utf-8")
        )
        message = observation.get("payload", {}).get("final_message")
        actual = json.loads(message) if isinstance(message, str) else None
    except (OSError, ValueError, KeyError):
        actual = None
    result = grade_answer(args[1], actual, Path.cwd())
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
