from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .coupling_focus import CouplingFocusError, coupling_focus_audit
from .probe_contract import validate_probe_contract


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str, files: dict[str, str]) -> None:
    for path, text in files.items():
        _write(root, path, text)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _make_repository(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "agent-economics@example.invalid")
    _git(root, "config", "user.name", "Agent Economics Qualification")
    _commit(root, "one", {"src/target.py": "1\n", "src/alpha.py": "1\n"})
    _commit(
        root,
        "two",
        {"src/target.py": "2\n", "src/alpha.py": "2\n", "src/beta.py": "2\n"},
    )
    _commit(root, "three", {"src/target.py": "3\n", "src/alpha.py": "3\n"})
    _commit(root, "beta", {"src/beta.py": "4\n"})
    _commit(
        root,
        "mega",
        {
            "src/target.py": "5\n",
            "src/alpha.py": "5\n",
            "src/g1.py": "1\n",
            "src/g2.py": "1\n",
            "src/g3.py": "1\n",
        },
    )


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []
    observations: dict[str, object] = {}

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        _make_repository(root)
        payload = coupling_focus_audit(
            repository_root=root,
            target_paths=["src/target.py"],
            history_max_commits=50,
            max_files_per_commit=3,
            min_shared_commits=2,
            top_n=10,
            candidate_suffixes=(".py",),
            use_default_excludes=False,
        )
        targets = [candidate["target"] for candidate in payload["candidates"]]
        if targets != ["src/alpha.py"]:
            failures.append(f"unexpected coupling candidates: {targets!r}")
        if (
            payload["candidates"]
            and payload["candidates"][0]["facts"]["shared_commits"] != 3
        ):
            failures.append("shared commit count is incorrect")
        if payload["economics"]["oversized_commits_skipped"] != 1:
            failures.append("mega-commit suppression was not exercised")
        if not any(
            item.get("code") == "correlation_not_dependency"
            for item in payload["warnings"]
        ):
            failures.append("correlation-vs-dependency warning missing")
        if payload["interpretation"].get("dependency_authority") is not False:
            failures.append(
                "historical co-change incorrectly gained dependency authority"
            )
        contract_errors = validate_probe_contract(payload)
        if contract_errors:
            failures.append(f"common contract invalid: {contract_errors}")

        truncated = coupling_focus_audit(
            repository_root=root,
            target_paths=["src/target.py"],
            history_max_commits=2,
            max_files_per_commit=10,
            min_shared_commits=1,
            top_n=10,
            use_default_excludes=False,
        )
        if not any(
            item.get("code") == "history_window_truncated"
            for item in truncated["uncertainty"]
        ):
            failures.append("bounded history did not publish truncation uncertainty")

        suffix_filtered = coupling_focus_audit(
            repository_root=root,
            target_paths=["src/target.py"],
            history_max_commits=50,
            max_files_per_commit=10,
            min_shared_commits=1,
            top_n=10,
            candidate_suffixes=(".md",),
            use_default_excludes=False,
        )
        if suffix_filtered["candidates"]:
            failures.append("candidate suffix filter failed")

        try:
            coupling_focus_audit(
                repository_root=root,
                target_paths=["src/target.py"],
                history_max_commits=50,
                history_max_bytes=8,
                use_default_excludes=False,
            )
        except CouplingFocusError:
            pass
        else:
            failures.append("history byte budget did not fail closed")

        identity_before = payload["repository"]["identity"]
        _commit(root, "new", {"src/other.py": "x\n"})
        after_commit = coupling_focus_audit(
            repository_root=root,
            target_paths=["src/target.py"],
            history_max_commits=50,
            max_files_per_commit=3,
            min_shared_commits=2,
            top_n=10,
            candidate_suffixes=(".py",),
            use_default_excludes=False,
        )
        if identity_before == after_commit["repository"]["identity"]:
            failures.append("repository identity did not invalidate on HEAD change")

        try:
            coupling_focus_audit(
                repository_root=root,
                target_paths=["../escape.py"],
                use_default_excludes=False,
            )
        except CouplingFocusError:
            pass
        else:
            failures.append("escaping target path did not fail closed")

        try:
            coupling_focus_audit(
                repository_root=root,
                target_paths=[".agent-artifacts/result.json"],
            )
        except CouplingFocusError:
            pass
        else:
            failures.append("target excluded by default policy did not fail closed")

        with tempfile.TemporaryDirectory() as non_git:
            try:
                coupling_focus_audit(
                    repository_root=Path(non_git),
                    target_paths=["x.py"],
                    use_default_excludes=False,
                )
            except CouplingFocusError:
                pass
            else:
                failures.append("coupling-focus ran without a Git repository")

        observations = {
            "targets": targets,
            "shared_commits": (
                payload["candidates"][0]["facts"]["shared_commits"]
                if payload["candidates"]
                else None
            ),
            "target_commits": payload["derived"]["target_commit_count"],
            "oversized_commits_skipped": payload["economics"][
                "oversized_commits_skipped"
            ],
            "history_truncated": any(
                item.get("code") == "history_window_truncated"
                for item in truncated["uncertainty"]
            ),
            "contract_errors": contract_errors,
        }

    result: dict[str, object] = {
        "probe": "coupling-focus",
        "qualification_phase": 8,
        "passed": not failures,
        "observations": observations,
        "failures": failures,
    }
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/coupling-focus-p8-qualification.json"),
    )
    args = parser.parse_args(argv)
    result = qualify(args.artifact_path)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
