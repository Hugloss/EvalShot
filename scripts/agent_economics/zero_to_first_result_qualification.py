from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path

from .__main__ import main as package_main
from .capabilities import capabilities
from .command_catalog import COMMANDS, PROBE_COMMANDS
from .doctor import doctor
from .profile_suggestion import profile_suggestion


def qualify() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-economics-doctor-") as raw:
        root = Path(raw)
        (root / "src/acme").mkdir(parents=True)
        (root / "src/acme/__init__.py").write_text("", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "scripts").mkdir()
        (root / "benchmarks").mkdir()
        (root / "pyproject.toml").write_text(
            "[project]\nname='acme'\nversion='0'\n"
            "[tool.ruff]\nextend-exclude=['benchmarks/retained']\n"
            "[tool.ruff.lint.mccabe]\nmax-complexity=8\n"
            "[tool.ruff.lint.pylint]\nmax-branches=9\nmax-args=6\nmax-locals=15\n",
            encoding="utf-8",
        )
        before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
        payload = doctor(root)
        after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
        assert before == after
        assert payload["suggestions"]["source_roots"] == ["src/acme"]
        assert payload["suggestions"]["tests_roots"] == ["tests"]
        assert payload["suggestions"]["source_root_evidence"] == [
            {"path": "src/acme", "status": "DETECTED", "basis": "python_package_layout"}
        ]
        assert payload["suggestions"]["test_root_evidence"] == [
            {"path": "tests", "status": "DETECTED", "basis": "test_directory_layout"}
        ]
        assert payload["suggestions"]["package_names"] == ["acme"]
        assert payload["suggestions"]["quality_analysis_roots"] == [
            "src/acme",
            "scripts",
            "benchmarks",
        ]
        assert payload["suggestions"]["quality_analysis_root_evidence"] == [
            {
                "path": "src/acme",
                "status": "DETECTED",
                "basis": "python_package_layout",
            },
            {
                "path": "scripts",
                "status": "PROPOSED",
                "basis": "conventional_directory_name",
            },
            {
                "path": "benchmarks",
                "status": "PROPOSED",
                "basis": "conventional_directory_name",
            },
        ]
        assert payload["suggestions"]["ruff"]["limits"] == {
            "C901": 8,
            "PLR0912": 9,
            "PLR0913": 6,
            "PLR0914": 15,
        }
        assert payload["suggestions"]["ruff"]["extend_exclude"] == [
            "benchmarks/retained"
        ]
        expected_quality_readiness = (
            "READY" if payload["environment"]["ruff"]["available"] else "NEEDS_RUFF"
        )
        assert payload["readiness"]["quality_debt"] == expected_quality_readiness
        profile = profile_suggestion(payload)
        assert profile["status"] == "REVIEW_REQUIRED"
        assert profile["repository"] == {
            "package_roots": ["src/acme"],
            "package_root_evidence": payload["suggestions"]["source_root_evidence"],
            "test_roots": ["tests"],
            "test_root_evidence": payload["suggestions"]["test_root_evidence"],
        }
        assert profile["quality_debt"]["analysis_roots"] == [
            "src/acme",
            "scripts",
            "benchmarks",
        ]
        assert (
            profile["quality_debt"]["analysis_root_evidence"]
            == payload["suggestions"]["quality_analysis_root_evidence"]
        )
        assert (
            profile["interpretation"][
                "proposed_roots_require_review_before_persistent_use"
            ]
            is True
        )
        assert profile["quality_debt"]["limits"]["C901"] == 8
        assert profile["interpretation"]["writes_repository_configuration"] is False
        if payload["environment"]["ruff"]["available"]:
            assert "ruff_supply" not in profile["unresolved"]
        else:
            assert "ruff_supply" in profile["unresolved"]
        assert (
            payload["interpretation"]["suggestions_are_not_repository_authority"]
            is True
        )
        assert payload["readiness"]["test_focus"] == "READY"

        (root / "src/second").mkdir()
        (root / "src/second/__init__.py").write_text("", encoding="utf-8")
        ambiguous = doctor(root)
        assert ambiguous["suggestions"]["source_roots"] == ["src/acme", "src/second"]
        assert "source_root" in ambiguous["ambiguity"]
        assert ambiguous["readiness"]["hotspot_focus"] == "NEEDS_SOURCE_ROOT"
        assert ambiguous["readiness"]["test_focus"] == "NEEDS_CONFIG"
        ambiguous_profile = profile_suggestion(ambiguous)
        assert "package_roots" in ambiguous_profile["unresolved"]
        assert ambiguous_profile["status"] == "REVIEW_REQUIRED"

        capability = capabilities(repository_root=root)
        assert capability["capabilities"]["probes"]["available"] == list(PROBE_COMMANDS)
        assert "quality-debt" in PROBE_COMMANDS

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        package_main(["--help"])
    help_text = output.getvalue()
    for command in COMMANDS:
        assert command.name in help_text
    assert "Start in an unfamiliar repository" in help_text

    print('{"cases":10,"status":"PASS","tool":"zero-to-first-result"}')


if __name__ == "__main__":
    qualify()
