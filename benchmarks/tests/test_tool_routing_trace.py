from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from benchmarks.__main__ import main as benchmark_main
from benchmarks.tool_routing import routing_artifact_sha256
from benchmarks.tool_routing_trace import (
    CATALOG_CAPTURE_SCHEMA,
    TRACE_SCHEMA,
    exit_code,
    score_trace,
)


_READY_CATALOG = {
    "schema": CATALOG_CAPTURE_SCHEMA,
    "host": "chatgpt",
    "capture_id": "capture-1",
    "tools": [
        {"name": "mcp__hashmarks__task_evidence"},
        {"name": "mcp__GitHub__search"},
        {"name": "mcp__GitHub__fetch_file"},
    ],
}


def _trace(
    calls: list[dict[str, object]],
    *,
    catalog: object = _READY_CATALOG,
) -> dict[str, object]:
    assert isinstance(catalog, dict)
    return {
        "schema": TRACE_SCHEMA,
        "host": str(catalog["host"]),
        "capture_id": str(catalog["capture_id"]),
        "catalog_sha256": routing_artifact_sha256(catalog),
        "calls": calls,
    }


class ToolRoutingTraceTests(unittest.TestCase):
    def test_hashmarks_before_native_search_is_pass(self) -> None:
        score = score_trace(
            catalog_payload=_READY_CATALOG,
            trace_payload=_trace(
                [
                    {
                        "tool": "mcp__hashmarks__task_evidence",
                        "status": "completed",
                        "output": {"schema": "hashmarks.task-evidence.v2"},
                    },
                    {
                        "tool": "mcp__GitHub__search",
                        "status": "completed",
                    },
                ]
            ),
            subject="hashmarks",
            required_tool="hashmarks_task_evidence",
        )

        self.assertEqual(score["outcome"], "PASS")
        self.assertEqual(score["routing_evaluation"]["outcome"], "PASS")
        self.assertTrue(
            score["routing_evaluation"]["required_before_native_discovery"]
        )
        self.assertEqual(
            score["routing_evaluation"]["first_native_discovery_tool"],
            "mcp__GitHub__search",
        )
        self.assertEqual(score["calls"][0]["result_basis"], "captured-output")
        self.assertGreater(score["calls"][0]["result_bytes"], 0)
        self.assertEqual(exit_code(score), 0)

    def test_native_search_before_hashmarks_is_fail(self) -> None:
        score = score_trace(
            catalog_payload=_READY_CATALOG,
            trace_payload=_trace(
                [
                    {
                        "tool": "mcp__GitHub__search",
                        "status": "completed",
                    },
                    {
                        "tool": "mcp__hashmarks__task_evidence",
                        "status": "completed",
                        "output": {"schema": "hashmarks.task-evidence.v2"},
                    },
                ]
            ),
            subject="hashmarks",
            required_tool="hashmarks_task_evidence",
        )

        self.assertEqual(score["outcome"], "FAIL")
        self.assertFalse(
            score["routing_evaluation"]["required_before_native_discovery"]
        )
        self.assertEqual(exit_code(score), 1)

    def test_missing_hashmarks_catalog_is_environment_blocked_not_fail(self) -> None:
        catalog = {
            "schema": CATALOG_CAPTURE_SCHEMA,
            "host": "chatgpt",
            "capture_id": "blocked-1",
            "tools": [
                {"name": "mcp__GitHub__search"},
                {"name": "mcp__GitHub__fetch_file"},
            ],
        }
        score = score_trace(
            catalog_payload=catalog,
            trace_payload=_trace(
                [
                    {
                        "tool": "mcp__GitHub__search",
                        "status": "completed",
                    }
                ],
                catalog=catalog,
            ),
            subject="hashmarks",
            required_tool="hashmarks_task_evidence",
        )

        self.assertEqual(score["outcome"], "ENVIRONMENT_BLOCKED")
        self.assertEqual(
            score["catalog_admission"]["reason_codes"],
            ["required-subject-tool-missing"],
        )
        self.assertEqual(exit_code(score), 2)

    def test_trace_rejects_different_catalog_generation(self) -> None:
        stale_trace = _trace(
            [
                {
                    "tool": "mcp__hashmarks__task_evidence",
                    "status": "completed",
                    "result_bytes": 42,
                }
            ]
        )
        current_catalog = {
            "schema": CATALOG_CAPTURE_SCHEMA,
            "host": "chatgpt",
            "capture_id": "capture-1",
            "tools": [
                {"name": "mcp__hashmarks__task_evidence"},
                {"name": "mcp__GitHub__search"},
                {"name": "mcp__GitHub__fetch_file"},
                {"name": "mcp__GitHub__fetch"},
            ],
        }
        with self.assertRaisesRegex(
            ValueError,
            "catalog_sha256 does not match",
        ):
            score_trace(
                catalog_payload=current_catalog,
                trace_payload=stale_trace,
                subject="hashmarks",
                required_tool="hashmarks_task_evidence",
            )

    def test_trace_rejects_different_capture_session(self) -> None:
        catalog = dict(_READY_CATALOG)
        catalog["capture_id"] = "capture-2"
        with self.assertRaisesRegex(
            ValueError,
            "capture_id differ",
        ):
            score_trace(
                catalog_payload=catalog,
                trace_payload=_trace(
                    [
                        {
                            "tool": "mcp__hashmarks__task_evidence",
                            "status": "completed",
                            "result_bytes": 42,
                        }
                    ]
                ),
                subject="hashmarks",
                required_tool="hashmarks_task_evidence",
            )

    def test_opaque_router_before_hashmarks_is_unknown(self) -> None:
        score = score_trace(
            catalog_payload=_READY_CATALOG,
            trace_payload=_trace(
                [
                    {"tool": "functions.exec", "status": "completed"},
                    {
                        "tool": "mcp__hashmarks__task_evidence",
                        "status": "completed",
                        "output": {"schema": "hashmarks.task-evidence.v2"},
                    },
                    {
                        "tool": "mcp__GitHub__search",
                        "status": "completed",
                    },
                ]
            ),
            subject="hashmarks",
            required_tool="hashmarks_task_evidence",
        )

        self.assertEqual(score["outcome"], "UNKNOWN")
        self.assertIsNone(
            score["routing_evaluation"]["required_before_native_discovery"]
        )
        self.assertEqual(
            score["calls"][0]["routing_observability"],
            "opaque",
        )
        self.assertEqual(exit_code(score), 3)

    def test_expanded_router_preserves_observable_child_order(self) -> None:
        score = score_trace(
            catalog_payload=_READY_CATALOG,
            trace_payload=_trace(
                [
                    {
                        "tool": "functions.exec",
                        "status": "completed",
                        "nested_calls": [
                            {
                                "tool": "mcp__hashmarks__task_evidence",
                                "status": "completed",
                                "output": {
                                    "schema": "hashmarks.task-evidence.v2"
                                },
                            },
                            {
                                "tool": "mcp__GitHub__search",
                                "status": "completed",
                            },
                        ],
                    }
                ]
            ),
            subject="hashmarks",
            required_tool="hashmarks_task_evidence",
        )

        self.assertEqual(score["outcome"], "PASS")
        self.assertEqual(score["call_count"], 3)
        self.assertEqual(
            [row["ordinal"] for row in score["calls"]],
            [1, 2, 3],
        )
        self.assertEqual(
            score["calls"][0]["routing_observability"],
            "expanded",
        )

    def test_completed_hashmarks_without_observable_result_is_unknown(self) -> None:
        score = score_trace(
            catalog_payload=_READY_CATALOG,
            trace_payload=_trace(
                [
                    {
                        "tool": "mcp__hashmarks__task_evidence",
                        "status": "completed",
                    },
                    {
                        "tool": "mcp__GitHub__search",
                        "status": "completed",
                    },
                ]
            ),
            subject="hashmarks",
            required_tool="hashmarks_task_evidence",
        )

        self.assertEqual(score["outcome"], "UNKNOWN")
        self.assertTrue(
            score["routing_evaluation"]["required_call_attempted"]
        )
        self.assertIsNone(
            score["routing_evaluation"]["required_call_succeeded"]
        )

    def test_trace_rejects_unbounded_nested_router_depth(self) -> None:
        call: dict[str, object] = {
            "tool": "mcp__hashmarks__task_evidence",
            "status": "completed",
            "result_bytes": 42,
        }
        for _ in range(18):
            call = {
                "tool": "functions.exec",
                "status": "completed",
                "nested_calls": [call],
            }

        with self.assertRaisesRegex(
            ValueError,
            "exceeds maximum nested tool depth",
        ):
            score_trace(
                catalog_payload=_READY_CATALOG,
                trace_payload=_trace([call]),
                subject="hashmarks",
            required_tool="hashmarks_task_evidence",
            )

    def test_capture_cannot_supply_derived_routing_authority(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "derived routing fields owned by the scorer",
        ):
            score_trace(
                catalog_payload=_READY_CATALOG,
                trace_payload=_trace(
                    [
                        {
                            "tool": "mcp__GitHub__search",
                            "tool_class": "other",
                            "status": "completed",
                        }
                    ]
                ),
                subject="hashmarks",
            required_tool="hashmarks_task_evidence",
            )

    def test_catalog_cli_materializes_bound_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw-catalog.json"
            capture = root / "catalog.json"
            raw.write_text(
                json.dumps(
                    [
                        "mcp__hashmarks__task_evidence",
                        "mcp__GitHub__search",
                    ]
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = benchmark_main(
                    [
                        "tool-routing-catalog",
                        "--catalog",
                        str(raw),
                        "--subject",
                        "hashmarks",
                        "--required-tool",
                        "hashmarks_task_evidence",
                        "--host",
                        "chatgpt",
                        "--capture-id",
                        "capture-cli-1",
                        "--output-capture",
                        str(capture),
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], CATALOG_CAPTURE_SCHEMA)
            self.assertEqual(payload["host"], "chatgpt")
            self.assertEqual(payload["capture_id"], "capture-cli-1")
            evidence = json.loads(stdout.getvalue())
            self.assertEqual(evidence["status"], "READY")
            self.assertEqual(evidence["catalog_capture"], str(capture))
            self.assertEqual(
                evidence["catalog_sha256"],
                routing_artifact_sha256(payload),
            )

    def test_catalog_cli_rejects_partial_capture_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw-catalog.json"
            raw.write_text(
                json.dumps(
                    [
                        "mcp__hashmarks__task_evidence",
                        "mcp__GitHub__search",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SystemExit,
                "--host, --capture-id, and --output-capture",
            ):
                benchmark_main(
                    [
                        "tool-routing-catalog",
                        "--catalog",
                        str(raw),
                        "--subject",
                        "hashmarks",
                        "--required-tool",
                        "hashmarks_task_evidence",
                        "--host",
                        "chatgpt",
                    ]
                )

    def test_trace_cli_writes_canonical_score_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.json"
            trace = root / "trace.json"
            output = root / "score.json"
            catalog.write_text(
                json.dumps(_READY_CATALOG),
                encoding="utf-8",
            )
            trace.write_text(
                json.dumps(
                    _trace(
                        [
                            {
                                "tool": "mcp__hashmarks__task_evidence",
                                "status": "completed",
                                "result_bytes": 42,
                            },
                            {
                                "tool": "mcp__GitHub__search",
                                "status": "completed",
                            },
                        ]
                    )
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = benchmark_main(
                    [
                        "tool-routing-trace",
                        "--catalog",
                        str(catalog),
                        "--trace",
                        str(trace),
                        "--subject",
                        "hashmarks",
                        "--required-tool",
                        "hashmarks_task_evidence",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue().strip(), str(output))
            score = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(score["outcome"], "PASS")
            self.assertTrue(score["catalog_sha256"].startswith("sha256:"))
            self.assertTrue(score["trace_sha256"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
