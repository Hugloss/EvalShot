"""Deterministic independent-review handoff for evaluation authority gaps.

Produces a work queue, never reviewer decisions. No generated queue, model
answer, or green CI is an independent approval or a fixture-grounded oracle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "agentscookbook.eval-independent-review-queue.v1"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def build_review_queue(skill: Mapping[str, Any], multidomain: Mapping[str, Any]) -> dict[str, Any]:
    if (not isinstance(skill, Mapping) or not isinstance(skill.get("cases"), list)
            or not isinstance(multidomain, Mapping)
            or not isinstance(multidomain.get("cases"), list)):
        raise ValueError("invalid-review-corpora")
    skills: dict[str, set[str]] = {}
    skill_ids: set[str] = set()
    for item in skill["cases"]:
        if not isinstance(item, dict):
            raise ValueError("invalid-skill-case")
        name, kind, case_id = item.get("skill"), item.get("kind"), item.get("id")
        if (not all(isinstance(v, str) and v and len(v) <= 256 for v in (name, kind, case_id))
                or case_id in skill_ids):
            raise ValueError("invalid-or-duplicate-skill-identity")
        skill_ids.add(case_id)
        skills.setdefault(name, set()).add(kind)
    cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for case in multidomain["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError("invalid-multidomain-case")
        case_id = case["id"]
        if not case_id or case_id in case_ids or len(case_id) > 256:
            raise ValueError("duplicate-or-invalid-multidomain-identity")
        case_ids.add(case_id)
        review = case.get("review")
        if not isinstance(review, dict):
            raise ValueError("missing-review-authority")
        state = review.get("state")
        if state not in ("pending", "approved", "escalated", "rejected"):
            raise ValueError("unknown-review-state")
        # Even an "approved" field can be self-asserted; until an independently
        # verified reviewer receipt exists, keep it in the validation queue.
        frozen = {key: value for key, value in case.items() if key != "review"}
        cases.append({
            "id": case_id,
            "family": case.get("family"),
            "repository": case.get("repository_name"),
            "frozen_case_sha256": _digest(frozen),
            "current_review_state": state,
            "required_action": (
                "verify-independent-approval" if state == "approved"
                else "independent-oracle-review"
            ),
            "independent_approval_supplied_by_this_queue": False,
        })
    confusion = [{
        "skill": name,
        "required_action": "author-and-independently-validate-confusion-case",
        "fixture_and_oracle_required": True,
        "confusion_case_approved_by_this_queue": False,
    } for name, kinds in sorted(skills.items()) if "confusion" not in kinds]
    return {
        "schema": SCHEMA,
        "reviewed_corpus_is_immutable": True,
        "multidomain_cases_total": len(case_ids),
        "multidomain_review_required": len(cases),
        "skills_total": len(skills),
        "skills_missing_confusion": len(confusion),
        "multidomain_queue": sorted(cases, key=lambda x: x["id"]),
        "skill_confusion_queue": confusion,
        "independent_review_completed": False,
        "qualification_policy": (
            "Only independent reviewers working from pinned sources can "
            "approve oracle cases; a generated queue is not their review. "
            "Do not generate missing confusion cases from scenario text "
            "alone, and never count them as evaluated before fixture/oracle "
            "authority is independently established."
        ),
    }


def build_repo_review_queue(root: Path) -> dict[str, Any]:
    paths = (
        root / "evals/sharp-skill-cases.json",
        root / "benchmarks/suites/repository-intelligence/multidomain-v2/evidence.json",
    )
    docs = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("review-corpus-not-a-regular-file")
        docs.append(json.loads(path.read_text(encoding="utf-8")))
    return build_review_queue(docs[0], docs[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    print(json.dumps(build_repo_review_queue(args.repo_root.resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
