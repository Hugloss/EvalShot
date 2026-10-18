"""Human-readable live benchmark result projection.

This module renders already-observed trial statuses for the terminal. It owns no
benchmark truth: receipts, scoring, campaign authority, and qualification remain
unchanged.
"""

from __future__ import annotations

from collections import Counter
from statistics import mean
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable

from benchmarks.harness.report import (
    ReportError,
    classify_assistance_pair,
    repository_location_outcome_topology,
)
from benchmarks.harness.runner import TrialRunResult, bind_result_to_receipt
from benchmarks.harness.treatment_attribution import contracted_treatment_evidence

from benchmarks.harness.suite import SuiteDefinition


_QUALIFYING_OUTCOMES = {"PASS", "FAIL", "NO_QUALIFYING_DEFECT"}


def _subject_label(subject: str) -> str:
    if subject == "none":
        return "Bare"
    return subject.replace("_", " ").replace("-", " ").title()


def _replicate_key(row: dict[str, Any]) -> object:
    if "replicate_id" in row:
        return (row["trial"], "replicate", row["replicate_id"])
    if "seed" in row:
        return (row["trial"], "seed", row["seed"])
    return (row["trial"],)


def _render_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def render(row: list[str]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        )

    return [
        render(headers),
        "-+-".join("-" * width for width in widths),
        *(render(row) for row in rows),
    ]


def _display_outcome(receipt: dict[str, Any]) -> str:
    status = str(receipt.get("status") or "UNKNOWN")
    grade = receipt.get("scoring", {}).get("oracle_grade", {})
    if not isinstance(grade, dict):
        return status
    if grade.get("semantic_gradeable") is False:
        return "UNGRADABLE"
    semantic_status = grade.get("semantic_status")
    if semantic_status == "CORRECT":
        return "PASS"
    if semantic_status == "INCORRECT":
        return "FAIL"
    return status


def _subject_tool_use(
    receipt: dict[str, Any],
) -> tuple[bool | None, int | None, tuple[str, ...], str]:
    agent = receipt.get("measurements", {}).get("agent", {})
    if not isinstance(agent, dict):
        return None, None, (), "unknown"
    invoked = agent.get("subject_tool_invoked")
    if not isinstance(invoked, bool):
        invoked = None
    calls = agent.get("subject_mcp_calls")
    if isinstance(calls, bool) or not isinstance(calls, int):
        calls = agent.get("subject_mcp_calls_observed")
    if isinstance(calls, bool) or not isinstance(calls, int):
        calls = None
    names = agent.get("subject_tool_names")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        tool_names: tuple[str, ...] = ()
    else:
        tool_names = tuple(sorted(names))
    observability = agent.get("subject_tool_observability")
    if observability not in {"complete", "partial"}:
        observability = "unknown"
    return invoked, calls, tool_names, observability


class LiveTaskMatrix:
    """Project completed task/agent groups into compact terminal matrices."""

    def __init__(
        self,
        suite: SuiteDefinition,
        selected_rows: list[dict[str, Any]],
    ) -> None:
        self._conditions = {
            str(condition["id"]): condition
            for condition in suite.experiment["conditions"]
        }
        self._expected: Counter[tuple[str, str]] = Counter()
        self._condition_order: dict[tuple[str, str], list[str]] = {}
        self._outcomes: dict[
            tuple[str, str],
            list[tuple[dict[str, Any], str, dict[str, Any]]],
        ] = {}
        self._emitted: set[tuple[str, str]] = set()

        for row in selected_rows:
            condition = self._conditions[str(row["condition_id"])]
            group = (str(row["task_id"]), str(condition["agent"]))
            self._expected[group] += 1
            order = self._condition_order.setdefault(group, [])
            condition_id = str(row["condition_id"])
            if condition_id not in order:
                order.append(condition_id)

    def record(self, row: dict[str, Any], receipt: dict[str, Any]) -> str | None:
        if receipt.get("definition_id") != row["definition_id"]:
            raise ValueError("live matrix receipt does not match selected definition")
        condition = self._conditions[str(row["condition_id"])]
        group = (str(row["task_id"]), str(condition["agent"]))
        condition_id = str(row["condition_id"])
        self._outcomes.setdefault(group, []).append((row, condition_id, receipt))

        if group in self._emitted:
            return None
        if len(self._outcomes[group]) != self._expected[group]:
            return None

        self._emitted.add(group)
        return self._render(group)

    def _render(self, group: tuple[str, str]) -> str:
        task_id, agent = group
        outcomes = self._outcomes[group]
        conditions = self._condition_order[group]
        subjects = [str(self._conditions[item]["subject"]) for item in conditions]
        repeated = {subject for subject, count in Counter(subjects).items() if count > 1}

        def label(condition_id: str) -> str:
            subject = str(self._conditions[condition_id]["subject"])
            name = _subject_label(subject)
            return f"{name} ({condition_id})" if subject in repeated else name

        trial_indexes = sorted({int(row["trial"]) for row, _, _ in outcomes})
        replicate_ids: dict[int, set[object]] = {}
        for row, _, _ in outcomes:
            replicate_ids.setdefault(int(row["trial"]), set()).add(
                row.get("replicate_id", row.get("seed", row["trial"]))
            )
        by_trial_condition = {
            (int(row["trial"]), condition_id): _display_outcome(receipt)
            for row, condition_id, receipt in outcomes
        }

        headers = ["Replicate ID", *(label(condition_id) for condition_id in conditions)]
        table_rows = [
            [
                (
                    str(next(iter(replicate_ids[trial])))
                    if len(replicate_ids[trial]) == 1
                    else f"trial {trial} (mixed IDs)"
                ),
                *(
                    by_trial_condition.get((trial, condition_id), "-")
                    for condition_id in conditions
                ),
            ]
            for trial in trial_indexes
        ]

        lines = [
            "",
            f"RESULT {task_id} [{agent}]",
            "",
            *_render_table(headers, table_rows),
        ]

        topology_rows: list[list[str]] = []
        for condition_id in conditions:
            topologies = Counter(
                topology
                for _row, observed_condition, receipt in outcomes
                if observed_condition == condition_id
                and (
                    topology := repository_location_outcome_topology(receipt)
                )
                is not None
            )
            if not topologies:
                continue
            topology_rows.append(
                [
                    label(condition_id),
                    str(topologies["exact"]),
                    str(topologies["same-file-wrong-symbol"]),
                    str(topologies["same-symbol-wrong-file"]),
                    str(topologies["same-directory-location-mismatch"]),
                    str(topologies["different-location"]),
                    str(topologies["ungradeable"]),
                ]
            )
        if topology_rows:
            lines.extend(["", "Repository-location outcome topology"])
            lines.extend(
                _render_table(
                    [
                        "Subject",
                        "Exact",
                        "Same file",
                        "Same symbol",
                        "Same directory",
                        "Different",
                        "Ungradeable",
                    ],
                    topology_rows,
                )
            )

        if "none" in subjects:
            controls: dict[object, dict[str, Any]] = {}
            for row, condition_id, receipt in outcomes:
                if self._conditions[condition_id]["subject"] != "none":
                    continue
                key = _replicate_key(row)
                if key in controls:
                    raise ReportError(
                        f"multiple bare executions for pair {task_id} / {agent} / {key}"
                    )
                controls[key] = receipt
            assisted_conditions = [
                item for item in conditions if self._conditions[item]["subject"] != "none"
            ]
            if assisted_conditions:
                lines.extend(["", "Subject-configured condition vs Bare (all pairs)"])
                transition_rows: list[list[str]] = []
                for condition_id in assisted_conditions:
                    assisted = {
                        _replicate_key(row): receipt
                        for row, observed_condition, receipt in outcomes
                        if observed_condition == condition_id
                    }
                    counts: Counter[str] = Counter()
                    for key in sorted(assisted, key=repr):
                        baseline = controls.get(key)
                        candidate = assisted.get(key)
                        transition = (
                            classify_assistance_pair(baseline, candidate)
                            if baseline is not None and candidate is not None
                            else None
                        )
                        counts[transition or "excluded"] += 1
                    transition_rows.append(
                        [
                            label(condition_id),
                            str(counts["gain"]),
                            str(counts["preserved"]),
                            str(counts["unresolved"]),
                            str(counts["regression"]),
                            str(counts["excluded"]),
                        ]
                    )
                lines.extend(
                    _render_table(
                        [
                            "Subject",
                            "Gain",
                            "Preserved",
                            "Unresolved",
                            "Regression",
                            "Excluded",
                        ],
                        transition_rows,
                    )
                )

                lines.extend(["", "Paired outcome by subject use"])
                attributed_rows: list[list[str]] = []
                for condition_id in assisted_conditions:
                    assisted = {
                        _replicate_key(row): receipt
                        for row, observed_condition, receipt in outcomes
                        if observed_condition == condition_id
                    }
                    by_use: dict[str, Counter[str]] = {
                        "invoked": Counter(),
                        "not-invoked": Counter(),
                        "unknown": Counter(),
                    }
                    for key in sorted(assisted, key=repr):
                        baseline = controls.get(key)
                        candidate = assisted.get(key)
                        transition = (
                            classify_assistance_pair(baseline, candidate)
                            if baseline is not None and candidate is not None
                            else None
                        )
                        invoked, _calls, _names, _observability = _subject_tool_use(
                            candidate
                        )
                        use_state = (
                            "invoked"
                            if invoked is True
                            else "not-invoked"
                            if invoked is False
                            else "unknown"
                        )
                        by_use[use_state][transition or "excluded"] += 1
                    for use_state, interpretation in (
                        ("invoked", "subject-use observed"),
                        ("not-invoked", "not attributable to subject tool"),
                        ("unknown", "invocation unknown"),
                    ):
                        counts = by_use[use_state]
                        pairs = sum(counts.values())
                        if pairs == 0:
                            continue
                        attributed_rows.append(
                            [
                                label(condition_id),
                                use_state,
                                str(pairs),
                                str(counts["gain"]),
                                str(counts["preserved"]),
                                str(counts["unresolved"]),
                                str(counts["regression"]),
                                str(counts["excluded"]),
                                interpretation,
                            ]
                        )
                if attributed_rows:
                    lines.extend(
                        _render_table(
                            [
                                "Subject",
                                "Use",
                                "Pairs",
                                "Gain",
                                "Preserved",
                                "Unresolved",
                                "Regression",
                                "Excluded",
                                "Interpretation",
                            ],
                            attributed_rows,
                        )
                    )

                lines.extend(["", "Paired outcome by contracted treatment"])
                treatment_rows: list[list[str]] = []
                for condition_id in assisted_conditions:
                    assisted = {
                        _replicate_key(row): receipt
                        for row, observed_condition, receipt in outcomes
                        if observed_condition == condition_id
                    }
                    by_treatment: dict[
                        tuple[str, str], Counter[str]
                    ] = {}
                    for key in sorted(assisted, key=repr):
                        baseline = controls.get(key)
                        candidate = assisted.get(key)
                        transition = (
                            classify_assistance_pair(baseline, candidate)
                            if baseline is not None and candidate is not None
                            else None
                        )
                        treatment = contracted_treatment_evidence(candidate)
                        group_key = (
                            str(treatment["state"]),
                            str(treatment["attribution_interpretation"]),
                        )
                        by_treatment.setdefault(group_key, Counter())[
                            transition or "excluded"
                        ] += 1
                    for (state, interpretation), counts in sorted(
                        by_treatment.items()
                    ):
                        pairs = sum(counts.values())
                        if pairs == 0:
                            continue
                        treatment_rows.append(
                            [
                                label(condition_id),
                                state,
                                str(pairs),
                                str(counts["gain"]),
                                str(counts["preserved"]),
                                str(counts["unresolved"]),
                                str(counts["regression"]),
                                str(counts["excluded"]),
                                interpretation,
                            ]
                        )
                if treatment_rows:
                    lines.extend(
                        _render_table(
                            [
                                "Subject",
                                "Contracted state",
                                "Pairs",
                                "Gain",
                                "Preserved",
                                "Unresolved",
                                "Regression",
                                "Excluded",
                                "Attribution",
                            ],
                            treatment_rows,
                        )
                    )

                lines.extend(["", "Subject tool use"])
                usage_rows: list[list[str]] = []
                exposure_notes: list[str] = []
                for condition_id in assisted_conditions:
                    usage = [
                        _subject_tool_use(receipt)
                        for _row, observed_condition, receipt in outcomes
                        if observed_condition == condition_id
                    ]
                    invoked = sum(value is True for value, *_rest in usage)
                    not_invoked = sum(value is False for value, *_rest in usage)
                    unknown = sum(value is None for value, *_rest in usage)
                    total_calls = sum(
                        calls
                        for _value, calls, _names, _observability in usage
                        if calls is not None
                    )
                    tool_names = sorted(
                        {
                            name
                            for _value, _calls, names, _observability in usage
                            for name in names
                        }
                    )
                    observability = sorted(
                        {
                            value
                            for _invoked, _calls, _names, value in usage
                            if value != "unknown"
                        }
                    )
                    usage_rows.append(
                        [
                            label(condition_id),
                            str(invoked),
                            str(not_invoked),
                            str(unknown),
                            str(total_calls),
                            ",".join(tool_names) or "-",
                            ",".join(observability) or "unknown",
                        ]
                    )
                    if usage and invoked == 0 and unknown == 0:
                        exposure_notes.append(
                            f"{label(condition_id)} on {task_id}: configured condition, "
                            f"0/{len(usage)} subject invocations; outcomes are "
                            "not attributable to the subject tool"
                        )
                lines.extend(
                    _render_table(
                        [
                            "Subject",
                            "Invoked",
                            "Not invoked",
                            "Unknown",
                            "Observed calls",
                            "Tools",
                            "Observation",
                        ],
                        usage_rows,
                    )
                )
                if exposure_notes:
                    lines.extend(
                        [
                            "",
                            "Exposure note",
                            *exposure_notes,
                        ]
                    )

                lines.extend(["", "Subject use by replicate"])
                adoption_headers = [
                    "Replicate ID",
                    *(label(condition_id) for condition_id in assisted_conditions),
                ]
                adoption_rows: list[list[str]] = []
                for trial in trial_indexes:
                    replicate_label = (
                        str(next(iter(replicate_ids[trial])))
                        if len(replicate_ids[trial]) == 1
                        else f"trial {trial} (mixed IDs)"
                    )
                    cells = [replicate_label]
                    for condition_id in assisted_conditions:
                        receipt = next(
                            (
                                receipt
                                for row, observed_condition, receipt in outcomes
                                if int(row["trial"]) == trial
                                and observed_condition == condition_id
                            ),
                            None,
                        )
                        if receipt is None:
                            cells.append("-")
                            continue
                        invoked, calls, names, observability = _subject_tool_use(receipt)
                        state = (
                            "used"
                            if invoked is True
                            else "not-used"
                            if invoked is False
                            else "unknown"
                        )
                        detail = ",".join(names)
                        count = f" calls={calls}" if calls is not None else ""
                        tools = f" {detail}" if detail else ""
                        observation = (
                            f" [{observability}]" if observability != "unknown" else ""
                        )
                        cells.append(f"{state}{tools}{count}{observation}")
                    adoption_rows.append(cells)
                lines.extend(_render_table(adoption_headers, adoption_rows))

        return "\n".join(lines)


def _duration(value: float | None) -> str:
    if value is None:
        return "estimating..."
    seconds = max(0, int(round(value)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def render_campaign_admission(event: dict[str, Any], *, elapsed: float) -> str:
    """Render model-free campaign admission progress without owning authority."""
    stage = str(event.get("stage") or "unknown")
    if stage in {"condition-authority", "condition-authority-complete"}:
        index = event.get("index")
        total = event.get("total")
        task_id = event.get("task_id")
        condition_id = event.get("condition_id")
        if stage == "condition-authority-complete":
            timings = event.get("timings_ms")
            timings = timings if isinstance(timings, dict) else {}
            detail = " | ".join(
                f"{label} {int(timings[key])}ms"
                for key, label in (
                    ("materialize", "materialize"),
                    ("snapshot", "snapshot"),
                    ("participant_prepare", "prepare"),
                    ("oracle_health", "oracle"),
                    ("condition_authority", "authority"),
                    ("total", "total"),
                )
                if isinstance(timings.get(key), (int, float))
            )
            suffix = f" | {detail}" if detail else ""
            return (
                f"ADMISSION [{index}/{total}] {task_id} / {condition_id} | "
                f"authority OK{suffix} | admission elapsed {_duration(elapsed)}"
            )
        return (
            f"ADMISSION [{index}/{total}] {task_id} / {condition_id} | "
            f"checking authority | admission elapsed {_duration(elapsed)}"
        )
    if stage == "campaign-harness-authority":
        duration_ms = event.get("duration_ms")
        detail = (
            f" | {int(duration_ms)}ms"
            if isinstance(duration_ms, (int, float))
            else ""
        )
        return (
            "ADMISSION harness authority | "
            f"{event.get('status') or 'checking'}{detail} | "
            f"admission elapsed {_duration(elapsed)}"
        )
    if stage == "oracle-review":
        return (
            "ADMISSION oracle review | "
            f"{event.get('status') or 'checking'} | "
            f"admission elapsed {_duration(elapsed)}"
        )
    if stage == "campaign-authority":
        total = event.get("total")
        suffix = f" | conditions {total}" if total is not None else ""
        return (
            "ADMISSION campaign authority | "
            f"{event.get('status') or 'checking'}{suffix} | "
            f"admission elapsed {_duration(elapsed)}"
        )
    return (
        f"ADMISSION {stage} | {event.get('status') or 'checking'} | "
        f"admission elapsed {_duration(elapsed)}"
    )


class LiveCampaignProgress:
    """Render bounded human/agent-readable campaign progress and ETA."""

    def __init__(
        self,
        suite: SuiteDefinition,
        selected_rows: list[dict[str, Any]],
        initial_status: dict[str, Any],
    ) -> None:
        self._conditions = {
            str(condition["id"]): condition
            for condition in suite.experiment["conditions"]
        }
        self._total = len(selected_rows)
        self._processed = 0
        self._durations: list[float] = []
        self._durations_by_condition: dict[str, list[float]] = {}
        self._definition_condition = {
            str(row["definition_id"]): str(row["condition_id"])
            for row in selected_rows
        }
        self._states = {
            str(row["definition_id"]): str(row["state"])
            for row in initial_status["rows"]
        }
        self._verified = int(initial_status["complete_trials"])
        self._pending = int(initial_status["pending_trials"])
        self._interrupted = int(initial_status["interrupted_trials"])
        self._unresolved = int(initial_status.get("unresolved_outcome_trials", 0))
        self._task_order: list[str] = []
        for row in selected_rows:
            task_id = str(row["task_id"])
            if task_id not in self._task_order:
                self._task_order.append(task_id)

    def _qualification_suffix(self) -> str:
        if self._unresolved <= 0:
            return ""
        noun = "outcome" if self._unresolved == 1 else "outcomes"
        return (
            f" | qualification BLOCKED | unresolved {self._unresolved} {noun} "
            "| continuing diagnostic evidence"
        )

    def start_line(self, row: dict[str, Any], *, elapsed: float) -> str:
        condition = self._conditions[str(row["condition_id"])]
        task_id = str(row["task_id"])
        task_index = self._task_order.index(task_id) + 1
        trial = int(row["trial"])
        trials = int(condition["trials"])
        eta = self._eta()
        return (
            f"[{self._processed + 1}/{self._total} this run] "
            f"task {task_index}/{len(self._task_order)} {task_id} | "
            f"{row['condition_id']} | replicate {trial + 1}/{trials} | "
            f"run elapsed {_duration(elapsed)} | verified {self._verified}/{self._total} | "
            f"pending {self._pending} | interrupted {self._interrupted} | "
            f"execution ETA {_duration(eta)}"
            f"{self._qualification_suffix()}"
        )

    def finish_line(
        self,
        result: TrialRunResult,
        *,
        elapsed: float,
        trial_seconds: float,
    ) -> str:
        self._processed += 1
        previous = self._states.get(result.definition_id)
        if previous != "COMPLETE":
            self._verified += 1
            if previous == "PENDING":
                self._pending -= 1
            elif previous == "INTERRUPTED":
                self._interrupted -= 1
            self._states[result.definition_id] = "COMPLETE"
            if result.status not in _QUALIFYING_OUTCOMES:
                self._unresolved += 1
        if not result.reused and trial_seconds > 0:
            self._durations.append(trial_seconds)
            condition_id = self._definition_condition[result.definition_id]
            self._durations_by_condition.setdefault(condition_id, []).append(
                trial_seconds
            )
        eta = self._eta()
        mode = (
            "reused"
            if result.reused
            else "recovered"
            if result.recovered
            else "executed"
        )
        average = mean(self._durations) if self._durations else None
        return (
            f"PROGRESS processed {self._processed}/{self._total} "
            f"({(100 * self._processed / self._total):.1f}%) | "
            f"verified {self._verified}/{self._total} | {mode} | "
            f"run elapsed {_duration(elapsed)} | pending {self._pending} | "
            f"interrupted {self._interrupted} | avg {_duration(average)}/execution | "
            f"execution ETA {_duration(eta)}"
            f"{self._qualification_suffix()}"
        )

    def heartbeat_line(
        self,
        row: dict[str, Any],
        *,
        stage: str,
        elapsed: float,
        trial_seconds: float,
    ) -> str:
        return (
            f"ACTIVE {row['task_id']} / {row['condition_id']} replicate {int(row['trial']) + 1} | "
            f"stage {stage} | trial elapsed {_duration(trial_seconds)} | "
            f"run elapsed {_duration(elapsed)} | verified {self._verified}/{self._total}"
            f"{self._qualification_suffix()}"
        )

    def abort_line(
        self,
        row: dict[str, Any],
        *,
        stage: str,
        elapsed: float,
        error: Exception,
    ) -> str:
        return (
            f"ABORT {row['task_id']} / {row['condition_id']} replicate {int(row['trial']) + 1} | "
            f"stage {stage} | processed {self._processed}/{self._total} | "
            f"verified {self._verified}/{self._total} | run elapsed {_duration(elapsed)} | {error}"
        )

    def _eta(self) -> float | None:
        if self._pending + self._interrupted <= 0:
            return 0.0
        if not self._durations:
            return None
        fallback = mean(self._durations)
        remaining = [
            definition_id
            for definition_id, state in self._states.items()
            if state in {"PENDING", "INTERRUPTED"}
        ]
        estimate = 0.0
        for definition_id in remaining:
            condition_id = self._definition_condition.get(definition_id)
            samples = self._durations_by_condition.get(str(condition_id), [])
            estimate += mean(samples) if samples else fallback
        return estimate


class TrialHeartbeat:
    """Emit bounded activity lines without mutating trial evidence."""

    def __init__(
        self,
        *,
        progress: LiveCampaignProgress,
        row: dict[str, Any],
        run_started: float,
        emit: Callable[[str], None],
        interval: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._progress = progress
        self._row = row
        self._run_started = run_started
        self._emit = emit
        self._interval = interval
        self._clock = clock
        self._trial_started = clock()
        self._last_emitted = self._trial_started
        self._stage = "admission"
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def stage(self) -> str:
        with self._lock:
            return self._stage

    def update_stage(self, stage: str) -> None:
        with self._lock:
            self._stage = stage

    def tick(self, now: float | None = None) -> str | None:
        now = self._clock() if now is None else now
        with self._lock:
            if self._stop.is_set() or now - self._last_emitted < self._interval:
                return None
            self._last_emitted = now
            stage = self._stage
        return self._progress.heartbeat_line(
            self._row,
            stage=stage,
            elapsed=now - self._run_started,
            trial_seconds=now - self._trial_started,
        )

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            line = self.tick()
            if line is not None:
                self._emit(line)

    def __enter__(self) -> TrialHeartbeat:
        self._thread = Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()


def render_trial_failure(
    *,
    row: dict[str, Any],
    subject: str,
    result: TrialRunResult,
    receipt: dict[str, Any] | None = None,
) -> str | None:
    """Render one failure from the durable receipt authority when available."""
    authoritative = (
        bind_result_to_receipt(result, receipt)
        if receipt is not None
        else result
    )
    if authoritative.status == "PASS":
        return None
    replicate_id = row.get("replicate_id", row.get("seed"))
    lines = [
        "",
        f"FAILURE {row['task_id']}",
        f"Replicate ordinal: {int(row['trial']) + 1}",
        f"Replicate ID: {replicate_id if replicate_id is not None else 'unknown'}",
        f"Condition: {row['condition_id']}",
        f"Subject: {_subject_label(subject)}",
        f"Status: {authoritative.status}",
        f"Stage: {authoritative.stage or 'unknown'}",
        f"Reason code: {authoritative.reason_code or 'unknown'}",
        f"Reason: {authoritative.reason or 'none'}",
        f"Evidence: {result.result_dir}",
        f"Receipt: {result.result_dir / 'result.json'}",
    ]
    if receipt is not None:
        semantic = _display_outcome(receipt)
        grade = receipt.get("scoring", {}).get("oracle_grade", {})
        format_compliant = (
            grade.get("format_compliant") if isinstance(grade, dict) else None
        )
        lines.extend(
            [
                f"Semantic outcome: {semantic}",
                (
                    "Format compliant: "
                    + (
                        "yes"
                        if format_compliant is True
                        else "no"
                        if format_compliant is False
                        else "unknown"
                    )
                ),
            ]
        )
    if subject != "none" and receipt is not None:
        invoked, calls, names, observability = _subject_tool_use(receipt)
        lines.extend(
            [
                (
                    "Subject tool invoked: "
                    + (
                        "yes"
                        if invoked is True
                        else "no"
                        if invoked is False
                        else "unknown"
                    )
                ),
                f"Subject MCP calls observed: {calls if calls is not None else 'unknown'}",
                f"Subject tools observed: {','.join(names) if names else '-'}",
                f"Subject tool observation: {observability}",
            ]
        )
    if result.recovered:
        lines.append(
            "Recovery: prior interrupted attempt preserved; "
            "execution retried as a numbered attempt"
        )
    if authoritative.diagnostic:
        lines.extend(["", "--- diagnostic ---", authoritative.diagnostic])
    return "\n".join(lines)



def render_run_blockers(
    failures: list[dict[str, Any]], trace_diagnostics: dict[str, Any] | None = None
) -> str | None:
    """Keep the cause and receipt visible without repeating raw diagnostics."""
    if not failures:
        return None
    trials = (
        trace_diagnostics.get("trials", [])
        if isinstance(trace_diagnostics, dict)
        else []
    )
    causes = {
        row.get("trial_id"): row.get("terminal_failure_cause")
        for row in trials
        if isinstance(row, dict)
    }
    cause_counts = Counter(
        causes.get(failure.get("trial_id")) or failure.get("reason_code") or "unknown"
        for failure in failures
    )
    label = "failure" if len(failures) == 1 else "failures"
    lines = [
        "",
        f"RUN BLOCKERS {len(failures)} operational {label}",
        "Causes: " + ", ".join(
            f"{cause} {count}" for cause, count in sorted(cause_counts.items())
        ),
        "Full diagnostics remain in each receipt and trace-diagnostics.json.",
    ]
    for failure in failures:
        cause = causes.get(failure.get("trial_id")) or failure.get("reason_code") or "unknown"
        lines.append(
            f"- {failure['task_id']} / {failure['condition_id']} / "
            f"replicate {failure['replicate_id']}: {cause} | "
            f"receipt {failure['receipt']}"
        )
    return "\n".join(lines)
