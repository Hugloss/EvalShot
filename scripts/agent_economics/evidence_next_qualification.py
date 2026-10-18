from __future__ import annotations

from .evidence_next import EvidenceNextError, next_evidence
from .evidence_next import main as next_main

import contextlib
import io
import json
import tempfile
from pathlib import Path


def qualify() -> None:
    payload = {
        "schema": {"name": "agent-economics-probe", "version": 1},
        "tool": {"name": "quality-debt", "version": "0.15.0"},
        "candidates": [
            {
                "target": "pkg/hot.py",
                "required_next_evidence": [
                    {
                        "kind": "test_focus",
                        "target": "pkg/hot.py",
                        "reason": "debt magnitude does not establish edit safety",
                    }
                ],
            },
            {
                "target": "pkg/other.py",
                "required_next_evidence": [
                    {
                        "kind": "test_focus",
                        "target": "pkg/other.py",
                        "reason": "recover test ownership",
                    }
                ],
            },
        ],
    }
    profile = {
        "repository": {"package_roots": ["pkg"], "test_roots": ["tests"]},
    }
    try:
        next_evidence(payload, profile=profile)
    except EvidenceNextError:
        pass
    else:
        raise AssertionError("multiple candidates without --target must fail closed")

    multiple_required = dict(payload)
    multiple_required["candidates"] = [
        {
            "target": "pkg/hot.py",
            "required_next_evidence": [
                {"kind": "test_focus", "reason": "first"},
                {"kind": "test_focus", "reason": "second"},
            ],
        }
    ]
    try:
        next_evidence(multiple_required, target="pkg/hot.py", profile=profile)
    except EvidenceNextError:
        pass
    else:
        raise AssertionError("multiple required-next-evidence items must fail closed")

    proposed_profile = {
        "repository": {
            "package_roots": ["pkg"],
            "package_root_evidence": [
                {"path": "pkg", "status": "PROPOSED", "basis": "convention"}
            ],
            "test_roots": ["tests"],
            "test_root_evidence": [
                {
                    "path": "tests",
                    "status": "DETECTED",
                    "basis": "test_directory_layout",
                }
            ],
        },
        "quality_debt": {
            "analysis_roots": ["pkg", "scripts"],
            "analysis_root_evidence": [
                {"path": "pkg", "status": "DETECTED", "basis": "python_package_layout"},
                {
                    "path": "scripts",
                    "status": "PROPOSED",
                    "basis": "conventional_directory_name",
                },
            ],
        },
    }
    proposed = next_evidence(
        payload,
        target="pkg/hot.py",
        profile=proposed_profile,
        python_argv=["uv", "run", "python"],
    )
    assert proposed["command"] is None
    assert proposed["unresolved"] == ["package_root_not_detected"]
    assert proposed["interpretation"]["profile_root_provenance_is_preserved"] is True

    detected_profile = {
        "repository": {
            "package_roots": ["pkg"],
            "package_root_evidence": [
                {"path": "pkg", "status": "DETECTED", "basis": "python_package_layout"}
            ],
            "test_roots": ["tests"],
            "test_root_evidence": [
                {
                    "path": "tests",
                    "status": "DETECTED",
                    "basis": "test_directory_layout",
                }
            ],
        },
        "quality_debt": proposed_profile["quality_debt"],
    }
    detected = next_evidence(
        payload,
        target="pkg/hot.py",
        profile=detected_profile,
        python_argv=["uv", "run", "python"],
    )
    assert detected["command"] is not None
    assert detected["unresolved"] == []

    no_runtime = next_evidence(payload, target="pkg/hot.py", profile=profile)
    assert no_runtime["command"] is None
    assert no_runtime["unresolved"] == ["python_runtime_argv"]

    result = next_evidence(
        payload,
        target="pkg/hot.py",
        profile=profile,
        python_argv=["uv", "run", "python"],
    )
    assert result["next_evidence"]["kind"] == "test_focus"
    assert result["command"] == [
        "uv",
        "run",
        "python",
        "-m",
        "agent_economics",
        "test-focus",
        "--repository-root",
        ".",
        "--source-root",
        "pkg",
        "--tests-root",
        "tests",
        "--changed-path",
        "pkg/hot.py",
        "--artifact-path",
        ".agent-artifacts/test-focus.json",
    ]
    assert result["unresolved"] == []
    assert result["interpretation"]["does_not_execute_command"] is True
    assert result["interpretation"]["does_not_authorize_edit"] is True

    missing_profile = next_evidence(
        payload, target="pkg/hot.py", python_argv=["python"]
    )
    assert missing_profile["command"] is None
    assert missing_profile["unresolved"] == ["profile"]

    ambiguous = next_evidence(
        payload,
        target="pkg/hot.py",
        profile={
            "repository": {"package_roots": ["pkg", "other"], "test_roots": ["tests"]}
        },
        python_argv=["python"],
    )
    assert ambiguous["command"] is None
    assert ambiguous["unresolved"] == ["single_package_root"]

    try:
        next_evidence(
            payload, target="missing.py", profile=profile, python_argv=["python"]
        )
    except EvidenceNextError:
        pass
    else:
        raise AssertionError("unknown target must fail closed")

    unsupported = dict(payload)
    unsupported["candidates"] = [
        {
            "target": "pkg/hot.py",
            "required_next_evidence": [{"kind": "invented", "reason": "fixture"}],
        }
    ]
    result = next_evidence(unsupported, profile=profile, python_argv=["python"])
    assert result["command"] is None
    assert result["unresolved"] == ["unsupported_next_evidence:invented"]

    with tempfile.TemporaryDirectory(prefix="agent-economics-next-cli-") as raw:
        root = Path(raw)
        artifact_path = root / "artifact.json"
        profile_path = root / "profile.json"
        artifact_path.write_text(json.dumps(payload), encoding="utf-8")
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            next_main(
                [
                    str(artifact_path),
                    "--target",
                    "pkg/hot.py",
                    "--profile",
                    str(profile_path),
                    "--python-command",
                    "uv",
                    "--python-command",
                    "run",
                    "--python-command",
                    "python",
                ]
            )
        cli_result = json.loads(output.getvalue())
        assert cli_result["command"][:4] == ["uv", "run", "python", "-m"]

    print('{"cases":11,"status":"PASS","tool":"evidence-next"}')


if __name__ == "__main__":
    qualify()


def test_next_evidence_stops_when_declared_boundaries_are_satisfied() -> None:
    payload = {
        "schema": {"name": "agent-economics-probe", "version": 1},
        "tool": {"name": "context-focus"},
        "candidates": [
            {
                "target": "src/a.py",
                "required_next_evidence": [
                    {"kind": "test_focus", "reason": "need behavior evidence"}
                ],
            }
        ],
    }

    result = next_evidence(
        payload,
        target="src/a.py",
        sufficiency={
            "risk_boundaries": [{"identity": "behavior"}],
            "proofs": [
                {
                    "boundary": "behavior",
                    "provider_reference": {
                        "provider": "hashmarks",
                        "evidence_identity": "sha256:behavior-proof",
                    },
                    "freshness": {"state": "fresh"},
                    "relationship": {
                        "classification": "direct",
                        "evidence_identity": "sha256:behavior-relationship",
                    },
                }
            ],
        },
    )

    assert result["next_evidence"] is None
    assert result["command"] is None
    assert result["stop"]["stop_acquiring_evidence"] is True


def test_next_evidence_reopens_only_for_proven_scope_expansion() -> None:
    payload = {
        "schema": {"name": "agent-economics-probe", "version": 1},
        "tool": {"name": "context-focus"},
        "candidates": [
            {
                "target": "src/a.py",
                "required_next_evidence": [
                    {"kind": "test_focus", "reason": "new consumer boundary"}
                ],
            }
        ],
    }

    result = next_evidence(
        payload,
        target="src/a.py",
        sufficiency={
            "risk_boundaries": [{"identity": "behavior"}],
            "proofs": [
                {
                    "boundary": "behavior",
                    "provider_reference": {
                        "provider": "hashmarks",
                        "evidence_identity": "sha256:behavior-proof",
                    },
                    "freshness": {"state": "fresh"},
                    "relationship": {
                        "classification": "direct",
                        "evidence_identity": "sha256:behavior-relationship",
                    },
                }
            ],
            "scope_expansion_evidence": [
                {
                    "provider": "hashmarks",
                    "evidence_identity": "sha256:impact-delta",
                    "boundary": "new-consumer",
                }
            ],
        },
    )

    assert result["next_evidence"]["kind"] == "test_focus"
    assert "stop" not in result
