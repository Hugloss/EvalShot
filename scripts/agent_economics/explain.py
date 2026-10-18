from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .probe_contract import validate_probe_contract


class ExplainError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExplainError(f"cannot read artifact: {exc}") from exc
    errors = validate_probe_contract(value)
    if errors:
        raise ExplainError("invalid probe artifact: " + "; ".join(errors[:5]))
    assert isinstance(value, dict)
    return value


def explain(payload: dict[str, Any], *, target: str | None = None) -> dict[str, Any]:
    errors = validate_probe_contract(payload)
    if errors:
        raise ExplainError("invalid probe artifact: " + "; ".join(errors[:5]))
    tool = payload["tool"]["name"]
    candidates = payload["candidates"]
    selected = None
    if target is not None:
        matches = [row for row in candidates if row.get("target") == target]
        if len(matches) != 1:
            raise ExplainError(f"target must identify exactly one candidate: {target}")
        selected = matches[0]
    elif len(candidates) == 1:
        selected = candidates[0]
    elif len(candidates) > 1:
        raise ExplainError("artifact has multiple candidates; --target is required")

    result: dict[str, Any] = {
        "tool": tool,
        "repository_identity": payload["repository"]["identity"],
        "candidate_count": len(candidates),
        "uncertainty_count": len(payload["uncertainty"]),
        "warning_count": len(payload["warnings"]),
        "economics": payload["economics"],
        "selected": None,
        "interpretation": {
            "projection_only": True,
            "artifact_remains_authoritative_source": True,
            "does_not_add_recommendations": True,
        },
    }
    if selected is not None:
        result["selected"] = {
            "target": selected["target"],
            "facts": selected["facts"],
            "derived": selected["derived"],
            "evidence": selected["evidence"],
            "uncertainty": selected["uncertainty"],
            "required_next_evidence": selected["required_next_evidence"],
            "verification_suggestions": selected["verification_suggestions"],
        }
    if tool == "quality-debt":
        result["summary"] = payload["derived"].get("summary", {})
        result["baseline_comparison"] = payload["derived"].get(
            "baseline_comparison", {}
        )
    elif tool == "test-focus":
        result["verification_suggestions"] = payload["verification_suggestions"]
        result["required_next_evidence"] = payload["required_next_evidence"]
        result["deferred_evidence"] = payload["deferred_evidence"]
    return result


def _human(result: dict[str, Any]) -> str:
    lines = [
        f"AGENT ECONOMICS — {result['tool']}",
        f"Candidates ............... {result['candidate_count']}",
        f"Uncertainty .............. {result['uncertainty_count']}",
        f"Warnings ................. {result['warning_count']}",
    ]
    summary = result.get("summary")
    if isinstance(summary, dict):
        for key in ("locations", "rule_findings", "excess"):
            if key in summary:
                lines.append(f"{key.replace('_', ' ').title():26} {summary[key]}")
    selected = result.get("selected")
    if isinstance(selected, dict):
        lines.extend(("", f"TARGET {selected['target']}"))
        facts = selected.get("facts")
        if isinstance(facts, dict):
            for key, value in facts.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    lines.append(f"{key}: {value}")
        required = selected.get("required_next_evidence")
        if isinstance(required, list) and required:
            lines.append("Required next evidence:")
            for item in required:
                if isinstance(item, dict):
                    lines.append(f"  - {item.get('kind')}: {item.get('reason')}")
        verification = selected.get("verification_suggestions")
        if isinstance(verification, list) and verification:
            lines.append("Verification suggestions:")
            for item in verification:
                if isinstance(item, dict):
                    detail = item.get("path") or item.get("command") or item.get("name")
                    lines.append(
                        f"  - {item.get('stage') or item.get('kind')}: {detail} — {item.get('reason')}"
                    )
        uncertainty = selected.get("uncertainty")
        if isinstance(uncertainty, list) and uncertainty:
            lines.append("Uncertainty:")
            for item in uncertainty:
                if isinstance(item, dict):
                    lines.append(
                        f"  - {item.get('code')}: {item.get('message') or item.get('reason')}"
                    )
    lines.append("")
    lines.append(
        "This is a projection of the artifact; it adds no edit recommendation or authority."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Explain a probe artifact without adding new reasoning or recommendations."
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--target", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = explain(_load(args.artifact), target=args.target)
    except ExplainError as exc:
        raise SystemExit(f"explain: {exc}") from exc
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else _human(result))


if __name__ == "__main__":
    main()
