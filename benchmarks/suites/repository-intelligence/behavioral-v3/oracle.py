"""Independent oracle for behavioral-v3 agent outcomes."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CASES = json.loads(r"""{
    "post_change-00": {
        "expected": {
            "changed_path": "benchmark_case/post_change_00/src/engine.py",
            "verification_path": "benchmark_case/post_change_00/tests/test_engine.py",
            "unrelated_reusable": true
        },
        "file_checks": [
            {
                "path": "benchmark_case/post_change_00/src/engine.py",
                "contains": "return \"new\" if value == \"accepted\" else value"
            }
        ]
    },
    "post_change-01": {
        "expected": {
            "changed_path": "benchmark_case/post_change_01/src/route.py",
            "new_owner": "benchmark_case/post_change_01/src/engine_b.py",
            "verification_path": "benchmark_case/post_change_01/tests/test_route.py"
        },
        "file_checks": [
            {
                "path": "benchmark_case/post_change_01/src/route.py",
                "contains": "from .engine_b import widget"
            }
        ]
    },
    "change_impact-00": {
        "expected": {
            "changed_path": "benchmark_case/change_impact_00/src/core.py",
            "implementation_impact": [
                "benchmark_case/change_impact_00/src/service.py",
                "benchmark_case/change_impact_00/src/route.py"
            ],
            "verification_impact": [
                "benchmark_case/change_impact_00/tests/test_route.py"
            ]
        },
        "file_checks": [
            {
                "path": "benchmark_case/change_impact_00/src/core.py",
                "contains": "return \"new\""
            }
        ]
    },
    "change_impact-01": {
        "expected": {
            "changed_path": "benchmark_case/change_impact_01/src/engine.ts",
            "implementation_impact": [
                "benchmark_case/change_impact_01/src/service.ts",
                "benchmark_case/change_impact_01/src/route.ts"
            ],
            "verification_impact": [
                "benchmark_case/change_impact_01/tests/route.test.ts"
            ]
        },
        "file_checks": [
            {
                "path": "benchmark_case/change_impact_01/src/engine.ts",
                "contains": "return \"new\";"
            }
        ]
    },
    "verification-00": {
        "expected": {
            "path": "benchmark_case/verification_00/tests/feature0042/test_contract.py",
            "runner": "pytest",
            "scope": "test-node"
        },
        "file_checks": []
    },
    "verification-01": {
        "expected": {
            "path": "benchmark_case/verification_01/widget/widget_test.go",
            "runner": "go-test"
        },
        "file_checks": []
    },
    "dependency_delta-00": {
        "expected": {
            "component": "dummy-dep",
            "transition": "version-selection",
            "old_version": "1.0.0",
            "new_version": "2.0.0",
            "causation": "not-inferred"
        },
        "file_checks": []
    },
    "dependency_delta-01": {
        "expected": {
            "component": "example.fixture:dummy-dep",
            "transition": "relationship-only",
            "selection_changed": false,
            "relationship_changed": true
        },
        "file_checks": []
    }
}""")


def _final_message() -> object:
    path = Path(os.environ["BENCHMARK_OBSERVATION_PATH"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    message = payload.get("payload", {}).get("final_message")
    if not isinstance(message, str):
        raise ValueError("agent final_message is missing")
    return json.loads(message)


def _grade(case_id: str) -> tuple[bool, str]:
    case = CASES[case_id]
    actual = _final_message()
    if actual != case["expected"]:
        return False, "final JSON differs from frozen independent oracle"
    for check in case["file_checks"]:
        text = Path(check["path"]).read_text(encoding="utf-8")
        if check["contains"] not in text:
            return False, f"expected edited bytes missing from {check['path']}"
    return True, "passed"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in {"health", "grade"}:
        print("usage: oracle.py {health|grade} CASE", file=sys.stderr)
        return 2
    action, case_id = args
    if case_id not in CASES:
        print(f"unknown case: {case_id}", file=sys.stderr)
        return 2
    if action == "health":
        return 0
    try:
        passed, reason = _grade(case_id)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"passed": False, "reason": str(exc)}))
        return 1
    print(json.dumps({"passed": passed, "reason": reason}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
