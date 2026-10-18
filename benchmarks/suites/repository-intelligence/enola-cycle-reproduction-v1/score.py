"""Verified functional/cycle counts for the adapted Enola task."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from benchmarks.harness.bundle import verify_bundle
from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.selection import select_definitions
from benchmarks.harness.suite import load_suite

CODES = {
    0: (True, False),
    10: (True, True),
    11: (False, False),
    12: (False, True),
}


def score(
    results: Path,
    *,
    agents: tuple[str, ...] = (),
    require_complete: bool = True,
) -> dict[str, dict[str, int]]:
    suite = load_suite(Path(__file__).resolve().parent)
    selected = {
        str(row["definition_id"]) for row in select_definitions(suite, agents=agents)
    }
    build_report(
        suite=suite,
        results_root=results,
        require_complete=require_complete,
        selected_definitions=selected,
    )
    groups: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "valid": 0,
            "functional_success": 0,
            "cycle_shipped": 0,
            "functional_and_acyclic": 0,
            "incomplete_or_invalid": 0,
        }
    )
    for directory in sorted(results.iterdir()) if results.exists() else []:
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        valid, reason = verify_bundle(directory)
        if not valid:
            raise ValueError(f"invalid result bundle {directory}: {reason}")
        receipt = json.loads((directory / "result.json").read_text())
        if receipt["definition_id"] not in selected:
            continue
        condition = receipt["condition"]["id"]
        row = groups[condition]
        if receipt["status"] not in {"PASS", "FAIL"}:
            row["incomplete_or_invalid"] += 1
            continue
        events = [
            json.loads(line)
            for line in (directory / "events.jsonl").read_text().splitlines()
        ]
        grades = [event for event in events if event["kind"] == "oracle.graded"]
        if len(grades) != 1:
            raise ValueError(f"missing oracle grade in {directory}")
        code = grades[0]["payload"]["payload"]["process"]["return_code"]
        if code not in CODES:
            raise ValueError(f"unknown oracle grade {code} in {directory}")
        works, cycle = CODES[code]
        row["valid"] += 1
        row["functional_success"] += works
        row["cycle_shipped"] += cycle
        row["functional_and_acyclic"] += works and not cycle
    return dict(sorted(groups.items()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    try:
        result = score(
            args.results,
            agents=tuple(args.agent),
            require_complete=not args.allow_incomplete,
        )
    except ReportError as exc:
        raise SystemExit(f"cycle score unavailable: {exc}") from exc
    print(json.dumps(result, indent=2, sort_keys=True))
