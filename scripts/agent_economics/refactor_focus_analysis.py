from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PythonFileAnalysis:
    path: Path
    source: str
    tree: ast.AST | None
    line_count: int
    byte_count: int
    parse_error: str | None
    content_sha256: str | None


class AnalysisCache:
    """Read and parse each Python file at most once per probe run."""

    def __init__(self) -> None:
        self._records: dict[Path, PythonFileAnalysis] = {}
        self.files_read = 0
        self.bytes_read = 0
        self.ast_parses = 0
        self.read_failures = 0
        self.parse_failures = 0
        self.cache_hits = 0

    def get(self, path: Path) -> PythonFileAnalysis:
        key = path.resolve()
        existing = self._records.get(key)
        if existing is not None:
            self.cache_hits += 1
            return existing

        try:
            raw = key.read_bytes()
        except OSError as exc:
            self.read_failures += 1
            record = PythonFileAnalysis(
                path=key,
                source="",
                tree=None,
                line_count=0,
                byte_count=0,
                parse_error=f"read_error:{type(exc).__name__}",
                content_sha256=None,
            )
            self._records[key] = record
            return record

        self.files_read += 1
        self.bytes_read += len(raw)
        content_sha256 = hashlib.sha256(raw).hexdigest()
        source = raw.decode("utf-8", errors="replace")
        self.ast_parses += 1
        try:
            tree: ast.AST | None = ast.parse(source)
            parse_error = None
        except (SyntaxError, ValueError) as exc:
            tree = None
            parse_error = f"parse_error:{type(exc).__name__}"
            self.parse_failures += 1

        record = PythonFileAnalysis(
            path=key,
            source=source,
            tree=tree,
            line_count=len(source.splitlines()),
            byte_count=len(raw),
            parse_error=parse_error,
            content_sha256=content_sha256,
        )
        self._records[key] = record
        return record

    def prewarm(self, paths: list[Path]) -> None:
        for path in paths:
            self.get(path)

    @property
    def unique_files_cached(self) -> int:
        return len(self._records)

    def metrics(self) -> dict[str, int]:
        return {
            "files_read": self.files_read,
            "bytes_read": self.bytes_read,
            "ast_parses": self.ast_parses,
            "read_failures": self.read_failures,
            "parse_failures": self.parse_failures,
            "cache_hits": self.cache_hits,
            "unique_files_cached": self.unique_files_cached,
        }
