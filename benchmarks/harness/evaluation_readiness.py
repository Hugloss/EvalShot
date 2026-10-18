"""Model-free readiness register for the integrated E217-E228 evaluator program.

This is not a quality score. It counts frozen-corpus authority gaps and reports
what must still be observed before claiming an empirical result.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "agentscookbook.evaluation-readiness.v1"


def build_readiness(root: Path) -> dict[str, Any]:
    root = root.resolve()
    skill_path = root / "evals/sharp-skill-cases.json"
    multidomain_path = root / "benchmarks/suites/repository-intelligence/multidomain-v2/evidence.json"
    matrix_path = root / "benchmarks/harbor/repository-intelligence-task-evidence-find-factorial-v1.json"
    skill = json.loads(skill_path.read_text(encoding="utf-8"))
    multidomain = json.loads(multidomain_path.read_text(encoding="utf-8"))
    from benchmarks.harbor_matrix import load_matrix

    factorial = load_matrix(matrix_path)
    if (
        not isinstance(skill, dict) or not isinstance(skill.get("cases"), list)
        or not isinstance(multidomain, dict) or not isinstance(multidomain.get("cases"), list)
        or not isinstance(factorial, dict) or not isinstance(factorial.get("factorial"), dict)
    ):
        raise ValueError("invalid frozen evaluation corpus authority")
    covered: dict[str, set[str]] = {}
    fixture_positive = 0
    pinned_positive = 0
    for case in skill["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("skill"), str):
            raise ValueError("malformed skill case")
        covered.setdefault(case["skill"], set()).add(str(case.get("kind", "unknown")))
        if case.get("fixture") and case.get("expected") == "FINDING":
            fixture_positive += 1
            contract = case.get("evidence_contract")
            if (
                isinstance(contract, dict)
                and contract.get("status") == "pinned-fixture-anchor"
                and isinstance(contract.get("anchors"), list) and contract["anchors"]
            ):
                pinned_positive += 1
    review_states = Counter()
    for case in multidomain["cases"]:
        if not isinstance(case, dict):
            raise ValueError("malformed multidomain case")
        review = case.get("review")
        state = review.get("state") if isinstance(review, dict) else None
        review_states[str(state or "unknown")] += 1
    missing_confusion = sorted(skill_name for skill_name, classes in covered.items() if "confusion" not in classes)
    pending_review = review_states["pending"] + review_states["unknown"]
    return {
        "schema": SCHEMA,
        "evaluation_claim_qualified": False,
        "qualification_blockers": {
            "unreviewed_multidomain_cases": pending_review,
            "skills_without_confusion_case": len(missing_confusion),
            "fixture_positive_without_pinned_anchor": fixture_positive - pinned_positive,
            "host_model_input_delivery_attested": False,
            "presentation_intervention_run_qualified": False,
            "freshness_intervention_run_qualified": False,
            "factorial_model_run_qualified": False,
        },
        "skill_corpus": {
            "cases": len(skill["cases"]),
            "skills": len(covered),
            "skills_missing_confusion": missing_confusion,
            "fixture_positive": fixture_positive,
            "fixture_positive_with_pinned_anchor": pinned_positive,
        },
        "multidomain": {
            "cases": len(multidomain["cases"]),
            "review_states": dict(sorted(review_states.items())),
            "review_authority": "independent-reviewer-owned-not-self-approved",
        },
        "factorial": {
            "frozen_components": factorial["factorial"].get("components"),
            "matrix_admitted_by_code": True,
            "model_run_observed": False,
        },
        "qualification_policy": (
            "Code/CI qualification is not an empirical campaign. New controlled "
            "model runs and independent case reviews cannot be inferred from "
            "committed experiment definitions or green unit tests."
        ),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    print(json.dumps(build_readiness(root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
