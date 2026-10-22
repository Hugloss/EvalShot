"""Reviewer-only AST observations from pinned Python source, not oracle approval.

The frozen task declares a candidate path and symbol. We inspect *only that
file* at the audited Git commit, never search for competing owners or decide
that the nominated implementation is canonical. No repository code is run.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from benchmarks.dogfood.hashmarks_corpus import _safe_file
from benchmarks.dogfood.hashmarks_coverage import build_coverage
from benchmarks.dogfood.hashmarks_review_handoff import _read_input
from benchmarks.harness.identity import digest

SCHEMA = "evalshot.hashmarks-dogfood-declarations.v1"
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MAX_SOURCE_BYTES = 1_000_000


def _git_bytes(checkout: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "--no-optional-locks", *args],
            capture_output=True,
            check=False,
            timeout=15,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _source_bytes(checkout: Path, commit: str, path: str) -> bytes | None:
    # Byte/size checks must precede reading a repository blob. The Git object
    # and requested path are bound to the existing frozen source audit.
    if not _SHA40.fullmatch(commit) or not _safe_file(path):
        return None
    object_ref = f"{commit}:{path}"
    size = _git_bytes(checkout, "cat-file", "-s", object_ref)
    if size is None:
        return None
    try:
        count = int(size.strip())
    except ValueError:
        return None
    if count > _MAX_SOURCE_BYTES or count < 0:
        return None
    result = _git_bytes(checkout, "show", object_ref)
    if result is None or len(result) != count:
        return None
    return result


class _Declarations(ast.NodeVisitor):
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.stack: list[str] = []
        self.matches: list[dict[str, Any]] = []

    def _visit_named(self, node: ast.AST, name: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if name == self.symbol:
                self.matches.append({
                    "qualname": ".".join([*self.stack, name]),
                    "kind": "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                })
        self.stack.append(name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_named(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_named(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_named(node, node.name)


def observe(audit: Mapping[str, Any], *, checkout: Path) -> dict[str, Any]:
    """Observe only unassigned candidate files; cannot infer oracle truth."""
    build_coverage(audit)  # delegated source-report structure validation
    if not checkout.is_dir() or _git_bytes(checkout, "rev-parse", "--is-inside-work-tree") != b"true\n":
        raise ValueError("checkout must be a readable Git working tree")
    reports: list[dict[str, Any]] = []
    for row in sorted(audit["tasks"], key=lambda r: (r["suite"], r["task"])):
        if row["experiment_membership"] != "UNASSIGNED":
            continue
        path, symbol = row.get("oracle_path"), row.get("oracle_symbol")
        status, matches, sha256 = "NOT_INSPECTED", [], None
        if row["source"] != "VERIFIED_PATH" or row["problems"]:
            status = "SOURCE_PRECONDITION_FAILED"
        elif not isinstance(path, str) or not path.endswith(".py"):
            status = "NOT_PYTHON_CANDIDATE"
        elif not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            status = "INVALID_SYMBOL"
        else:
            content = _source_bytes(checkout, row["commit"], path)
            if content is None:
                status = "SOURCE_UNAVAILABLE"
            else:
                sha256 = hashlib.sha256(content).hexdigest()
                try:
                    source = content.decode("utf-8")
                    parsed = ast.parse(source, filename=path)
                except (UnicodeDecodeError, SyntaxError, ValueError):
                    status = "SOURCE_NOT_PARSEABLE"
                else:
                    visitor = _Declarations(symbol)
                    visitor.visit(parsed)
                    matches = sorted(visitor.matches, key=lambda m: (m["line"], m["qualname"]))
                    status = (
                        "DECLARATION_ABSENT" if not matches else
                        "MULTIPLE_DECLARATIONS" if len(matches) > 1 else
                        "DECLARATION_OBSERVED"
                    )
        reports.append({
            "suite": row["suite"],
            "task": row["task"],
            "commit": row["commit"],
            "candidate_path": path,
            "candidate_symbol": symbol,
            "semantic_task_digest": row.get("task_semantic_digest"),
            "source_sha256": sha256,
            "status": status,
            "declarations_in_candidate_file": matches,
            "scope": "ONLY_THE_FROZEN_NOMINATED_FILE",
            "unique_owner_proven": False,
            "oracle_approved": False,
            "headroom_established": False,
        })
    return {
        "schema": SCHEMA,
        "qualification": "REVIEWER_ONLY_SOURCE_OBSERVATION",
        "source_audit_identity": "sha256:" + digest(audit),
        "summary": {
            "unassigned_candidates": len(reports),
            "declarations_observed": sum(r["status"] == "DECLARATION_OBSERVED" for r in reports),
            "unresolved_or_inapplicable": sum(r["status"] != "DECLARATION_OBSERVED" for r in reports),
            "unique_owners_proven": 0,
            "oracles_approved": 0,
            "headroom_established": 0,
        },
        "observations": reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.resolve() == args.audit.resolve():
            raise ValueError("output must not overwrite source audit")
        report = observe(_read_input(args.audit), checkout=args.checkout)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        parser.error(str(exc))
    body = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
