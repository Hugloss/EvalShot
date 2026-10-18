from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from benchmarks.__main__ import _parser, main
from benchmarks.harness.selection import (
    SelectionError,
    parse_agent_arguments,
    select_definitions,
)
from benchmarks.harness.suite import load_suite


HELDOUT = (
    Path(__file__).resolve().parents[1] / "suites/repository-intelligence/heldout-v1"
)


class ExplicitAgentSelectionTests(unittest.TestCase):
    def test_check_allows_no_agent_filter(self) -> None:
        args = _parser().parse_args(["check", "--suite", "suite", "--env-file", ".env"])
        self.assertEqual(args.agent, [])

    def test_preflight_requires_env_file(self) -> None:
        with self.assertRaises(SystemExit):
            _parser().parse_args(
                [
                    "preflight",
                    "--suite",
                    "suite",
                    "--harness-root",
                    ".",
                ]
            )

    def test_run_requires_env_file(self) -> None:
        with self.assertRaises(SystemExit):
            _parser().parse_args(
                [
                    "run",
                    "--suite",
                    "suite",
                    "--harness-root",
                    ".",
                ]
            )

    def test_run_accepts_explicit_agent(self) -> None:
        args = _parser().parse_args(
            [
                "run",
                "--resume",
                "--suite",
                "suite",
                "--harness-root",
                ".",
                "--env-file",
                ".env",
                "--agent",
                "opencode-native",
            ]
        )
        self.assertEqual(args.agent, ["opencode-native"])

    def test_agent_list_accepts_csv_and_repeated_arguments(self) -> None:
        self.assertEqual(
            parse_agent_arguments(["codex-native, opencode-native"]),
            ("codex-native", "opencode-native"),
        )
        self.assertEqual(
            parse_agent_arguments(["codex-native", "opencode-native"]),
            ("codex-native", "opencode-native"),
        )
        for values in ([""], ["codex-native,"], ["codex-native", "codex-native"]):
            with self.subTest(values=values), self.assertRaises(SelectionError):
                parse_agent_arguments(values)

    def test_condition_accepts_only_its_matching_single_agent(self) -> None:
        suite = load_suite(HELDOUT)
        selected = select_definitions(
            suite,
            tasks=("locate-prefix-path-enumerator",),
            agents=("codex-native",),
            condition="hashmarks-codex-native",
        )
        self.assertEqual(len(selected), 3)
        for agents in (("opencode-native",), ("codex-native", "opencode-native")):
            with self.subTest(agents=agents), self.assertRaises(SelectionError):
                select_definitions(
                    suite,
                    agents=agents,
                    condition="hashmarks-codex-native",
                )

    def test_cli_plan_selects_108_or_216_definitions(self) -> None:
        for value, expected in (
            ("codex-native", 108),
            ("codex-native,opencode-native", 216),
        ):
            with self.subTest(value=value):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(
                        main(["plan", "--suite", str(HELDOUT), "--agent", value]),
                        0,
                    )
                self.assertEqual(len(json.loads(output.getvalue())), expected)

    def test_selection_rejects_an_agent_with_no_matching_trials(self) -> None:
        suite = SimpleNamespace(
            tasks={"task": {}},
            agents={"codex-native": {}, "unused": {}},
            subjects={"none": {}},
            experiment={
                "conditions": [
                    {"id": "bare", "agent": "codex-native", "subject": "none"}
                ]
            },
            trial_definitions=lambda: [
                {"definition_id": "one", "task_id": "task", "condition_id": "bare"}
            ],
        )
        with self.assertRaisesRegex(SelectionError, "unused"):
            select_definitions(suite, agents=("codex-native", "unused"))


if __name__ == "__main__":
    unittest.main()
