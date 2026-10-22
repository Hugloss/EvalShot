"""Source-bound syntactic call evidence for independent dogfood review.

This is not a call graph: an AST Call with matching spelling does not prove
runtime target, import identity, delegation, reachability, or owner semantics.
It complements (does not replace) the pinned-tree declaration collision scan.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
from typing import Any, Mapping

from benchmarks.dogfood.hashmarks_collisions import (
    _MAX_TOTAL_BYTES,
    _MAX_TRACKED_PYTHON,
    _commit_tree,
    _python_paths,
)
from benchmarks.dogfood.hashmarks_coverage import build_coverage
from benchmarks.dogfood.hashmarks_declarations import _git_bytes, _source_bytes
from benchmarks.dogfood.hashmarks_review_handoff import _read_input
from benchmarks.dogfood.hashmarks_corpus import _safe_file
from benchmarks.harness.identity import digest

SCHEMA = "evalshot.hashmarks-dogfood-callsites.v1"
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MAX_CALL_SITES = 1500


class _CallsiteVisitor(ast.NodeVisitor):
    def __init__(self, symbols: set[str]) -> None:
        self.symbols = symbols
        self.lexical_scope: list[str] = []
        self.hits: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}

    def _nested(self, node: ast.AST) -> None:
        self.lexical_scope.append(node.name)
        self.generic_visit(node)
        self.lexical_scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._nested(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._nested(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._nested(node)

    def visit_Call(self, node: ast.Call) -> None:
        expression = node.func
        symbol: str | None = None
        form: str | None = None
        receiver: str | None = None
        if isinstance(expression, ast.Name):
            symbol, form = expression.id, "NAME_CALL"
        elif isinstance(expression, ast.Attribute):
            symbol, form = expression.attr, "ATTRIBUTE_CALL"
            if isinstance(expression.value, ast.Name):
                receiver = (
                    expression.value.id
                    if expression.value.id in {"self", "cls"}
                    else "OTHER_NAMED_RECEIVER"
                )
            else:
                receiver = "COMPLEX_RECEIVER"
        if symbol in self.symbols:
            self.hits[symbol].append({
                "line": node.lineno,
                "column": node.col_offset,
                "lexical_scope": ".".join(self.lexical_scope) or "<module>",
                "form": form,
                "receiver_syntax": receiver,
                "target_resolved": False,
            })
        self.generic_visit(node)


def _scan_calls(checkout: Path, commit: str, symbols: set[str]) -> dict[str, Any]:
    paths = _python_paths(checkout, commit)
    blank: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    if paths is None:
        return {"status": "TREE_LIST_UNAVAILABLE", "calls": blank,
                "tracked_python": 0, "parsed_python": 0, "skipped": []}
    if len(paths) > _MAX_TRACKED_PYTHON:
        return {"status": "FILE_COUNT_BOUND_EXCEEDED", "calls": blank,
                "tracked_python": len(paths), "parsed_python": 0, "skipped": []}

    calls = blank
    skipped: list[dict[str, str]] = []
    bytes_seen = 0
    parsed_count = 0
    for filename, filekind in paths:
        if filekind != "REGULAR":
            skipped.append({"path": filename, "reason": "NON_REGULAR_OR_UNSAFE"})
            continue
        content = _source_bytes(checkout, commit, filename)
        if content is None:
            skipped.append({"path": filename, "reason": "SOURCE_UNAVAILABLE_OR_TOO_LARGE"})
            continue
        bytes_seen += len(content)
        if bytes_seen > _MAX_TOTAL_BYTES:
            skipped.append({"path": filename, "reason": "TOTAL_SOURCE_BYTE_LIMIT"})
            break
        try:
            parsed = ast.parse(content.decode("utf-8"), filename=filename)
        except (UnicodeDecodeError, SyntaxError, ValueError, RecursionError):
            skipped.append({"path": filename, "reason": "UNPARSEABLE_PYTHON"})
            continue
        parsed_count += 1
        visitor = _CallsiteVisitor(symbols)
        try:
            visitor.visit(parsed)
        except RecursionError:
            skipped.append({"path": filename, "reason": "AST_WALK_LIMIT"})
            continue
        for symbol in sorted(symbols):
            for hit in visitor.hits[symbol]:
                calls[symbol].append({"path": filename, **hit})
            if len(calls[symbol]) > _MAX_CALL_SITES:
                return {"status": "CALLSITE_COUNT_BOUND_EXCEEDED", "calls": calls,
                        "tracked_python": len(paths), "parsed_python": parsed_count,
                        "skipped": skipped}
    return {
        "status": "COMPLETE" if not skipped else "INCOMPLETE",
        "calls": calls,
        "tracked_python": len(paths),
        "parsed_python": parsed_count,
        "skipped": skipped,
    }


def observe_callsites(audit: Mapping[str, Any], *, checkout: Path) -> dict[str, Any]:
    """Bounded static call expressions; never infer which declaration is called."""
    build_coverage(audit)
    if not checkout.is_dir() or _git_bytes(
        checkout, "rev-parse", "--is-inside-work-tree"
    ) != b"true\n":
        raise ValueError("checkout must be a readable Git working tree")

    selected = sorted(
        (row for row in audit["tasks"] if row["experiment_membership"] == "UNASSIGNED"),
        key=lambda r: (r["suite"], r["task"]),
    )
    symbols_by_commit: dict[str, set[str]] = {}
    expected_trees: dict[str, str] = {}
    for row in selected:
        symbol, path = row.get("oracle_symbol"), row.get("oracle_path")
        if row["source"] != "VERIFIED_PATH" or row["problems"]:
            continue
        if not isinstance(path, str) or not path.endswith(".py") or not _safe_file(path):
            continue
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            continue
        commit, tree = row["commit"], row.get("tree")
        if commit in expected_trees and expected_trees[commit] != tree:
            raise ValueError("frozen candidate commits have conflicting trees")
        expected_trees[commit] = tree
        symbols_by_commit.setdefault(commit, set()).add(symbol)

    scans: dict[str, dict[str, Any]] = {}
    for commit, symbols in sorted(symbols_by_commit.items()):
        if _commit_tree(checkout, commit) != expected_trees[commit]:
            scans[commit] = {"status": "SOURCE_IDENTITY_MISMATCH",
                             "calls": {}, "tracked_python": 0,
                             "parsed_python": 0, "skipped": []}
        else:
            scans[commit] = _scan_calls(checkout, commit, symbols)

    rows: list[dict[str, Any]] = []
    for row in selected:
        symbol, path = row.get("oracle_symbol"), row.get("oracle_path")
        status = "NOT_ELIGIBLE"
        scan = scans.get(row["commit"])
        hits: list[dict[str, Any]] = []
        if row["source"] != "VERIFIED_PATH" or row["problems"]:
            status = "SOURCE_PRECONDITION_FAILED"
        elif not isinstance(path, str) or not path.endswith(".py") or not _safe_file(path):
            status = "NOT_PYTHON_SOURCE_CANDIDATE"
        elif not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            status = "INVALID_SYMBOL"
        elif scan is None or scan["status"] == "SOURCE_IDENTITY_MISMATCH":
            status = "SOURCE_IDENTITY_MISMATCH"
        else:
            hits = sorted(
                scan["calls"].get(symbol, []),
                key=lambda x: (x["path"], x["line"], x["column"], x["lexical_scope"]),
            )
            status = (
                "SCAN_INCOMPLETE" if scan["status"] != "COMPLETE" else
                "STATIC_CALL_EXPRESSIONS_OBSERVED" if hits else
                "NO_STATIC_CALL_EXPRESSIONS_OBSERVED"
            )
        rows.append({
            "suite": row["suite"], "task": row["task"], "commit": row["commit"],
            "candidate_symbol": symbol, "candidate_path": path,
            "task_semantic_digest": row.get("task_semantic_digest"),
            "status": status,
            "scan_complete": status in {
                "STATIC_CALL_EXPRESSIONS_OBSERVED",
                "NO_STATIC_CALL_EXPRESSIONS_OBSERVED",
            },
            "same_spelling_call_sites": hits,
            "call_site_count": len(hits),
            "runtime_target_resolved": False,
            "semantic_owner_proven": False,
            "oracle_approved": False,
            "baseline_headroom_established": False,
        })
    return {
        "schema": SCHEMA,
        "qualification": "STATIC_SYNTAX_ONLY",
        "source_audit_identity": "sha256:" + digest(audit),
        "summary": {
            "unassigned_candidates": len(rows),
            "complete_candidate_scans": sum(row["scan_complete"] for row in rows),
            "candidates_with_call_syntax": sum(bool(row["call_site_count"]) for row in rows),
            "semantic_owners_proven": 0,
            "approved_oracles": 0,
            "baseline_headroom_established": 0,
        },
        "commit_scans": {
            commit: {k: v for k, v in scan.items() if k != "calls"}
            for commit, scan in sorted(scans.items())
        },
        "observations": rows,
    }


def main(argv: list[str] | None = None) -> int:
    from benchmarks.dogfood.hashmarks_review_handoff import _read_input

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.resolve() == args.audit.resolve():
            raise ValueError("output must not overwrite source audit")
        report = observe_callsites(_read_input(args.audit), checkout=args.checkout)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        parser.error(str(exc))
    raw = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
