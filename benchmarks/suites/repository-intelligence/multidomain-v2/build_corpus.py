"""Materialize the frozen multidomain case draft from inspected source anchors.

This is an authoring tool, never called by benchmark execution. Generated cases
remain drafts until independent reviewers approve their prompts and oracles.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OLDER = ROOT.parent / "heldout-v1"
REPOSITORIES = (
    (
        "hashmarks",
        "python",
        "https://github.com/Hugloss/Hashmarks.git",
        "85453be578e8ff46b8d79b363f140a915d6804f6",
        "667b7066be9ef6cab6e1ce3a28a603481f07387e",
    ),
    (
        "doctor-scheduler-core",
        "python",
        "https://github.com/Hugloss/doctor-scheduler-core.git",
        "6d1080136d54fb6da142dff2e302b31da69cd345",
        "e91dbeaaaf100e2c673308ebca7d0325a314e9a0",
    ),
    (
        "doctor-scheduler-frontend",
        "typescript",
        "https://github.com/Hugloss/doctor-scheduler-frontend.git",
        "618bdbdfef1a6a2267433970fd9ff5ffd8af5b10",
        "b0a102602cf13f9d45b30ef1b64c1608a9a33ecf",
    ),
    (
        "uv-fleet-updater",
        "typescript",
        "https://github.com/Hugloss/uv-fleet-updater.git",
        "fbfad7ad14d96d2cb51a6da931b0fb1a60f66e9d",
        "3ded8be71da0ac93700dcf3a3af76eb172e75ff8",
    ),
)
ANCHORS = (
    (
        ("hashmarks/codemap/repository_index_store.py", "paths_under"),
        ("hashmarks/codemap/indexing_lifecycle.py", "_sync_remove_stale_paths"),
        ("hashmarks/codemap/repository_file_discovery.py", "_prune_discovery_dirs"),
        ("hashmarks/mcp_surface.py", "task_evidence"),
        ("hashmarks/test_shards.py", "repository_content_identity"),
    ),
    (
        (
            "src/doctor_scheduler_solver_service/request_intents.py",
            "parse_request_reason",
        ),
        (
            "src/doctor_scheduler_solver_service/request_intents.py",
            "request_matches_window",
        ),
        (
            "src/doctor_scheduler_solver_service/request_intents.py",
            "weekly_intent_matches_window",
        ),
        ("src/doctor_scheduler_solver_service/utils.py", "make_json_safe"),
        ("src/doctor_scheduler_solver_service/utils.py", "safe_slug"),
    ),
    (
        ("convex/scheduleRevisionModel.ts", "canonicalScheduleScope"),
        ("convex/scheduleRevisionModel.ts", "scheduleContentHash"),
        ("convex/jsonPayloadEncoding.ts", "encodeConvexJsonPayload"),
        ("convex/scheduleNormalization.ts", "assignmentRowsForSchedule"),
        ("src/core/urlUtils.ts", "normalizeUrl"),
        (
            "src/hooks/general/useDepartmentSelectionGuard.ts",
            "useDepartmentSelectionGuard",
        ),
    ),
    (
        ("ux/src/domain/reads/readInvalidation.ts", "readScopesInvalidatedByMutation"),
        ("ux/src/domain/reads/readResources.ts", "resourcesInvalidatedByMutation"),
        ("ux/src/domain/mutations/WriteCommandResult.ts", "withRefreshOutcome"),
        ("ux/src/domain/fleet/runLifecycle.ts", "isFleetRunTerminal"),
        ("ux/src/app/api/httpTransport.ts", "readRequest"),
        ("ux/src/domain/fleet/runLifecycle.ts", "runPollDelay"),
    ),
)
FAMILIES = ("logs", "splunk", "dependencies", "semantics", "identities", "code_owners")
ROLES = (
    "diagnosis",
    "diagnosis",
    "decision",
    "decision",
    "verification",
    "verification",
    "diagnosis",
    "decision",
    "verification",
    "decision",
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _fixture(
    family: str, case_no: int, repository: str, path: str, symbol: str
) -> tuple[str, dict[str, object]]:
    if family == "logs":
        frames = 2 if case_no % 2 else 1
        body = f"2026-09-14T10:00:00Z ERROR service={repository} symbol={symbol} request=sample-{case_no}\n"
        if frames == 2:
            body += f"  at {symbol} (frame 1)\n  at caller (frame 2)\n"
        else:
            body += f"  at {symbol} (frame 1)\n"
        body += (
            f"2026-09-14T10:00:01Z INFO service={repository} request=sample-{case_no}\n"
        )
        expectation = {
            "path": path,
            "symbol": symbol,
            "frame_count": frames,
            "runtime_source_match": "unknown",
        }
    elif family == "splunk":
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            (
                "_serial",
                "_time",
                "source",
                "sourcetype",
                "host",
                "index",
                "splunk_server",
                "_raw",
            )
        )
        raw = f"ERROR name={symbol} request=sample-{case_no}"
        if case_no % 2:
            raw += "\ntraceback continuation, still one CSV record"
        writer.writerow(
            (
                1,
                "2026-09-14T10:00:00+00:00",
                "masked",
                "app:log",
                "masked",
                "sample",
                "masked",
                raw,
            )
        )
        writer.writerow(
            (
                1 if case_no % 3 == 2 else 2,
                "2026-09-14T10:00:01+00:00",
                "masked",
                "app:log",
                "masked",
                "sample",
                "masked",
                "INFO unrelated event",
            )
        )
        body = output.getvalue()
        expectation = {
            "event_count": 2,
            "path": path,
            "symbol": symbol,
            "runtime_source_match": "unknown",
        }
    elif family == "dependencies":
        value = {
            "consumer_symbol": symbol,
            "declaration": {"name": f"sample-library-{case_no}", "range": "^2"},
            "resolution": {
                "name": f"sample-library-{case_no}",
                "version": f"2.{case_no}.0",
                "source": "registry",
            },
            "import_owner": None,
        }
        body = json.dumps(value, indent=2) + "\n"
        expectation = {
            "consumer": path,
            "selected_version": f"2.{case_no}.0",
            "import_owner": "unknown",
        }
    elif family == "semantics":
        left = f"scope-{case_no}"
        right = left if case_no % 2 == 0 else f"scope-{case_no}-other"
        body = (
            json.dumps(
                {
                    "concept": symbol,
                    "declarations": [
                        {"source_symbol": symbol, "value": left},
                        {"source": "config/consumer.json", "value": right},
                    ],
                },
                indent=2,
            )
            + "\n"
        )
        expectation = {"source": path, "declarations_agree": left == right}
    elif family == "identities":
        before = f"semantic-{case_no}"
        semantic_changed = case_no % 4 in (2, 3)
        artifact_changed = case_no % 4 in (1, 3)
        after = f"semantic-{case_no}-changed" if semantic_changed else before
        body = (
            json.dumps(
                {
                    "source_symbol": symbol,
                    "before": {"artifact": "artifact-a", "semantic": before},
                    "after": {
                        "artifact": "artifact-b" if artifact_changed else "artifact-a",
                        "semantic": after,
                    },
                },
                indent=2,
            )
            + "\n"
        )
        expectation = {
            "source": path,
            "artifact_changed": artifact_changed,
            "semantic_changed": semantic_changed,
        }
    else:
        body = (
            json.dumps({"task_symbol": symbol, "ownership_hint": "hint-only"}, indent=2)
            + "\n"
        )
        ambiguous = symbol in {"task_evidence", "repair_lone_half_days_snapshot"}
        expectation = {
            "path": None if ambiguous else path,
            "symbol": symbol,
            "owner_state": "ambiguous" if ambiguous else "unique-source-declaration",
        }
    suffix = "csv" if family == "splunk" else "txt" if family == "logs" else "json"
    name = f"fixtures/{family}-{case_no:02d}.{suffix}"
    file = ROOT / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(body, encoding="utf-8")
    return name, expectation


def main(*, force: bool = False) -> None:
    if (ROOT / "evidence.json").exists() and not force:
        raise SystemExit(
            "corpus already exists; --force explicitly discards case edits and review records"
        )
    for kind in ("agents", "subjects"):
        (ROOT / kind).mkdir(exist_ok=True)
        for source in (OLDER / kind).glob("*.json"):
            shutil.copyfile(source, ROOT / kind / source.name)
    cases = []
    task_ids = []
    for family_index, family in enumerate(FAMILIES):
        for index in range(10):
            global_index = family_index * 10 + index
            repo_index = global_index % 4
            name, language, url, commit, tree = REPOSITORIES[repo_index]
            anchor = ANCHORS[repo_index][(global_index // 4) % len(ANCHORS[repo_index])]
            path, symbol = anchor
            if family == "code_owners" and index == 3:
                path, symbol = (
                    "src/doctor_scheduler_solver_service/compute.py",
                    "repair_lone_half_days_snapshot",
                )
            fixture, expected = _fixture(family, index, name, path, symbol)
            fixture_target = (
                fixture.removesuffix(".txt") + ".log" if family == "logs" else fixture
            )
            case_id = f"{family}-{index:02d}"
            role = ROLES[index]
            question = {
                "logs": f"Inspect {fixture_target}. Count the stack frames in the ERROR observation, locate its repository implementation, and state whether this export proves runtime source-byte equivalence.",
                "splunk": f"Inspect the frozen Splunk CSV at {fixture}. Count logical events, locate the ERROR symbol's repository implementation, and state whether the export proves source-byte equivalence.",
                "dependencies": f"Inspect the caller-supplied dependency observation in {fixture}. Find the pinned source declaration of its consumer_symbol and identify the selected version. Report import ownership as unknown unless independent evidence proves it.",
                "semantics": f"Inspect the two caller-supplied declarations in {fixture}. Compare their values and find the pinned source declaration of source_symbol. Do not treat the supplied conceptual association as repository authority.",
                "identities": f"Inspect the caller-supplied endpoints in {fixture}. Distinguish source artifact change from semantic identity change and find the pinned declaration of source_symbol.",
                "code_owners": f"Inspect {fixture} and the pinned repository. Decide whether the named symbol has one source declaration owner. Use null for path if ownership is ambiguous; treat the hint as non-authoritative.",
            }[family]
            expected_paths = (
                [
                    "hashmarks/codemap/evidence_packet.py",
                    "hashmarks/codemap/service.py",
                    "hashmarks/mcp_surface.py",
                    "hashmarks/mcp_server.py",
                ]
                if family == "code_owners" and symbol == "task_evidence"
                else [
                    "src/doctor_scheduler_solver_service/compute.py",
                    "src/doctor_scheduler_solver_service/workflows/draft_repairs.py",
                    "src/doctor_scheduler_solver_service/processor.py",
                ]
                if family == "code_owners"
                and symbol == "repair_lone_half_days_snapshot"
                else [path]
            )
            case = {
                "id": case_id,
                "family": family,
                "role": role,
                "language": language,
                "repository_name": name,
                "repository": {"url": url, "commit": commit, "tree": tree},
                "question": question,
                "probe_query": (
                    f"Identify source declaration ownership for {symbol}; preserve ambiguity if the name has multiple owners"
                    if family == "code_owners"
                    else f"Find the source declaration for {symbol} relevant to this {family} observation"
                ),
                "expected": expected,
                "expected_paths": expected_paths,
                "fixture": {
                    "artifact": fixture,
                    "sha256": hashlib.sha256((ROOT / fixture).read_bytes()).hexdigest(),
                    "target": fixture_target,
                },
                "direct_support": ["hashmarks", "enola"]
                if family == "dependencies"
                else ["hashmarks"],
                "agent_task": index < 6,
                "review": {"state": "pending", "approvals": []},
            }
            cases.append(case)
            if index < 6:
                task_ids.append(case_id)
                prompt = (
                    question
                    + " Return exactly one JSON object with keys "
                    + ", ".join(expected)
                    + ", and no other text."
                )
                _write_json(
                    ROOT / "tasks" / f"{case_id}.json",
                    {
                        "id": case_id,
                        "version": 1,
                        "family": family,
                        "mode": "read_only",
                        "repository": case["repository"],
                        "prompt": prompt,
                        "mutation": None,
                        "fixtures": [case["fixture"]],
                        "oracle": {
                            "adapter": "expected-json",
                            "identity": {"id": f"{case_id}-oracle", "version": "1"},
                            "configuration": {"expected": expected},
                        },
                        "budgets": {
                            "timeout_seconds": 600,
                            "max_tool_calls": 100,
                            "max_output_bytes": 50000000,
                        },
                        "contamination": {
                            "allowed_change_globs": [],
                            "allowed_generated_globs": [
                                "**/__pycache__/**",
                                ".pytest_cache/**",
                            ],
                        },
                    },
                )
    _write_json(
        ROOT / "evidence.json",
        {"schema": "agents-cookbook-multidomain-evidence.v1", "cases": cases},
    )
    old_experiment = json.loads((OLDER / "experiment.json").read_text(encoding="utf-8"))
    for condition in old_experiment["conditions"]:
        condition["trials"] = 1
        condition.pop("seed", None)
        condition["replicate_ids"] = [8201]
    old_experiment.update(
        id="repository-intelligence-multidomain-v2", version=2, tasks=task_ids
    )
    _write_json(ROOT / "experiment.json", old_experiment)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    main(force=parser.parse_args().force)
