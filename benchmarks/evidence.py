"""Explicit, model-free native evidence probes for multidomain cases.

Path visibility is a diagnostic, not an answer or authority score. The agent
oracles remain the independent task-outcome measure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmarks.config import BenchmarkConfig, BenchmarkConfigError
from benchmarks.adapters.enola import EnolaSubject
from benchmarks.adapters.hashmarks import HashmarksSubject
from benchmarks.harness.admission import harness_identity, runtime_environment_identity
from benchmarks.harness.model import TrialContext
from benchmarks.harness.runtime_authority import transport_runtime_authority
from benchmarks.harness.source import materialize_repository
from benchmarks.harness.workspace import WorkspaceError, isolated_environment
from benchmarks.hashmarks_task_evidence import is_task_evidence_packet

FAMILIES = ("logs", "splunk", "dependencies", "semantics", "identities", "code_owners")
SUBJECTS = ("hashmarks", "enola")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def review_digest(case: dict[str, Any]) -> str:
    semantic = {key: value for key, value in case.items() if key != "review"}
    return _digest(json.dumps(semantic, sort_keys=True).encode())


def load_cases(path: Path) -> list[dict[str, Any]]:
    root = path.parent.resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "agents-cookbook-multidomain-evidence.v1":
        raise ValueError("unknown evidence corpus schema")
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != 60:
        raise ValueError("evidence corpus requires exactly 60 cases")
    ids: set[str] = set()
    families: Counter[str] = Counter()
    agent_families: Counter[str] = Counter()
    agent_roles: Counter[tuple[str, str]] = Counter()
    repositories: Counter[str] = Counter()
    languages: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        family = case.get("family")
        if not isinstance(case_id, str) or case_id in ids or family not in FAMILIES:
            raise ValueError("duplicate/invalid evidence case or family")
        ids.add(case_id)
        families[family] += 1
        if case.get("agent_task") is True:
            agent_families[family] += 1
            agent_roles[(family, str(case.get("role")))] += 1
        repositories[str(case.get("repository_name"))] += 1
        languages.add(str(case.get("language")))
        repository = case.get("repository")
        if not isinstance(repository, dict) or not all(
            isinstance(repository.get(key), str) and repository[key]
            for key in ("url", "commit", "tree")
        ):
            raise ValueError(f"{case_id}: missing pinned repository")
        fixture = case.get("fixture")
        if not isinstance(fixture, dict):
            raise ValueError(f"{case_id}: missing fixture")
        artifact = (root / str(fixture.get("artifact", ""))).resolve()
        try:
            artifact.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{case_id}: fixture escapes corpus") from exc
        if not artifact.is_file() or _digest(artifact.read_bytes()) != fixture.get(
            "sha256"
        ):
            raise ValueError(f"{case_id}: fixture missing or changed")
        target = Path(str(fixture.get("target", "")))
        if target.is_absolute() or ".." in target.parts or not target.parts:
            raise ValueError(f"{case_id}: unsafe fixture target")
        review = case.get("review")
        if not isinstance(review, dict) or review.get("state") not in {
            "pending",
            "approved",
        }:
            raise ValueError(f"{case_id}: missing review state")
        approvals = review.get("approvals")
        if not isinstance(approvals, list):
            raise ValueError(f"{case_id}: review approvals must be a list")
        reviewers: set[str] = set()
        for approval in approvals:
            if (
                not isinstance(approval, dict)
                or not isinstance(approval.get("reviewer"), str)
                or not approval["reviewer"]
                or approval.get("case_sha256") != review_digest(case)
                or approval["reviewer"] in reviewers
            ):
                raise ValueError(f"{case_id}: invalid or stale case review approval")
            reviewers.add(approval["reviewer"])
        if review["state"] == "approved" and len(reviewers) < 2:
            raise ValueError(
                f"{case_id}: approved case needs two independent reviewers"
            )
        expected = case.get("expected")
        if not isinstance(expected, dict) or not expected:
            raise ValueError(f"{case_id}: missing independent answer")
        paths = case.get("expected_paths")
        if (
            not isinstance(paths, list)
            or not paths
            or any(
                not isinstance(path, str) or not path or path.startswith("/")
                for path in paths
            )
            or len(paths) != len(set(paths))
        ):
            raise ValueError(f"{case_id}: missing expected paths")
        if not isinstance(case.get("question"), str) or not case["question"]:
            raise ValueError(f"{case_id}: missing question")
        if not isinstance(case.get("probe_query"), str) or not case["probe_query"]:
            raise ValueError(f"{case_id}: missing native probe query")
        support = case.get("direct_support")
        if not isinstance(support, list) or not support or set(support) - set(SUBJECTS):
            raise ValueError(f"{case_id}: invalid direct subject support")
    if any(
        families[family] != 10 or agent_families[family] != 6 for family in FAMILIES
    ):
        raise ValueError(
            "evidence corpus requires ten cases and six agent tasks per family"
        )
    if any(
        agent_roles[(family, role)] != 2
        for family in FAMILIES
        for role in ("diagnosis", "decision", "verification")
    ):
        raise ValueError(
            "agent tasks require two diagnosis, decision, and verification cases per family"
        )
    if len(repositories) < 4 or repositories.get("hashmarks", 0) > 15:
        raise ValueError(
            "evidence corpus requires four sources and at most 15 Hashmarks cases"
        )
    if languages != {"python", "typescript"}:
        raise ValueError("evidence corpus requires Python and TypeScript")
    task_dir = root / "tasks"
    expected_tasks = {case["id"] for case in cases if case["agent_task"] is True}
    actual_tasks = {item.stem for item in task_dir.glob("*.json")}
    if expected_tasks != actual_tasks:
        raise ValueError("agent task files do not match evidence corpus membership")
    for case in cases:
        if case["id"] not in expected_tasks:
            continue
        task = json.loads((task_dir / f"{case['id']}.json").read_text(encoding="utf-8"))
        if (
            task.get("repository") != case["repository"]
            or task.get("fixtures") != [case["fixture"]]
            or task.get("oracle", {}).get("configuration", {}).get("expected")
            != case["expected"]
            or not str(task.get("prompt", "")).startswith(case["question"])
        ):
            raise ValueError(f"{case['id']}: agent task diverges from evidence case")
    return cases


def _receipt_dir(results: Path, case_id: str, subject_id: str) -> Path:
    return results / f"{case_id}--{subject_id}"


def grade_direct_claims(
    case: dict[str, Any], subject_id: str, raw: str
) -> dict[str, Any]:
    """Grade only claims with a case oracle and a documented native shape."""
    if subject_id != "hashmarks":
        return {
            "parse_valid": None,
            "answer_correctness": "NOT_ASSESSED",
            "authority_claims": "NOT_ASSESSED",
            "abstention": "NOT_ASSESSED",
            "provenance_structure": "NOT_ASSESSED",
        }
    try:
        packet = json.loads(raw)
    except json.JSONDecodeError:
        packet = None
    if not is_task_evidence_packet(packet):
        return {
            "parse_valid": False,
            "answer_correctness": "NOT_ASSESSED",
            "authority_claims": "NOT_ASSESSED",
            "abstention": "NOT_ASSESSED",
            "provenance_structure": "NOT_ASSESSED",
        }
    receipt = packet.get("evidence_receipt")
    provenance = (
        "PRESENT"
        if isinstance(receipt, dict)
        and all(
            isinstance(receipt.get(key), str) and receipt[key]
            for key in ("repository_identity", "evidence_identity")
        )
        and isinstance(packet.get("evidence_packet_identity"), str)
        else "MISSING"
    )
    if case["family"] != "code_owners":
        return {
            "parse_valid": True,
            "answer_correctness": "NOT_ASSESSED",
            "authority_claims": "NOT_ASSESSED",
            "abstention": "NOT_ASSESSED",
            "provenance_structure": provenance,
        }
    ownership = packet.get("ownership")
    if not isinstance(ownership, dict):
        return {
            "parse_valid": True,
            "answer_correctness": "UNRESOLVED",
            "authority_claims": "NOT_ASSESSED",
            "abstention": "NOT_ASSESSED",
            "provenance_structure": provenance,
        }
    owner = ownership.get("owner")
    owner_path = owner.get("path") if isinstance(owner, dict) else None
    ambiguity = ownership.get("ambiguity")
    ambiguous = ambiguity.get("ambiguous") if isinstance(ambiguity, dict) else None
    expected = case["expected"]
    if expected["owner_state"] == "ambiguous":
        correct = owner_path is None and ambiguous is True
        conflict = owner_path is not None
        abstention = "CORRECT" if correct else "INCORRECT" if conflict else "UNRESOLVED"
    else:
        correct = owner_path == expected["path"] and ambiguous is False
        conflict = owner_path is not None and owner_path != expected["path"]
        abstention = "NOT_APPLICABLE" if owner_path is not None else "UNRESOLVED"
    return {
        "parse_valid": True,
        "answer_correctness": "PASS"
        if correct
        else "FAIL"
        if conflict
        else "UNRESOLVED",
        "authority_claims": "CONFLICT" if conflict else "NO_CONFLICT_OBSERVED",
        "abstention": abstention,
        "provenance_structure": provenance,
    }


def _verify(directory: Path) -> dict[str, Any]:
    receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("schema") != "agents-cookbook-evidence-observation.v2":
        raise ValueError(f"unknown evidence receipt schema: {directory}")
    raw = (directory / "raw.txt").read_bytes()
    if receipt.get("raw_sha256") != _digest(raw):
        raise ValueError(f"invalid evidence raw digest: {directory}")
    sealed = (directory / "seal.json").read_text(encoding="utf-8")
    if json.loads(sealed).get("receipt_sha256") != _digest(
        (directory / "receipt.json").read_bytes()
    ):
        raise ValueError(f"invalid evidence receipt seal: {directory}")
    if directory.name != f"{receipt.get('case_id')}--{receipt.get('subject')}":
        raise ValueError(f"evidence receipt identity mismatch: {directory}")
    return receipt


def run_case(
    case: dict[str, Any],
    *,
    corpus_root: Path,
    subject_id: str,
    cache: Path,
    work: Path,
    results: Path,
    local_sources: dict[str, Path] | None = None,
    source: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source = os.environ if source is None else source
    destination = _receipt_dir(results, case["id"], subject_id)
    if destination.exists():
        _verify(destination)
        raise ValueError(
            f"evidence result already exists; choose a fresh results root: {destination}"
        )
    results.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    definition_sha = _digest(json.dumps(case, sort_keys=True).encode())
    harness_observed = harness_identity(Path(__file__).resolve().parents[1])
    raw = ""
    observed: dict[str, Any] | None = None
    environment_observed: dict[str, Any] | None = None
    metrics: dict[str, Any] = {}
    if subject_id not in case.get("direct_support", ["hashmarks"]):
        status = "NOT_COMPARABLE"
    else:
        with tempfile.TemporaryDirectory(prefix="evidence-", dir=work) as temporary:
            trial = Path(temporary)
            workspace = trial / "workspace"
            materialize_repository(
                repository=case["repository"],
                destination=workspace,
                cache_root=cache,
                local_source=(local_sources or {}).get(case["repository_name"]),
            )
            fixture = case["fixture"]
            target = (workspace / fixture["target"]).resolve()
            target.relative_to(workspace.resolve())
            if target.exists():
                raise ValueError(f"fixture target already exists: {fixture['target']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            artifact = (corpus_root / fixture["artifact"]).resolve()
            data = artifact.read_bytes()
            if _digest(data) != fixture["sha256"]:
                raise ValueError("fixture changed since corpus validation")
            target.write_bytes(data)
            control = trial / "control"
            environment = isolated_environment(control, source=source)
            transport_runtime_authority(source, environment)
            environment_observed = runtime_environment_identity(
                environment, control_root=control
            )
            context = TrialContext(workspace, control, environment)
            subject = (
                HashmarksSubject() if subject_id == "hashmarks" else EnolaSubject()
            )
            try:
                prepared = subject.prepare(context)
                observed = prepared.payload.get("observed_identity")
                if not prepared.payload.get("available"):
                    status = "INCOMPLETE"
                    raw = str(
                        prepared.payload.get("reason") or "subject preparation failed"
                    )
                else:
                    answer = (
                        subject.direct_task_evidence(context, str(case["probe_query"]))
                        if subject_id == "hashmarks"
                        else subject.query(context, str(case["probe_query"]))
                    )
                    raw = answer.raw
                    metrics = answer.measurements
                    process = answer.payload.get("process")
                    if isinstance(process, dict) and (
                        process.get("timed_out")
                        or process.get("stdout_truncated")
                        or process.get("stderr_truncated")
                        or process.get("return_code") not in (None, 0)
                    ):
                        status = "INCOMPLETE"
                    else:
                        status = "OBSERVED"
            finally:
                subject.cleanup(context)
    paths = case["expected_paths"]
    path_visible = (
        all(isinstance(path, str) and path in raw for path in paths)
        if status == "OBSERVED"
        else None
    )
    claims = (
        grade_direct_claims(case, subject_id, raw)
        if status == "OBSERVED"
        else {
            "parse_valid": None,
            "answer_correctness": "NOT_ASSESSED",
            "authority_claims": "NOT_ASSESSED",
            "abstention": "NOT_ASSESSED",
            "provenance_structure": "NOT_ASSESSED",
        }
    )
    if status == "OBSERVED" and claims["parse_valid"] is False:
        status = "INVALID_RESPONSE"
        path_visible = None
    receipt = {
        "schema": "agents-cookbook-evidence-observation.v2",
        "case_id": case["id"],
        "family": case["family"],
        "subject": subject_id,
        "definition_sha256": definition_sha,
        "status": status,
        "path_visible": path_visible,
        **claims,
        "probe_semantics": None
        if status == "NOT_COMPARABLE"
        else "task-evidence"
        if subject_id == "hashmarks"
        else "architecture-explain",
        "support_basis": "declared-native-probe-protocol",
        "reason": "no comparable native probe in this protocol"
        if status == "NOT_COMPARABLE"
        else None,
        "review_state": case["review"]["state"],
        "subject_observed": observed,
        "harness_observed": harness_observed,
        "environment_observed": environment_observed,
        "metrics": metrics,
        "raw_sha256": _digest(raw.encode()),
    }
    with tempfile.TemporaryDirectory(
        prefix=".evidence-bundle-", dir=results
    ) as temporary:
        staging = Path(temporary)
        (staging / "raw.txt").write_text(raw, encoding="utf-8")
        receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
        (staging / "receipt.json").write_bytes(receipt_bytes)
        (staging / "seal.json").write_text(
            json.dumps({"receipt_sha256": _digest(receipt_bytes)}) + "\n",
            encoding="utf-8",
        )
        os.rename(staging, destination)
    return receipt


def report(
    cases: list[dict[str, Any]], results: Path, subjects: tuple[str, ...]
) -> dict[str, Any]:
    rows = []
    missing = []
    for case in cases:
        for subject in subjects:
            directory = _receipt_dir(results, case["id"], subject)
            if directory.exists():
                row = _verify(directory)
                if row["definition_sha256"] != _digest(
                    json.dumps(case, sort_keys=True).encode()
                ):
                    raise ValueError(f"stale evidence definition: {directory}")
                rows.append(row)
            else:
                missing.append(f"{case['id']}--{subject}")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    observed_identity: dict[str, Any] = {}
    observed_environment: dict[str, Any] = {}
    harness_authority = rows[0]["harness_observed"] if rows else None
    for row in rows:
        if row["harness_observed"] != harness_authority:
            raise ValueError("mixed direct evidence harness authority")
        grouped[(row["subject"], row["family"])].append(row)
        if row["status"] == "OBSERVED":
            subject = row["subject"]
            identity = row["subject_observed"]
            if subject in observed_identity and observed_identity[subject] != identity:
                raise ValueError(f"mixed direct evidence subject authority: {subject}")
            observed_identity[subject] = identity
            environment = row["environment_observed"]
            if (
                subject in observed_environment
                and observed_environment[subject] != environment
            ):
                raise ValueError(
                    f"mixed direct evidence environment authority: {subject}"
                )
            observed_environment[subject] = environment
    return {
        "schema": "agents-cookbook-evidence-report.v2",
        "expected": len(cases) * len(subjects),
        "observed": len(rows),
        "missing": missing,
        "complete": not missing,
        "reviewed_cases": sum(case["review"]["state"] == "approved" for case in cases),
        "subjects": {
            subject: {
                family: {
                    "observed": len(grouped[(subject, family)]),
                    "statuses": dict(
                        Counter(row["status"] for row in grouped[(subject, family)])
                    ),
                    "visible_paths": sum(
                        row["path_visible"] is True
                        for row in grouped[(subject, family)]
                    ),
                    "path_visibility_denominator": sum(
                        row["path_visible"] is not None
                        for row in grouped[(subject, family)]
                    ),
                    "owner_correct": sum(
                        row.get("answer_correctness") == "PASS"
                        for row in grouped[(subject, family)]
                    ),
                    "owner_claim_conflicts": sum(
                        row.get("authority_claims") == "CONFLICT"
                        for row in grouped[(subject, family)]
                    ),
                }
                for family in FAMILIES
            }
            for subject in subjects
        },
        "authority": {
            "path_visibility_is_diagnostic": True,
            "native_answer_grading": {
                subject: ["code_owners"] if subject == "hashmarks" else []
                for subject in subjects
            },
            "corpus_review_state": "approved"
            if all(case["review"]["state"] == "approved" for case in cases)
            else "draft",
            "overall_winner": None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("validate", "review-digests", "run", "report")
    )
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--subject", action="append", choices=SUBJECTS)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--case", action="append")
    parser.add_argument(
        "--local-source", action="append", default=[], metavar="REPO=PATH"
    )
    args = parser.parse_args(argv)
    cases = load_cases(args.corpus)
    if args.command == "review-digests":
        selected = [case for case in cases if not args.case or case["id"] in args.case]
        if args.case and set(args.case) != {case["id"] for case in selected}:
            parser.error("unknown evidence case ID")
        print(
            json.dumps(
                {case["id"]: review_digest(case) for case in selected},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "validate":
        reviewed = sum(case["review"]["state"] == "approved" for case in cases)
        print(
            json.dumps(
                {
                    "cases": len(cases),
                    "agent_tasks": sum(case["agent_task"] for case in cases),
                    "reviewed": reviewed,
                    "corpus_state": "approved" if reviewed == 60 else "draft",
                },
                indent=2,
            )
        )
        return 0
    if not args.subject or args.results is None:
        parser.error("run/report require --subject and --results")
    if len(args.subject) != len(set(args.subject)):
        parser.error("duplicate direct evidence subject")
    subjects = tuple(dict.fromkeys(args.subject))
    if args.command == "report":
        print(
            json.dumps(report(cases, args.results, subjects), indent=2, sort_keys=True)
        )
        return 0
    if args.env_file is None or args.cache is None or args.work is None:
        parser.error("run requires --env-file, --cache, and --work")
    try:
        config = BenchmarkConfig.load(args.env_file)
        if "hashmarks" in subjects:
            config.require("HASHMARKS_BENCH_SOURCE")
    except BenchmarkConfigError as exc:
        parser.error(str(exc))
    selected = [case for case in cases if not args.case or case["id"] in args.case]
    if args.case and set(args.case) != {case["id"] for case in selected}:
        parser.error("unknown evidence case ID")
    local_sources: dict[str, Path] = {}
    known_repositories = {case["repository_name"] for case in cases}
    for entry in args.local_source:
        if "=" not in entry:
            parser.error("--local-source requires REPO=PATH")
        name, source = entry.split("=", 1)
        if (
            name not in known_repositories
            or name in local_sources
            or not Path(source).is_dir()
        ):
            parser.error("--local-source has unknown, duplicate, or missing repository")
        local_sources[name] = Path(source).resolve()
    for case in selected:
        for subject in subjects:
            print(
                json.dumps(
                    run_case(
                        case,
                        corpus_root=args.corpus.parent,
                        subject_id=subject,
                        cache=args.cache,
                        work=args.work,
                        results=args.results,
                        local_sources=local_sources,
                        source=config.runtime_environment(),
                    ),
                    sort_keys=True,
                )
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, WorkspaceError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
