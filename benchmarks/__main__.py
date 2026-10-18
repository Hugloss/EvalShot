"""Command-line entry point for reusable empirical benchmark suites."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

from benchmarks.config import BenchmarkConfig, BenchmarkConfigError
from benchmarks.harbor_commands import run_harbor_command
from benchmarks.harness.harbor_backend import HarborBackendError
from benchmarks.matrix_profiles import MatrixProfileError, load_profile
from benchmarks.diagnostic import DiagnosticError, prepare_diagnostic_suite
from benchmarks.tool_probe import ToolProbeError, prepare_tool_probe_suite
from benchmarks.tool_probe_score import smoke_gate
from benchmarks.tool_routing import (
    catalog_admission,
    catalog_tool_names,
    routing_artifact_sha256,
)
from benchmarks.tool_routing_trace import (
    build_catalog_capture,
    exit_code as tool_routing_exit_code,
    score_trace as score_tool_routing_trace,
)
from benchmarks.hashmarks_retrieval_probe import (
    HashmarksRetrievalProbeError,
    run_hashmarks_retrieval_probe,
)
from benchmarks.openai_responses_routing_probe import (
    OpenAIRoutingProbeError,
    run_probe as run_openai_routing_probe,
)
from benchmarks.openai_routing_dogfood import (
    OpenAIRoutingDogfoodError,
    OpenAIRoutingSettings,
    campaign_exit_code as openai_dogfood_exit_code,
    list_dogfood_runs,
    preflight_campaign as preflight_openai_dogfood,
    routing_run_root,
    run_campaign as run_openai_dogfood,
    run_saved_campaign as run_saved_openai_dogfood,
    select_dogfood_run,
)
from benchmarks.harness.campaign import (
    CampaignError,
    campaign_status,
    resolve_campaign_paths,
)
from benchmarks.harness.campaign_authority import (
    CampaignAuthorityError,
    admit_campaign,
    audit_campaign,
    read_campaign,
    verify_campaign_suite_authority,
    verify_saved_campaign,
)
from benchmarks.harness.preflight import preflight_trial
from benchmarks.harness.decision_evidence import build_decision_evidence
from benchmarks.harness.derived_report_set import (
    DerivedReportSetError,
    REPORT_SET_MANIFEST,
    inspect_report_set,
    publish_report_set,
    verified_score_path,
)
from benchmarks.harness.oracle_reviews import (
    OracleReviewError,
    oracle_review_guide,
    oracle_reviews_declared,
    validate_oracle_reviews,
)
from benchmarks.harness.oracle_review_runner import run_pending_oracle_reviews
from benchmarks.harness.readiness import check_runtime_readiness
from benchmarks.harness.runtime_authority import required_runtime_authority
from benchmarks.harness.report import ReportError, build_report
from benchmarks.harness.trace_diagnostics import (
    TraceDiagnosticError,
    build_trace_diagnostics,
)
from benchmarks.harness.runner import (
    TrialRunnerError,
    run_trial,
)
from benchmarks.harness.campaign_execution import run_campaign_rows
from benchmarks.harness.run_store import (
    RunStoreError,
    active_definition,
    exclusive_store,
    list_saved_runs,
    prepare_saved_run,
    select_saved_run,
)
from benchmarks.harness.live_console import (
    LiveCampaignProgress,
    LiveTaskMatrix,
    TrialHeartbeat,
    render_campaign_admission,
    render_run_blockers,
    render_trial_failure,
)
from benchmarks.harness.selection import (
    SelectionError,
    parse_agent_arguments,
    select_definitions,
    select_scoring_definitions,
)
from benchmarks.harness.suite import load_runtime_suite, load_suite
from scripts.agent_economics.bounded_process import retain_lock_in_subprocesses


def _add_selectors(
    command: argparse.ArgumentParser,
    *,
    require_agent: bool = False,
) -> None:
    command.add_argument("--task", action="append", default=[])
    command.add_argument(
        "--agent",
        action="append",
        default=[],
        required=require_agent,
    )
    command.add_argument("--subject", action="append", default=[])
    command.add_argument("--condition")


def _add_campaign_paths(
    command: argparse.ArgumentParser,
    *,
    execution: bool,
) -> None:
    command.add_argument(
        "--root",
        type=Path,
        help=(
            "store of numbered campaigns; defaults to BENCHMARK_CAMPAIGN_ROOT"
        ),
    )
    if execution:
        command.add_argument("--cache", type=Path)
        command.add_argument("--work", type=Path)
    command.add_argument("--results", type=Path)
    command.add_argument("--run-id", help="select an older numbered run or legacy")


def _add_execution_inputs(command: argparse.ArgumentParser) -> None:
    command.add_argument("--harness-root", type=Path)
    command.add_argument(
        "--env-file",
        type=Path,
        required=True,
        help="explicit benchmark environment file; no file is auto-discovered",
    )
    command.add_argument("--source", type=Path)
    command.add_argument(
        "--codex-auth",
        type=Path,
        help="copy only this auth.json into isolated CODEX_HOME for Codex runs",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="./benchmark")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-suite")
    validate.add_argument("--suite", type=Path, required=True)

    diagnostic = sub.add_parser("diagnostic-prepare")
    diagnostic.add_argument("--suite", type=Path, required=True)
    diagnostic.add_argument("--score", type=Path, required=True)
    diagnostic.add_argument("--source-results", type=Path, required=True)
    diagnostic.add_argument("--output-suite", type=Path, required=True)
    diagnostic.add_argument("--include-task", action="append", default=[])

    trace_diagnostics = sub.add_parser("trace-diagnostics")
    trace_diagnostics.add_argument("--results", type=Path, required=True)
    trace_diagnostics.add_argument("--output", type=Path)

    tool_probe = sub.add_parser("tool-probe-prepare")
    tool_probe.add_argument("--suite", type=Path, required=True)
    tool_probe.add_argument("--subject", required=True)
    tool_probe.add_argument("--task", action="append", default=[])
    tool_probe.add_argument("--output-suite", type=Path, required=True)
    tool_probe.add_argument("--runtime-env-file", type=Path)
    tool_probe.add_argument("--reuse", action="store_true")

    probe_gate = sub.add_parser("tool-probe-smoke-gate")
    probe_gate.add_argument("--root", type=Path, required=True)
    probe_gate.add_argument("--subject", required=True)

    routing_catalog = sub.add_parser("tool-routing-catalog")
    routing_catalog.add_argument("--catalog", type=Path, required=True)
    routing_catalog.add_argument("--subject", required=True)
    routing_catalog.add_argument("--required-tool", required=True)

    routing_catalog.add_argument("--host")
    routing_catalog.add_argument("--capture-id")
    routing_catalog.add_argument("--output-capture", type=Path)

    routing_trace = sub.add_parser("tool-routing-trace")
    routing_trace.add_argument("--catalog", type=Path, required=True)
    routing_trace.add_argument("--trace", type=Path, required=True)
    routing_trace.add_argument("--subject", required=True)
    routing_trace.add_argument("--required-tool", required=True)
    routing_trace.add_argument("--output", type=Path)

    retrieval_probe = sub.add_parser("hashmarks-retrieval-probe")
    retrieval_probe.add_argument("--results", type=Path, required=True)
    retrieval_probe.add_argument("--task", required=True)
    retrieval_probe.add_argument("--workspace", type=Path, required=True)
    retrieval_probe.add_argument("--executable", type=Path, required=True)
    retrieval_probe.add_argument("--output", type=Path, required=True)

    openai_routing = sub.add_parser("openai-routing-probe")
    openai_routing.add_argument("--workspace", type=Path, required=True)
    openai_routing.add_argument("--handoff", type=Path, required=True)
    openai_routing.add_argument("--tunnel-client", type=Path, required=True)
    openai_routing.add_argument("--tunnel-id", required=True)
    openai_routing.add_argument("--model", required=True)
    openai_routing.add_argument("--prompt-file", type=Path, required=True)
    openai_routing.add_argument("--output-dir", type=Path, required=True)

    openai_preflight = sub.add_parser("openai-routing-preflight")
    openai_preflight.add_argument("--workspace", type=Path, required=True)
    openai_preflight.add_argument("--handoff", type=Path, required=True)
    openai_preflight.add_argument("--tunnel-client", type=Path, required=True)
    openai_preflight.add_argument("--tunnel-id", required=True)
    openai_preflight.add_argument("--model", required=True)
    openai_preflight.add_argument("--manifest", type=Path, required=True)
    openai_preflight.add_argument("--output", type=Path)

    openai_dogfood = sub.add_parser("openai-routing-dogfood")
    openai_dogfood.add_argument("--workspace", type=Path, required=True)
    openai_dogfood.add_argument("--handoff", type=Path, required=True)
    openai_dogfood.add_argument("--tunnel-client", type=Path, required=True)
    openai_dogfood.add_argument("--tunnel-id", required=True)
    openai_dogfood.add_argument("--model", required=True)
    openai_dogfood.add_argument("--manifest", type=Path, required=True)
    openai_dogfood.add_argument("--repeats", type=int, default=1)
    openai_dogfood.add_argument("--output-dir", type=Path, required=True)

    openai_easy_check = sub.add_parser("openai-routing-check")
    openai_easy_check.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
    )

    openai_easy_new = sub.add_parser("openai-routing-new")
    openai_easy_new.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
    )

    openai_easy_runs = sub.add_parser("openai-routing-runs")
    openai_easy_runs.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
    )

    openai_easy_status = sub.add_parser("openai-routing-status")
    openai_easy_status.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
    )
    openai_easy_status.add_argument("--run-id")

    reviews = sub.add_parser("oracle-review-check")
    reviews.add_argument("--suite", type=Path, required=True)
    reviews.add_argument("--require-complete", action="store_true")

    review = sub.add_parser("oracle-review")
    review.add_argument("--suite", type=Path, required=True)
    review.add_argument("--execute", action="store_true")
    review.add_argument("--cache", type=Path)

    check = sub.add_parser(
        "check",
        aliases=("doctor",),
        help="model-free runtime authority and native host diagnostics",
    )
    check.add_argument("--suite", type=Path)
    check.add_argument(
        "--agent",
        action="append",
        default=[],
        help="optional native agent filter; without it, check every suite agent",
    )
    check.add_argument(
        "--env-file",
        type=Path,
        required=True,
        help="explicit benchmark environment file; no file is auto-discovered",
    )

    plan = sub.add_parser("plan")
    plan.add_argument("--suite", type=Path)
    _add_selectors(plan)

    preflight = sub.add_parser("preflight")
    preflight.add_argument("--suite", type=Path)
    _add_selectors(preflight)
    _add_campaign_paths(preflight, execution=True)
    _add_execution_inputs(preflight)

    audit = sub.add_parser("campaign-audit")
    audit.add_argument("--suite", type=Path)
    _add_selectors(audit)
    _add_campaign_paths(audit, execution=True)
    _add_execution_inputs(audit)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--suite", type=Path)
    _add_selectors(prepare)
    _add_campaign_paths(prepare, execution=True)
    _add_execution_inputs(prepare)
    prepare.add_argument("--new", action="store_true", required=True)

    run = sub.add_parser("run")
    run.add_argument("--suite", type=Path)
    _add_selectors(run)
    _add_campaign_paths(run, execution=True)
    _add_execution_inputs(run)
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--auto", action="store_true")
    mode.add_argument("--new", action="store_true")
    mode.add_argument("--resume", action="store_true")
    run.add_argument(
        "--no-json-results",
        action="store_true",
        help=(
            "suppress the raw per-trial JSON list on stdout; "
            "durable JSON reports remain saved under the run reports directory"
        ),
    )

    runs = sub.add_parser("runs")
    runs.add_argument("--env-file", type=Path)
    runs.add_argument("--root", type=Path)

    status = sub.add_parser("status")
    status.add_argument("--suite", type=Path)
    status.add_argument("--env-file", type=Path)
    _add_selectors(status)
    _add_campaign_paths(status, execution=False)
    status.add_argument("--require-qualified", action="store_true")

    report = sub.add_parser("report")
    report.add_argument("--suite", type=Path)
    report.add_argument("--env-file", type=Path)
    _add_selectors(report)
    _add_campaign_paths(report, execution=False)
    report.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="report available valid receipts without requiring every frozen definition",
    )

    explain = sub.add_parser(
        "explain",
        help="explain paired Harbor outcome changes from observable mechanism evidence",
    )
    explain.add_argument("--env-file", type=Path)
    explain.add_argument("--root", type=Path)
    explain.add_argument("--run-id")
    explain.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="inspect mechanism evidence before the full Harbor matrix qualifies",
    )

    ablation = sub.add_parser(
        "ablation",
        help="report controlled Harbor component-ablation contrasts",
    )
    ablation.add_argument("--env-file", type=Path)
    ablation.add_argument("--root", type=Path)
    ablation.add_argument("--run-id")
    ablation.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="inspect ablation evidence before the full Harbor matrix qualifies",
    )

    reports = sub.add_parser("reports")
    reports.add_argument("--suite", type=Path)
    reports.add_argument("--env-file", type=Path, required=True)
    reports.add_argument("--root", type=Path)
    reports.add_argument("--run-id")
    reports.add_argument("--agent", action="append", default=[])

    score = sub.add_parser("score")
    score.add_argument("--env-file", type=Path, required=True)
    score.add_argument("--suite", type=Path)
    score.add_argument("--root", type=Path)
    score.add_argument("--agent", action="append", default=[])
    score.add_argument("--score-script", type=Path)
    score.add_argument("--output", type=Path)
    score.add_argument("--run-id")
    score.add_argument(
        "--require-analysis-evidence",
        action="store_true",
        help=(
            "fail unless the canonical score reports "
            "analysis_evidence.evidence_state=minimum-evidence-observed"
        ),
    )

    for command in (
        check,
        plan,
        preflight,
        audit,
        prepare,
        run,
        runs,
        status,
        report,
        explain,
        ablation,
        reports,
        score,
    ):
        command.add_argument(
            "--matrix",
            default="heldout",
            help="committed benchmark matrix id (default: heldout)",
        )

    regrade_score = sub.add_parser("regrade-score")
    regrade_score.add_argument("--suite", type=Path, required=True)
    regrade_score.add_argument("--source-results", type=Path, required=True)
    regrade_score.add_argument("--output", type=Path, required=True)
    regrade_score.add_argument("--agent", action="append", required=True)

    return parser


def _resolve_config(args: argparse.Namespace) -> BenchmarkConfig:
    try:
        config = BenchmarkConfig.load(getattr(args, "env_file", None))
        config.require_for(args.command)
        if hasattr(args, "suite") and args.suite is None:
            args.suite = config.path("BENCHMARK_SUITE_PATH")
        if (
            hasattr(args, "root")
            and args.root is None
            and getattr(args, "results", None) is None
        ):
            args.root = config.path("BENCHMARK_CAMPAIGN_ROOT")
        if hasattr(args, "harness_root") and args.harness_root is None:
            args.harness_root = config.path("BENCHMARK_HARNESS_REPO_ROOT")
        if hasattr(args, "agent") and not args.agent and args.command not in {"check", "doctor"}:
            args.agent = list(config.agents())
        if args.command == "score":
            args.score_script = args.score_script or config.path(
                "BENCHMARK_SCORE_SCRIPT_PATH"
            )
            args.output = args.output or config.path("BENCHMARK_SCORE_OUTPUT_PATH")
        if hasattr(args, "suite") and args.suite is None:
            raise BenchmarkConfigError("BENCHMARK_SUITE_PATH or --suite is required")
        if (
            args.command in {
                "preflight",
                "campaign-audit",
                "prepare",
                "run",
                "report",
                "reports",
                "score",
            }
            and not args.agent
        ):
            raise BenchmarkConfigError("BENCHMARK_AGENT or --agent is required")
        if (
            args.command in {"preflight", "campaign-audit", "prepare", "run"}
            and args.harness_root is None
        ):
            raise BenchmarkConfigError(
                "BENCHMARK_HARNESS_REPO_ROOT or --harness-root is required"
            )
        return config
    except BenchmarkConfigError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc


def _select(args, suite):
    try:
        return select_definitions(
            suite,
            tasks=tuple(args.task),
            agents=tuple(args.agent),
            subjects=tuple(args.subject),
            condition=args.condition,
        )
    except SelectionError as exc:
        raise SystemExit(str(exc)) from exc


def _paths(args, *, need_execution: bool, results_optional: bool = False):
    try:
        root = args.root
        run_id = None
        if root is not None:
            if any(
                value is not None
                for value in (getattr(args, "cache", None), getattr(args, "work", None), args.results)
            ):
                raise CampaignError("--root cannot be combined with --cache, --work, or --results")
            selected_run = select_saved_run(root, getattr(args, "run_id", None))
            root = selected_run.root
            run_id = selected_run.run_id
        elif getattr(args, "run_id", None) is not None:
            raise CampaignError("--run-id requires --root")
        paths = resolve_campaign_paths(
            root=root,
            cache=getattr(args, "cache", None),
            work=getattr(args, "work", None),
            results=args.results,
            need_execution=need_execution,
            results_optional=results_optional,
        )
        return replace(paths, run_id=run_id)
    except (CampaignError, RunStoreError) as exc:
        raise SystemExit(str(exc)) from exc


def _reports_dir(run_root: Path) -> Path:
    return run_root / "reports"


def _write_derived_json(run_root: Path, filename: str, payload: object) -> Path:
    """Atomically refresh one small, shareable derived report."""
    directory = _reports_dir(run_root)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / filename
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def _emit_run_results(results: list[dict[str, object]], *, enabled: bool) -> None:
    """Emit raw per-trial JSON when the selected caller keeps that channel enabled."""
    if enabled:
        print(json.dumps(results, indent=2, sort_keys=True))


def _score_environment(runtime_source) -> dict[str, str]:
    environment = dict(runtime_source)
    project_root = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (project_root, environment.get("PYTHONPATH", "")))
    )
    return environment


def _score_contract(config: BenchmarkConfig, suite) -> tuple[Path, Path]:
    """Resolve the one suite-owned scoring contract used by run completion."""
    script = (config.path("BENCHMARK_SCORE_SCRIPT_PATH") or (suite.root / "score.py")).resolve()
    output = config.path("BENCHMARK_SCORE_OUTPUT_PATH") or Path("score.json")
    if not script.is_file():
        raise ReportError(f"benchmark score script does not exist: {script}")
    if script.parent != suite.root.resolve():
        raise ReportError(
            f"benchmark score script must belong to selected suite: {suite.root}"
        )
    if output.is_absolute() or len(output.parts) != 1:
        raise ReportError(
            "BENCHMARK_SCORE_OUTPUT_PATH must be a filename within "
            "the selected run's reports directory"
        )
    return script, output


def _validate_score_cli(script: Path, runtime_source) -> None:
    """Prove the frozen-selection scorer interface before participant work."""
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        env=_score_environment(runtime_source),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ReportError(
            "benchmark score contract check failed"
            + (f": {detail}" if detail else "")
        )
    help_text = "\n".join((completed.stdout, completed.stderr))
    missing = [
        option
        for option in ("--results", "--output", "--agent", "--definition-id")
        if option not in help_text
    ]
    if missing:
        raise ReportError(
            "benchmark score script lacks required frozen-selection option(s): "
            + ", ".join(missing)
        )


def _validate_reporting_contract(config: BenchmarkConfig, suite, runtime_source) -> tuple[Path, Path]:
    script, output = _score_contract(config, suite)
    _validate_score_cli(script, runtime_source)
    return script, output


def _require_analysis_evidence(score_path: Path) -> None:
    try:
        payload = json.loads(score_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"cannot read benchmark score for analysis gate: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportError("benchmark score for analysis gate must be one JSON object")
    analysis = payload.get("analysis_evidence")
    if not isinstance(analysis, dict):
        raise ReportError("benchmark score has no analysis_evidence contract")
    state = analysis.get("evidence_state")
    if state != "minimum-evidence-observed":
        raise ReportError(
            "benchmark analysis evidence is below the frozen minimum"
            + (f": {state}" if isinstance(state, str) else "")
        )


def _canonical_campaign_persistence_gap(
    *,
    suite,
    results_root: Path,
    rows,
) -> str | None:
    manifest = read_campaign(results_root)
    selected = {str(row["definition_id"]) for row in rows}
    frozen = {str(value) for value in manifest["selected_definitions"]}
    if selected != frozen:
        return "selected definitions differ from frozen campaign"
    try:
        verify_campaign_suite_authority(suite=suite, campaign=manifest)
    except CampaignAuthorityError as exc:
        return str(exc)
    return None


def _same_saved_run_under_lock(
    *,
    store_root: Path,
    run_id: str | None,
    run_root: Path,
) -> None:
    """Prove canonical derived writes still target the run selected before locking."""
    saved = select_saved_run(store_root, run_id)
    if saved.root.resolve() != run_root.resolve():
        raise RunStoreError(
            "selected benchmark run changed before canonical persistence"
        )


def _refresh_canonical_status(
    *,
    store_root: Path,
    paths,
    suite,
    rows,
) -> tuple[dict[str, object] | None, str | None]:
    """Recompute and persist status only while owning the campaign store."""
    try:
        with exclusive_store(store_root):
            _same_saved_run_under_lock(
                store_root=store_root,
                run_id=paths.run_id,
                run_root=paths.root,
            )
            selected_definitions = {
                str(row["definition_id"]) for row in rows
            }
            status = campaign_status(
                suite=suite,
                results_root=paths.results,
                selected_definitions=selected_definitions,
            )
            status["paths"] = paths.as_dict()
            status["run_id"] = paths.run_id
            status["selection"] = _selection_metadata(suite, rows)
            status["paths"]["reports"] = str(_reports_dir(paths.root))
            status["derived_report_set"] = inspect_report_set(
                run_root=paths.root,
                expected_run_id=paths.run_id,
            )
            gap = _canonical_campaign_persistence_gap(
                suite=suite,
                results_root=paths.results,
                rows=rows,
            )
            if gap is not None:
                return None, gap
            _write_derived_json(paths.root, "status.json", status)
            return status, None
    except RunStoreError as exc:
        return None, f"run store is active or changed: {exc}"


def _derive_trace_and_decision(
    *,
    report_data: dict[str, object],
    results_root: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    """Build one deterministic diagnostic projection for every report path."""
    trace_diagnostics = build_trace_diagnostics(results_root)
    decision = build_decision_evidence(
        report_data,
        trace_diagnostics=trace_diagnostics,
    )
    return trace_diagnostics, decision


def _refresh_canonical_report(
    *,
    store_root: Path,
    paths,
    suite,
    rows,
    args,
    config: BenchmarkConfig,
    runtime_source,
) -> tuple[dict[str, object] | None, str | None]:
    """Refresh the entire canonical derived generation under the store lock."""
    try:
        with exclusive_store(store_root):
            _same_saved_run_under_lock(
                store_root=store_root,
                run_id=paths.run_id,
                run_root=paths.root,
            )
            gap = _canonical_campaign_persistence_gap(
                suite=suite,
                results_root=paths.results,
                rows=rows,
            )
            if gap is not None:
                return None, gap
            final_status = campaign_status(
                suite=suite,
                results_root=paths.results,
                selected_definitions={
                    str(row["definition_id"]) for row in rows
                },
            )
            if not final_status["complete"]:
                return None, "canonical report refresh requires a complete campaign"
            written = _persist_completed_run_reports(
                args=args,
                config=config,
                suite=suite,
                rows=rows,
                paths=paths,
                runtime_source=runtime_source,
                final_status=final_status,
            )
            report_data = json.loads(
                written["report"].read_text(encoding="utf-8")
            )
            if not isinstance(report_data, dict):
                raise ReportError("canonical report must be one JSON object")
            return report_data, None
    except (
        RunStoreError,
        ReportError,
        DerivedReportSetError,
        OSError,
        ValueError,
    ) as exc:
        return None, f"run store is active or changed: {exc}"


def _selection_metadata(suite, rows) -> dict[str, object]:
    """Project selection metadata from the exact resolved definition rows."""
    conditions = {
        str(condition["id"]): condition for condition in suite.experiment["conditions"]
    }
    condition_ids = sorted({str(row["condition_id"]) for row in rows})
    return {
        "tasks": sorted({str(row["task_id"]) for row in rows}),
        "agents": sorted(
            {
                str(conditions[str(row["condition_id"])]["agent"])
                for row in rows
            }
        ),
        "subjects": sorted(
            {
                str(conditions[str(row["condition_id"])]["subject"])
                for row in rows
            }
        ),
        "condition": condition_ids[0] if len(condition_ids) == 1 else None,
        "bare_control_included": any(
            conditions[str(row["condition_id"])]["subject"] == "none"
            for row in rows
        ),
    }


def _selected_agent_ids(suite, rows: list[dict[str, object]]) -> list[str]:
    conditions = {
        str(condition["id"]): condition
        for condition in suite.experiment["conditions"]
    }
    return sorted(
        {
            str(conditions[str(row["condition_id"])]["agent"])
            for row in rows
        }
    )


def _assert_saved_run_agents(
    saved: SavedRun,
    agents: list[str] | tuple[str, ...] | set[str],
) -> dict[str, object]:
    """Bind resume/scoring to the campaign's frozen agent population."""
    manifest = read_campaign(saved.root / "results")
    frozen_agents = sorted(str(value) for value in manifest.get("agents", {}))
    selected_agents = sorted(set(str(value) for value in agents))
    if frozen_agents != selected_agents:
        raise RunStoreError(
            f"saved run {saved.run_id} agent selection does not match "
            f"BENCHMARK_AGENT (frozen={frozen_agents}, "
            f"selected={selected_agents}); use the frozen agent set to resume "
            "or score, or start a new run"
        )
    return manifest


def _assert_saved_run_selection(
    saved: SavedRun,
    *,
    suite,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    """Fail before admission when selected tasks/conditions differ from the run."""
    selected_agents = _selected_agent_ids(suite, rows)
    manifest = _assert_saved_run_agents(saved, selected_agents)
    selected = {str(row["definition_id"]) for row in rows}
    frozen = set(manifest["selected_definitions"])
    if frozen != selected:
        raise RunStoreError(
            f"saved run {saved.run_id} frozen definition selection does not "
            "match the current benchmark selection; use the same task/subject/"
            "condition selection to resume, or use make benchmark-new"
        )
    return manifest


def _guard_automatic_start(
    root: Path,
    *,
    suite,
    rows: list[dict[str, object]],
) -> None:
    """Require an explicit choice only for an unfinished matching latest run."""
    saved_runs = list_saved_runs(root)
    if not saved_runs:
        return

    latest = saved_runs[-1]
    manifest = read_campaign(latest.root / "results")
    selected = {str(row["definition_id"]) for row in rows}
    frozen = set(manifest["selected_definitions"])
    if frozen != selected:
        print(
            f"BENCHMARK auto | latest run {latest.run_id} has a different "
            "frozen selection; starting a new run",
            file=sys.stderr,
            flush=True,
        )
        return

    status = campaign_status(
        suite=suite,
        results_root=latest.root / "results",
        selected_definitions=selected,
    )
    if status["complete"]:
        return

    raise RunStoreError(
        f"saved run {latest.run_id} is unfinished "
        f"({status['complete_trials']}/{status['expected_trials']} verified, "
        f"{status['pending_trials']} pending, "
        f"{status['interrupted_trials']} interrupted); choose explicitly: "
        "make benchmark-resume or make benchmark-new"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run" and args.resume and not args.run_id:
        raise SystemExit("run --resume requires --run-id; list saved runs first")
    try:
        matrix_profile = load_profile(getattr(args, "matrix", "heldout"))
        if matrix_profile.backend == "harbor":
            return run_harbor_command(args, matrix_profile)
        if args.command in {"explain", "ablation"}:
            raise MatrixProfileError(
                f"{args.command} is available only for Harbor matrices"
            )
    except (MatrixProfileError, HarborBackendError, RunStoreError, CampaignAuthorityError, OSError, ValueError) as exc:
        raise SystemExit(f"benchmark matrix unavailable: {exc}") from exc
    explicit_score_output = getattr(args, "output", None) is not None
    if args.command == "trace-diagnostics":
        try:
            evidence = build_trace_diagnostics(args.results)
        except (TraceDiagnosticError, OSError, ValueError) as exc:
            raise SystemExit(f"trace diagnostics unavailable: {exc}") from exc
        if args.output is None:
            print(json.dumps(evidence, indent=2, sort_keys=True))
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(str(args.output))
        return 0
    if args.command == "tool-probe-prepare":
        try:
            kwargs = {
                "source_suite": args.suite,
                "destination": args.output_suite,
                "subject": args.subject,
                "runtime_env_file": args.runtime_env_file,
                "reuse": args.reuse,
            }
            if args.task:
                kwargs["task_ids"] = tuple(args.task)
            evidence = prepare_tool_probe_suite(**kwargs)
        except (ToolProbeError, OSError, ValueError) as exc:
            raise SystemExit(f"tool probe unavailable: {exc}") from exc
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0
    if args.command == "tool-probe-smoke-gate":
        try:
            saved = select_saved_run(args.root)
            score_path = verified_score_path(
                run_root=saved.root,
                expected_run_id=saved.run_id,
            )
            score = json.loads(score_path.read_text(encoding="utf-8"))
            smoke_gate(score, subject=args.subject)
        except (
            RunStoreError,
            DerivedReportSetError,
            OSError,
            ValueError,
        ) as exc:
            raise SystemExit(f"tool probe smoke gate failed: {exc}") from exc
        print(f"tool probe smoke gate passed: {saved.run_id}")
        return 0
    if args.command == "tool-routing-catalog":
        try:
            payload = json.loads(args.catalog.read_text(encoding="utf-8"))
            capture_args = (args.host, args.capture_id, args.output_capture)
            if any(value is not None for value in capture_args):
                if not all(value is not None for value in capture_args):
                    raise ValueError(
                        "--host, --capture-id, and --output-capture must be supplied together"
                    )
                payload = build_catalog_capture(
                    payload,
                    host=args.host,
                    capture_id=args.capture_id,
                )
                args.output_capture.parent.mkdir(parents=True, exist_ok=True)
                args.output_capture.write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            names = catalog_tool_names(payload)
            evidence = catalog_admission(
                names,
                subject=args.subject,
                required_tool=args.required_tool,
            )
            evidence["catalog_sha256"] = routing_artifact_sha256(payload)
            if args.output_capture is not None:
                evidence["catalog_capture"] = str(args.output_capture)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"tool routing catalog unavailable: {exc}") from exc
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0 if evidence["status"] == "READY" else 2
    if args.command == "tool-routing-trace":
        try:
            catalog_payload = json.loads(args.catalog.read_text(encoding="utf-8"))
            trace_payload = json.loads(args.trace.read_text(encoding="utf-8"))
            evidence = score_tool_routing_trace(
                catalog_payload=catalog_payload,
                trace_payload=trace_payload,
                subject=args.subject,
                required_tool=args.required_tool,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(f"tool routing trace unavailable: {exc}") from exc
        rendered = json.dumps(evidence, indent=2, sort_keys=True)
        if args.output is None:
            print(rendered)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
            print(str(args.output))
        return tool_routing_exit_code(evidence)
    if args.command in {
        "openai-routing-check",
        "openai-routing-new",
        "openai-routing-runs",
        "openai-routing-status",
    }:
        try:
            if args.command in {
                "openai-routing-runs",
                "openai-routing-status",
            }:
                run_root = routing_run_root(args.env_file)
            else:
                settings = OpenAIRoutingSettings.load(args.env_file)

            if args.command == "openai-routing-runs":
                print(
                    json.dumps(
                        {
                            "schema": "agents-cookbook-openai-routing-runs.v1",
                            "runs": list_dogfood_runs(run_root),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 0
            if args.command == "openai-routing-status":
                selected = select_dogfood_run(
                    run_root,
                    args.run_id,
                )
                summary_path = Path(str(selected["path"])) / "summary.json"
                payload = dict(selected)
                if summary_path.is_file():
                    summary = json.loads(
                        summary_path.read_text(encoding="utf-8")
                    )
                    if isinstance(summary, dict):
                        payload["summary"] = summary
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0

            openai_api_key = os.environ.get("OPENAI_API_KEY", "")
            control_plane_api_key = os.environ.get(
                "CONTROL_PLANE_API_KEY",
                "",
            )
            if args.command == "openai-routing-check":
                evidence = preflight_openai_dogfood(
                    manifest_path=settings.manifest,
                    workspace=settings.workspace,
                    handoff_path=settings.handoff,
                    tunnel_client=settings.tunnel_client,
                    tunnel_id=settings.tunnel_id,
                    model=settings.model,
                    openai_api_key=openai_api_key,
                    control_plane_api_key=control_plane_api_key,
                )
                print(json.dumps(evidence, indent=2, sort_keys=True))
                return 0

            run_id, output_dir, summary = run_saved_openai_dogfood(
                settings=settings,
                openai_api_key=openai_api_key,
                control_plane_api_key=control_plane_api_key,
            )
            print(
                json.dumps(
                    {
                        "run_id": run_id,
                        "run_root": str(output_dir),
                        "summary": str(output_dir / "summary.json"),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return openai_dogfood_exit_code(summary)
        except (
            OSError,
            json.JSONDecodeError,
            OpenAIRoutingProbeError,
            OpenAIRoutingDogfoodError,
            ValueError,
        ) as exc:
            raise SystemExit(f"OpenAI routing benchmark unavailable: {exc}") from exc

    if args.command == "openai-routing-preflight":
        try:
            openai_api_key = os.environ.get("OPENAI_API_KEY", "")
            control_plane_api_key = os.environ.get(
                "CONTROL_PLANE_API_KEY",
                "",
            )
            evidence = preflight_openai_dogfood(
                manifest_path=args.manifest,
                workspace=args.workspace,
                handoff_path=args.handoff,
                tunnel_client=args.tunnel_client,
                tunnel_id=args.tunnel_id,
                model=args.model,
                openai_api_key=openai_api_key,
                control_plane_api_key=control_plane_api_key,
            )
        except (
            OSError,
            OpenAIRoutingProbeError,
            OpenAIRoutingDogfoodError,
            ValueError,
        ) as exc:
            raise SystemExit(f"OpenAI routing preflight unavailable: {exc}") from exc
        rendered = json.dumps(evidence, indent=2, sort_keys=True)
        if args.output is None:
            print(rendered)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
            print(str(args.output))
        return 0

    if args.command == "openai-routing-dogfood":
        try:
            openai_api_key = os.environ.get("OPENAI_API_KEY", "")
            control_plane_api_key = os.environ.get(
                "CONTROL_PLANE_API_KEY",
                "",
            )
            summary = run_openai_dogfood(
                manifest_path=args.manifest,
                workspace=args.workspace,
                handoff_path=args.handoff,
                tunnel_client=args.tunnel_client,
                tunnel_id=args.tunnel_id,
                model=args.model,
                repeats=args.repeats,
                output_dir=args.output_dir,
                openai_api_key=openai_api_key,
                control_plane_api_key=control_plane_api_key,
            )
        except (
            OSError,
            OpenAIRoutingProbeError,
            OpenAIRoutingDogfoodError,
            ValueError,
        ) as exc:
            raise SystemExit(f"OpenAI routing dogfood unavailable: {exc}") from exc
        print(str(args.output_dir / "summary.json"))
        return openai_dogfood_exit_code(summary)

    if args.command == "openai-routing-probe":
        try:
            prompt = args.prompt_file.read_text(encoding="utf-8")
            openai_api_key = os.environ.get("OPENAI_API_KEY", "")
            control_plane_api_key = os.environ.get(
                "CONTROL_PLANE_API_KEY",
                "",
            )
            receipt = run_openai_routing_probe(
                workspace=args.workspace,
                handoff_path=args.handoff,
                tunnel_client=args.tunnel_client,
                tunnel_id=args.tunnel_id,
                model=args.model,
                prompt=prompt,
                openai_api_key=openai_api_key,
                control_plane_api_key=control_plane_api_key,
            )
        except (OSError, OpenAIRoutingProbeError, ValueError) as exc:
            raise SystemExit(f"OpenAI routing probe unavailable: {exc}") from exc

        args.output_dir.mkdir(parents=True, exist_ok=True)
        artifacts = {
            "catalog.json": receipt["catalog"],
            "trace.json": receipt["trace"],
            "score.json": receipt["score"],
            "receipt.json": receipt,
        }
        for name, payload in artifacts.items():
            (args.output_dir / name).write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(str(args.output_dir / "receipt.json"))
        return tool_routing_exit_code(receipt["score"])
    if args.command == "hashmarks-retrieval-probe":
        try:
            evidence = run_hashmarks_retrieval_probe(
                results_root=args.results,
                task_id=args.task,
                workspace=args.workspace,
                executable=args.executable,
            )
        except (HashmarksRetrievalProbeError, OSError, ValueError) as exc:
            raise SystemExit(f"Hashmarks retrieval probe unavailable: {exc}") from exc
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(str(args.output))
        return 0
    if args.command == "oracle-review-check":
        suite = load_suite(args.suite)
        try:
            result = validate_oracle_reviews(
                suite,
                require_complete=args.require_complete,
            )
        except OracleReviewError as exc:
            raise SystemExit(f"oracle review unavailable: {exc}") from exc
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["complete"] or not args.require_complete else 2
    if args.command == "oracle-review":
        suite = load_suite(args.suite)
        try:
            if args.execute:
                result = run_pending_oracle_reviews(
                    suite,
                    cache_root=args.cache,
                )
                print(json.dumps(result, indent=2, sort_keys=True))
                if not result["complete"]:
                    print(
                        "Next: make benchmark-oracle-review-check",
                        file=sys.stderr,
                    )
                return 0
            print(oracle_review_guide(suite))
        except OracleReviewError as exc:
            raise SystemExit(f"oracle review unavailable: {exc}") from exc
        return 0
    if args.command == "diagnostic-prepare":
        try:
            evidence = prepare_diagnostic_suite(
                source_suite=args.suite,
                score_path=args.score,
                source_results=args.source_results,
                destination=args.output_suite,
                include_tasks=set(args.include_task),
            )
        except (DiagnosticError, OSError, ValueError) as exc:
            raise SystemExit(f"diagnostic suite unavailable: {exc}") from exc
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0
    if args.command == "regrade-score":
        script = args.suite.resolve() / "score.py"
        if not script.is_file():
            raise SystemExit(f"selected suite has no score script: {script}")
        invocation = [
            sys.executable,
            str(script),
            "--regrade-source-results",
            str(args.source_results),
            "--output",
            str(args.output),
        ]
        for agent in args.agent:
            invocation.extend(("--agent", agent))
        environment = dict(os.environ)
        project_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, (project_root, environment.get("PYTHONPATH", "")))
        )
        return subprocess.run(invocation, env=environment, check=False).returncode
    config = _resolve_config(args)
    runtime_source = config.runtime_environment()
    if hasattr(args, "agent"):
        try:
            args.agent = list(parse_agent_arguments(args.agent))
        except SelectionError as exc:
            raise SystemExit(str(exc)) from exc
    if args.command == "runs":
        if args.root is None:
            raise SystemExit("provide --root or BENCHMARK_CAMPAIGN_ROOT")
        try:
            saved = list_saved_runs(args.root)
        except RunStoreError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps({
            "latest": saved[-1].run_id if saved else None,
            "runs": [{"run_id": run.run_id, "root": str(run.root)} for run in saved],
        }, indent=2, sort_keys=True))
        return 0
    if args.command == "score":
        assert args.root is not None
        assert args.output is not None
        assert args.score_script is not None
        script = args.score_script.resolve()
        if not script.is_file():
            raise SystemExit(f"ERROR: benchmark score script does not exist: {script}")
        if script.parent != args.suite.resolve():
            raise SystemExit(
                f"ERROR: benchmark score script must belong to selected suite: {args.suite}"
            )
        try:
            _validate_score_cli(script, runtime_source)
        except ReportError as exc:
            raise SystemExit(f"benchmark reporting contract unavailable: {exc}") from exc
        try:
            with exclusive_store(args.root):
                saved = select_saved_run(args.root, args.run_id)
                manifest = _assert_saved_run_agents(saved, args.agent)
                suite_for_score = load_suite(args.suite)
                verify_campaign_suite_authority(
                    suite=suite_for_score,
                    campaign=manifest,
                )
                if not explicit_score_output:
                    if args.output.is_absolute() or len(args.output.parts) != 1:
                        raise SystemExit(
                            "BENCHMARK_SCORE_OUTPUT_PATH must be a filename within "
                            "the selected run's reports directory"
                        )
                    frozen_ids = tuple(
                        str(value) for value in manifest["selected_definitions"]
                    )
                    rows = select_scoring_definitions(
                        suite_for_score,
                        agents=tuple(args.agent),
                        definition_ids=frozen_ids,
                    )
                    paths = resolve_campaign_paths(
                        root=saved.root,
                        cache=None,
                        work=None,
                        results=None,
                        need_execution=False,
                    )
                    paths = replace(paths, run_id=saved.run_id)
                    assert paths.results is not None
                    final_status = campaign_status(
                        suite=suite_for_score,
                        results_root=paths.results,
                        selected_definitions=set(frozen_ids),
                    )
                    if not final_status["complete"]:
                        raise ReportError(
                            "canonical score refresh requires a complete campaign"
                        )
                    written = _persist_completed_run_reports(
                        args=args,
                        config=config,
                        suite=suite_for_score,
                        rows=rows,
                        paths=paths,
                        runtime_source=runtime_source,
                        final_status=final_status,
                        score_contract=(script, args.output),
                    )
                    score_path = written["score"]
                else:
                    invocation = [
                        sys.executable,
                        str(script),
                        "--results",
                        str(saved.root / "results"),
                        "--output",
                        str(args.output),
                    ]
                    for agent in args.agent:
                        invocation.extend(("--agent", agent))
                    for definition_id in sorted(
                        str(value) for value in manifest["selected_definitions"]
                    ):
                        invocation.extend(("--definition-id", definition_id))
                    completed = subprocess.run(
                        invocation,
                        env=_score_environment(runtime_source),
                        check=False,
                    )
                    if completed.returncode != 0:
                        return completed.returncode
                    score_path = args.output
                if args.require_analysis_evidence:
                    try:
                        _require_analysis_evidence(score_path)
                    except ReportError as exc:
                        raise SystemExit(
                            f"benchmark analysis evidence gate failed: {exc}"
                        ) from exc
                return 0
        except (
            RunStoreError,
            CampaignAuthorityError,
            ReportError,
            SelectionError,
        ) as exc:
            raise SystemExit(str(exc)) from exc
    suite = (
        load_runtime_suite(args.suite)
        if args.command in {"check", "doctor"}
        else load_suite(args.suite)
    )

    if args.command == "validate-suite":
        print(
            json.dumps(
                {
                    "suite": suite.experiment["suite"],
                    "experiment": suite.experiment["id"],
                    "tasks": len(suite.tasks),
                    "subjects": len(suite.subjects),
                    "agents": len(suite.agents),
                    "trials": len(suite.trial_definitions()),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command in {"check", "doctor"}:
        unknown_agents = sorted(set(args.agent) - set(suite.agents))
        if unknown_agents:
            raise SystemExit("unknown benchmark agent(s): " + ", ".join(unknown_agents))
        authority_rows = [
            {"condition_id": str(condition["id"])}
            for condition in suite.experiment["conditions"]
            if not args.agent or str(condition["agent"]) in args.agent
        ]
        if not authority_rows:
            raise SystemExit("benchmark check has no suite conditions")
        required = required_runtime_authority(suite, authority_rows)
        try:
            config.require(*required)
        except BenchmarkConfigError as exc:
            raise SystemExit(f"ERROR: {exc}") from exc
        try:
            report = check_runtime_readiness(
                suite,
                agents=tuple(args.agent),
                source=runtime_source,
            )
        except (OSError, ValueError) as exc:
            raise SystemExit(f"benchmark runtime check failed: {exc}") from exc
        for item in report.checks:
            print(item.line())
        print(
            "benchmark runtime: READY"
            if report.ready
            else "benchmark runtime: NOT READY"
        )
        return 0 if report.ready else 2

    rows = [] if args.command == "reports" else _select(args, suite)

    if args.command in {"preflight", "campaign-audit", "prepare", "run"}:
        try:
            config.require(*required_runtime_authority(suite, rows))
            _validate_reporting_contract(config, suite, runtime_source)
        except BenchmarkConfigError as exc:
            raise SystemExit(f"ERROR: {exc}") from exc
        except ReportError as exc:
            raise SystemExit(f"benchmark reporting contract unavailable: {exc}") from exc

    if args.command == "reports":
        try:
            _validate_reporting_contract(config, suite, runtime_source)
        except ReportError as exc:
            raise SystemExit(f"benchmark reporting contract unavailable: {exc}") from exc

    if args.command == "plan":
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0

    if args.command == "reports":
        if args.root is None:
            raise SystemExit(
                "reports requires --root or BENCHMARK_CAMPAIGN_ROOT"
            )
        try:
            with exclusive_store(args.root):
                saved = select_saved_run(args.root, args.run_id)
                manifest = _assert_saved_run_agents(saved, args.agent)
                verify_campaign_suite_authority(suite=suite, campaign=manifest)
                frozen_ids = tuple(
                    str(value) for value in manifest["selected_definitions"]
                )
                rows = select_scoring_definitions(
                    suite,
                    agents=tuple(args.agent),
                    definition_ids=frozen_ids,
                )
                paths = resolve_campaign_paths(
                    root=saved.root,
                    cache=None,
                    work=None,
                    results=None,
                    need_execution=False,
                )
                paths = replace(paths, run_id=saved.run_id)
                assert paths.results is not None
                selected_definitions = set(frozen_ids)
                final_status = campaign_status(
                    suite=suite,
                    results_root=paths.results,
                    selected_definitions=selected_definitions,
                )
                if not final_status["complete"]:
                    raise ReportError(
                        "complete report set requires every frozen definition to finish"
                    )
                written = _persist_completed_run_reports(
                    args=args,
                    config=config,
                    suite=suite,
                    rows=rows,
                    paths=paths,
                    runtime_source=runtime_source,
                    final_status=final_status,
                )
        except (
            RunStoreError,
            CampaignAuthorityError,
            ReportError,
            SelectionError,
        ) as exc:
            raise SystemExit(f"benchmark reports unavailable: {exc}") from exc
        print(
            json.dumps(
                {
                    "run_id": paths.run_id,
                    "reports": {
                        key: str(value) for key, value in written.items()
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "status":
        paths = _paths(args, need_execution=False)
        assert paths.results is not None
        status = campaign_status(
            suite=suite,
            results_root=paths.results,
            selected_definitions={str(row["definition_id"]) for row in rows},
            active_definition=(
                active_definition(args.root, paths.run_id)
                if args.root is not None and paths.run_id is not None
                else None
            ),
        )
        status["paths"] = paths.as_dict()
        status["run_id"] = paths.run_id
        status["selection"] = _selection_metadata(suite, rows)
        if paths.root is not None:
            status["paths"]["reports"] = str(_reports_dir(paths.root))
            refreshed, persistence_gap = _refresh_canonical_status(
                store_root=args.root,
                paths=paths,
                suite=suite,
                rows=rows,
            )
            if refreshed is not None:
                status = refreshed
            else:
                print(
                    f"STATUS inspection only | {persistence_gap}; canonical "
                    "reports/status.json unchanged",
                    file=sys.stderr,
                    flush=True,
                )
        print(json.dumps(status, indent=2, sort_keys=True))
        return (
            2
            if (
                status["conflicting_trials"]
                or status["corrupt_bundles"]
                or status["foreign_bundles"]
                or (
                    isinstance(status.get("derived_report_set"), dict)
                    and status["derived_report_set"].get("status") == "INVALID"
                )
                or (args.require_qualified and not status["qualified"])
            )
            else 0
        )

    if args.command == "report":
        paths = _paths(args, need_execution=False)
        assert paths.results is not None
        if not paths.results.is_dir() or not any(paths.results.iterdir()):
            raise SystemExit(
                f"no benchmark receipts in {paths.results}; run the selected suite first"
            )
        try:
            report_data = build_report(
                suite=suite,
                results_root=paths.results,
                require_complete=not args.allow_incomplete,
                selected_definitions={str(row["definition_id"]) for row in rows},
                selection=_selection_metadata(suite, rows),
            )
        except ReportError as exc:
            raise SystemExit(
                f"benchmark report unavailable: {exc}; use --allow-incomplete to inspect a partial campaign"
            ) from exc
        report_data["run_id"] = paths.run_id
        if paths.root is not None:
            report_data["reports_dir"] = str(_reports_dir(paths.root))
            if args.allow_incomplete:
                persistence_gap = "--allow-incomplete is inspection-only"
            else:
                refreshed, persistence_gap = _refresh_canonical_report(
                    store_root=args.root,
                    paths=paths,
                    suite=suite,
                    rows=rows,
                    args=args,
                    config=config,
                    runtime_source=runtime_source,
                )
                if refreshed is not None:
                    report_data = refreshed
            if persistence_gap is not None:
                print(
                    f"REPORT inspection only | {persistence_gap}; canonical "
                    "report.json and decision-evidence.json unchanged",
                    file=sys.stderr,
                    flush=True,
                )
        print(json.dumps(report_data, indent=2, sort_keys=True))
        return 0

    if args.command in {"prepare", "run"}:
        if args.root is None or any(
            value is not None for value in (args.cache, args.work, args.results)
        ):
            raise SystemExit("prepare/run requires --root or BENCHMARK_CAMPAIGN_ROOT without separate paths")
        if (args.command == "prepare" or args.new) and args.run_id is not None:
            raise SystemExit("--run-id can only be used with run --resume")
        try:
            with exclusive_store(args.root) as lock_fd, retain_lock_in_subprocesses(lock_fd):
                admission_started = time.monotonic()

                def on_admission_progress(event):
                    print(
                        render_campaign_admission(
                            event,
                            elapsed=time.monotonic() - admission_started,
                        ),
                        file=sys.stderr,
                        flush=True,
                    )

                if args.command == "run" and args.auto:
                    _guard_automatic_start(args.root, suite=suite, rows=rows)
                create_new = args.command == "prepare" or args.new or args.auto
                if create_new:
                    saved, campaign = prepare_saved_run(
                        args.root,
                        lambda staged: admit_campaign(
                            suite=suite,
                            rows=rows,
                            results_root=staged / "results",
                            harness_root=args.harness_root,
                            cache_root=staged / "cache",
                            work_root=staged / "work",
                            local_source=args.source,
                            codex_auth=args.codex_auth,
                            source=runtime_source,
                            on_progress=on_admission_progress,
                        ),
                    )
                else:
                    saved = select_saved_run(args.root, args.run_id)
                    _assert_saved_run_selection(saved, suite=suite, rows=rows)
                    resume_started = time.monotonic()
                    campaign = verify_saved_campaign(
                        suite=suite,
                        rows=rows,
                        results_root=saved.root / "results",
                        harness_root=args.harness_root,
                    )
                    print(
                        "RESUME campaign authority | verified | "
                        f"{int(round((time.monotonic() - resume_started) * 1000))}ms",
                        file=sys.stderr,
                        flush=True,
                    )
                if args.command == "prepare":
                    print(json.dumps({
                        "run_id": saved.run_id,
                        "root": str(saved.root),
                        "campaign_id": campaign["campaign_id"],
                    }, indent=2, sort_keys=True))
                    return 0
                paths = resolve_campaign_paths(
                    root=saved.root, cache=None, work=None, results=None,
                    need_execution=True,
                )
                paths = replace(paths, run_id=saved.run_id)
                return _execute_run(
                    args,
                    suite,
                    rows,
                    paths,
                    campaign,
                    runtime_source,
                    config,
                )
        except (RunStoreError, CampaignAuthorityError, OracleReviewError) as exc:
            raise SystemExit(f"benchmark run unavailable: {exc}") from exc

    paths = _paths(
        args,
        need_execution=True,
        results_optional=args.command in {"preflight", "campaign-audit"},
    )
    assert paths.cache is not None
    assert paths.work is not None

    if args.command == "campaign-audit":
        try:
            review = (
                validate_oracle_reviews(suite, require_complete=False)
                if oracle_reviews_declared(suite)
                else None
            )
            authority = audit_campaign(
                suite=suite,
                rows=rows,
                results_root=paths.results,
                harness_root=args.harness_root,
                cache_root=paths.cache,
                work_root=paths.work,
                local_source=args.source,
                codex_auth=args.codex_auth,
                source=runtime_source,
            )
        except (CampaignAuthorityError, OracleReviewError, OSError) as exc:
            raise SystemExit(f"campaign audit failed: {exc}") from exc
        print(
            json.dumps(
                {
                    "audit": "model-free; no campaign authority published",
                    "selected_definitions": len(authority["selected_definitions"]),
                    "task_conditions": sum(
                        len(x) for x in authority["task_conditions"].values()
                    ),
                    "task_inputs": len(authority["task_inputs"]),
                    "oracle_review": review,
                    "ready_for_campaign": review is None or review["complete"],
                    "paths": paths.as_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if review is None or review["complete"] else 2

    if args.command == "preflight":
        results = []
        counts: Counter[str] = Counter()
        for row in rows:
            result = preflight_trial(
                suite=suite,
                task_id=str(row["task_id"]),
                condition_id=str(row["condition_id"]),
                trial_index=int(row["trial"]),
                harness_root=args.harness_root,
                cache_root=paths.cache,
                work_root=paths.work,
                results_root=paths.results,
                local_source=args.source,
                codex_auth=args.codex_auth,
                source=runtime_source,
            )
            value = result.as_dict()
            results.append(value)
            counts[result.status] += 1
            detail = f": {result.reason}" if result.reason else ""
            print(
                f"{result.task_id} / {result.condition_id}: {result.status}{detail}",
                file=sys.stderr,
                flush=True,
            )
        payload = {
            "paths": paths.as_dict(),
            "selection": _selection_metadata(suite, rows),
            "summary": dict(sorted(counts.items())),
            "ready": all(row["status"] in {"READY", "COMPLETE"} for row in results),
            "trials": results,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["ready"] else 2

    raise SystemExit(f"unsupported benchmark command: {args.command}")


def _persist_completed_run_reports(
    *,
    args,
    config: BenchmarkConfig,
    suite,
    rows,
    paths,
    runtime_source,
    final_status: dict[str, object],
    score_contract: tuple[Path, Path] | None = None,
) -> dict[str, Path]:
    """Persist derived artifacts after final campaign verification."""
    assert paths.root is not None
    assert paths.results is not None

    verify_campaign_suite_authority(
        suite=suite,
        campaign=read_campaign(paths.results),
    )
    selection = _selection_metadata(suite, rows)
    reports_dir = _reports_dir(paths.root)
    score_script, score_output = (
        score_contract if score_contract is not None else _score_contract(config, suite)
    )

    status_payload = dict(final_status)
    status_payload["paths"] = paths.as_dict()
    status_payload["paths"]["reports"] = str(reports_dir)
    status_payload["run_id"] = paths.run_id
    status_payload["selection"] = selection
    report_data = build_report(
        suite=suite,
        results_root=paths.results,
        require_complete=True,
        selected_definitions={str(row["definition_id"]) for row in rows},
        selection=selection,
    )
    report_data["run_id"] = paths.run_id
    report_data["reports_dir"] = str(reports_dir)

    trace_data, decision_data = _derive_trace_and_decision(
        report_data=report_data,
        results_root=paths.results,
    )
    decision_data["run_id"] = paths.run_id

    score_path = reports_dir / score_output
    reports_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary_score = tempfile.mkstemp(
        prefix=f".{score_path.name}.staged.",
        dir=reports_dir,
    )
    os.close(fd)
    staged_score = Path(temporary_score)
    try:
        invocation = [
            sys.executable,
            str(score_script),
            "--results",
            str(paths.results),
            "--output",
            str(staged_score),
        ]
        for agent in args.agent:
            invocation.extend(("--agent", agent))
        for definition_id in sorted(str(row["definition_id"]) for row in rows):
            invocation.extend(("--definition-id", definition_id))
        completed = subprocess.run(
            invocation,
            env=_score_environment(runtime_source),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise ReportError(
                "benchmark score generation failed"
                + (f": {detail}" if detail else "")
            )
        if not staged_score.is_file() or staged_score.stat().st_size == 0:
            raise ReportError("benchmark score generation produced no output")

        score_bytes = staged_score.read_bytes()
        report_set = publish_report_set(
            run_root=paths.root,
            run_id=str(paths.run_id),
            report=report_data,
            trace_diagnostics=trace_data,
            decision_evidence=decision_data,
            score_artifact=score_output.name,
            score_bytes=score_bytes,
        )
        status_payload["derived_report_set"] = {
            "status": "VERIFIED",
            "verified": True,
            "score_bound": True,
            "score_artifact": report_set["score_artifact"],
            "generation_id": report_set["generation_id"],
            "run_id": report_set["run_id"],
            "artifacts": sorted(report_set["artifacts"]),
        }
        status_path = _write_derived_json(paths.root, "status.json", status_payload)
        report_path = reports_dir / "report.json"
        trace_path = reports_dir / "trace-diagnostics.json"
        decision_path = reports_dir / "decision-evidence.json"
        report_set_path = reports_dir / REPORT_SET_MANIFEST
    finally:
        staged_score.unlink(missing_ok=True)

    return {
        "status": status_path,
        "report": report_path,
        "decision_evidence": decision_path,
        "trace_diagnostics": trace_path,
        "report_set": report_set_path,
        "score": score_path,
    }


def _execute_run(args, suite, rows, paths, campaign, runtime_source, config) -> int:
    results = []
    blocking_failures: list[dict[str, object]] = []
    assert paths.results is not None
    live_matrix = LiveTaskMatrix(suite, rows)
    run_started = time.monotonic()
    conditions = {
        str(condition["id"]): condition
        for condition in suite.experiment["conditions"]
    }
    selected_definitions = {str(row["definition_id"]) for row in rows}
    initial_status = campaign_status(
        suite=suite,
        results_root=paths.results,
        selected_definitions=selected_definitions,
    )
    live_progress = LiveCampaignProgress(suite, rows, initial_status)
    initial_rows = {
        str(item["definition_id"]): item
        for item in initial_status["rows"]
    }
    unresolved = int(initial_status.get("unresolved_outcome_trials", 0))
    qualification_detail = (
        f" | qualification BLOCKED | unresolved {unresolved} "
        f"{'outcome' if unresolved == 1 else 'outcomes'} | "
        "continuing diagnostic evidence"
        if unresolved
        else ""
    )
    authority_transitions = int(
        initial_status.get("authority_epoch_transitions", 0)
    )
    authority_detail = (
        " | evidence TAINTED | "
        f"authority transitions {authority_transitions} | "
        "continuing diagnostic evidence"
        if authority_transitions
        else ""
    )
    print(
        f"RUN {paths.run_id} ({paths.root}) | CAMPAIGN {len(rows)} trials | "
        f"verified {initial_status['complete_trials']} | "
        f"pending {initial_status['pending_trials']} | "
        f"interrupted {initial_status['interrupted_trials']} | "
        "completed receipts will be reused; interrupted launches will be "
        "preserved and retried as numbered attempts"
        f"{qualification_detail}{authority_detail}",
        file=sys.stderr,
        flush=True,
    )
    active_heartbeat = None
    trial_started = run_started

    def on_start(row, _index):
        nonlocal active_heartbeat, trial_started
        print(
            live_progress.start_line(
                row,
                elapsed=time.monotonic() - run_started,
            ),
            file=sys.stderr,
            flush=True,
        )
        trial_started = time.monotonic()
        active_heartbeat = TrialHeartbeat(
            progress=live_progress,
            row=row,
            run_started=run_started,
            emit=lambda line: print(line, file=sys.stderr, flush=True),
        )
        return active_heartbeat

    def run_one(row):
        assert active_heartbeat is not None
        return run_trial(
            suite=suite,
            task_id=str(row["task_id"]),
            condition_id=str(row["condition_id"]),
            trial_index=int(row["trial"]),
            harness_root=args.harness_root,
            cache_root=paths.cache,
            results_root=paths.results,
            work_root=paths.work,
            local_source=args.source,
            codex_auth=args.codex_auth,
            source=runtime_source,
            campaign=campaign,
            on_progress=active_heartbeat.update_stage,
        )

    def on_error(row, exc):
        print(
            live_progress.abort_line(
                row,
                stage=active_heartbeat.stage if active_heartbeat is not None else "start",
                elapsed=time.monotonic() - run_started,
                error=exc,
            ),
            file=sys.stderr,
            flush=True,
        )
        try:
            observed = campaign_status(
                suite=suite,
                results_root=paths.results,
                selected_definitions=selected_definitions,
            )
            print(
                f"CAMPAIGN verified {observed['complete_trials']}/{len(rows)} | "
                f"pending {observed['pending_trials']} | "
                f"interrupted {observed['interrupted_trials']} | "
                f"qualified {observed['qualified']}",
                file=sys.stderr,
                flush=True,
            )
        except Exception as status_exc:
            print(
                f"CAMPAIGN status unavailable after abort: {status_exc}",
                file=sys.stderr,
                flush=True,
            )

    def on_result(row, result, receipt):
        epoch = receipt.get("execution", {}).get("authority_epoch")
        if isinstance(epoch, dict) and epoch.get("transitioned") is True:
            changed = ",".join(
                str(value)
                for value in epoch.get("changed_components", [])
            ) or "participant"
            print(
                "AUTHORITY EPOCH "
                f"{row['task_id']} / {row['condition_id']} | "
                f"epoch {epoch.get('epoch')} | changed {changed} | "
                "campaign evidence TAINTED | continuing",
                file=sys.stderr,
                flush=True,
            )
        summary = live_matrix.record(row, receipt)
        results.append(
            {
                "trial_id": result.trial_id,
                "definition_id": result.definition_id,
                "status": result.status,
                "result_dir": str(result.result_dir),
                "reused": result.reused,
                "recovered": result.recovered,
                "reason": result.reason,
                "stage": result.stage,
                "reason_code": result.reason_code,
                "diagnostic": result.diagnostic,
            }
        )
        detail = f": {result.reason}" if result.reason else ""
        print(
            f"{row['task_id']} / {row['condition_id']}: {result.status}{detail}",
            file=sys.stderr,
            flush=True,
        )
        condition = conditions[str(row["condition_id"])]
        failure = render_trial_failure(
            row=row,
            subject=str(condition["subject"]),
            result=result,
            receipt=receipt,
        )
        if failure is not None:
            print(failure, file=sys.stderr, flush=True)
            if result.status not in {"PASS", "FAIL", "NO_QUALIFYING_DEFECT"}:
                blocking_failures.append({
                    "trial_id": result.trial_id,
                    "task_id": row["task_id"],
                    "condition_id": row["condition_id"],
                    "replicate_id": row.get("replicate_id", row.get("seed", "unknown")),
                    "reason_code": result.reason_code,
                    "receipt": str(result.result_dir / "result.json"),
                })
        print(
            live_progress.finish_line(
                result,
                elapsed=time.monotonic() - run_started,
                trial_seconds=time.monotonic() - trial_started,
            ),
            file=sys.stderr,
            flush=True,
        )
        if summary is not None:
            print(summary, file=sys.stderr, flush=True)

    try:
        run_campaign_rows(
            rows=rows,
            initial_rows=initial_rows,
            results_root=paths.results,
            store_root=args.root,
            run_id=paths.run_id,
            run_one=run_one,
            on_start=on_start,
            on_result=on_result,
            on_error=on_error,
        )
    except Exception as exc:
        if isinstance(exc, (TrialRunnerError, ReportError, CampaignError, OSError, ValueError)):
            return 2
        raise

    try:
        final_status = campaign_status(
            suite=suite,
            results_root=paths.results,
            selected_definitions=selected_definitions,
        )
    except Exception as exc:
        print(
            f"ABORT final campaign verification | processed {len(results)}/{len(rows)} | {exc}",
            file=sys.stderr,
            flush=True,
        )
        _emit_run_results(results, enabled=not getattr(args, "no_json_results", False))
        return 2
    print(
        f"RUN SUMMARY processed {len(results)}/{len(rows)} | "
        f"verified {final_status['complete_trials']}/{len(rows)} | "
        f"outcomes {json.dumps(final_status['outcomes'], sort_keys=True)} | "
        f"evidence {'TAINTED' if final_status.get('evidence_tainted') else 'CLEAN'} | "
        f"authority transitions {final_status.get('authority_epoch_transitions', 0)} | "
        f"qualified {final_status['qualified']} | "
        f"run elapsed {int(time.monotonic() - run_started)}s",
        file=sys.stderr,
        flush=True,
    )
    try:
        report_paths = _persist_completed_run_reports(
            args=args,
            config=config,
            suite=suite,
            rows=rows,
            paths=paths,
            runtime_source=runtime_source,
            final_status=final_status,
        )
    except (OSError, ReportError, ValueError) as exc:
        print(
            f"REPORTS unavailable after completed execution: {exc}",
            file=sys.stderr,
            flush=True,
        )
        _emit_run_results(results, enabled=not getattr(args, "no_json_results", False))
        return 2
    print(
        "REPORTS saved | "
        f"status {report_paths['status']} | "
        f"report {report_paths['report']} | "
        f"decision {report_paths['decision_evidence']} | "
        f"trace-diagnostics {report_paths['trace_diagnostics']} | "
        f"report-set {report_paths['report_set']} | "
        f"score {report_paths['score']}",
        file=sys.stderr,
        flush=True,
    )

    if not final_status["qualified"]:
        blockers = [
            f"{label} {final_status[key]}"
            for key, label in (
                ("pending_trials", "pending"),
                ("interrupted_trials", "interrupted"),
                ("conflicting_trials", "conflicting"),
                ("unresolved_outcome_trials", "non-outcome receipts"),
            )
            if final_status.get(key)
        ]
        blockers.extend(
            f"{label} {len(final_status[key])}"
            for key, label in (
                ("corrupt_bundles", "corrupt bundles"),
                ("foreign_bundles", "foreign bundles"),
            )
            if final_status.get(key)
        )
        blockers.extend(
            f"{label}: {final_status[key]}"
            for key, label in (
                ("comparability_error", "comparability"),
                ("campaign_authority_error", "campaign authority"),
            )
            if final_status.get(key)
        )
        if final_status.get("evidence_tainted"):
            blockers.append(
                "participant authority transitions "
                f"{final_status.get('authority_epoch_transitions', 0)} "
                "(evidence preserved across immutable epochs)"
            )
        exposure = final_status.get("subject_exposure_qualification")
        if isinstance(exposure, dict) and exposure.get("qualified") is False:
            failures = [
                row
                for row in exposure.get("conditions", [])
                if isinstance(row, dict) and row.get("status") == "FAIL"
            ]
            blockers.append(
                "subject exposure "
                + (
                    "; ".join(
                        f"{row.get('condition_id')}="
                        + ",".join(
                            str(reason)
                            for reason in row.get("reason_codes", [])
                        )
                        for row in failures
                    )
                    if failures
                    else str(exposure.get("status", "failed"))
                )
            )
        print(
            "NOT QUALIFIED: "
            + (", ".join(blockers) if blockers else "inspect campaign status"),
            file=sys.stderr,
            flush=True,
        )
        print(
            "Next: inspect persisted qualification and diagnosis | "
            f"status {report_paths['status']} | "
            f"decision {report_paths['decision_evidence']} | "
            f"trace-diagnostics {report_paths['trace_diagnostics']}",
            file=sys.stderr,
            flush=True,
        )
    try:
        trace_diagnostics = json.loads(
            report_paths["trace_diagnostics"].read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        trace_diagnostics = None
    blocker_summary = render_run_blockers(blocking_failures, trace_diagnostics)
    if blocker_summary is not None:
        print(blocker_summary, file=sys.stderr, flush=True)
    _emit_run_results(results, enabled=not getattr(args, "no_json_results", False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
