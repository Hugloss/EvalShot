from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .hotspot_focus import hotspot_focus_audit
from .probe_contract import validate_probe_contract


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, stdout=subprocess.DEVNULL
    )


def _commit(root: Path, message: str) -> None:
    _git(root, "add", ".")
    _git(root, "commit", "-m", message)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _git(root, "init")
        _git(root, "config", "user.email", "one@example.invalid")
        _git(root, "config", "user.name", "One")
        _write(
            root / "src/app/a.py",
            "def a(x):\n    if x:\n        return 1\n    return 0\n",
        )
        _write(
            root / "src/app/b.py", "from app.a import a\ndef b():\n    return a(True)\n"
        )
        _write(
            root / "src/app/c.py",
            "from app.a import a\ndef c():\n    return a(False)\n",
        )
        _write(
            root / "tests/test_a.py",
            "from app.a import a\ndef test_a():\n    assert a(True) == 1\n",
        )
        _commit(root, "initial")
        for i in range(3):
            with (root / "src/app/a.py").open("a", encoding="utf-8") as handle:
                handle.write(f"# churn {i}\n")
            _commit(root, f"churn-a-{i}")
        _git(root, "config", "user.email", "two@example.invalid")
        _git(root, "config", "user.name", "Two")
        with (root / "src/app/a.py").open("a", encoding="utf-8") as handle:
            handle.write("# second author\n")
        _commit(root, "second-author")

        payload = hotspot_focus_audit(
            repository_root=root,
            source_root=root / "src/app",
            tests_root=root / "tests",
            package_name="app",
            tests_package_name="tests",
            top_n=3,
            history_policy="required",
            discovery_mode="git",
        )
        errors = validate_probe_contract(payload)
        assert not errors, errors
        assert payload["interpretation"]["opaque_composite_score"] is False
        assert payload["interpretation"]["ranking_policy"] == "lexicographic"
        first = payload["candidates"][0]
        assert first["target"] == "src/app/a.py", first
        facts = first["facts"]
        assert facts["fan_in"] == 2, facts
        assert facts["churn_commits"] >= 5, facts
        assert facts["distinct_authors"] == 2, facts
        assert 0 < facts["top_author_share"] < 1, facts
        assert facts["confirmed_tests"] == 1, facts
        assert facts["largest_definitions"][0]["qualified_name"] == "a"
        assert facts["largest_definitions"][0]["lines"] >= 4
        assert "risk_score" not in facts
        assert "score" not in facts

        static_only = hotspot_focus_audit(
            repository_root=root,
            source_root=root / "src/app",
            tests_root=None,
            package_name="app",
            top_n=2,
            history_policy="disabled",
            discovery_mode="filesystem",
        )
        assert validate_probe_contract(static_only) == []
        assert static_only["evidence"]["history"]["available"] is False
        assert static_only["evidence"]["test_tree_available"] is False
        for candidate in static_only["candidates"]:
            assert candidate["facts"]["churn_commits"] is None
            assert candidate["facts"]["confirmed_tests"] is None
        codes = {item["code"] for item in static_only["uncertainty"]}
        assert "test_tree_unavailable" in codes

        try:
            hotspot_focus_audit(
                repository_root=root,
                source_root=root / "src/app",
                package_name="app",
                history_policy="required",
                history_max_bytes=8,
                discovery_mode="git",
            )
        except ValueError as exc:
            assert "byte bound" in str(exc)
        else:
            raise AssertionError(
                "tiny history byte bound must fail closed when history is required"
            )

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "cases": [
                        "visible-independent-dimensions",
                        "lexicographic-ranking-no-opaque-score",
                        "static-fan-in",
                        "bounded-git-churn",
                        "anonymized-author-concentration",
                        "confirmed-test-evidence",
                        "static-only-unknown-not-zero",
                        "hard-history-byte-bound",
                        "common-contract",
                    ],
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
