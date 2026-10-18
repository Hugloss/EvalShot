"""Harbor matrix commands using the ordinary numbered benchmark run store."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from benchmarks.config import BenchmarkConfig
from benchmarks.harness.campaign import campaign_status
from benchmarks.harness.campaign_authority import (
    read_campaign,
    verify_campaign_suite_authority,
)
from benchmarks.harness.campaign_execution import run_campaign_rows
from benchmarks.harness.harbor_backend import (
    HarborBackendError,
    admit_harbor_run,
    credential_file,
    harbor_report,
    mcp_configs,
    observed_preflight,
    run_harbor_trial,
    settings_from_config,
    verify_harbor_run,
)
from benchmarks.harness.run_store import (
    exclusive_store,
    list_saved_runs,
    prepare_saved_run,
    select_saved_run,
    store_is_active,
)
from benchmarks.matrix_profiles import MatrixProfile, harbor_suite


def _write_report(run_root: Path, name: str, value: dict[str, Any]) -> Path:
    """Write only derived output; execution evidence is owned by receipts."""
    from benchmarks.harness.identity import canonical_json
    from benchmarks.harness.receipt import _promote

    directory = run_root / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    _promote(path, canonical_json(value))
    return path


def _selected_rows(suite) -> tuple[list[dict[str, Any]], set[str]]:
    rows = suite.trial_definitions()
    return rows, {str(row["definition_id"]) for row in rows}


def _inspect(run_root: Path, run_id: str, suite, selected: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    campaign = read_campaign(run_root / "results")
    if campaign.get("backend") != "harbor":
        raise HarborBackendError("selected run is not a Harbor matrix")
    verify_campaign_suite_authority(suite=suite, campaign=campaign)
    if set(campaign["selected_definitions"]) != selected:
        raise HarborBackendError("saved Harbor trial population differs from the selected matrix")
    status = campaign_status(
        suite=suite,
        results_root=run_root / "results",
        selected_definitions=selected,
    )
    status.update({"run_id": run_id, "run_root": str(run_root), "backend": "harbor"})
    return campaign, status


def run_harbor_command(args, profile: MatrixProfile) -> int:
    if args.command not in {
        "check", "doctor", "plan", "preflight", "campaign-audit",
        "run", "runs", "status", "report", "explain", "ablation", "reports", "score",
    }:
        raise HarborBackendError(f"{args.command} is unavailable for Harbor matrices")
    if getattr(args, "task", []) or getattr(args, "subject", []) or getattr(args, "condition", None):
        raise HarborBackendError("Harbor matrix trial selection is frozen by MATRIX")
    if getattr(args, "agent", []):
        raise HarborBackendError("Harbor harness selection is frozen by MATRIX")
    for name in ("suite", "cache", "work", "results", "source", "codex_auth", "harness_root"):
        if getattr(args, name, None) is not None:
            raise HarborBackendError(f"--{name.replace('_', '-')} is owned by the selected Harbor matrix")
    if args.command == "score" and any(
        getattr(args, name, None)
        for name in ("output", "score_script", "require_analysis_evidence")
    ):
        raise HarborBackendError("Harbor score is fixed by the selected matrix; use benchmark-report")
    suite, projection = harbor_suite(profile)
    rows, selected = _selected_rows(suite)
    if args.command == "plan":
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    root = (getattr(args, "root", None) or profile.root)
    if root is None:
        raise HarborBackendError("Harbor matrix has no run root")
    root = root.resolve()

    if args.command in {"check", "doctor", "preflight", "campaign-audit", "run"}:
        config = BenchmarkConfig.load(args.env_file)
        settings = replace(settings_from_config(config, profile), root=root)
        observed = observed_preflight(
            settings=settings,
            suite=suite,
            projection=projection,
            profile=profile,
            host=config.host,
        )
        if args.command in {"check", "doctor", "preflight", "campaign-audit"}:
            print(json.dumps({"matrix": profile.name, "expected_trials": len(rows), "preflight": observed}, indent=2, sort_keys=True))
            return 0
    else:
        config = None
        settings = None
        observed = None

    if args.command == "runs":
        saved = list_saved_runs(root)
        print(json.dumps({
            "matrix": profile.name,
            "latest": saved[-1].run_id if saved else None,
            "runs": [{"run_id": item.run_id, "root": str(item.root)} for item in saved],
        }, indent=2, sort_keys=True))
        return 0

    if args.command == "run":
        assert config is not None and settings is not None and observed is not None
        if args.resume and not args.run_id:
            raise HarborBackendError("resume requires --run-id (make RUN_ID=000001)")
        with exclusive_store(root):
            if args.auto:
                saved_runs = list_saved_runs(root)
                if saved_runs:
                    latest = saved_runs[-1]
                    campaign, status = _inspect(latest.root, latest.run_id, suite, selected)
                    if not status["complete"]:
                        raise HarborBackendError(
                            f"run {latest.run_id} is unfinished; choose --resume --run-id {latest.run_id} or --new"
                        )
            if args.new or args.auto:
                saved, campaign = prepare_saved_run(
                    root,
                    lambda staged: admit_harbor_run(
                        staged,
                        suite=suite,
                        profile=profile,
                        settings=settings,
                        preflight_receipt=observed,
                    ),
                )
            else:
                saved = select_saved_run(root, args.run_id)
                campaign = read_campaign(saved.root / "results")
                verify_harbor_run(
                    saved.root,
                    campaign,
                    suite=suite,
                    profile=profile,
                    preflight_receipt=observed,
                )
            print(
                f"RUN {saved.run_id} ({saved.root}) | MATRIX {profile.name} | {len(rows)} trials",
                file=sys.stderr,
                flush=True,
            )
            credentials = credential_file(saved.root, settings=settings, host=config.host)
            mcp = mcp_configs(
                saved.root,
                preflight_receipt=campaign["preflight"],
            )
            _, initial = _inspect(saved.root, saved.run_id, suite, selected)
            initial_rows = {row["definition_id"]: row for row in initial["rows"]}
            results = []

            def on_start(row, index):
                condition = next(
                    value for value in suite.experiment["conditions"]
                    if value["id"] == row["condition_id"]
                )
                print(
                    f"BENCHMARK {index}/{len(rows)} | {row['task_id']} | "
                    f"{condition['agent']} | {condition['subject']} | replicate {row['replicate_id']}",
                    file=sys.stderr,
                    flush=True,
                )
                return None

            def on_result(row, result, _receipt):
                results.append({
                    "definition_id": result.definition_id,
                    "trial_id": result.trial_id,
                    "status": result.status,
                    "reused": result.reused,
                    "receipt": str(result.result_dir / "result.json"),
                })
                print(f"{row['task_id']} / {row['condition_id']}: {result.status}", file=sys.stderr, flush=True)

            run_campaign_rows(
                rows=rows,
                initial_rows=initial_rows,
                results_root=saved.root / "results",
                store_root=root,
                run_id=saved.run_id,
                run_one=lambda row: run_harbor_trial(
                    suite=suite,
                    row=row,
                    run_root=saved.root,
                    campaign=campaign,
                    settings=settings,
                    host=config.host,
                    credentials=credentials,
                    mcp=mcp,
                ),
                on_start=on_start,
                on_result=on_result,
            )
            _, status = _inspect(saved.root, saved.run_id, suite, selected)
            _write_report(saved.root, "status.json", status)
            report = harbor_report(
                suite=suite,
                results_root=saved.root / "results",
                selected_definitions=selected,
            )
            report.update({"run_id": saved.run_id, "run_root": str(saved.root), "matrix": profile.name})
            if status["qualified"]:
                _write_report(saved.root, "report.json", report)
                _write_report(saved.root, "score.json", report)
                _write_report(
                    saved.root,
                    "mechanism.json",
                    report["mechanism_attribution"],
                )
                if report["component_ablation"]["applicable"]:
                    _write_report(
                        saved.root,
                        "ablation.json",
                        report["component_ablation"],
                    )
                if report["tool_factorial"]["applicable"]:
                    _write_report(
                        saved.root,
                        "factorial.json",
                        report["tool_factorial"],
                    )
            else:
                (saved.root / "reports/report.json").unlink(missing_ok=True)
                (saved.root / "reports/score.json").unlink(missing_ok=True)
                (saved.root / "reports/mechanism.json").unlink(missing_ok=True)
                (saved.root / "reports/ablation.json").unlink(missing_ok=True)
                (saved.root / "reports/factorial.json").unlink(missing_ok=True)
            print(
                f"RUN SUMMARY verified {status['complete_trials']}/{status['expected_trials']} | "
                f"outcomes {json.dumps(status['outcomes'], sort_keys=True)} | qualified {status['qualified']}",
                file=sys.stderr,
                flush=True,
            )
            if not getattr(args, "no_json_results", False):
                print(json.dumps(results, indent=2, sort_keys=True))
            return 0 if status["qualified"] else 2

    saved = select_saved_run(root, getattr(args, "run_id", None))
    campaign, status = _inspect(saved.root, saved.run_id, suite, selected)
    if campaign.get("matrix_profile_identity") != profile.identity:
        raise HarborBackendError("selected matrix definition changed since run admission")
    if args.command == "status":
        if not store_is_active(root):
            with exclusive_store(root):
                _, status = _inspect(saved.root, saved.run_id, suite, selected)
                _write_report(saved.root, "status.json", status)
                if not status["qualified"]:
                    (saved.root / "reports/report.json").unlink(missing_ok=True)
                    (saved.root / "reports/score.json").unlink(missing_ok=True)
                    (saved.root / "reports/mechanism.json").unlink(missing_ok=True)
                    (saved.root / "reports/ablation.json").unlink(missing_ok=True)
                (saved.root / "reports/factorial.json").unlink(missing_ok=True)
        print(json.dumps(status, indent=2, sort_keys=True))
        return 2 if (
            status["conflicting_trials"]
            or status["corrupt_bundles"]
            or status["foreign_bundles"]
            or (getattr(args, "require_qualified", False) and not status["qualified"])
        ) else 0
    report = harbor_report(
        suite=suite,
        results_root=saved.root / "results",
        selected_definitions=selected,
    )
    report.update({"run_id": saved.run_id, "run_root": str(saved.root), "matrix": profile.name})
    if not store_is_active(root):
        with exclusive_store(root):
            _, status = _inspect(saved.root, saved.run_id, suite, selected)
            report = harbor_report(
                suite=suite,
                results_root=saved.root / "results",
                selected_definitions=selected,
            )
            report.update({"run_id": saved.run_id, "run_root": str(saved.root), "matrix": profile.name})
            _write_report(saved.root, "status.json", status)
            if status["qualified"]:
                _write_report(saved.root, "report.json", report)
                _write_report(
                    saved.root,
                    "mechanism.json",
                    report["mechanism_attribution"],
                )
                if report["component_ablation"]["applicable"]:
                    _write_report(
                        saved.root,
                        "ablation.json",
                        report["component_ablation"],
                    )
                if report["tool_factorial"]["applicable"]:
                    _write_report(
                        saved.root,
                        "factorial.json",
                        report["tool_factorial"],
                    )
                if args.command in {"score", "reports"}:
                    _write_report(saved.root, "score.json", report)
            else:
                (saved.root / "reports/report.json").unlink(missing_ok=True)
                (saved.root / "reports/score.json").unlink(missing_ok=True)
                (saved.root / "reports/mechanism.json").unlink(missing_ok=True)
                (saved.root / "reports/ablation.json").unlink(missing_ok=True)
                (saved.root / "reports/factorial.json").unlink(missing_ok=True)
    if args.command == "explain":
        mechanism = {
            **report["mechanism_attribution"],
            "run_id": saved.run_id,
            "run_root": str(saved.root),
            "matrix": profile.name,
            "campaign_qualified": status["qualified"],
        }
        print(json.dumps(mechanism, indent=2, sort_keys=True))
        return 0 if status["qualified"] or getattr(
            args, "allow_incomplete", False
        ) else 2
    if args.command == "ablation":
        component = report["component_ablation"]
        if component.get("applicable") is not True:
            raise HarborBackendError(
                "selected Harbor matrix has no component-ablation arms"
            )
        ablation = {
            **component,
            "run_id": saved.run_id,
            "run_root": str(saved.root),
            "matrix": profile.name,
            "campaign_qualified": status["qualified"],
        }
        print(json.dumps(ablation, indent=2, sort_keys=True))
        return 0 if status["qualified"] or getattr(
            args, "allow_incomplete", False
        ) else 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if status["qualified"] or (
        args.command == "report" and getattr(args, "allow_incomplete", False)
    ) else 2
