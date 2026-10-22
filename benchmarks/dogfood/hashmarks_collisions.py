"""Pinned-tree same-name declaration scan for independent Hashmarks review.

Unlike the nominated-file AST check, this scans tracked Python files outside
the oracle-proposed path. A same-name declaration is *not* proof of equal
semantics, while an absence is *not* proof of unique ownership.
No repository code is imported or executed and no oracle is approved.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
from typing import Any, Mapping

from benchmarks.dogfood.hashmarks_corpus import _safe_file
from benchmarks.dogfood.hashmarks_coverage import build_coverage
from benchmarks.dogfood.hashmarks_declarations import (
    _Declarations, _git_bytes, _source_bytes,
)
from benchmarks.dogfood.hashmarks_review_handoff import _read_input
from benchmarks.harness.identity import digest

SCHEMA = "evalshot.hashmarks-dogfood-collisions.v1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SYMBOL = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*\Z")
_MAX_TRACKED_PYTHON = 1200
_MAX_TOTAL_BYTES = 24 * 1024 * 1024
_MAX_DECLARATIONS = 1500


def _commit_tree(checkout: Path, commit: str) -> str | None:
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        return None
    if _git_bytes(checkout, "cat-file", "-t", commit) != b"commit\n":
        return None
    raw = _git_bytes(checkout, "rev-parse", "--verify", f"{commit}^{{tree}}")
    value = raw.decode("ascii", errors="ignore").strip() if raw else ""
    return value if _SHA.fullmatch(value) else None


def _python_paths(checkout: Path, commit: str) -> list[tuple[str, str]] | None:
    raw = _git_bytes(checkout, "ls-tree", "-r", "--full-tree", "-z", commit)
    if raw is None:
        return None
    paths: list[tuple[str, str]] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        if b"\t" not in item:
            return None
        meta, name = item.split(b"\t", 1)
        try:
            filename = name.decode("utf-8")
            mode, objtype, _oid = meta.decode("ascii").split(" ", 2)
        except (UnicodeDecodeError, ValueError):
            return None
        if not filename.endswith(".py"):
            continue
        # Symlinks, Gitlinks and suspicious paths are not Python source.
        if not _safe_file(filename) or (mode, objtype) not in {
            ("100644", "blob"), ("100755", "blob")
        }:
            paths.append((filename, "NON_REGULAR_OR_UNSAFE"))
        else:
            paths.append((filename, "REGULAR"))
    return sorted(paths)


def _scan_commit(checkout: Path, commit: str, symbols: set[str]) -> dict[str, Any]:
    paths = _python_paths(checkout, commit)
    if paths is None:
        return {"status": "TREE_LIST_UNAVAILABLE", "matches": {},
                "tracked_python": 0, "read": 0, "skipped": [], "bytes_read": 0}
    if len(paths) > _MAX_TRACKED_PYTHON:
        return {"status": "FILE_COUNT_BOUND_EXCEEDED", "matches": {},
                "tracked_python": len(paths), "read": 0, "skipped": [],
                "bytes_read": 0}
    matches: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    skipped: list[dict[str, str]] = []
    consumed = 0
    read = 0
    for filename, entry_type in paths:
        if entry_type != "REGULAR":
            skipped.append({"path": filename, "reason": "NON_REGULAR_OR_UNSAFE"})
            continue
        payload = _source_bytes(checkout, commit, filename)
        if payload is None:
            skipped.append({"path": filename, "reason": "SOURCE_UNAVAILABLE_OR_TOO_LARGE"})
            continue
        consumed += len(payload)
        if consumed > _MAX_TOTAL_BYTES:
            skipped.append({"path": filename, "reason": "TOTAL_SOURCE_BYTE_LIMIT"})
            break
        try:
            parsed = ast.parse(payload.decode("utf-8"), filename=filename)
        except (SyntaxError, UnicodeDecodeError, ValueError, RecursionError):
            skipped.append({"path": filename, "reason": "UNPARSEABLE_PYTHON"})
            continue
        read += 1
        for symbol in sorted(symbols):
            visitor = _Declarations(symbol)
            visitor.visit(parsed)
            for match in visitor.matches:
                matches[symbol].append({"path": filename, **match})
                if len(matches[symbol]) > _MAX_DECLARATIONS:
                    return {"status": "DECLARATION_COUNT_BOUND_EXCEEDED",
                            "matches": matches, "tracked_python": len(paths),
                            "read": read, "skipped": skipped,
                            "bytes_read": consumed}
    return {
        "status": "COMPLETE" if not skipped else "INCOMPLETE",
        "matches": matches,
        "tracked_python": len(paths),
        "read": read,
        "skipped": skipped,
        "bytes_read": consumed,
    }


def observe_collisions(audit: Mapping[str, Any], *, checkout: Path) -> dict[str, Any]:
    """Find same-name declarations at an exact frozen commit, never owner truth."""
    build_coverage(audit)
    if not checkout.is_dir() or _git_bytes(
        checkout, "rev-parse", "--is-inside-work-tree"
    ) != b"true\n":
        raise ValueError("checkout must be a readable Git working tree")
    selected: list[Mapping[str, Any]] = [
        row for row in audit["tasks"] if row["experiment_membership"] == "UNASSIGNED"
    ]
    by_commit: dict[str, set[str]] = {}
    trees: dict[str, str] = {}
    for row in selected:
        symbol, path = row.get("oracle_symbol"), row.get("oracle_path")
        if row["source"] != "VERIFIED_PATH" or row["problems"]:
            continue
        if not isinstance(path, str) or not path.endswith(".py") or not _safe_file(path):
            continue
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            continue
        by_commit.setdefault(row["commit"], set()).add(symbol)
        if row["commit"] in trees and trees[row["commit"]] != row.get("tree"):
            raise ValueError("frozen tasks disagree on source tree for same commit")
        trees[row["commit"]] = row.get("tree")
    scans: dict[str, dict[str, Any]] = {}
    for commit, symbols in sorted(by_commit.items()):
        verified_tree = _commit_tree(checkout, commit)
        if verified_tree is None or verified_tree != trees[commit]:
            scans[commit] = {"status": "SOURCE_IDENTITY_MISMATCH",
                             "matches": {}, "tracked_python": 0, "read": 0,
                             "skipped": [], "bytes_read": 0}
        else:
            scans[commit] = _scan_commit(checkout, commit, symbols)

    rows: list[dict[str, Any]] = []
    for row in sorted(selected, key=lambda x: (x["suite"], x["task"])):
        symbol, path = row.get("oracle_symbol"), row.get("oracle_path")
        status = "NOT_ELIGIBLE"
        matches: list[dict[str, Any]] = []
        nominated: list[dict[str, Any]] = []
        alternatives: list[dict[str, Any]] = []
        scan = scans.get(row["commit"])
        if row["source"] != "VERIFIED_PATH" or row["problems"]:
            status = "SOURCE_PRECONDITION_FAILED"
        elif not isinstance(path, str) or not path.endswith(".py") or not _safe_file(path):
            status = "NOT_PYTHON_SOURCE_CANDIDATE"
        elif not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            status = "INVALID_SYMBOL"
        elif scan is None or scan["status"] == "SOURCE_IDENTITY_MISMATCH":
            status = "SOURCE_IDENTITY_MISMATCH"
        else:
            matches = sorted(scan["matches"].get(symbol, []),
                             key=lambda x: (x["path"], x["line"], x["qualname"]))
            nominated = [match for match in matches if match["path"] == path]
            alternatives = [match for match in matches if match["path"] != path]
            if scan["status"] != "COMPLETE":
                status = "SCAN_INCOMPLETE"
            elif not nominated:
                status = "NOMINATED_DECLARATION_NOT_OBSERVED"
            elif alternatives or len(nominated) > 1:
                status = "SAME_NAME_ALTERNATIVES_OBSERVED"
            else:
                status = "NO_OTHER_STATIC_DECLARATIONS_OBSERVED"
        rows.append({
            "suite": row["suite"],
            "task": row["task"],
            "commit": row["commit"],
            "candidate_path": path,
            "candidate_symbol": symbol,
            "task_semantic_digest": row.get("task_semantic_digest"),
            "status": status,
            "scan_complete": (
                status not in {
                    "SOURCE_PRECONDITION_FAILED",
                    "NOT_PYTHON_SOURCE_CANDIDATE",
                    "INVALID_SYMBOL",
                    "SOURCE_IDENTITY_MISMATCH",
                }
                and scan is not None
                and scan["status"] == "COMPLETE"
            ),
            "nominated_declarations": nominated,
            "other_file_same_name_declarations": alternatives,
            "same_file_declarations": len(nominated),
            "total_same_name_declarations": len(matches),
            "unique_semantic_owner_proven": False,
            "oracle_approved": False,
            "baseline_headroom_known": False,
        })
    return {
        "schema": SCHEMA,
        "qualification": "STATIC_ALTERNATIVE_CANDIDATES_ONLY",
        "source_audit_identity": "sha256:" + digest(audit),
        "summary": {
            "unassigned_candidates": len(rows),
            "complete_scans": sum(r["scan_complete"] for r in rows),
            "other_declarations_found": sum(
                bool(r["other_file_same_name_declarations"]) for r in rows),
            "semantic_owner_claims": 0,
            "approved_oracles": 0,
            "baseline_headroom_established": 0,
        },
        "commit_scans": {
            commit: {key: value for key, value in scan.items() if key != "matches"}
            for commit, scan in sorted(scans.items())
        },
        "observations": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    from benchmarks.dogfood.hashmarks_review_handoff import _read_input
    try:
        if args.output and args.output.resolve() == args.audit.resolve():
            raise ValueError("output must not overwrite source audit")
        report = observe_collisions(_read_input(args.audit), checkout=args.checkout)
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
