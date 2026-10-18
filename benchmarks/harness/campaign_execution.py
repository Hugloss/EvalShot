"""Shared per-row reuse and execution boundary for campaign backends."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, ContextManager, Mapping

from .run_store import active_trial
from .runner import TrialRunResult, bind_result_to_receipt, reuse_completed_trial


def execute_campaign_row(
    *,
    row: Mapping[str, Any],
    initial_rows: Mapping[str, Mapping[str, Any]],
    results_root: Path,
    store_root: Path,
    run_id: str,
    run_one: Callable[[], TrialRunResult],
    context: ContextManager[None] | None = None,
) -> tuple[TrialRunResult, dict[str, Any]]:
    """Reuse a verified receipt or execute exactly one frozen definition."""
    definition = str(row["definition_id"])
    frozen = initial_rows.get(definition)
    if isinstance(frozen, Mapping) and frozen.get("state") == "COMPLETE":
        trial_ids = frozen.get("trial_ids")
        if (
            not isinstance(trial_ids, list)
            or len(trial_ids) != 1
            or not isinstance(trial_ids[0], str)
        ):
            raise ValueError("completed campaign row has invalid trial identity")
        result = reuse_completed_trial(
            results_root=results_root,
            definition_id=definition,
            trial_id=trial_ids[0],
        )
    else:
        with (context or nullcontext()), active_trial(store_root, run_id, definition):
            result = run_one()
    receipt = json.loads((result.result_dir / "result.json").read_text(encoding="utf-8"))
    return bind_result_to_receipt(result, receipt), receipt


def run_campaign_rows(
    *,
    rows: list[dict[str, Any]],
    initial_rows: Mapping[str, Mapping[str, Any]],
    results_root: Path,
    store_root: Path,
    run_id: str,
    run_one: Callable[[dict[str, Any]], TrialRunResult],
    on_start: Callable[[dict[str, Any], int], ContextManager[None] | None] | None = None,
    on_result: Callable[[dict[str, Any], TrialRunResult, dict[str, Any]], None] | None = None,
    on_error: Callable[[dict[str, Any], Exception], None] | None = None,
) -> None:
    """Run one frozen population through the same reuse and launch boundary."""
    for index, row in enumerate(rows, 1):
        context = on_start(row, index) if on_start is not None else None
        try:
            result, receipt = execute_campaign_row(
                row=row,
                initial_rows=initial_rows,
                results_root=results_root,
                store_root=store_root,
                run_id=run_id,
                run_one=lambda row=row: run_one(row),
                context=context,
            )
            if on_result is not None:
                on_result(row, result, receipt)
        except Exception as exc:
            if on_error is not None:
                on_error(row, exc)
            raise
