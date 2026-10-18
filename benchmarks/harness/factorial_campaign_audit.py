"""Exact matrix-coverage audit for model-backed Harbor factorial campaigns.

Consumes immutable, verified Harbor bundle projections. A complete grid is
necessary but NOT sufficient for an empirical effectiveness/causality claim.
No tool-result, agent prose, or self-reported receipt proves model delivery.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from benchmarks.harbor_matrix import load_matrix, matrix_factorial

from .factorial_attribution import ROLES, factorial_quartet
from .mechanism_attribution import (
    MechanismAttributionError,
    load_harbor_bundle_projection,
)

SCHEMA = "agentscookbook.factorial-campaign-coverage.v1"
MAX_EXPECTED_CELLS = 10_000


def _axis(value: object, label: str) -> list[str]:
    if (not isinstance(value, list) or not value
            or not all(isinstance(x, str) and x and len(x) <= 256 for x in value)
            or len(set(value)) != len(value)):
        raise ValueError("invalid-matrix-" + label)
    return value


def _expected(matrix: Mapping[str, Any], mode: str) -> tuple[list[str], list[str], list[str], int, dict[str, Any]]:
    if mode not in ("smoke", "matrix"):
        raise ValueError("unsupported-factorial-mode")
    harnesses = _axis(matrix.get("harnesses"), "harnesses")
    subjects = _axis(matrix.get("subjects"), "subjects")
    modes = matrix.get("modes")
    spec = modes.get(mode) if isinstance(modes, dict) else None
    if not isinstance(spec, dict):
        raise ValueError("missing-matrix-mode")
    tasks = _axis(spec.get("tasks"), "tasks")
    attempts = spec.get("attempts")
    if type(attempts) is not int or not 1 <= attempts <= 20:
        raise ValueError("invalid-matrix-attempts")
    contract = matrix_factorial(dict(matrix))
    if not isinstance(contract, dict) or set(contract["arms"].values()) - set(subjects):
        raise ValueError("factorial-arms-not-in-matrix")
    if subjects.count("none") != 1 or set(subjects) != set(contract["arms"].values()) | {"none"}:
        raise ValueError("factorial-control-or-projection-mismatch")
    if len(harnesses) * len(subjects) * len(tasks) * attempts > MAX_EXPECTED_CELLS:
        raise ValueError("factorial-matrix-too-large")
    return harnesses, subjects, tasks, attempts, contract


def audit_factorial_campaign(
    projections: list[Mapping[str, Any]],
    matrix: Mapping[str, Any],
    *,
    mode: str,
    campaign_id: str,
    corrupt_bundles: int = 0,
) -> dict[str, Any]:
    """Require every control + 4 arms at the exact task/harness/replicate grid.

    No absent row, ungradeable outcome, foreign campaign, duplicate cell,
    mixed model, source contract drift or unqualified arm is silently dropped.
    """
    if not isinstance(campaign_id, str) or not campaign_id or len(campaign_id) > 256:
        raise ValueError("explicit-campaign-id-required")
    if type(corrupt_bundles) is not int or corrupt_bundles < 0:
        raise ValueError("invalid-corrupt-bundle-count")
    harnesses, subjects, tasks, attempts, contract = _expected(matrix, mode)
    expected_keys = {
        (h, t, replicate, subject)
        for h in harnesses for t in tasks
        for replicate in range(1, attempts + 1)
        for subject in subjects
    }
    cells: dict[tuple[str, str, int, str], Mapping[str, Any]] = {}
    issues: Counter[str] = Counter()
    models: dict[str, set[str]] = defaultdict(set)
    source_ids: set[str] = set()
    for item in projections:
        if not isinstance(item, Mapping):
            issues["invalid-projection"] += 1
            continue
        receipt = item.get("receipt")
        if not isinstance(receipt, Mapping):
            issues["missing-receipt"] += 1
            continue
        execution = receipt.get("execution")
        if not isinstance(execution, Mapping) or execution.get("campaign_id") != campaign_id:
            issues["foreign-or-missing-campaign-id"] += 1
            continue
        key = (receipt.get("harness"), receipt.get("task_id"),
               receipt.get("replicate_id"), receipt.get("subject"))
        if (
            type(key[2]) is not int
            or not all(isinstance(value, str) for value in (key[0], key[1], key[3]))
            or key not in expected_keys
        ):
            issues["foreign-grid-cell"] += 1
            continue
        if key in cells:
            issues["duplicate-grid-cell"] += 1
            continue
        cells[key] = item
        model = receipt.get("model")
        if not isinstance(model, str) or not model or len(model) > 256:
            issues["missing-or-invalid-model"] += 1
        else:
            models[str(key[0])].add(model)
        if receipt.get("status") not in ("PASS", "FAIL"):
            issues["incomplete-or-ungradeable-status"] += 1
        trace = item.get("trace")
        if (not isinstance(trace, Mapping) or trace.get("available") is not True
                or trace.get("tool_order_complete") is not True):
            issues["incomplete-tool-order"] += 1
        elif key[3] == "none" and trace.get("subject_tools") != []:
            issues["contaminated-bare-control"] += 1
        if key[3] != "none":
            treatment = execution.get("mcp_treatment")
            identity = treatment.get("source_contract_identity") if isinstance(treatment, Mapping) else None
            if not isinstance(identity, str) or not identity or len(identity) > 512:
                issues["missing-source-contract-identity"] += 1
            else:
                source_ids.add(identity)
    if len(source_ids) > 1:
        issues["source-contract-drift"] += 1
    for harness in harnesses:
        if len(models[harness]) != 1:
            issues["missing-or-mixed-model-per-harness"] += 1
    missing = expected_keys - set(cells)
    if missing:
        issues["missing-grid-cell"] = len(missing)
    qualified_quartets = 0
    quartet_reasons: Counter[str] = Counter()
    for h in harnesses:
        for t in tasks:
            for rep in range(1, attempts + 1):
                arms = {
                    role: cells.get((h, t, rep, subject))
                    for role, subject in contract["arms"].items()
                }
                if any(v is None for v in arms.values()):
                    continue
                try:
                    result = factorial_quartet(arms, contract)
                except (MechanismAttributionError, ValueError, KeyError, TypeError):
                    quartet_reasons["invalid-factorial-contract-or-receipt"] += 1
                    continue
                if result.get("treatment_qualified") is True:
                    qualified_quartets += 1
                else:
                    quartet_reasons[str(result.get("qualification_reason", "unknown"))] += 1
    for reason, count in quartet_reasons.items():
        issues["factorial:" + reason] += count
    expected_quartets = len(harnesses) * len(tasks) * attempts
    if qualified_quartets != expected_quartets:
        issues["unqualified-or-missing-quartet"] = expected_quartets - qualified_quartets
    if corrupt_bundles:
        issues["corrupt-or-unverifiable-bundle"] += corrupt_bundles
    complete = not issues and len(cells) == len(expected_keys)
    return {
        "schema": SCHEMA,
        "campaign_id": campaign_id,
        "mode": mode,
        "coverage_state": "COMPLETE" if complete else "INCOMPLETE",
        "matrix_expected_cells": len(expected_keys),
        "observed_unique_cells": len(cells),
        "input_projections": len(projections),
        "qualified_complete_quartets": qualified_quartets,
        "matrix_expected_quartets": expected_quartets,
        "issues": dict(sorted(issues.items())),
        "per_harness_model_identity": {
            h: (next(iter(models[h])) if len(models[h]) == 1 else None)
            for h in harnesses
        },
        "model_input_delivery_attested": False,
        "independent_oracle_review_attested": False,
        "empirical_effect_qualified": False,
        "interpretation": (
            "COMPLETE means only that all expected verified bundle cells are "
            "present and internally consistent. It does not prove independent "
            "oracle review, model delivery, intervention randomization, "
            "causal effect, or product uplift."
        ),
    }


def audit_bundle_directory(
    root: Path, matrix: Mapping[str, Any], *, mode: str, campaign_id: str,
) -> dict[str, Any]:
    projections: list[Mapping[str, Any]] = []
    corrupt = 0
    if root.is_dir() and not root.is_symlink():
        for directory in sorted(root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            if directory.is_symlink():
                corrupt += 1
                continue
            try:
                projections.append(load_harbor_bundle_projection(directory))
            except (MechanismAttributionError, OSError, ValueError, TypeError):
                corrupt += 1
    else:
        corrupt += 1
    return audit_factorial_campaign(
        projections, matrix, mode=mode,
        campaign_id=campaign_id, corrupt_bundles=corrupt,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--matrix", type=Path, default=Path(
        "benchmarks/harbor/repository-intelligence-task-evidence-find-factorial-v1.json"))
    parser.add_argument("--mode", choices=("smoke", "matrix"), required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    report = audit_bundle_directory(
        args.results_root, load_matrix(args.matrix),
        mode=args.mode, campaign_id=args.campaign_id,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if args.require_complete and report["coverage_state"] != "COMPLETE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
