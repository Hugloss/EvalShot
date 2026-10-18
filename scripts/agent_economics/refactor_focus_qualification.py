from __future__ import annotations

import argparse
import ast
import json
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from .refactor_focus_models import MATCH_AUTHORITY
from .refactor_focus_workflow import refactor_focus_audit

Expectation = Literal[
    "relevant_confirmed",
    "relationship_supporting",
    "heuristic_candidate",
    "irrelevant",
]


@dataclass(frozen=True)
class QualificationCase:
    name: str
    source_path: str
    expected_test_path: str
    expectation: Expectation
    expected_match_type: str | None = None


CASES = (
    QualificationCase(
        "exact_import",
        "src/samplepkg/exact_case.py",
        "tests/test_exact_relation.py",
        "relevant_confirmed",
        "import_exact",
    ),
    QualificationCase(
        "helper_loader",
        "src/samplepkg/helper_case.py",
        "tests/test_helper_behavior.py",
        "relevant_confirmed",
        "support_loader",
    ),
    QualificationCase(
        "package_init_import",
        "src/samplepkg/package/__init__.py",
        "tests/test_package_import_behavior.py",
        "relevant_confirmed",
        "import_exact",
    ),
    QualificationCase(
        "exact_overrides_same_name",
        "src/samplepkg/exact_named.py",
        "tests/test_exact_named.py",
        "relevant_confirmed",
        "import_exact",
    ),
    QualificationCase(
        "mirrored_path_only",
        "src/samplepkg/mirrored_only.py",
        "tests/test_mirrored_only.py",
        "relationship_supporting",
        "mirrored_path",
    ),
    QualificationCase(
        "direct_name_only",
        "src/samplepkg/name_only_case.py",
        "tests/nested/test_name_only_case.py",
        "relationship_supporting",
        "direct_name",
    ),
    QualificationCase(
        "transitive_owner",
        "src/samplepkg/transitive_target.py",
        "tests/test_transitive_bridge_target.py",
        "relationship_supporting",
        "transitive_owner",
    ),
    QualificationCase(
        "feature_candidate",
        "src/samplepkg/feature_alpha_beta.py",
        "tests/test_feature_alpha_beta_behavior.py",
        "heuristic_candidate",
        "feature_fallback",
    ),
    QualificationCase(
        "same_name_without_import",
        "src/samplepkg/shadow_name.py",
        "tests/test_shadow_name.py",
        "irrelevant",
        "mirrored_path",
    ),
    QualificationCase(
        "unrelated_negative",
        "src/samplepkg/unrelated.py",
        "tests/test_other_behavior.py",
        "irrelevant",
        None,
    ),
    QualificationCase(
        "literal_importlib",
        "src/samplepkg/unsupported_dynamic.py",
        "tests/test_runtime_dynamic_check.py",
        "relevant_confirmed",
        "dynamic_import_literal",
    ),
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _oversized_source(*lines: str, minimum_lines: int = 25) -> str:
    body = list(lines)
    while len(body) < minimum_lines:
        body.append(f"# qualification padding {len(body) + 1}")
    return "\n".join(body) + "\n"


def materialize_corpus(root: Path) -> None:
    _write(root / "src/samplepkg/__init__.py", "# qualification package\n")

    _write(root / "src/samplepkg/exact_case.py", _oversized_source("VALUE = 1"))
    _write(root / "tests/test_exact_relation.py", "import samplepkg.exact_case\n")

    _write(root / "src/samplepkg/helper_case.py", _oversized_source("VALUE = 2"))
    _write(root / "tests/support/helper_loader.py", "import samplepkg.helper_case\n")
    _write(
        root / "tests/test_helper_behavior.py", "import checks.support.helper_loader\n"
    )

    _write(
        root / "src/samplepkg/package/__init__.py",
        _oversized_source("PACKAGE_VALUE = 3"),
    )
    _write(root / "tests/test_package_import_behavior.py", "import samplepkg.package\n")

    _write(root / "src/samplepkg/exact_named.py", _oversized_source("VALUE = 4"))
    _write(root / "tests/test_exact_named.py", "import samplepkg.exact_named\n")

    _write(root / "src/samplepkg/mirrored_only.py", _oversized_source("VALUE = 5"))
    _write(
        root / "tests/test_mirrored_only.py",
        "def test_placeholder():\n    assert True\n",
    )

    _write(root / "src/samplepkg/name_only_case.py", _oversized_source("VALUE = 6"))
    _write(
        root / "tests/nested/test_name_only_case.py",
        "def test_placeholder():\n    assert True\n",
    )

    _write(root / "src/samplepkg/transitive_target.py", _oversized_source("VALUE = 7"))
    _write(
        root / "src/samplepkg/transitive_bridge.py",
        "import samplepkg.transitive_target\n",
    )
    _write(
        root / "tests/test_transitive_bridge_target.py",
        "import samplepkg.transitive_bridge\n",
    )

    _write(root / "src/samplepkg/feature_alpha_beta.py", _oversized_source("VALUE = 8"))
    _write(
        root / "tests/test_feature_alpha_beta_behavior.py",
        "def test_placeholder():\n    assert True\n",
    )

    _write(root / "src/samplepkg/shadow_name.py", _oversized_source("VALUE = 9"))
    _write(
        root / "tests/test_shadow_name.py", "def test_unrelated():\n    assert True\n"
    )

    _write(root / "src/samplepkg/unrelated.py", _oversized_source("VALUE = 10"))
    _write(
        root / "tests/test_other_behavior.py", "def test_other():\n    assert True\n"
    )

    _write(
        root / "src/samplepkg/unsupported_dynamic.py",
        _oversized_source("VALUE = 11"),
    )
    _write(
        root / "tests/test_runtime_dynamic_check.py",
        'import importlib\n\ndef test_dynamic():\n    assert importlib.import_module("samplepkg.unsupported_dynamic")\n',
    )


def _run_probe(
    root: Path,
    *,
    top_n: int,
    artifact_name: str,
) -> tuple[dict[str, object], float, dict[str, object]]:
    artifact_path = root / artifact_name
    exit_codes: list[int] = []
    read_calls: Counter[str] = Counter()
    parse_calls = 0
    original_read_bytes = Path.read_bytes
    original_ast_parse = ast.parse

    def emit(_level: str, _event: str, **_payload: object) -> None:
        return None

    def exit_code(code: int) -> None:
        exit_codes.append(code)

    def counted_read_bytes(path: Path) -> bytes:
        read_calls[path.resolve().as_posix()] += 1
        return original_read_bytes(path)

    def counted_ast_parse(*args: object, **kwargs: object) -> ast.AST:
        nonlocal parse_calls
        parse_calls += 1
        return original_ast_parse(*args, **kwargs)

    started = time.perf_counter()
    with (
        patch.object(Path, "read_bytes", counted_read_bytes),
        patch(
            "ast.parse",
            counted_ast_parse,
        ),
    ):
        refactor_focus_audit(
            emit=emit,
            exit_code=exit_code,
            source_root=root / "src/samplepkg",
            tests_root=root / "tests",
            repository_root=root,
            artifact_path=artifact_path,
            package_name="samplepkg",
            tests_package_name="checks",
            file_line_threshold=20,
            function_line_threshold=10,
            top_n=top_n,
            transitive_max_depth=2,
        )
    elapsed = time.perf_counter() - started
    if exit_codes != [0]:
        raise RuntimeError(f"probe exit codes were {exit_codes!r}")
    observer = {
        "read_calls_total": sum(read_calls.values()),
        "read_calls_by_path": dict(sorted(read_calls.items())),
        "max_reads_per_path": max(read_calls.values(), default=0),
        "ast_parse_calls": parse_calls,
    }
    return (
        json.loads(artifact_path.read_text(encoding="utf-8")),
        elapsed,
        observer,
    )


def _classification(
    case: QualificationCase,
    row: dict[str, object],
) -> tuple[str, dict[str, object] | None, list[str]]:
    matches = row.get("matches", [])
    assert isinstance(matches, list)
    expected_match = next(
        (
            match
            for match in matches
            if isinstance(match, dict)
            and match.get("test_path") == case.expected_test_path
        ),
        None,
    )
    failures: list[str] = []

    if expected_match is not None:
        match_type = str(expected_match.get("match_type"))
        expected_authority = MATCH_AUTHORITY.get(match_type, "candidate")
        if expected_match.get("evidence_authority") != expected_authority:
            failures.append(
                f"authority mismatch: {match_type} expected {expected_authority} "
                f"got {expected_match.get('evidence_authority')}"
            )
        if case.expected_match_type and match_type != case.expected_match_type:
            failures.append(
                f"match type expected {case.expected_match_type} got {match_type}"
            )

    authority = expected_match.get("evidence_authority") if expected_match else None
    if case.expectation == "relevant_confirmed":
        classification = (
            "TRUE_RELEVANT" if authority == "confirmed" else "MISSED_RELEVANT"
        )
    elif authority == "confirmed":
        classification = "FALSE_RELEVANT"
    elif case.expectation == "irrelevant" and expected_match is None:
        classification = "TRUE_NEGATIVE"
    else:
        classification = "UNKNOWN"

    if authority != "confirmed":
        forbidden_actions = {
            "split_existing_test_file",
            "update_existing_test_file",
            "keep_existing_test_file",
        }
        if row.get("has_corresponding_tests"):
            failures.append("non-confirmed evidence set has_corresponding_tests=true")
        if row.get("test_sync_required_if_split"):
            failures.append(
                "non-confirmed evidence set test_sync_required_if_split=true"
            )
        if row.get("max_test_lines") != 0:
            failures.append("non-confirmed evidence influenced max_test_lines")
        if row.get("recommended_test_action") in forbidden_actions:
            failures.append("non-confirmed evidence prescribed a confirmed-test action")

    return classification, expected_match, failures


def contract_candidate_as_legacy_row(candidate: dict[str, object]) -> dict[str, object]:
    evidence = candidate.get("evidence", {})
    facts = candidate.get("facts", {})
    derived = candidate.get("derived", {})
    interpretation = candidate.get("interpretation", {})
    recommendations = candidate.get("recommendations", {})
    assert isinstance(evidence, dict)
    assert isinstance(facts, dict)
    assert isinstance(derived, dict)
    assert isinstance(interpretation, dict)
    assert isinstance(recommendations, dict)
    confirmed = list(evidence.get("confirmed", []))
    supporting = list(evidence.get("supporting", []))
    heuristic = list(evidence.get("candidate", []))
    matches = [*confirmed, *supporting, *heuristic]
    return {
        "source_path": candidate.get("target"),
        "matches": matches,
        "confirmed_matches": confirmed,
        "supporting_matches": supporting,
        "candidate_matches": heuristic,
        "corresponding_test_count": facts.get("corresponding_test_count"),
        "supporting_test_count": facts.get("supporting_test_count"),
        "candidate_test_count": facts.get("candidate_test_count"),
        "has_corresponding_tests": derived.get("has_corresponding_tests"),
        "correspondence_status": derived.get("correspondence_status"),
        "test_sync_required_if_split": derived.get("test_sync_required_if_split"),
        "max_test_lines": facts.get("max_confirmed_test_lines"),
        "recommended_test_action": recommendations.get("test_action"),
        "recommended_strategy": recommendations.get("strategy"),
    }


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="agent-economics-refactor-focus-") as tmp:
        root = Path(tmp)
        materialize_corpus(root)

        full, elapsed, full_observer = _run_probe(
            root, top_n=100, artifact_name="full.json"
        )
        bounded, bounded_elapsed, bounded_observer = _run_probe(
            root, top_n=3, artifact_name="bounded.json"
        )

        candidates = full["candidates"]
        assert isinstance(candidates, list)
        rows = [
            contract_candidate_as_legacy_row(item)
            for item in candidates
            if isinstance(item, dict)
        ]
        rows_by_source = {
            row["source_path"]: row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("source_path"), str)
        }

        counts = {
            "TRUE_RELEVANT": 0,
            "FALSE_RELEVANT": 0,
            "MISSED_RELEVANT": 0,
            "UNKNOWN": 0,
            "TRUE_NEGATIVE": 0,
        }
        case_results: list[dict[str, object]] = []
        failures: list[str] = []

        for case in CASES:
            row = rows_by_source.get(case.source_path)
            if row is None:
                counts["MISSED_RELEVANT"] += int(
                    case.expectation == "relevant_confirmed"
                )
                failures.append(f"{case.name}: source row missing: {case.source_path}")
                case_results.append(
                    {
                        "name": case.name,
                        "classification": "ROW_MISSING",
                        "expectation": case.expectation,
                    }
                )
                continue

            classification, match, case_failures = _classification(case, row)
            counts[classification] += 1
            failures.extend(f"{case.name}: {message}" for message in case_failures)
            case_results.append(
                {
                    "name": case.name,
                    "expectation": case.expectation,
                    "classification": classification,
                    "source_path": case.source_path,
                    "expected_test_path": case.expected_test_path,
                    "observed_match": match,
                    "correspondence_status": row.get("correspondence_status"),
                    "recommended_test_action": row.get("recommended_test_action"),
                    "recommended_strategy": row.get("recommended_strategy"),
                }
            )

        if counts["FALSE_RELEVANT"]:
            failures.append("qualification introduced false authority")
        if counts["MISSED_RELEVANT"]:
            failures.append(
                "qualification missed a relationship that must be confirmed"
            )

        tp = counts["TRUE_RELEVANT"]
        fp = counts["FALSE_RELEVANT"]
        fn = counts["MISSED_RELEVANT"]
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0

        full_derived = full["derived"]
        bounded_derived = bounded["derived"]
        assert isinstance(full_derived, dict) and isinstance(bounded_derived, dict)
        oversized = int(full_derived["oversized_source_count"])
        selected = int(bounded_derived["selected_count"])
        candidate_reduction = 1.0 - (selected / oversized) if oversized else 0.0

        python_files = sorted(root.rglob("*.py"))
        python_bytes = sum(path.stat().st_size for path in python_files)
        expected_python_files = len(python_files)

        full_economics = full.get("economics")
        bounded_economics = bounded.get("economics")
        if not isinstance(full_economics, dict) or not isinstance(
            bounded_economics, dict
        ):
            failures.append("P2 economics block missing from probe artifact")
            full_economics = {}
            bounded_economics = {}

        for label, economics, observer in (
            ("full", full_economics, full_observer),
            ("bounded", bounded_economics, bounded_observer),
        ):
            if economics.get("files_read") != expected_python_files:
                failures.append(
                    f"{label}: files_read expected {expected_python_files} "
                    f"got {economics.get('files_read')}"
                )
            if economics.get("bytes_read") != python_bytes:
                failures.append(
                    f"{label}: bytes_read expected {python_bytes} "
                    f"got {economics.get('bytes_read')}"
                )
            if economics.get("ast_parses") != expected_python_files:
                failures.append(
                    f"{label}: ast_parses expected {expected_python_files} "
                    f"got {economics.get('ast_parses')}"
                )
            if economics.get("unique_files_cached") != expected_python_files:
                failures.append(
                    f"{label}: unique_files_cached expected {expected_python_files} "
                    f"got {economics.get('unique_files_cached')}"
                )
            if economics.get("read_failures") != 0:
                failures.append(f"{label}: unexpected read failures")
            if economics.get("parse_failures") != 0:
                failures.append(f"{label}: unexpected parse failures")
            if observer["read_calls_total"] != expected_python_files:
                failures.append(
                    f"{label}: independent read observer saw "
                    f"{observer['read_calls_total']} calls, expected {expected_python_files}"
                )
            if observer["max_reads_per_path"] > 1:
                failures.append(f"{label}: a Python file was read more than once")
            if observer["ast_parse_calls"] != expected_python_files:
                failures.append(
                    f"{label}: independent AST observer saw "
                    f"{observer['ast_parse_calls']} parses, expected {expected_python_files}"
                )
            if int(economics.get("cache_hits", 0)) <= 0:
                failures.append(f"{label}: cache was not reused after prewarm")
            if economics.get("transitive_max_depth") != 2:
                failures.append(f"{label}: transitive bound missing or changed")

        bounded_candidates = bounded["candidates"]
        assert isinstance(bounded_candidates, list)
        bounded_rows = [
            contract_candidate_as_legacy_row(item)
            for item in bounded_candidates
            if isinstance(item, dict)
        ]
        evidence_paths = {
            str(row["source_path"]) for row in bounded_rows if isinstance(row, dict)
        }
        for row in bounded_rows:
            if not isinstance(row, dict):
                continue
            for match in row.get("matches", []):
                if isinstance(match, dict) and isinstance(match.get("test_path"), str):
                    evidence_paths.add(match["test_path"])
        evidence_reduction = (
            1.0 - (len(evidence_paths) / len(python_files)) if python_files else 0.0
        )
        expected_evidence_lines = 0
        for relative_path in evidence_paths:
            expected_evidence_lines += len(
                (root / relative_path)
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()
            )
        if bounded_economics.get("evidence_files_selected") != len(evidence_paths):
            failures.append(
                "bounded: evidence_files_selected does not match selected evidence set"
            )
        if bounded_economics.get("evidence_lines_selected") != expected_evidence_lines:
            failures.append(
                "bounded: evidence_lines_selected does not match selected evidence lines"
            )
        if bounded_economics.get("candidate_reduction") != candidate_reduction:
            failures.append("bounded: candidate_reduction accounting mismatch")

        result: dict[str, object] = {
            "probe": "refactor-focus",
            "qualification_phase": 2,
            "passed": not failures,
            "authority_counts": counts,
            "precision": precision,
            "recall": recall,
            "economics": {
                "oversized_source_candidates": oversized,
                "bounded_selected": selected,
                "candidate_reduction": candidate_reduction,
                "bounded_evidence_files": len(evidence_paths),
                "corpus_python_files": len(python_files),
                "evidence_reduction": evidence_reduction,
                "full_probe_runtime_ms": round(elapsed * 1000, 3),
                "bounded_probe_runtime_ms": round(bounded_elapsed * 1000, 3),
                "probe_reported_full": full_economics,
                "probe_reported_bounded": bounded_economics,
                "independent_full_observer": full_observer,
                "independent_bounded_observer": bounded_observer,
                "expected_python_files": expected_python_files,
                "expected_python_bytes": python_bytes,
                "expected_bounded_evidence_lines": expected_evidence_lines,
                "accounting_note": (
                    "P2 independently observes file reads and ast.parse calls and "
                    "fails if any Python file is reread/reparsed during one probe run."
                ),
            },
            "cases": case_results,
            "failures": failures,
        }

        if artifact_path is not None:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the stdlib-only P2 qualification corpus for refactor-focus.",
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/refactor-focus-qualification.json"),
        help="Qualification JSON destination.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = qualify(args.artifact_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
