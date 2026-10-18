from __future__ import annotations

from .explain import ExplainError, explain
from .probe_contract import build_probe_contract


def _payload(tool: str) -> dict[str, object]:
    return build_probe_contract(
        tool_name=tool,
        tool_version="fixture",
        generated_at="2026-01-01T00:00:00Z",
        repository={"root": ".", "identity": "sha256:" + "0" * 64},
        configuration_values={},
        evidence={},
        derived={
            "summary": {"locations": 4, "rule_findings": 6, "excess": 19, "files": {}},
            "baseline_comparison": {"state": "REDUCED"},
        }
        if tool == "quality-debt"
        else {},
        interpretation={},
        uncertainty=[],
        warnings=[{"code": "fixture", "message": "fixture warning"}],
        candidates=[
            {
                "target": "pkg/hot.py",
                "facts": {"excess": 19},
                "evidence": {"analyzer": "ruff"},
                "derived": {"excess": 19},
                "interpretation": {},
                "recommendations": {},
                "uncertainty": [],
                "required_next_evidence": [
                    {"kind": "test_focus", "reason": "recover test evidence"}
                ],
                "verification_suggestions": [
                    {
                        "kind": "test_file",
                        "stage": "affected",
                        "path": "tests/test_hot.py",
                        "reason": "confirmed owner of a dependent source",
                    }
                ],
            }
        ],
        required_next_evidence=[],
        deferred_evidence=[],
        verification_suggestions=[],
        economics={"files_read": 7, "elapsed_ms": 2.5},
    )


def qualify() -> None:
    payload = _payload("quality-debt")
    result = explain(payload, target="pkg/hot.py")
    assert result["summary"]["excess"] == 19
    assert result["selected"]["required_next_evidence"] == [
        {"kind": "test_focus", "reason": "recover test evidence"}
    ]
    assert result["economics"] == {"files_read": 7, "elapsed_ms": 2.5}
    assert result["interpretation"]["does_not_add_recommendations"] is True

    multi = _payload("quality-debt")
    multi["candidates"] = [
        *multi["candidates"],
        {**multi["candidates"][0], "target": "pkg/other.py"},
    ]
    try:
        explain(multi)
    except ExplainError:
        pass
    else:
        raise AssertionError("multiple candidates without --target must fail closed")

    original = __import__("json").dumps(payload, sort_keys=True)
    explain(payload)
    assert __import__("json").dumps(payload, sort_keys=True) == original

    try:
        explain(payload, target="missing.py")
    except ExplainError:
        pass
    else:
        raise AssertionError("unknown target must fail closed")

    invalid = dict(payload)
    invalid.pop("economics")
    try:
        explain(invalid)
    except ExplainError:
        pass
    else:
        raise AssertionError("invalid common contract must be rejected")

    test_result = explain(_payload("test-focus"))
    assert "verification_suggestions" in test_result
    assert test_result["tool"] == "test-focus"
    from .explain import _human

    human = _human(explain(_payload("test-focus"), target="pkg/hot.py"))
    assert "Verification suggestions:" in human
    assert "tests/test_hot.py" in human
    assert "confirmed owner of a dependent source" in human

    print('{"cases":7,"status":"PASS","tool":"explain"}')


if __name__ == "__main__":
    qualify()
