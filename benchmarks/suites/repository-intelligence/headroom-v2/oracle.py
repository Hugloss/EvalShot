"""Independent headroom v2 oracle with separate answer shape observation."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CASES = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))["cases"]

_JSON_FENCE = re.compile(r"```json[ \t]*\r?\n(?P<body>.*?)\r?\n```", re.DOTALL)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def observe_answer(message: object) -> dict[str, object]:
    """Accept one JSON object or one whole json fence; record the prompt format."""
    if not isinstance(message, str):
        return {
            "actual": None,
            "answer_shape": "MISSING",
            "format_compliant": False,
            "semantic_gradeable": False,
            "parse_reason": "final message is missing",
        }
    stripped = message.strip()
    match = _JSON_FENCE.fullmatch(stripped)
    if match:
        shape = "JSON_FENCE"
        payload = match.group("body").strip()
    elif "```" in stripped:
        return {
            "actual": None,
            "answer_shape": "PROSE_OR_MALFORMED",
            "format_compliant": False,
            "semantic_gradeable": False,
            "parse_reason": "expected one bare JSON object or one whole json fence",
        }
    else:
        shape = "BARE_JSON"
        payload = stripped
    try:
        actual = json.loads(payload, object_pairs_hook=_unique_object)
    except (ValueError, json.JSONDecodeError) as exc:
        return {
            "actual": None,
            "answer_shape": "PROSE_OR_MALFORMED",
            "format_compliant": False,
            "semantic_gradeable": False,
            "parse_reason": f"invalid JSON object: {exc}",
        }
    if not isinstance(actual, dict):
        return {
            "actual": None,
            "answer_shape": "PROSE_OR_MALFORMED",
            "format_compliant": False,
            "semantic_gradeable": False,
            "parse_reason": "final message JSON is not an object",
        }
    return {
        "actual": actual,
        "answer_shape": shape,
        "format_compliant": shape == "BARE_JSON",
        "semantic_gradeable": True,
        "parse_reason": None,
    }


def grade_answer(
    case_id: str,
    actual: object,
    workspace: Path,
    *,
    answer_observation: dict[str, object] | None = None,
) -> dict[str, Any]:
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
    gradeable = (
        answer_observation.get("semantic_gradeable") is True
        if answer_observation is not None
        else isinstance(actual, dict)
    )
    passed = gradeable and authority == "BOUNDED" and resolution == "COMPLETE"
    reason = (
        "all frozen claims and edited bytes verified"
        if passed
        else str(answer_observation["parse_reason"])
        if not gradeable and answer_observation is not None
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
            "semantic_gradeable": gradeable,
            "semantic_success": passed if gradeable else None,
            "answer_shape": (
                answer_observation["answer_shape"]
                if answer_observation
                else "DIRECT_OBJECT"
            ),
            "format_compliant": (
                answer_observation["format_compliant"] if answer_observation else True
            ),
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
    except (OSError, ValueError, KeyError):
        message = None
    answer_observation = observe_answer(message)
    result = grade_answer(
        args[1],
        answer_observation["actual"],
        Path.cwd(),
        answer_observation=answer_observation,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
