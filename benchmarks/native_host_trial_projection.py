#!/usr/bin/env python3
"""Project already-collected host trial exports into the paired evaluation contracts.

No synthetic receipts, scoring guesses, or provider-specific trace parsing.
The export MUST be created by an independently trusted native-host adapter.
"""
import argparse
import json
import sys
from pathlib import Path

from paired_host_capture_audit import audit


def _string(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: missing string")
    return value


def project(export):
    if not isinstance(export, dict) or export.get("schema") != "agentscookbook.native-host-trials.v1":
        raise ValueError("unsupported native host export schema")
    trials = export.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("no native host trials")
    tasks = {}
    capture = []
    seen_trials, seen_deliveries = set(), set()
    for i, trial in enumerate(trials):
        where = f"trials[{i}]"
        if not isinstance(trial, dict):
            raise ValueError(f"{where}: not an object")
        task = _string(trial.get("task_id"), where)
        arm = trial.get("arm")
        if arm not in ("baseline", "treatment"):
            raise ValueError(f"{where}: invalid arm")
        agent = _string(trial.get("native_agent"), where)
        if agent not in ("opencode-native", "codex-native"):
            raise ValueError(f"{where}: unsupported native agent")
        authority = _string(trial.get("matched_authority_sha256"), where)
        if len(authority) != 64 or any(c not in "0123456789abcdef" for c in authority):
            raise ValueError(f"{where}: invalid matched authority digest")
        for key in ("first_choice_owner_correct", "patch_test_correct", "host_attested"):
            if type(trial.get(key)) is not bool:
                raise ValueError(f"{where}: {key} must be boolean")
        if not trial["host_attested"]:
            raise ValueError(f"{where}: host attestation absent")
        reads = trial.get("exploratory_native_source_reads")
        if type(reads) is not int or reads < 0:
            raise ValueError(f"{where}: native reads missing/invalid")
        host_trial = _string(trial.get("host_trial_id"), where)
        delivery = _string(trial.get("delivery_id"), where)
        sha = _string(trial.get("payload_sha256"), where)
        path = _string(trial.get("payload_path"), where)
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError(f"{where}: invalid payload digest")
        if host_trial in seen_trials or delivery in seen_deliveries:
            raise ValueError(f"{where}: duplicate host trial or delivery")
        seen_trials.add(host_trial)
        seen_deliveries.add(delivery)
        record = tasks.setdefault(task, {})
        if arm in record:
            raise ValueError(f"{where}: duplicate task/arm")
        record[arm] = {
            "matched_authority_sha256": authority,
            "native_agent": agent,
            "first_choice_owner_correct": trial["first_choice_owner_correct"],
            "exploratory_native_source_reads": reads,
            "patch_test_correct": trial["patch_test_correct"],
            "delivery": {
                "host_attested": True,
                "host_trial_id": host_trial,
                "delivery_id": delivery,
                "payload_sha256": sha,
                "visible_payload_sha256": sha,
            },
        }
        capture.append({
            "task_id": task, "arm": arm, "host_trial_id": host_trial,
            "delivery_id": delivery, "payload_sha256": sha, "payload_path": path,
        })
    pairs = []
    for task in sorted(tasks):
        arms = tasks[task]
        if set(arms) != {"baseline", "treatment"}:
            raise ValueError(f"{task}: unmatched native host trial")
        if arms["baseline"]["matched_authority_sha256"] != arms["treatment"]["matched_authority_sha256"]:
            raise ValueError(f"{task}: cross-arm experiment authority mismatch")
        if arms["baseline"]["native_agent"] != arms["treatment"]["native_agent"]:
            raise ValueError(f"{task}: cross-arm native agent mismatch")
        pairs.append({
            "task_id": task,
            **{arm: {k: v for k, v in arms[arm].items()
                     if k not in ("matched_authority_sha256", "native_agent")}
               for arm in ("baseline", "treatment")},
        })
    return (
        {"schema": "agentscookbook.paired-outcome.v1", "pairs": pairs},
        {"schema": "agentscookbook.host-capture.v1", "entries": sorted(capture, key=lambda x: (x["task_id"], x["arm"]))},
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_export", type=Path)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        source = json.loads(args.native_export.read_text(encoding="utf-8"))
        paired, manifest = project(source)
        decision = audit(paired, manifest, args.capture_root)
        if args.output_dir.exists():
            raise ValueError("output directory already exists; refusing overwrite")
        args.output_dir.mkdir(parents=True, exist_ok=False)
        for name, value in (
            ("paired-campaign.json", paired),
            ("host-capture-manifest.json", manifest),
            ("audited-decision.json", decision),
        ):
            (args.output_dir / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"INADMISSIBLE: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"qualification": decision["qualification"], "pairs": decision["matched_tasks"],
                      "output_dir": str(args.output_dir)}, sort_keys=True))
    return 0 if decision["qualification"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
