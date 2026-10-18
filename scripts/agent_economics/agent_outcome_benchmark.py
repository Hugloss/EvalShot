from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


class BenchmarkError(ValueError):
    pass


@dataclass(frozen=True)
class Outcome:
    task_id: str
    mode: str
    correct: bool
    independent_qualification_activations: int = 0
    files_opened: int = 0
    evidence_bytes: int = 0
    context_tokens_estimate: int = 0
    tool_calls: int = 0
    local_commands: int = 0
    repair_iterations: int = 0
    seconds_to_first_correct_edit: float | None = None
    focused_verifications: int = 0
    broad_verifications: int = 0
    failed_edits: int = 0
    no_progress_stops: int = 0
    bridge_elapsed_ms: float = 0.0
    local_independent_qualification_agree: bool | None = None
    treatment_id: str = "default"
    repository_id: str | None = None
    task_fixture_id: str | None = None
    corpus_id: str | None = None
    initial_source_id: str | None = None
    agent_profile: str | None = None
    execution_environment_id: str | None = None
    run_id: str | None = None
    bridge_implementation_id: str | None = None
    manifest_id: str | None = None
    local_qualification_id: str | None = None
    final_source_id: str | None = None
    independent_qualification_authority_id: str | None = None
    independent_qualification_evidence_id: str | None = None
    oracle_opened_after_freeze: bool | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"baseline", "bridge"}:
            raise BenchmarkError("mode must be baseline or bridge")
        numeric = (
            self.independent_qualification_activations,
            self.files_opened,
            self.evidence_bytes,
            self.context_tokens_estimate,
            self.tool_calls,
            self.local_commands,
            self.repair_iterations,
            self.focused_verifications,
            self.broad_verifications,
            self.failed_edits,
            self.no_progress_stops,
        )
        if any(value < 0 for value in numeric) or self.bridge_elapsed_ms < 0:
            raise BenchmarkError("benchmark counters must be non-negative")
        if (
            self.seconds_to_first_correct_edit is not None
            and self.seconds_to_first_correct_edit < 0
        ):
            raise BenchmarkError("time-to-edit must be non-negative")
        for name in ("task_id", "treatment_id"):
            if not getattr(self, name).strip():
                raise BenchmarkError(f"{name} must be non-empty")

    @property
    def pair_key(self) -> tuple[str, str]:
        return self.task_id, self.treatment_id


def load_outcomes(
    path: Path, *, max_bytes: int = 5_000_000, max_records: int = 10_000
) -> list[Outcome]:
    size = path.stat().st_size
    if size > max_bytes:
        raise BenchmarkError("benchmark input exceeds byte bound")
    raw = path.read_bytes()
    records: list[Outcome] = []
    for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            records.append(Outcome(**value))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BenchmarkError(f"invalid benchmark record at line {line_no}") from exc
        if len(records) > max_records:
            raise BenchmarkError("benchmark record count exceeds bound")
    return records


def _compatibility_errors(baseline: Outcome, bridge: Outcome) -> list[str]:
    errors: list[str] = []
    for field in (
        "repository_id",
        "task_fixture_id",
        "corpus_id",
        "initial_source_id",
        "agent_profile",
        "execution_environment_id",
        "independent_qualification_authority_id",
    ):
        left, right = getattr(baseline, field), getattr(bridge, field)
        if left is not None and right is not None and left != right:
            errors.append(f"{field} mismatch")
    return errors


def _strict_protocol_errors(baseline: Outcome, bridge: Outcome) -> list[str]:
    errors: list[str] = []
    shared_required = (
        "repository_id",
        "task_fixture_id",
        "corpus_id",
        "initial_source_id",
        "agent_profile",
        "execution_environment_id",
    )
    for field in shared_required:
        if not getattr(baseline, field) or not getattr(bridge, field):
            errors.append(f"{field} missing")
    if not baseline.run_id or not bridge.run_id:
        errors.append("run_id missing")
    elif baseline.run_id == bridge.run_id:
        errors.append("run_id must differ between baseline and bridge")
    for mode, outcome in (("baseline", baseline), ("bridge", bridge)):
        for field in (
            "final_source_id",
            "independent_qualification_authority_id",
            "independent_qualification_evidence_id",
        ):
            if not getattr(outcome, field):
                errors.append(f"{mode} {field} missing")
        if outcome.oracle_opened_after_freeze is not True:
            errors.append(f"{mode} oracle freeze evidence missing")
    for field in ("bridge_implementation_id", "manifest_id", "local_qualification_id"):
        if not getattr(bridge, field):
            errors.append(f"bridge {field} missing")
    if bridge.local_independent_qualification_agree is None:
        errors.append("bridge local_independent_qualification_agree unknown")
    return errors


def compare(
    outcomes: list[Outcome],
    *,
    require_complete_pairs: bool = True,
    strict_dogfood: bool = False,
) -> dict[str, object]:
    grouped: dict[tuple[str, str], dict[str, Outcome]] = {}
    for outcome in outcomes:
        modes = grouped.setdefault(outcome.pair_key, {})
        if outcome.mode in modes:
            raise BenchmarkError(
                f"duplicate {outcome.mode} record for task {outcome.task_id} treatment {outcome.treatment_id}"
            )
        modes[outcome.mode] = outcome
    incomplete = [
        key
        for key, modes in sorted(grouped.items())
        if set(modes) != {"baseline", "bridge"}
    ]
    if incomplete and require_complete_pairs:
        raise BenchmarkError(f"incomplete benchmark pairs: {incomplete[:10]}")
    pairs = [
        (key, modes["baseline"], modes["bridge"])
        for key, modes in sorted(grouped.items())
        if {"baseline", "bridge"} <= modes.keys()
    ]
    if not pairs:
        raise BenchmarkError("at least one paired baseline/bridge task is required")
    for key, baseline, bridge in pairs:
        errors = _compatibility_errors(baseline, bridge)
        if errors:
            raise BenchmarkError(f"incomparable pair {key}: {', '.join(errors)}")
        if strict_dogfood:
            protocol_errors = _strict_protocol_errors(baseline, bridge)
            if protocol_errors:
                raise BenchmarkError(
                    f"incomplete dogfood pair {key}: {', '.join(protocol_errors)}"
                )

    metrics = [
        "independent_qualification_activations",
        "files_opened",
        "evidence_bytes",
        "context_tokens_estimate",
        "tool_calls",
        "local_commands",
        "repair_iterations",
        "focused_verifications",
        "broad_verifications",
        "failed_edits",
        "no_progress_stops",
    ]
    deltas: dict[str, dict[str, float]] = {}
    for metric in metrics:
        baseline_total = sum(float(getattr(b, metric)) for _, b, _ in pairs)
        bridge_total = sum(float(getattr(g, metric)) for _, _, g in pairs)
        deltas[metric] = {
            "baseline_total": baseline_total,
            "bridge_total": bridge_total,
            "delta": bridge_total - baseline_total,
            "reduction_fraction": (baseline_total - bridge_total) / baseline_total
            if baseline_total
            else 0.0,
        }

    paired_times = [
        (b.seconds_to_first_correct_edit, g.seconds_to_first_correct_edit)
        for _, b, g in pairs
        if b.seconds_to_first_correct_edit is not None
        and g.seconds_to_first_correct_edit is not None
    ]
    timing = {
        "paired_measurements": len(paired_times),
        "baseline_total_seconds": sum(x for x, _ in paired_times),
        "bridge_total_seconds": sum(y for _, y in paired_times),
        "delta_seconds": sum(y - x for x, y in paired_times),
        "bridge_overhead_ms_total": sum(g.bridge_elapsed_ms for _, _, g in pairs),
    }
    protocol = {
        "bridge_receipts_bound": sum(
            1
            for _, _, g in pairs
            if all(
                (
                    g.bridge_implementation_id,
                    g.manifest_id,
                    g.local_qualification_id,
                    g.final_source_id,
                )
            )
        ),
        "independent_qualification_evidence_bound": sum(
            1
            for _, _, g in pairs
            if g.independent_qualification_evidence_id is not None
        ),
        "oracle_opened_after_freeze": sum(
            1 for _, _, g in pairs if g.oracle_opened_after_freeze is True
        ),
        "oracle_protocol_unknown": sum(
            1 for _, _, g in pairs if g.oracle_opened_after_freeze is None
        ),
    }
    correctness = {
        "baseline_correct": sum(1 for _, b, _ in pairs if b.correct),
        "bridge_correct": sum(1 for _, _, g in pairs if g.correct),
        "paired_tasks": len(pairs),
        "incomplete_pairs": len(incomplete),
        "local_independent_qualification_disagreements": sum(
            1 for _, _, g in pairs if g.local_independent_qualification_agree is False
        ),
        "local_independent_qualification_unknown": sum(
            1 for _, _, g in pairs if g.local_independent_qualification_agree is None
        ),
    }
    promotion = {
        "correctness_not_reduced": correctness["bridge_correct"]
        >= correctness["baseline_correct"],
        "independent_qualification_activations_not_increased": deltas[
            "independent_qualification_activations"
        ]["delta"]
        <= 0,
        "context_not_increased": deltas["context_tokens_estimate"]["delta"] <= 0,
        "local_independent_qualification_disagreement_zero": correctness[
            "local_independent_qualification_disagreements"
        ]
        == 0
        and correctness["local_independent_qualification_unknown"] == 0,
        "all_pairs_complete": correctness["incomplete_pairs"] == 0,
        "strict_dogfood_protocol": strict_dogfood,
    }
    payload = {
        "schema": {"name": "agent-outcome-benchmark", "version": 3},
        "correctness": correctness,
        "metrics": deltas,
        "timing": timing,
        "experiment_protocol": protocol,
        "promotion_evidence": promotion,
        "authority": {
            "benchmark_is_measurement_not_release_authority": True,
            "automatic_promotion": False,
            "promotion_evidence_is_not_a_verdict": True,
        },
    }
    payload["identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return payload


def outcome_template(
    *, task_id: str, treatment_id: str = "default"
) -> list[dict[str, object]]:
    return [
        asdict(
            Outcome(
                task_id=task_id,
                treatment_id=treatment_id,
                mode="baseline",
                correct=False,
            )
        ),
        asdict(
            Outcome(
                task_id=task_id, treatment_id=treatment_id, mode="bridge", correct=False
            )
        ),
    ]


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare paired baseline and capability-bridge outcomes against repository-owned qualification authority."
    )
    parser.add_argument("--input", type=Path, help="JSONL Outcome records")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument(
        "--template-task", help="emit a baseline/bridge JSONL template for this task id"
    )
    parser.add_argument("--treatment-id", default="default")
    parser.add_argument(
        "--strict-dogfood",
        action="store_true",
        help="fail closed unless empirical closeout provenance is complete",
    )
    args = parser.parse_args(argv)
    if args.template_task:
        print(
            "\n".join(
                json.dumps(row, sort_keys=True)
                for row in outcome_template(
                    task_id=args.template_task, treatment_id=args.treatment_id
                )
            )
        )
        return
    if args.input is None:
        parser.error("--input is required unless --template-task is used")
    payload = compare(load_outcomes(args.input), strict_dogfood=args.strict_dogfood)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.artifact:
        args.artifact.parent.mkdir(parents=True, exist_ok=True)
        args.artifact.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
