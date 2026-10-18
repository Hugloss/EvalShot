"""Configuration authority for explicit benchmark commands."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.__main__ import main
from benchmarks.config import BenchmarkConfig, BenchmarkConfigError

SUITE = (
    Path(__file__).resolve().parents[1] / "suites/repository-intelligence/heldout-v1"
)


class BenchmarkConfigTests(unittest.TestCase):
    def test_file_wins_and_each_load_is_a_fresh_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / ".env"
            file.write_text("HASHMARKS_BENCH_SOURCE=/first\n", encoding="utf-8")
            host = {
                "HASHMARKS_BENCH_SOURCE": "/shell",
                "BENCHMARK_AGENT": "opencode-native",
                "PATH": "/host/bin",
            }
            first = BenchmarkConfig.load(file, host=host)
            host["PATH"] = "/changed/bin"
            file.write_text("HASHMARKS_BENCH_SOURCE=/second\n", encoding="utf-8")
            second = BenchmarkConfig.load(file, host=host)
            self.assertEqual(
                first.runtime_environment()["HASHMARKS_BENCH_SOURCE"], "/first"
            )
            self.assertEqual(first.runtime_environment()["PATH"], "/host/bin")
            self.assertNotIn("BENCHMARK_AGENT", first.runtime_environment())
            self.assertEqual(
                second.runtime_environment()["HASHMARKS_BENCH_SOURCE"], "/second"
            )
            self.assertEqual(second.runtime_environment()["PATH"], "/changed/bin")

    def test_missing_blank_and_duplicate_settings_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / ".env"
            file.write_text("BENCHMARK_AGENT=\n", encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkConfigError, "BENCHMARK_AGENT"):
                BenchmarkConfig.load(file, host={}).require("BENCHMARK_AGENT")
            file.write_text(
                "BENCHMARK_AGENT=codex-native\nBENCHMARK_AGENT=opencode-native\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                BenchmarkConfigError, "duplicate BENCHMARK_AGENT"
            ):
                BenchmarkConfig.load(file, host={})

    def test_check_uses_file_authority_with_explicit_suite_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / ".env"
            file.write_text(
                f"BENCHMARK_SUITE_PATH={SUITE}\n"
                "HASHMARKS_BENCH_SOURCE=/file-source\n"
                "BENCHMARK_OPENCODE_AGENT=build\n",
                encoding="utf-8",
            )
            fake = SimpleNamespace(checks=(), ready=True)
            with (
                patch.dict(os.environ, {"HASHMARKS_BENCH_SOURCE": "/shell-source"}),
                patch(
                    "benchmarks.__main__.check_runtime_readiness", return_value=fake
                ) as check,
                redirect_stdout(io.StringIO()),
            ):
                outcome = main(
                    ["check", "--env-file", str(file), "--suite", str(SUITE)]
                )
            self.assertEqual(outcome, 0)
            self.assertEqual(
                check.call_args.kwargs["source"]["HASHMARKS_BENCH_SOURCE"],
                "/file-source",
            )

    def test_doctor_reuses_check_runtime_authority_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / ".env"
            file.write_text(
                f"BENCHMARK_SUITE_PATH={SUITE}\n"
                "HASHMARKS_BENCH_SOURCE=/file-source\n"
                "BENCHMARK_OPENCODE_AGENT=build\n",
                encoding="utf-8",
            )
            fake = SimpleNamespace(checks=(), ready=True)
            with (
                patch(
                    "benchmarks.__main__.check_runtime_readiness",
                    return_value=fake,
                ) as check,
                redirect_stdout(io.StringIO()),
            ):
                outcome = main(["doctor", "--env-file", str(file)])
            self.assertEqual(outcome, 0)
            check.assert_called_once()
            self.assertEqual(
                check.call_args.kwargs["source"]["HASHMARKS_BENCH_SOURCE"],
                "/file-source",
            )

    def test_score_uses_file_selection_and_cli_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / ".env"
            scorer = SUITE / "score.py"
            file.write_text(
                f"BENCHMARK_SUITE_PATH={SUITE}\n"
                "BENCHMARK_CAMPAIGN_ROOT=/tmp/file-campaign\n"
                "BENCHMARK_AGENT=codex-native,opencode-native\n"
                f"BENCHMARK_SCORE_SCRIPT_PATH={scorer}\n"
                "BENCHMARK_SCORE_OUTPUT_PATH=score.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "must belong to selected suite"):
                main(
                    [
                        "score",
                        "--env-file",
                        str(file),
                        "--score-script",
                        str(SUITE.parent / "multidomain-v2/score.py"),
                    ]
                )

            saved = SimpleNamespace(run_id="000001", root=root)
            manifest = {
                "selected_definitions": ["a" * 64],
                "agents": {
                    "codex-native": {},
                    "opencode-native": {},
                },
            }
            row = {
                "definition_id": "a" * 64,
                "task_id": "task-a",
                "condition_id": "bare",
                "trial": 0,
            }
            score_path = root / "reports/score.json"
            with (
                patch("benchmarks.__main__._validate_score_cli"),
                patch(
                    "benchmarks.__main__.select_saved_run",
                    return_value=saved,
                ),
                patch(
                    "benchmarks.__main__._assert_saved_run_agents",
                    return_value=manifest,
                ),
                patch("benchmarks.__main__.verify_campaign_suite_authority"),
                patch(
                    "benchmarks.__main__.select_scoring_definitions",
                    return_value=[row],
                ),
                patch(
                    "benchmarks.__main__.campaign_status",
                    return_value={"complete": True, "qualified": True},
                ),
                patch(
                    "benchmarks.__main__._persist_completed_run_reports",
                    return_value={"score": score_path},
                ) as persist,
            ):
                self.assertEqual(
                    main(["score", "--env-file", str(file), "--root", str(root)]), 0
                )

            persist.assert_called_once()
            self.assertEqual(
                persist.call_args.kwargs["score_contract"],
                (scorer.resolve(), Path("score.json")),
            )
            self.assertEqual(
                persist.call_args.kwargs["args"].agent,
                ["codex-native", "opencode-native"],
            )
