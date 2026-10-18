#!/usr/bin/env python3
"""Fail-closed, paired held-out outcome qualification. Stdlib only.

Input is independently collected host evidence, not a model self-report.
This is a qualification *consumer*; it does not fabricate or infer host receipts.
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


def _required(obj, keys, where):
    if not isinstance(obj, dict) or any(k not in obj for k in keys):
        raise ValueError(f"{where}: missing required fields {sorted(set(keys) - set(obj) if isinstance(obj, dict) else keys)}")


def _bool(value, where):
    if type(value) is not bool:
        raise ValueError(f"{where}: expected boolean")
    return value


def _int(value, where):
    if type(value) is not int or value < 0:
        raise ValueError(f"{where}: expected nonnegative integer")
    return value


def _receipt(row, where):
    _required(row, ("host_attested", "payload_sha256", "visible_payload_sha256", "delivery_id", "host_trial_id"), where)
    if not _bool(row["host_attested"], where) or not row["host_trial_id"] or not row["delivery_id"]:
        raise ValueError(f"{where}: missing host attestation / identity")
    for name in ("payload_sha256", "visible_payload_sha256"):
        v = row[name]
        if not isinstance(v, str) or len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
            raise ValueError(f"{where}: invalid {name}")
    if row["payload_sha256"] != row["visible_payload_sha256"]:
        raise ValueError(f"{where}: returned payload differs from host-attested model-visible payload")


def evaluate(doc):
    _required(doc, ("schema", "pairs"), "campaign")
    if doc["schema"] != "agentscookbook.paired-outcome.v1":
        raise ValueError("unsupported schema")
    pairs = doc["pairs"]
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("no pairs: cannot qualify")
    task_ids, trial_ids, delivery_ids = set(), set(), set()
    owner_baseline = owner_treatment = reads_baseline = reads_treatment = 0
    corrected = regressed = 0
    for index, pair in enumerate(pairs):
        where = f"pairs[{index}]"
        _required(pair, ("task_id", "baseline", "treatment"), where)
        task_id = pair["task_id"]
        if not isinstance(task_id, str) or not task_id or task_id in task_ids:
            raise ValueError(f"{where}: missing or duplicate task_id")
        task_ids.add(task_id)
        for arm in ("baseline", "treatment"):
            row = pair[arm]
            loc = f"{where}.{arm}"
            _required(row, ("first_choice_owner_correct", "exploratory_native_source_reads", "patch_test_correct", "delivery"), loc)
            _bool(row["first_choice_owner_correct"], loc)
            _bool(row["patch_test_correct"], loc)
            _int(row["exploratory_native_source_reads"], loc)
            _receipt(row["delivery"], loc)
            receipt = row["delivery"]
            if receipt["host_trial_id"] in trial_ids or receipt["delivery_id"] in delivery_ids:
                raise ValueError(f"{loc}: duplicate trial/delivery identity")
            trial_ids.add(receipt["host_trial_id"])
            delivery_ids.add(receipt["delivery_id"])
        b, t = pair["baseline"], pair["treatment"]
        owner_baseline += b["first_choice_owner_correct"]
        owner_treatment += t["first_choice_owner_correct"]
        reads_baseline += b["exploratory_native_source_reads"]
        reads_treatment += t["exploratory_native_source_reads"]
        corrected += int(not b["patch_test_correct"] and t["patch_test_correct"])
        regressed += int(b["patch_test_correct"] and not t["patch_test_correct"])
    n = len(pairs)
    owner_gain_pp = 100 * (owner_treatment - owner_baseline) / n
    read_reduction = None if reads_baseline == 0 else (reads_baseline - reads_treatment) / reads_baseline
    # Exact one-sided McNemar / sign test: H0 treatment is no worse.
    # A credible regression needs discordant-task evidence (not raw trial count).
    discordant = corrected + regressed
    p_regression = sum(math.comb(discordant, k) for k in range(regressed, discordant + 1)) / (2 ** discordant) if discordant else 1.0
    correctness_ok = p_regression >= 0.05
    gain_ok = owner_gain_pp >= 10.0 - 1e-9
    read_ok = read_reduction is not None and read_reduction >= 0.20 - 1e-9
    return {
        "schema": "agentscookbook.paired-outcome-result.v1",
        "matched_tasks": n,
        "host_attested_trials": n * 2,
        "first_choice_owner": {"baseline_correct": owner_baseline, "treatment_correct": owner_treatment, "improvement_percentage_points": owner_gain_pp, "target_percentage_points": 10, "pass": gain_ok},
        "exploratory_native_source_reads": {"baseline": reads_baseline, "treatment": reads_treatment, "reduction_fraction": read_reduction, "target_fraction": 0.20, "pass": read_ok},
        "patch_test_correctness": {"baseline_only_correct": regressed, "treatment_only_correct": corrected, "one_sided_exact_regression_p": p_regression, "alpha": 0.05, "no_credible_regression": correctness_ok},
        "model_visible_delivery": {"all_trials_exact_host_attested": True},
        "qualification": "PASS" if gain_ok and read_ok and correctness_ok else "FAIL",
        "note": "Not proof of causality or absence of regression; small campaigns can be underpowered. Require independently frozen task matching, oracle and host traces upstream.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path, help="JSON of matched held-out task pairs")
    parser.add_argument("--output", type=Path, help="write deterministic decision JSON")
    args = parser.parse_args(argv)
    try:
        result = evaluate(json.loads(args.campaign.read_text(encoding="utf-8")))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"INADMISSIBLE: {exc}", file=sys.stderr)
        return 2
    result_text = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(result_text, encoding="utf-8")
    else:
        print(result_text, end="")
    return 0 if result["qualification"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
