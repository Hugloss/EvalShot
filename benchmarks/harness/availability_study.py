"""Explicit native availability study; historical subject-use reports stay unchanged.

This read-only projection keeps every frozen assignment in the primary analysis.
Server catalog admission and native tool returns do not attest model input.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.adapters.codex import (
    _final_message,
    _metrics as codex_metrics,
    _tracked_tool_name,
    parse_codex_jsonl,
)
from benchmarks.adapters.opencode_native import _metrics as opencode_metrics
from benchmarks.harness.campaign_authority import (
    CampaignAuthorityError,
    read_campaign,
    verify_campaign_suite_authority,
)
from benchmarks.harness.identity import digest
from benchmarks.harness.report import ReportError, _receipts, build_report
from benchmarks.harness.suite import SuiteDefinition, load_suite
from benchmarks.tool_routing import classify_tool, is_subject_tool


SCHEMA = "evalshot.native-availability-study.v1"



def _completed_tool_order_diagnostic(
    metrics: dict[str, Any], trace_state: str, subject: str
) -> dict[str, Any]:
    """Observations of completed tools, not decision, request, or attention proof."""
    unknown = {
        "state": "UNKNOWN",
        "first_completed_tool": None,
        "first_completed_class": None,
        "subject_first_completed": None,
        "subject_first_completed_ordinal": None,
    }
    if trace_state != "COMPLETE_MCP_TELEMETRY":
        return unknown
    sequence = metrics.get("tool_sequence")
    ordinals = metrics.get("subject_tool_call_ordinals")
    if (
        not isinstance(sequence, list)
        or not all(isinstance(item, str) and item for item in sequence)
        or not isinstance(ordinals, list)
        or not all(type(item) is int and 1 <= item <= len(sequence) for item in ordinals)
    ):
        return unknown
    first = sequence[0] if sequence else None
    return {
        "state": "OBSERVED_COMPLETION_ORDER",
        "first_completed_tool": first,
        "first_completed_class": (
            classify_tool(first, subject=subject) if first is not None else None
        ),
        "subject_first_completed": (
            is_subject_tool(first, subject) if first is not None else False
        ),
        "subject_first_completed_ordinal": ordinals[0] if ordinals else None,
    }


def _codex_tool_lifecycle_conflict(events: list[dict[str, Any]]) -> bool:
    """Require stable tool identity for a native item ID across its lifecycle.

    Historical completion-only traces remain valid evidence of completion,
    but a started ID cannot complete as a different item type or MCP tool.
    """
    starts: dict[str, tuple[object, str | None]] = {}
    completions: set[str] = set()
    for event in events:
        phase = event.get("type")
        if phase not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return True
        identity = item["id"]
        if phase == "item.started":
            if identity in starts or identity in completions:
                return True
            starts[identity] = (item.get("type"), _tracked_tool_name(item))
        else:
            if identity in completions:
                return True
            if identity in starts and starts[identity] != (
                item.get("type"),
                _tracked_tool_name(item),
            ):
                return True
            completions.add(identity)
    return False


def _codex_started_tool_order(
    events: list[dict[str, Any]], trace_state: str, subject: str
) -> dict[str, Any]:
    """Only complete, matching native start/completion pairs prove start order.

    This records observable tool starts, not the model's internal decision order,
    delivery of tool results to a model, or tool usefulness. Older completion-
    only JSONL does not claim a start order.
    """
    unknown = {
        "state": "UNKNOWN",
        "first_started_tool": None,
        "first_started_class": None,
        "subject_first_started": None,
        "subject_first_started_ordinal": None,
    }
    if trace_state != "COMPLETE_MCP_TELEMETRY":
        return unknown
    started: dict[str, str] = {}
    completed: dict[str, str] = {}
    order: list[str] = []
    for event in events:
        phase = event.get("type")
        if phase not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            return unknown
        name = _tracked_tool_name(item)
        if name is None:
            continue
        identity = item.get("id")
        if not isinstance(identity, str) or not identity:
            return unknown
        if phase == "item.started":
            if identity in started:
                return unknown
            started[identity] = name
            order.append(name)
        else:
            if identity in completed or identity not in started:
                return unknown
            completed[identity] = name
    if started != completed:
        return unknown
    first = order[0] if order else None
    ordinal = next(
        (i for i, name in enumerate(order, 1) if is_subject_tool(name, subject)),
        None,
    )
    return {
        "state": "OBSERVED_START_ORDER",
        "first_started_tool": first,
        "first_started_class": (
            classify_tool(first, subject=subject) if first is not None else None
        ),
        "subject_first_started": (
            is_subject_tool(first, subject) if first is not None else False
        ),
        "subject_first_started_ordinal": ordinal,
    }


def _codex_subject_start_observed(
    events: list[dict[str, Any]], subject: str
) -> bool | None:
    """Observe a start within one native thread/turn, never return or model use."""
    if not events or events[0].get("type") != "thread.started":
        return None
    # An isolated tool event without its turn boundary, or a post-terminal
    # event, cannot establish that the selected native run initiated the call.
    turns = [
        index for index, event in enumerate(events)
        if event.get("type") == "turn.started"
    ]
    if len(turns) != 1:
        return None
    start = turns[0]
    if start <= 0:
        return None
    terminal = next(
        (
            index for index in range(start + 1, len(events))
            if events[index].get("type") in {"turn.completed", "turn.failed", "error"}
        ),
        len(events),
    )
    for event in events[start + 1 : terminal]:
        if event.get("type") != "item.started":
            continue
        item = event.get("item")
        if (
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"]
            and item.get("type") == "mcp_tool_call"
            and item.get("server") == subject
            and isinstance(item.get("tool"), str)
            and item["tool"]
        ):
            return True
    # Completion-only hosts and incomplete exports cannot prove no start.
    return None


def inspect_native_trace(
    raw: str, *, adapter: str, subject: str, terminal_complete: bool,
) -> dict[str, Any]:
    """Read native formats using the production adapters, without host attestations.

    Complete means supported MCP telemetry in this trace, never exhaustive shell
    activity, provider request capture, or proof the model read returned content.
    """
    metrics: dict[str, Any] = {}
    final = None
    state = "UNKNOWN"
    events: list[dict[str, Any]] = []
    if adapter == "codex":
        events, errors = parse_codex_jsonl(raw.encode("utf-8"))
        final = _final_message(events)
        malformed = any(event.get("type") not in {
            "thread.started", "turn.started", "turn.completed", "turn.failed", "error",
            "item.started", "item.updated", "item.completed",
        } for event in events) or any(
            event.get("type") in {"item.started", "item.updated", "item.completed"}
            and (not isinstance(event.get("item"), dict)
                 or not isinstance(event["item"].get("id"), str)
                 or not event["item"]["id"]
                 or event["item"].get("type") not in {
                     "reasoning", "agent_message", "command_execution", "file_change",
                     "mcp_tool_call", "web_search", "todo_list", "error",
                 }
                 or event["item"].get("type") == "mcp_tool_call" and not all(
                     isinstance(event["item"].get(key), str) and event["item"][key]
                     for key in ("server", "tool")
                 ))
            for event in events
        )
        unfinished = {
            event["item"].get("id") for event in events
            if event.get("type") in {"item.started", "item.updated"}
            and isinstance(event.get("item"), dict)
        } - {
            event["item"].get("id") for event in events
            if event.get("type") == "item.completed"
            and isinstance(event.get("item"), dict)
        }
        metrics = codex_metrics(events, subject_server=subject)
        boundaries = (
            bool(events) and events[0].get("type") == "thread.started"
            and sum(event.get("type") == "thread.started" for event in events) == 1
            and sum(event.get("type") == "turn.started" for event in events) == 1
            and sum(event.get("type") == "turn.completed" for event in events) == 1
            and events[-1].get("type") == "turn.completed"
        )
        if (
            not errors
            and not malformed
            and not unfinished
            and not _codex_tool_lifecycle_conflict(events)
            and boundaries
            and final
            and terminal_complete
        ):
            state = "COMPLETE_MCP_TELEMETRY"
    elif adapter == "opencode-native":
        try:
            exported = json.loads(raw)
        except ValueError:
            exported = None
        messages = exported.get("messages") if isinstance(exported, dict) else None
        if isinstance(messages, list) and messages and all(
            isinstance(message, dict) and isinstance(message.get("info"), dict)
            and isinstance(message.get("parts"), list)
            and all(isinstance(part, dict) for part in message["parts"])
            for message in messages
        ):
            metrics = opencode_metrics(
                exported, mcp_servers=(subject,), selected_server=subject,
            )
            assistants = [m for m in messages if m["info"].get("role") == "assistant"]
            if assistants:
                parts = assistants[-1]["parts"]
                last_tool = max((i for i, p in enumerate(parts) if p.get("type") == "tool"), default=-1)
                text = [p.get("text") for p in parts[last_tool + 1:]
                        if p.get("type") == "text" and isinstance(p.get("text"), str)]
                final = "\n".join(text).strip() or None
            tools = [p for m in messages for p in m["parts"] if p.get("type") == "tool"]
            complete_tools = all(
                isinstance(p.get("tool"), str) and p["tool"]
                and isinstance(p.get("state"), dict)
                and p["state"].get("status") in {"completed", "error"}
                for p in tools
            )
            known_parts = all(
                p.get("type") in {"text", "reasoning", "tool", "step-start", "step-finish", "snapshot", "patch", "file"}
                for m in messages for p in m["parts"]
            )
            # A subject-qualified spelling not understood by this adapter is
            # unsupported telemetry, never evidence of zero subject calls.
            unresolved_subject_name = (
                any(is_subject_tool(p.get("tool"), subject) for p in tools)
                and metrics.get("subject_tool_invoked") is not True
            )
            if terminal_complete and final and complete_tools and known_parts and not unresolved_subject_name and metrics.get("subject_tool_observability") == "complete":
                state = "COMPLETE_MCP_TELEMETRY"
    invoked = metrics.get("subject_tool_invoked")
    # An explicit positive remains observable in a partial trace. A negative
    # needs a complete supported scope; an absent/unsupported trace is unknown.
    invocation = True if invoked is True else False if state == "COMPLETE_MCP_TELEMETRY" else None
    returned = any(
        row.get("outcome") == "successful-result-observed"
        for row in metrics.get("subject_tool_result_evidence", [])
    )
    return {
        "trace_state": state,
        "subject_invoked": invocation,
        "subject_start_observed": (
            _codex_subject_start_observed(events, subject)
            if adapter == "codex"
            else None
        ),
        "successful_return_observed": returned if returned else False if invocation is False else None,
        "final_answer": final,
        "model_request_inclusion": "UNKNOWN",
        "model_attention": "UNKNOWN",
        "completed_tool_order": _completed_tool_order_diagnostic(metrics, state, subject),
        "started_tool_order": (
            _codex_started_tool_order(events, state, subject)
            if adapter == "codex"
            else {
                "state": "UNKNOWN",
                "first_started_tool": None,
                "first_started_class": None,
                "subject_first_started": None,
                "subject_first_started_ordinal": None,
            }
        ),
    }


def _catalog_observed(campaign: dict[str, Any], subject: str) -> bool:
    proofs = campaign.get("subject_tool_catalog_proofs", [])
    if not isinstance(proofs, list):
        return False
    for proof in proofs:
        if not isinstance(proof, dict) or proof.get("subject_id") != subject:
            continue
        keys = ("subject_id", "required_tool", "required_operation", "protocol_version", "tool_names")
        identity = {key: proof.get(key) for key in keys}
        names = identity["tool_names"]
        if (all(isinstance(identity[key], str) and identity[key] for key in keys[:-1])
                and isinstance(names, list) and names and all(isinstance(n, str) and n for n in names)
                and names == sorted(set(names))
                and identity["required_operation"] in names
                and identity["required_tool"] == subject + "_" + str(identity["required_operation"])
                and proof.get("required_tool_visible") is True
                and proof.get("catalog_sha256") == digest(identity)):
            return True
    return False


def _outcomes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = Counter("success" if row["success"] is True else "failure"
                     if row["success"] is False else "unknown" for row in rows)
    count = len(rows)
    return {
        "assigned_trials": count,
        "successes": values["success"], "failures": values["failure"],
        "unknown_outcomes": values["unknown"],
        "success_rate": values["success"] / count if count and not values["unknown"] else None,
        "success_rate_bounds": [values["success"] / count,
                                (values["success"] + values["unknown"]) / count] if count else None,
    }


def _tool_order_summary(
    rows: list[dict[str, Any]], *, field: str, phase: str, observed_state: str
) -> dict[str, Any]:
    """Keep one assigned population across both passive tool-order projections."""
    records = [row[field] for row in rows]
    first_key = "first_" + phase + "_tool"
    subject_key = "subject_first_" + phase
    return {
        "assigned_trials": len(rows),
        subject_key: sum(row[subject_key] is True for row in records),
        "other_tool_first_" + phase: sum(
            row["state"] == observed_state
            and row[first_key] is not None
            and row[subject_key] is False
            for row in records
        ),
        "no_tracked_tool": sum(
            row["state"] == observed_state and row[first_key] is None
            for row in records
        ),
        "unknown": sum(row["state"] != observed_state for row in records),
        "claim_scope": (
            "observed-" + phase + "-tool-order-only;"
            "not-native-tool-choice-or-model-request-proof"
        ),
    }


def build_availability_study(
    *, suite: SuiteDefinition, results_root: Path,
    baseline_condition: str, available_condition: str,
) -> dict[str, Any]:
    """Compare one native configuration with and without optional Hashmarks.

    The explicit command is the study opt-in. It does not reinterpret historical
    campaign qualification or exclude assigned non-invokers from the comparison.
    """
    conditions = {c["id"]: c for c in suite.experiment["conditions"]}
    if baseline_condition == available_condition or any(
        name not in conditions for name in (baseline_condition, available_condition)
    ):
        raise ReportError("select distinct existing baseline and availability conditions")
    bare, available = conditions[baseline_condition], conditions[available_condition]
    if (suite.subjects[bare["subject"]].get("kind") != "control"
            or available["subject"] != "hashmarks"
            or bare["agent"] != available["agent"]
            or bare.get("context") != available.get("context")):
        raise ReportError("availability comparison requires matching native agent/context and control/Hashmarks")
    agent = suite.agents[bare["agent"]]
    adapter = agent.get("adapter")
    config = agent.get("configuration", {})
    if (adapter not in {"codex", "opencode-native"}
            or adapter == "codex" and config.get("native_host") is not True
            or config.get("diagnostic_required_tool")):
        raise ReportError("availability requires an optional-tool native configuration")
    expected = [d for d in suite.trial_definitions()
                if d["condition_id"] in {baseline_condition, available_condition}]
    def pair_key(row: dict[str, Any]) -> tuple[str, int]:
        return str(row["task_id"]), row.get("replicate_id", row.get("seed"))
    assignments = [
        [pair_key(d) for d in expected if d["condition_id"] == name]
        for name in (baseline_condition, available_condition)
    ]
    # Set equality alone can collapse duplicate frozen task/replicate slots
    # and silently replace one receipt in the later by-arm key projection.
    if any(len(rows) != len(set(rows)) for rows in assignments):
        raise ReportError(
            "availability conditions contain duplicate task/replicate assignments"
        )
    assignment_sets = [set(rows) for rows in assignments]
    if assignment_sets[0] != assignment_sets[1]:
        raise ReportError("availability conditions must select identical task/replicate assignments")
    selected = {d["definition_id"] for d in expected}
    canonical = build_report(
        suite=suite, results_root=results_root, require_complete=False,
        selected_definitions=selected,
    )
    campaign = read_campaign(results_root)
    verify_campaign_suite_authority(suite=suite, campaign=campaign)
    if not selected.issubset(set(campaign.get("selected_definitions", []))):
        raise ReportError("availability assignments exceed the frozen campaign")
    # The ordinary report already validates bundles, launch claims, frozen plan,
    # scoring/comparability and authority epochs. Its use-required policy remains.
    receipts = {r["definition_id"]: r for r in _receipts(results_root) if r["definition_id"] in selected}
    catalog = _catalog_observed(campaign, "hashmarks")
    rows = []
    for definition in expected:
        receipt = receipts.get(definition["definition_id"])
        observation = inspect_native_trace("", adapter=str(adapter), subject="hashmarks", terminal_complete=False)
        issues = []
        success = None
        if receipt is None:
            issues.append("missing-assigned-trial")
        else:
            execution = receipt.get("execution", {})
            if execution.get("campaign_id") != campaign["campaign_id"]:
                issues.append("trial-campaign-binding-unproven")
            evidence = execution.get("artifacts", {}).get("agent_trace")
            if isinstance(evidence, dict):
                terminal = execution.get("agent_terminal")
                observation = inspect_native_trace(
                    (results_root / receipt["trial_id"] / evidence["path"]).read_text(encoding="utf-8"),
                    adapter=str(adapter), subject="hashmarks",
                    terminal_complete=isinstance(terminal, dict) and terminal.get("type") == "turn.completed",
                )
            if observation["trace_state"] != "COMPLETE_MCP_TELEMETRY":
                issues.append("native-mcp-telemetry-incomplete")
            if observation["final_answer"] != execution.get("agent_answer") or observation["final_answer"] is None:
                issues.append("native-answer-receipt-join-unproven")
            grade = receipt.get("scoring", {}).get("oracle_grade", {})
            if receipt.get("status") in {"PASS", "FAIL", "NO_QUALIFYING_DEFECT"} and grade.get("valid") is not False:
                success = grade.get("semantic_success", grade.get("passed"))
                success = success if type(success) is bool else None
            if definition["condition_id"] == available_condition:
                if not catalog:
                    issues.append("admitted-server-catalog-unproven")
                if receipt.get("authority", {}).get("subject", {}).get("available") is not True:
                    issues.append("subject-availability-unproven")
                if receipt.get("measurements", {}).get("agent", {}).get("subject_tool_configured") is not True:
                    issues.append("subject-configuration-unproven")
            else:
                if observation["subject_invoked"] is True:
                    issues.append("baseline-subject-contamination")
                if receipt.get("measurements", {}).get("agent", {}).get("subject_tool_configured") is not False:
                    issues.append("baseline-subject-configuration-unproven")
        if success is None:
            issues.append("oracle-outcome-unknown")
        rows.append({**definition, "success": success,
                     "status": receipt.get("status") if receipt else "MISSING",
                     **{k: v for k, v in observation.items() if k != "final_answer"},
                     "issues": issues})
    arm_rows = {c: [r for r in rows if r["condition_id"] == c]
                for c in (baseline_condition, available_condition)}
    subject_rows = arm_rows[available_condition]
    invoked = sum(r["subject_invoked"] is True for r in subject_rows)
    started = sum(r["subject_start_observed"] is True for r in subject_rows)
    unknown = sum(r["subject_invoked"] is None for r in subject_rows)
    first_order_summary = _tool_order_summary(
        subject_rows, field="completed_tool_order",
        phase="completed", observed_state="OBSERVED_COMPLETION_ORDER",
    )
    started_order_summary = _tool_order_summary(
        subject_rows, field="started_tool_order",
        phase="started", observed_state="OBSERVED_START_ORDER",
    )
    pair_rows = []
    tainted = canonical["campaign_qualification"]["evidence_tainted"]
    by_arm = {c: {pair_key(r): r for r in rs} for c, rs in arm_rows.items()}
    for key in sorted(assignment_sets[0]):
        left, right = by_arm[baseline_condition][key], by_arm[available_condition][key]
        before, after = left["success"], right["success"]
        pair_rows.append({"task_id": key[0], "replicate_id": key[1],
                          "baseline_success": before, "available_success": after,
                          "delta": int(after) - int(before) if not tainted and before is not None and after is not None else None})
    qualified = not tainted and all(not r["issues"] for r in rows)
    return {
        "schema": SCHEMA, "campaign_id": campaign["campaign_id"],
        "study_measurement_qualified": qualified,
        "historical_subject_use_qualification": canonical["campaign_qualification"]["subject_exposure"]["status"],
        "authority_epoch_tainted": tainted,
        "primary_availability_intention_to_treat": {
            "conditions": {c: _outcomes(rs) for c, rs in arm_rows.items()},
            "pairs": pair_rows, "expected_pairs": len(pair_rows),
            "known_pair_deltas": sum(p["delta"] is not None for p in pair_rows),
            "mean_paired_delta": sum(p["delta"] for p in pair_rows) / len(pair_rows)
            if qualified and pair_rows else None,
            "claim_scope": "all-frozen-assignments-descriptive-only",
        },
        "native_subject_start_diagnostic": {
            "assigned_trials": len(subject_rows),
            "observed_starts": started,
            "not_established": len(subject_rows) - started,
            "claim_scope": "observed-native-mcp-start-only;not-adoption-or-return",
        },
        "voluntary_adoption": {
            "assigned_trials": len(subject_rows),
            "invoked": invoked,
            "not_invoked": len(subject_rows) - invoked - unknown,
            "unknown": unknown,
            # Operational counts may survive invalid/contaminated receipts,
            # but only a qualified study may publish an adoption rate.
            "rate": (
                invoked / len(subject_rows)
                if qualified and subject_rows and not unknown
                else None
            ),
            "rate_qualification": (
                "qualified" if qualified else "unqualified-operational-counts-only"
            ),
        },
        "completed_tool_order_diagnostic": first_order_summary,
        "started_tool_order_diagnostic": started_order_summary,
        "successful_return_subset_diagnostic": {
            **_outcomes([r for r in subject_rows if r["successful_return_observed"] is True]),
            "claim_scope": "post-assignment-selected-subset-descriptive-only",
            "evidence_of_model_delivery": False,
        },
        "model_delivered_subset_diagnostic": {"state": "UNKNOWN", "trials": None,
                                              "reason": "native trace does not capture submitted model requests"},
        "server_catalog_observed": catalog, "host_catalog_inclusion": "UNKNOWN",
        "trials": rows, "causal_effect_proven": False, "model_attention_proven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--baseline-condition", required=True)
    parser.add_argument("--available-condition", required=True)
    args = parser.parse_args()
    try:
        report = build_availability_study(
            suite=load_suite(args.suite), results_root=args.results_root,
            baseline_condition=args.baseline_condition, available_condition=args.available_condition,
        )
    except (OSError, ValueError, KeyError, TypeError, CampaignAuthorityError) as exc:
        print(json.dumps({"schema": SCHEMA, "study_measurement_qualified": False, "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["study_measurement_qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
