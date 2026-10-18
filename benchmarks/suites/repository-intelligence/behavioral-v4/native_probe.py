"""Call the candidate Hashmarks public API with a frozen, isolated case."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hashmarks import CodeMap
from hashmarks.adapters import (
    maven_dependency_observation,
    uv_lock_dependency_observation,
)

DIRECT_CASES = (
    "post_change-00",
    "post_change-01",
    "change_impact-00",
    "change_impact-03",
    "verification-00",
    "verification-02",
    "dependency_delta-00",
    "dependency_delta-01",
    "correlation-00",
    "correlation-01",
    "declarations-00",
    "declarations-01",
    "freshness-00",
    "freshness-01",
    "negative_bounds-00",
    "negative_bounds-02",
)


def _bundle(*anchors: dict, provider: str = "runtime-log") -> list[dict]:
    return [
        {
            "bundle_id": "external-observation",
            "producer": {"kind": provider},
            "completeness": "complete",
            "scope": {"kind": "fixture"},
            "truncation": "complete",
            "anchors": list(anchors),
        }
    ]


def _declaration(path: str, value: str, provider: str) -> dict:
    return {
        "declaration_id": provider,
        "value_state": "resolved",
        "value": value,
        "producer": {"provider": provider},
        "evidence": [{"path": path, "start_line": 1, "end_line": 1}],
    }


def _group(declarations: list[dict]) -> dict:
    return {
        "group_id": "python-series",
        "semantic_namespace": "fixture",
        "concept": {"kind": "runtime-series", "identity": "python-minor"},
        "scope": {"repository": "fixture"},
        "correspondence": {
            "state": "declared",
            "basis": {"provider": "fixture", "rule": "same-declared-minor"},
        },
        "declarations": declarations,
        "coverage": {
            "state": "complete",
            "truncation": "complete",
            "expected_declaration_ids": [row["declaration_id"] for row in declarations],
            "scope": {"repository": "fixture"},
            "provenance": {"provider": "fixture"},
        },
    }


def observe(case_id: str, workspace: Path) -> dict:
    base = Path("benchmark_case") / case_id.replace("-", "_")
    state = workspace.parent / "candidate-state"
    with CodeMap(workspace, state_dir=state) as codemap:
        codemap.sync()
        if case_id == "post_change-00":
            task = "change accepted transform behavior and verify focused test"
            previous = codemap.task_evidence(task)
            path = base / "src/engine.py"
            (workspace / path).write_text(
                'def transform(value: str) -> str:\n    return "new" if value == "accepted" else value\n',
                encoding="utf-8",
            )
            delta = codemap.task_post_change_delta(
                task, [str(path)], previous_evidence=previous
            )
            full = codemap.task_evidence(task)
            delta_bytes = len(json.dumps(delta, sort_keys=True).encode())
            full_bytes = len(json.dumps(full, sort_keys=True).encode())
            return {
                "previous": previous,
                "delta": delta,
                "full": full,
                "delta_bytes": delta_bytes,
                "full_bytes": full_bytes,
                "smaller_than_full": delta_bytes < full_bytes,
            }
        if case_id == "post_change-01":
            task = "change route widget behavior and verify route test"
            previous = codemap.task_evidence(task)
            path = base / "src/route.py"
            text = (workspace / path).read_text(encoding="utf-8")
            (workspace / path).write_text(
                text.replace("engine_a", "engine_b"), encoding="utf-8"
            )
            delta = codemap.task_post_change_delta(
                task, [str(path)], previous_evidence=previous
            )
            full = codemap.task_evidence(task)
            delta_bytes = len(json.dumps(delta, sort_keys=True).encode())
            full_bytes = len(json.dumps(full, sort_keys=True).encode())
            return {
                "previous": previous,
                "delta": delta,
                "full": full,
                "delta_bytes": delta_bytes,
                "full_bytes": full_bytes,
                "smaller_than_full": delta_bytes < full_bytes,
            }
        if case_id == "change_impact-00":
            path = base / "src/core.py"
            text = (workspace / path).read_text(encoding="utf-8")
            (workspace / path).write_text(
                text.replace('"old"', '"new"'), encoding="utf-8"
            )
            return codemap.task_change_impact("shared route contract", [str(path)])
        if case_id == "change_impact-03":
            codemap.enrich_projects(
                ("npm-package-graph", "maven-pom-graph", "declared-project-links")
            )
            path = base / "openapi.yaml"
            (workspace / path).write_text("openapi: 3.1.1\n", encoding="utf-8")
            return codemap.task_change_impact("shared OpenAPI contract", [str(path)])
        if case_id.startswith("verification-"):
            path = (
                base / "tests/feature0042/test_contract.py"
                if case_id == "verification-00"
                else base / "tests/widget.test.ts"
            )
            return codemap.verification_plan(
                str(path),
                symbol="test_contract" if case_id == "verification-00" else None,
            )
        if case_id == "dependency_delta-00":
            before = uv_lock_dependency_observation(
                lock=(workspace / base / "before/uv.lock").read_bytes()
            )
            after = uv_lock_dependency_observation(
                lock=(workspace / base / "after/uv.lock").read_bytes()
            )
            left = codemap.dependency_resolution_evidence(before)
            right = codemap.dependency_resolution_evidence(after)
            return codemap.dependency_resolution_delta(left, right)
        if case_id == "dependency_delta-01":
            before = maven_dependency_observation(
                trees={"app": (workspace / base / "before/tree.json").read_bytes()},
                inventories={},
                complete_tree_contexts=("app",),
            )
            after = maven_dependency_observation(
                trees={"app": (workspace / base / "after/tree.json").read_bytes()},
                inventories={},
                complete_tree_contexts=("app",),
            )
            left = codemap.dependency_resolution_evidence(before)
            right = codemap.dependency_resolution_evidence(after)
            return codemap.dependency_resolution_delta(left, right)
        if case_id == "correlation-00":
            return codemap.correlate_evidence(
                _bundle(
                    {
                        "anchor_id": "frame",
                        "path": "/app/" + str(base / "src/worker.py"),
                        "symbol": "process_output_data",
                        "line": 2,
                    }
                ),
                path_mappings=[{"external_prefix": "/app", "repository_prefix": ""}],
            )
        if case_id == "correlation-01":
            return codemap.correlate_evidence(
                _bundle(
                    {
                        "anchor_id": "conflict",
                        "path": str(base / "src/owner.py"),
                        "symbol": "second",
                        "line": 2,
                    }
                )
            )
        if case_id.startswith("declarations-"):
            left = str(base / "pyproject.toml")
            right = str(base / "Dockerfile")
            values = ("3.12", "3.12" if case_id == "declarations-00" else "3.13")
            rows = [
                _declaration(left, values[0], "project-config"),
                _declaration(right, values[1], "container-config"),
            ]
            return codemap.repository_declarations([_group(rows)])
        if case_id.startswith("freshness-"):
            path = base / "src/app.py"
            (workspace / path).write_text(
                "def new_owner():\n    return 2\n", encoding="utf-8"
            )
            if case_id == "freshness-00":
                return codemap.symbol("old_owner")
            return codemap.outline(str(path))
        if case_id == "negative_bounds-00":
            narrow = codemap.task_entry_points("collision_owner", limit=1, per_role=1)
            complete = codemap.task_entry_points(
                "collision_owner", limit=20, per_role=3
            )
            return {"narrow": narrow, "complete": complete}
        if case_id == "negative_bounds-02":
            task = json.loads(
                (workspace / base / "observations/query.json").read_text(
                    encoding="utf-8"
                )
            )["query"]
            return codemap.task_entry_points(task, limit=20)
    raise ValueError(f"no native probe for {case_id}")


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in DIRECT_CASES:
        print("usage: native_probe.py CASE WORKSPACE", file=sys.stderr)
        return 2
    packet = observe(sys.argv[1], Path(sys.argv[2]))
    print(json.dumps(packet, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
