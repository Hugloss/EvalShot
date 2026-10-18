"""Execute evidence-escalated oracle reviews with a distinct read-only reviewer."""

from __future__ import annotations

import ast
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.harness.identity import digest
from benchmarks.harness.oracle_reviews import (
    OracleReviewError,
    oracle_review_path,
    validate_oracle_reviews,
)
from benchmarks.harness.source import materialize_repository
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded


REVIEWER_AGENT = "plan"
REVIEWER_ID = "opencode-plan-independent-oracle-review"
DECISION_PREFIX = "BENCHMARK_ORACLE_DECISION="
_RUNTIME_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "opencode-runtime.js"
)


def _verify_expected_owner(
    workspace: Path,
    task: dict[str, Any],
) -> dict[str, str] | None:
    """Prove the frozen expected location exists before spending reviewer tokens."""
    if task["oracle"]["adapter"] != "repository-location-json":
        return None
    expected = task["oracle"]["configuration"].get("expected")
    if not isinstance(expected, dict):
        raise OracleReviewError("repository-location oracle has no expected owner")
    path_value = expected.get("path")
    symbol_value = expected.get("symbol")
    if not isinstance(path_value, str) or not isinstance(symbol_value, str):
        raise OracleReviewError("repository-location expected owner is malformed")

    root = workspace.resolve()
    path = (root / path_value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise OracleReviewError("expected owner path escapes pinned repository") from exc
    if not path.is_file():
        raise OracleReviewError(
            f"pinned expected owner path is missing before review: {path_value}"
        )

    terminal_symbol = symbol_value.rsplit(".", 1)[-1]
    if path.suffix == ".py":
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError) as exc:
            raise OracleReviewError(
                f"cannot inspect pinned expected owner source: {path_value}"
            ) from exc
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
        }
        if terminal_symbol not in names:
            raise OracleReviewError(
                "pinned expected owner symbol is missing before review: "
                f"{path_value}::{symbol_value}"
            )
    else:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise OracleReviewError(
                f"cannot inspect pinned expected owner source: {path_value}"
            ) from exc
        if terminal_symbol not in source:
            raise OracleReviewError(
                "pinned expected owner symbol is missing before review: "
                f"{path_value}::{symbol_value}"
            )
    return {"path": path_value, "symbol": symbol_value}


def _review_prompt(
    task_id: str,
    task: dict[str, Any],
    *,
    expected_owner: dict[str, str] | None,
) -> str:
    task_digest = digest(task)
    decision_example: dict[str, Any] = {
        "task_id": task_id,
        "task_digest": task_digest,
        "decision": "unique|ambiguous|invalid",
        "reason": "brief evidence",
    }
    if expected_owner is not None:
        decision_example["observed_owner"] = expected_owner
    rendered_decision = json.dumps(decision_example, separators=(",", ":"))
    return f"""You are performing an independent benchmark-oracle source audit.

Inspect only this pinned repository checkout. Do not use or search for any
agentsCookbook oracle-review evidence, prior reviewer rationale, or prior answer.

Task id: {task_id}
Task digest: {task_digest}
Frozen benchmark prompt:
{task["prompt"]}

Expected semantic owner under review:
{json.dumps(expected_owner, sort_keys=True)}

The harness independently proved that expected path/symbol exists in this exact
pinned checkout. You must inspect that source and its callers yourself.

Decide whether the expected owner is the unique defensible semantic owner for
the frozen prompt.

Decision rules:
- unique: the expected owner is uniquely defensible.
- ambiguous: another owner is also defensible.
- invalid: the observed expected owner exists but is not semantically defensible.
- inability to inspect source is NOT an invalid oracle decision; emit no decision.

For repository-location tasks, observed_owner must exactly name the source
location you actually inspected. Put the source evidence briefly in reason.
Use tool calls for analysis. Your terminal response must be exactly one JSON
object and nothing else: no prose, no Markdown, no code fence, no prefix.
{rendered_decision}
"""

def _bounded_answer_preview(value: str, *, limit: int = 1200) -> str:
    rendered = value.strip().replace("\x00", "")
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + "…"


def _decision_payloads(stdout: str) -> list[str]:
    """Return explicit decision envelopes without fuzzy JSON extraction."""
    candidates: list[str] = []

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("`") and line.endswith("`"):
            line = line.strip("`").strip()
        if line.startswith(DECISION_PREFIX):
            candidates.append(line[len(DECISION_PREFIX) :].strip())

    stripped = stdout.strip()
    try:
        whole = json.loads(stripped)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict):
        candidates.append(stripped)

    fenced = re.findall(
        r"```(?:json)?\s*\n(.*?)\n```",
        stdout,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for payload in fenced:
        candidate = payload.strip()
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(candidate)

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            normalized = json.dumps(
                json.loads(candidate),
                sort_keys=True,
                separators=(",", ":"),
            )
        except json.JSONDecodeError:
            normalized = candidate
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(candidate)
    return unique


def _parse_decision(
    stdout: str,
    *,
    task_id: str,
    task_digest: str,
    expected_owner: dict[str, str] | None,
) -> dict[str, Any]:
    candidates = _decision_payloads(stdout)
    if len(candidates) != 1:
        preview = _bounded_answer_preview(stdout)
        raise OracleReviewError(
            "independent reviewer must emit exactly one explicit benchmark oracle "
            f"decision; found {len(candidates)} candidate(s); "
            f"terminal answer preview={preview!r}"
        )
    try:
        value = json.loads(candidates[0])
    except ValueError as exc:
        preview = _bounded_answer_preview(stdout)
        raise OracleReviewError(
            "independent reviewer decision is not valid JSON; "
            f"terminal answer preview={preview!r}"
        ) from exc
    if not isinstance(value, dict):
        raise OracleReviewError("independent reviewer decision must be one JSON object")
    if value.get("task_id") != task_id or value.get("task_digest") != task_digest:
        raise OracleReviewError("independent reviewer decision identity mismatch")
    if value.get("decision") not in {"unique", "ambiguous", "invalid"}:
        raise OracleReviewError("independent reviewer decision is invalid")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise OracleReviewError("independent reviewer reason is missing")
    if expected_owner is not None and value.get("observed_owner") != expected_owner:
        raise OracleReviewError(
            "independent reviewer did not observe the deterministically present "
            "expected owner; no semantic review evidence was recorded"
        )
    return {
        "task_id": task_id,
        "task_digest": task_digest,
        "decision": str(value["decision"]),
        "reason": reason.strip(),
        "observed_owner": value.get("observed_owner"),
    }


def _run_review(
    workspace: Path,
    prompt: str,
    *,
    task_id: str,
) -> dict[str, str]:
    with tempfile.TemporaryDirectory(
        prefix="agentscookbook-oracle-prompt-"
    ) as tmp:
        prompt_path = Path(tmp) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        result = run_bounded(
            repository_root=workspace,
            argv=(
                "node",
                str(_RUNTIME_SCRIPT),
                "run-export",
                "--repo",
                str(workspace),
                "--agent",
                REVIEWER_AGENT,
                "--title",
                f"agentscookbook-oracle-review-{task_id}",
                "--prompt-file",
                str(prompt_path),
                "--keep-session",
                "false",
            ),
            limits=ProcessLimits(
                timeout_seconds=600.0,
                max_stdout_bytes=5_000_000,
                max_stderr_bytes=200_000,
            ),
            environment={
                "AGENTS_COOKBOOK_RUN_DIR": "",
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_PRUNE": "1",
                "OPENCODE_AUTO_SHARE": "false",
            },
            close_stdin=True,
        )
    if result.executable_missing:
        raise OracleReviewError(
            "Node.js is required for the qualified OpenCode review runtime"
        )
    if (
        result.timed_out
        or result.stdout_truncated
        or result.stderr_truncated
        or result.return_code != 0
    ):
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {stderr[:1200]}" if stderr else ""
        raise OracleReviewError(
            "independent OpenCode oracle reviewer runtime failed" + detail
        )
    try:
        envelope = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OracleReviewError(
            "independent OpenCode oracle reviewer emitted invalid runtime evidence"
        ) from exc
    run = envelope.get("run")
    if not isinstance(run, dict) or run.get("status") != 0:
        raise OracleReviewError(
            "independent OpenCode oracle reviewer did not complete cleanly"
        )
    final_text = envelope.get("final_text")
    if not isinstance(final_text, str) or not final_text.strip():
        detail = envelope.get("export_parse_error")
        suffix = f": {detail}" if isinstance(detail, str) and detail else ""
        raise OracleReviewError(
            "independent OpenCode oracle reviewer produced no terminal decision"
            + suffix
        )
    return {
        "stdout": final_text,
        "command_identity": result.command_identity,
    }


def _write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    raw = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".oracle-reviews-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run_pending_oracle_reviews(
    suite,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Run only reviews still required by the committed review-depth policy."""
    status = validate_oracle_reviews(suite, require_complete=False)
    pending = list(status["pending_tasks"])
    if not pending:
        return {
            "reviewed_tasks": [],
            "complete": True,
            "approved_tasks": status["approved_tasks"],
        }

    review_path = oracle_review_path(suite)
    evidence = json.loads(review_path.read_text(encoding="utf-8"))
    cache = (
        cache_root
        or Path(tempfile.gettempdir()) / "agentscookbook-oracle-review-cache"
    )
    reviewed: list[dict[str, Any]] = []

    for task_id in pending:
        task = suite.tasks[task_id]
        row = evidence["tasks"][task_id]
        requirement = row["review_requirement"]
        minimum_reviews = int(requirement["minimum_independent_reviews"])
        if len(row["reviews"]) >= minimum_reviews:
            non_unique = [
                review
                for review in row["reviews"]
                if review.get("decision") != "unique"
            ]
            if non_unique:
                raise OracleReviewError(
                    f"{task_id} has a non-unique independent review; "
                    "repair or retire the task instead of adding another reviewer"
                )
            continue
        if REVIEWER_ID in {review.get("reviewer") for review in row["reviews"]}:
            raise OracleReviewError(
                f"independent reviewer already recorded for {task_id}"
            )

        with tempfile.TemporaryDirectory(
            prefix=f"agentscookbook-oracle-review-{task_id}-"
        ) as tmp:
            workspace = Path(tmp) / "repository"
            materialize_repository(
                repository=task["repository"],
                destination=workspace,
                cache_root=cache,
            )
            expected_owner = _verify_expected_owner(workspace, task)
            prompt = _review_prompt(
                task_id,
                task,
                expected_owner=expected_owner,
            )
            runtime = _run_review(
                workspace,
                prompt,
                task_id=task_id,
            )
            decision = _parse_decision(
                runtime["stdout"],
                task_id=task_id,
                task_digest=digest(task),
                expected_owner=expected_owner,
            )

        row["reviews"].append(
            {
                "reviewer": REVIEWER_ID,
                "task_digest": decision["task_digest"],
                "decision": decision["decision"],
                "reason": decision["reason"],
                "observed_owner": decision["observed_owner"],
                "runtime": {
                    "host": "opencode",
                    "agent": REVIEWER_AGENT,
                    "command_identity": runtime["command_identity"],
                },
            }
        )
        reviewed.append(decision)

    _write_evidence(review_path, evidence)
    final = validate_oracle_reviews(suite, require_complete=False)
    return {
        "reviewed_tasks": reviewed,
        "approved_tasks": final["approved_tasks"],
        "pending_tasks": final["pending_tasks"],
        "complete": final["complete"],
    }
