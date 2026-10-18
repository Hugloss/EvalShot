from __future__ import annotations

import contextlib
import io
import json
import tempfile
from pathlib import Path

from .bounded_evidence_batches import (
    COMPLETE_PASS,
    INCOMPLETE,
    INCOMPLETE_BUDGET_EXCEEDED,
    INCOMPLETE_CONTROLLER_TIMEOUT,
    PRODUCT_FAILURE,
    aggregate_receipts,
    batch_descriptor,
    build_manifest,
    build_receipt,
    collapse_subbatch_receipts,
    derive_followup_manifest,
    next_resume_batch,
    subdivide_batch,
)
from .bounded_evidence_batches_cli import main as batch_cli_main


def _pass_results(targets: list[str]) -> list[dict[str, str]]:
    return [{"target": target, "status": "PASS"} for target in targets]


def _observed_receipt(
    descriptor: dict[str, object], **kwargs: object
) -> dict[str, object]:
    return build_receipt(
        descriptor,
        observed_repository_identity=str(descriptor["repository_identity"]),
        observed_provider_identity=str(descriptor["provider_identity"]),
        observed_operation=str(descriptor["operation"]),
        **kwargs,
    )


def qualify() -> dict[str, object]:
    failures: list[str] = []
    targets = [f"owner.symbol_{index:02d}" for index in range(90)]
    manifest = build_manifest(
        targets=targets,
        repository_identity="sha256:repo-generation-a",
        provider_identity="sha256:hashmarks-wheel-a",
        operation="verification_ownership_graph",
        batch_size=10,
        controller_budget_ms=45_000,
        batch_timeout_ms=30_000,
        minimum_headroom_ms=5_000,
    )
    if manifest["batch_count"] != 9:
        failures.append("90 targets did not partition into nine deterministic batches")
    if manifest.get("controller_headroom_ms") != 15_000:
        failures.append("controller headroom was not frozen into manifest identity")
    try:
        build_manifest(
            targets=targets[:2],
            repository_identity="sha256:repo-generation-a",
            provider_identity="sha256:hashmarks-wheel-a",
            operation="too-tight",
            batch_size=2,
            controller_budget_ms=45_000,
            batch_timeout_ms=43_000,
            minimum_headroom_ms=5_000,
        )
    except ValueError:
        pass
    else:
        failures.append("batch plan admitted timeout without controller headroom")

    receipts = []
    for index in range(9):
        batch = batch_descriptor(manifest, index)
        receipts.append(
            _observed_receipt(
                batch,
                results=_pass_results(list(batch["targets"])),
                execution_class="hosted-diagnostic",
                elapsed_ms=100 + index,
            )
        )
    aggregate = aggregate_receipts(manifest, receipts)
    if aggregate["status"] != COMPLETE_PASS or aggregate["passed_targets"] != 90:
        failures.append(
            "nine passing batches did not aggregate to complete 90-target proof"
        )
    if next_resume_batch(manifest, receipts) is not None:
        failures.append("complete campaign incorrectly requested resume")

    missing_receipts = receipts[:4] + receipts[5:]
    missing = aggregate_receipts(manifest, missing_receipts)
    if missing["status"] != INCOMPLETE or missing["missing_batch_indexes"] != [4]:
        failures.append("missing batch became complete evidence")
    if next_resume_batch(manifest, missing_receipts) != 4:
        failures.append("resume did not select first missing batch")

    timeout_batch = batch_descriptor(manifest, 4)
    timeout = _observed_receipt(
        timeout_batch,
        results=_pass_results(list(timeout_batch["targets"])[:3]),
        execution_class="hosted-diagnostic",
        elapsed_ms=45000,
        controller_status="TIMEOUT",
    )
    if timeout["status"] != INCOMPLETE_CONTROLLER_TIMEOUT or timeout["product_failure"]:
        failures.append("controller timeout was misclassified as product failure")
    timed_receipts = list(receipts)
    timed_receipts[4] = timeout
    timed_aggregate = aggregate_receipts(manifest, timed_receipts)
    if timed_aggregate["status"] != INCOMPLETE or timed_aggregate["product_failure"]:
        failures.append("timeout campaign was promoted or treated as product failure")
    if next_resume_batch(manifest, timed_receipts) != 4:
        failures.append("timeout campaign did not resume at timed-out batch")

    budget_batch = batch_descriptor(manifest, 5)
    budget_exceeded = _observed_receipt(
        budget_batch,
        results=_pass_results(list(budget_batch["targets"])),
        execution_class="hosted-diagnostic",
        elapsed_ms=30_001,
    )
    if budget_exceeded["status"] != INCOMPLETE_BUDGET_EXCEEDED:
        failures.append("completed-over-budget batch was promoted to complete evidence")
    budget_receipts = list(receipts)
    budget_receipts[5] = budget_exceeded
    budget_aggregate = aggregate_receipts(manifest, budget_receipts)
    if (
        budget_aggregate["status"] != INCOMPLETE
        or budget_aggregate["budget_exceeded_batch_indexes"] != [5]
        or budget_aggregate["product_failure"]
    ):
        failures.append(
            "budget-exceeded batch did not remain incomplete/non-product-failure"
        )

    children = subdivide_batch(timeout_batch, subbatch_size=5)
    if [child["targets"] for child in children] != [
        list(timeout_batch["targets"])[:5],
        list(timeout_batch["targets"])[5:],
    ]:
        failures.append("timeout subdivision did not preserve exact ordered targets")
    child_receipts = [
        _observed_receipt(
            child,
            results=_pass_results(list(child["targets"])),
            execution_class="hosted-diagnostic",
            elapsed_ms=500,
        )
        for child in children
    ]
    collapsed = collapse_subbatch_receipts(
        timeout_batch,
        children,
        child_receipts,
        execution_class="hosted-diagnostic",
    )
    if (
        collapsed["status"] != COMPLETE_PASS
        or collapsed["results"] != receipts[4]["results"]
    ):
        failures.append(
            "5+5 subdivision did not collapse to original 10-target semantics"
        )

    staged_receipts = []
    followup_targets = {
        targets[3]: "ambiguous-owner",
        targets[17]: "verifier-disagreement",
        targets[44]: "needs-heavy-graph",
        targets[80]: "needs-heavy-graph",
    }
    for index in range(9):
        batch = batch_descriptor(manifest, index)
        rows = []
        for target in batch["targets"]:
            rows.append(
                {
                    "target": target,
                    "status": "PASS",
                    "followup_required": target in followup_targets,
                    "followup_reason": followup_targets.get(target, ""),
                }
            )
        staged_receipts.append(
            _observed_receipt(
                batch,
                results=rows,
                execution_class="hosted-diagnostic",
                elapsed_ms=50,
            )
        )
    followup = derive_followup_manifest(
        manifest,
        staged_receipts,
        operation="verification_ownership_graph",
        batch_size=2,
    )
    if followup is None:
        failures.append(
            "explicit heavy-followup targets produced no follow-up manifest"
        )
    else:
        if followup["targets"] != [targets[3], targets[17], targets[44], targets[80]]:
            failures.append(
                "follow-up manifest did not preserve parent target ordering"
            )
        if followup["batch_count"] != 2:
            failures.append(
                "four heavy-followup targets did not become two bounded batches"
            )
        if followup.get("parent_manifest_identity") != manifest["manifest_identity"]:
            failures.append("follow-up manifest lost parent campaign identity")
        if not isinstance(followup.get("selection_identity"), str):
            failures.append("follow-up manifest did not bind selection evidence")

    try:
        derive_followup_manifest(
            manifest,
            staged_receipts[:-1],
            operation="verification_ownership_graph",
        )
    except ValueError:
        pass
    else:
        failures.append(
            "incomplete cheap-stage evidence produced an expensive follow-up manifest"
        )

    tampered = dict(staged_receipts[0])
    tampered_results = [dict(row) for row in tampered["results"]]
    tampered_results[0]["followup_required"] = True
    tampered["results"] = tampered_results
    try:
        aggregate_receipts(manifest, [tampered, *staged_receipts[1:]])
    except ValueError:
        pass
    else:
        failures.append(
            "tampered per-target follow-up evidence retained receipt authority"
        )

    failed_batch = batch_descriptor(manifest, 7)
    failed_results = _pass_results(list(failed_batch["targets"]))
    failed_results[2] = {
        "target": failed_results[2]["target"],
        "status": "FAIL",
        "failure_identity": "sha256:product-failure",
    }
    failure_receipt = _observed_receipt(
        failed_batch,
        results=failed_results,
        execution_class="hosted-diagnostic",
        elapsed_ms=1200,
    )
    if (
        failure_receipt["status"] != PRODUCT_FAILURE
        or not failure_receipt["product_failure"]
    ):
        failures.append("real target failure was not classified as product failure")

    stale_manifest = build_manifest(
        targets=targets,
        repository_identity="sha256:repo-generation-b",
        provider_identity="sha256:hashmarks-wheel-a",
        operation="verification_ownership_graph",
        batch_size=10,
    )
    try:
        aggregate_receipts(stale_manifest, receipts)
    except ValueError:
        pass
    else:
        failures.append("receipts from another repository generation were accepted")

    observed_mismatch_batch = batch_descriptor(manifest, 0)
    try:
        build_receipt(
            observed_mismatch_batch,
            results=_pass_results(list(observed_mismatch_batch["targets"])),
            execution_class="hosted-diagnostic",
            elapsed_ms=100,
            observed_repository_identity="sha256:repo-generation-b",
            observed_provider_identity=str(
                observed_mismatch_batch["provider_identity"]
            ),
            observed_operation=str(observed_mismatch_batch["operation"]),
        )
    except ValueError:
        pass
    else:
        failures.append(
            "receipt accepted execution observed from another repository generation"
        )

    try:
        build_receipt(
            observed_mismatch_batch,
            results=_pass_results(list(observed_mismatch_batch["targets"])),
            execution_class="hosted-diagnostic",
            elapsed_ms=100,
            observed_repository_identity=str(
                observed_mismatch_batch["repository_identity"]
            ),
            observed_provider_identity="sha256:hashmarks-wheel-b",
            observed_operation=str(observed_mismatch_batch["operation"]),
        )
    except ValueError:
        pass
    else:
        failures.append(
            "receipt accepted execution observed from another provider artifact"
        )

    changed_provider = build_manifest(
        targets=targets,
        repository_identity="sha256:repo-generation-a",
        provider_identity="sha256:hashmarks-wheel-b",
        operation="verification_ownership_graph",
        batch_size=10,
    )
    if changed_provider["manifest_identity"] == manifest["manifest_identity"]:
        failures.append("provider identity change did not change manifest identity")

    reordered = build_manifest(
        targets=list(reversed(targets)),
        repository_identity="sha256:repo-generation-a",
        provider_identity="sha256:hashmarks-wheel-a",
        operation="verification_ownership_graph",
        batch_size=10,
    )
    if reordered["manifest_identity"] == manifest["manifest_identity"]:
        failures.append("target ordering change did not change manifest identity")

    with tempfile.TemporaryDirectory(prefix="bounded-evidence-cli-") as raw:
        root = Path(raw)
        targets_path = root / "targets.json"
        manifest_path = root / "manifest.json"
        batch_path = root / "batch.json"
        results_path = root / "results.json"
        receipt_path = root / "receipt.json"
        targets_path.write_text(json.dumps(targets[:10]), encoding="utf-8")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            batch_cli_main(
                [
                    "plan",
                    "--targets-file",
                    str(targets_path),
                    "--repository-identity",
                    "sha256:repo-generation-a",
                    "--provider-identity",
                    "sha256:hashmarks-wheel-a",
                    "--operation",
                    "task_evidence",
                    "--batch-size",
                    "10",
                    "--controller-budget-ms",
                    "45000",
                    "--batch-timeout-ms",
                    "30000",
                    "--minimum-headroom-ms",
                    "5000",
                    "--artifact",
                    str(manifest_path),
                ]
            )
        cli_manifest = json.loads(output.getvalue())
        assert json.loads(manifest_path.read_text(encoding="utf-8")) == cli_manifest

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            batch_cli_main(
                [
                    "batch",
                    str(manifest_path),
                    "--index",
                    "0",
                    "--artifact",
                    str(batch_path),
                ]
            )
        cli_batch = json.loads(output.getvalue())
        results_path.write_text(
            json.dumps(_pass_results(list(cli_batch["targets"]))),
            encoding="utf-8",
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            batch_cli_main(
                [
                    "receipt",
                    str(batch_path),
                    "--results-file",
                    str(results_path),
                    "--execution-class",
                    "hosted-diagnostic",
                    "--elapsed-ms",
                    "123",
                    "--observed-repository-identity",
                    "sha256:repo-generation-a",
                    "--observed-provider-identity",
                    "sha256:hashmarks-wheel-a",
                    "--observed-operation",
                    "task_evidence",
                    "--artifact",
                    str(receipt_path),
                ]
            )
        cli_receipt = json.loads(output.getvalue())
        if cli_receipt["status"] != COMPLETE_PASS:
            failures.append("CLI receipt did not preserve complete PASS")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            batch_cli_main(
                [
                    "aggregate",
                    str(manifest_path),
                    str(receipt_path),
                ]
            )
        cli_aggregate = json.loads(output.getvalue())
        if cli_aggregate["status"] != COMPLETE_PASS:
            failures.append("CLI aggregate did not produce complete PASS")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            batch_cli_main(
                [
                    "resume",
                    str(manifest_path),
                    str(receipt_path),
                ]
            )
        cli_resume = json.loads(output.getvalue())
        if cli_resume["next_batch_index"] is not None:
            failures.append("CLI resume requested work after complete campaign")

    result = {
        "probe": "bounded-evidence-batches",
        "qualification_phase": "constrained-agent-economics",
        "passed": not failures,
        "observations": {
            "targets": 90,
            "batch_count": manifest["batch_count"],
            "complete_status": aggregate["status"],
            "missing_status": missing["status"],
            "timeout_status": timeout["status"],
            "budget_exceeded_status": budget_exceeded["status"],
            "controller_headroom_ms": manifest.get("controller_headroom_ms"),
            "subdivision_sizes": [len(child["targets"]) for child in children],
            "failure_status": failure_receipt["status"],
            "followup_target_count": 0
            if followup is None
            else followup["target_count"],
            "cli_complete_status": cli_aggregate["status"],
            "observed_identity_binding": "PASS",
        },
        "failures": failures,
    }
    return result


def main() -> None:
    result = qualify()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
