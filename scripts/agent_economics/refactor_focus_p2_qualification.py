from __future__ import annotations

import argparse
import ast
import json
import tempfile
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from .refactor_focus_qualification import (
    contract_candidate_as_legacy_row,
    materialize_corpus,
    qualify as qualify_p1,
)
from .refactor_focus_workflow import refactor_focus_audit


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

    return (
        json.loads(artifact_path.read_text(encoding="utf-8")),
        elapsed,
        {
            "read_calls_total": sum(read_calls.values()),
            "read_calls_by_path": dict(sorted(read_calls.items())),
            "max_reads_per_path": max(read_calls.values(), default=0),
            "ast_parse_calls": parse_calls,
        },
    )


def _validate_run(
    *,
    label: str,
    payload: dict[str, object],
    observer: dict[str, object],
    expected_files: int,
    expected_bytes: int,
    failures: list[str],
) -> dict[str, object]:
    economics = payload.get("economics")
    if not isinstance(economics, dict):
        failures.append(f"{label}: economics block missing")
        return {}

    expected = {
        "files_read": expected_files,
        "bytes_read": expected_bytes,
        "ast_parses": expected_files,
        "read_failures": 0,
        "parse_failures": 0,
        "unique_files_cached": expected_files,
        "transitive_max_depth": 2,
    }
    for key, value in expected.items():
        if economics.get(key) != value:
            failures.append(
                f"{label}: {key} expected {value!r} got {economics.get(key)!r}"
            )

    if int(economics.get("cache_hits", 0)) <= 0:
        failures.append(f"{label}: cache was not reused")
    if observer.get("read_calls_total") != expected_files:
        failures.append(
            f"{label}: independent read observer expected {expected_files} "
            f"got {observer.get('read_calls_total')}"
        )
    if int(observer.get("max_reads_per_path", 0)) > 1:
        failures.append(f"{label}: a Python file was read more than once")
    if observer.get("ast_parse_calls") != expected_files:
        failures.append(
            f"{label}: independent AST observer expected {expected_files} "
            f"got {observer.get('ast_parse_calls')}"
        )
    return economics


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []
    p1 = qualify_p1(None)
    if not p1.get("passed"):
        failures.append("P1 evidence-authority qualification regressed")

    with tempfile.TemporaryDirectory(
        prefix="agent-economics-refactor-focus-p2-"
    ) as tmp:
        root = Path(tmp)
        materialize_corpus(root)

        full, full_elapsed, full_observer = _run_probe(
            root,
            top_n=100,
            artifact_name="full.json",
        )
        bounded, bounded_elapsed, bounded_observer = _run_probe(
            root,
            top_n=3,
            artifact_name="bounded.json",
        )

        python_files = sorted(root.rglob("*.py"))
        expected_files = len(python_files)
        expected_bytes = sum(path.stat().st_size for path in python_files)

        full_economics = _validate_run(
            label="full",
            payload=full,
            observer=full_observer,
            expected_files=expected_files,
            expected_bytes=expected_bytes,
            failures=failures,
        )
        bounded_economics = _validate_run(
            label="bounded",
            payload=bounded,
            observer=bounded_observer,
            expected_files=expected_files,
            expected_bytes=expected_bytes,
            failures=failures,
        )

        bounded_derived = bounded.get("derived", {})
        assert isinstance(bounded_derived, dict)
        oversized = int(bounded_derived["oversized_source_count"])
        selected = int(bounded_derived["selected_count"])
        expected_candidate_reduction = (
            1.0 - (selected / oversized) if oversized else 0.0
        )
        if bounded_economics.get("candidate_reduction") != expected_candidate_reduction:
            failures.append("bounded: candidate_reduction accounting mismatch")

        candidates = bounded.get("candidates", [])
        assert isinstance(candidates, list)
        rows = [
            contract_candidate_as_legacy_row(item)
            for item in candidates
            if isinstance(item, dict)
        ]
        evidence_paths: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_path = row.get("source_path")
            if isinstance(source_path, str):
                evidence_paths.add(source_path)
            matches = row.get("matches", [])
            if not isinstance(matches, list):
                continue
            for match in matches:
                if isinstance(match, dict) and isinstance(match.get("test_path"), str):
                    evidence_paths.add(match["test_path"])

        expected_evidence_lines = sum(
            len(
                (root / relative_path)
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()
            )
            for relative_path in evidence_paths
        )
        if bounded_economics.get("evidence_files_selected") != len(evidence_paths):
            failures.append("bounded: evidence_files_selected accounting mismatch")
        if bounded_economics.get("evidence_lines_selected") != expected_evidence_lines:
            failures.append("bounded: evidence_lines_selected accounting mismatch")

        p1_economics = p1.get("economics", {})
        result: dict[str, object] = {
            "probe": "refactor-focus",
            "qualification_phase": 2,
            "passed": not failures,
            "p1_authority_counts": p1.get("authority_counts"),
            "p1_precision": p1.get("precision"),
            "p1_recall": p1.get("recall"),
            "economics": {
                "oversized_source_candidates": oversized,
                "bounded_selected": selected,
                "candidate_reduction": expected_candidate_reduction,
                "bounded_evidence_files": len(evidence_paths),
                "bounded_evidence_lines": expected_evidence_lines,
                "corpus_python_files": expected_files,
                "corpus_python_bytes": expected_bytes,
                "full_probe_runtime_ms": round(full_elapsed * 1000, 3),
                "bounded_probe_runtime_ms": round(bounded_elapsed * 1000, 3),
                "probe_reported_full": full_economics,
                "probe_reported_bounded": bounded_economics,
                "independent_full_observer": full_observer,
                "independent_bounded_observer": bounded_observer,
                "p1_candidate_reduction": (
                    p1_economics.get("candidate_reduction")
                    if isinstance(p1_economics, dict)
                    else None
                ),
            },
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
        description="Run P2 parse-once qualification for refactor-focus.",
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/refactor-focus-p2-qualification.json"),
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
