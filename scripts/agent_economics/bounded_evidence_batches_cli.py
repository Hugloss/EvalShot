from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .bounded_evidence_batches import (
    aggregate_receipts,
    batch_descriptor,
    build_manifest,
    build_receipt,
    derive_followup_manifest,
    next_resume_batch,
    subdivide_batch,
)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"evidence-batches: cannot read {path}: {exc}") from exc


def _load_object(path: Path) -> dict[str, object]:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise SystemExit(f"evidence-batches: {path} must contain a JSON object")
    return value


def _load_objects(paths: list[Path]) -> list[dict[str, object]]:
    return [_load_object(path) for path in paths]


def _load_targets(path: Path) -> list[str]:
    value = _load_json(path)
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict) and isinstance(value.get("targets"), list):
        rows = value["targets"]
    else:
        raise SystemExit(
            "evidence-batches: targets file must be a JSON list or object with a targets list"
        )
    targets = [str(item) for item in rows]
    if not targets or any(not item.strip() for item in targets):
        raise SystemExit("evidence-batches: targets must be non-empty strings")
    return targets


def _emit(payload: object, artifact: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if artifact is not None:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def _artifact_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Optional JSON output path; stdout is always emitted.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan, bind, resume, and aggregate bounded evidence batches without "
            "executing the underlying repository tool."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="Freeze an ordered target manifest.")
    plan.add_argument("--targets-file", type=Path, required=True)
    plan.add_argument("--repository-identity", required=True)
    plan.add_argument("--provider-identity", required=True)
    plan.add_argument("--operation", required=True)
    plan.add_argument("--batch-size", type=int, default=10)
    plan.add_argument(
        "--controller-budget-ms",
        type=int,
        help="External controller budget for one batch invocation.",
    )
    plan.add_argument(
        "--batch-timeout-ms",
        type=int,
        help="Internal batch timeout; must leave declared controller headroom.",
    )
    plan.add_argument("--minimum-headroom-ms", type=int, default=5_000)
    _artifact_arg(plan)

    batch = commands.add_parser("batch", help="Project one parent batch descriptor.")
    batch.add_argument("manifest", type=Path)
    batch.add_argument("--index", type=int, required=True)
    _artifact_arg(batch)

    receipt = commands.add_parser(
        "receipt", help="Bind externally observed PASS/FAIL results to one batch."
    )
    receipt.add_argument("descriptor", type=Path)
    receipt.add_argument("--results-file", type=Path, required=True)
    receipt.add_argument("--execution-class", required=True)
    receipt.add_argument("--elapsed-ms", type=int, required=True)
    receipt.add_argument("--observed-repository-identity", required=True)
    receipt.add_argument("--observed-provider-identity", required=True)
    receipt.add_argument("--observed-operation", required=True)
    receipt.add_argument(
        "--controller-status",
        choices=("COMPLETED", "TIMEOUT"),
        default="COMPLETED",
    )
    _artifact_arg(receipt)

    subdivide = commands.add_parser(
        "subdivide", help="Deterministically split one timed-out parent batch."
    )
    subdivide.add_argument("descriptor", type=Path)
    subdivide.add_argument("--subbatch-size", type=int, required=True)
    _artifact_arg(subdivide)

    aggregate = commands.add_parser(
        "aggregate", help="Aggregate complete identity-matched parent receipts."
    )
    aggregate.add_argument("manifest", type=Path)
    aggregate.add_argument("receipts", nargs="*", type=Path)
    _artifact_arg(aggregate)

    resume = commands.add_parser(
        "resume", help="Return the first missing/incomplete/failed parent batch index."
    )
    resume.add_argument("manifest", type=Path)
    resume.add_argument("receipts", nargs="*", type=Path)
    _artifact_arg(resume)

    followup = commands.add_parser(
        "followup",
        help="Derive an expensive-stage manifest from complete parent-stage receipts.",
    )
    followup.add_argument("manifest", type=Path)
    followup.add_argument("receipts", nargs="+", type=Path)
    followup.add_argument("--operation", required=True)
    followup.add_argument("--batch-size", type=int, default=5)
    _artifact_arg(followup)

    return parser


def _results(path: Path) -> list[Mapping[str, object]]:
    value = _load_json(path)
    if isinstance(value, dict):
        value = value.get("results")
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SystemExit(
            "evidence-batches: results file must be a JSON list or object with a results list"
        )
    return value


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            payload = build_manifest(
                targets=_load_targets(args.targets_file),
                repository_identity=args.repository_identity,
                provider_identity=args.provider_identity,
                operation=args.operation,
                batch_size=args.batch_size,
                controller_budget_ms=args.controller_budget_ms,
                batch_timeout_ms=args.batch_timeout_ms,
                minimum_headroom_ms=args.minimum_headroom_ms,
            )
        elif args.command == "batch":
            payload = batch_descriptor(_load_object(args.manifest), args.index)
        elif args.command == "receipt":
            payload = build_receipt(
                _load_object(args.descriptor),
                results=_results(args.results_file),
                execution_class=args.execution_class,
                elapsed_ms=args.elapsed_ms,
                observed_repository_identity=args.observed_repository_identity,
                observed_provider_identity=args.observed_provider_identity,
                observed_operation=args.observed_operation,
                controller_status=args.controller_status,
            )
        elif args.command == "subdivide":
            payload = subdivide_batch(
                _load_object(args.descriptor),
                subbatch_size=args.subbatch_size,
            )
        elif args.command == "aggregate":
            payload = aggregate_receipts(
                _load_object(args.manifest),
                _load_objects(args.receipts),
            )
        elif args.command == "resume":
            manifest = _load_object(args.manifest)
            receipts = _load_objects(args.receipts)
            payload = {
                "manifest_identity": manifest.get("manifest_identity"),
                "next_batch_index": next_resume_batch(manifest, receipts),
            }
        elif args.command == "followup":
            payload = derive_followup_manifest(
                _load_object(args.manifest),
                _load_objects(args.receipts),
                operation=args.operation,
                batch_size=args.batch_size,
            )
            if payload is None:
                payload = {
                    "status": "NO_FOLLOWUP_REQUIRED",
                    "followup_manifest": None,
                }
        else:  # pragma: no cover - argparse owns this branch
            raise AssertionError(args.command)
    except ValueError as exc:
        raise SystemExit(f"evidence-batches: {exc}") from exc
    _emit(payload, args.artifact)


if __name__ == "__main__":
    main()
