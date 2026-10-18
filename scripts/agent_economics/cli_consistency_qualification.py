from __future__ import annotations

from .context_focus_cli import build_parser as context_parser
from .hotspot_focus_cli import main as hotspot_main
from .quality_debt_cli import main as quality_main
from .test_focus_cli import build_parser as test_parser


def qualify() -> None:
    test = test_parser().parse_args(
        [
            "--source-root",
            "pkg",
            "--changed-path",
            "pkg/a.py",
            "--artifact",
            "a.json",
            "--format",
            "json",
            "--quiet",
        ]
    )
    assert test.artifact.as_posix() == "a.json"
    assert test.format == "json" and test.quiet is True

    legacy_test = test_parser().parse_args(
        [
            "--source-root",
            "pkg",
            "--changed-path",
            "pkg/a.py",
            "--artifact-path",
            "old.json",
        ]
    )
    assert legacy_test.artifact.as_posix() == "old.json"

    context = context_parser().parse_args(
        [
            "--task",
            "x",
            "--artifact",
            "c.json",
            "--format",
            "json",
            "--quiet",
        ]
    )
    assert context.artifact.as_posix() == "c.json"
    assert context.format == "json" and context.quiet is True

    legacy_context = context_parser().parse_args(
        [
            "--task",
            "x",
            "--artifact-path",
            "old-context.json",
        ]
    )
    assert legacy_context.artifact.as_posix() == "old-context.json"

    for main in (quality_main, hotspot_main):
        try:
            main(["--help"])
        except SystemExit as exc:
            assert exc.code == 0

    print('{"cases":6,"status":"PASS","tool":"cli-consistency"}')


if __name__ == "__main__":
    qualify()
