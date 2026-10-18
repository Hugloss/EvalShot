from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any

from .working_evidence import evidence_stop_facts


class EvidenceNextError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceNextError(f"cannot read artifact: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceNextError("artifact must be a JSON object")
    return value


def _candidate(payload: dict[str, Any], target: str | None) -> dict[str, Any]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise EvidenceNextError("artifact has no candidates")
    if target is None:
        if len(candidates) != 1:
            raise EvidenceNextError(
                "artifact has multiple candidates; --target is required"
            )
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise EvidenceNextError("candidate must be an object")
        return candidate
    matches = [
        row
        for row in candidates
        if isinstance(row, dict) and row.get("target") == target
    ]
    if len(matches) != 1:
        raise EvidenceNextError(f"target must identify exactly one candidate: {target}")
    return matches[0]


def next_evidence(
    payload: dict[str, Any],
    *,
    target: str | None = None,
    profile: dict[str, Any] | None = None,
    python_argv: list[str] | None = None,
    sufficiency: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if sufficiency is not None:
        boundaries = sufficiency.get("risk_boundaries", [])
        proofs = sufficiency.get("proofs", [])
        expansion = sufficiency.get("scope_expansion_evidence", [])
        if (
            not isinstance(boundaries, list)
            or not isinstance(proofs, list)
            or not isinstance(expansion, list)
        ):
            raise EvidenceNextError(
                "sufficiency risk_boundaries/proofs/scope_expansion_evidence must be lists"
            )
        stop = evidence_stop_facts(
            risk_boundaries=boundaries,
            proofs=proofs,
            scope_expansion_evidence=sufficiency.get("scope_expansion_evidence", []),
        )
        if stop["stop_acquiring_evidence"]:
            return {
                "schema": {"name": "agent-economics-next-evidence", "version": 1},
                "target": target,
                "current_probe": None,
                "next_evidence": None,
                "command": None,
                "command_display": None,
                "unresolved": [],
                "stop": stop,
                "interpretation": {
                    "evidence_sufficient": True,
                    "does_not_execute_command": True,
                    "does_not_authorize_edit": True,
                },
            }

    schema = payload.get("schema")
    if not isinstance(schema, dict) or schema.get("name") != "agent-economics-probe":
        raise EvidenceNextError("unsupported artifact schema")
    candidate = _candidate(payload, target)
    required = candidate.get("required_next_evidence")
    if not isinstance(required, list) or not required:
        raise EvidenceNextError("candidate has no required next evidence")
    if len(required) != 1:
        raise EvidenceNextError(
            "candidate has multiple required next evidence items; explicit selection is required"
        )
    item = required[0]
    if not isinstance(item, dict):
        raise EvidenceNextError("required next evidence must be an object")
    kind = item.get("kind")
    candidate_target = candidate.get("target")
    if not isinstance(kind, str) or not isinstance(candidate_target, str):
        raise EvidenceNextError("next evidence kind/target is invalid")

    command: list[str] | None = None
    unresolved: list[str] = []
    runtime_argv = list(python_argv) if python_argv is not None else None
    if runtime_argv is not None and (
        not runtime_argv
        or not all(isinstance(item, str) and item for item in runtime_argv)
    ):
        raise EvidenceNextError("python runtime argv must contain non-empty strings")
    if kind == "test_focus":
        if profile is None:
            unresolved.append("profile")
        else:
            repository = profile.get("repository")
            if not isinstance(repository, dict):
                raise EvidenceNextError("profile has no repository section")
            packages = repository.get("package_roots")
            tests = repository.get("test_roots")
            if not isinstance(packages, list) or len(packages) != 1:
                unresolved.append("single_package_root")
            if not isinstance(tests, list) or len(tests) != 1:
                unresolved.append("single_test_root")
            for field, selected, unresolved_code in (
                ("package_root_evidence", packages, "package_root_not_detected"),
                ("test_root_evidence", tests, "test_root_not_detected"),
            ):
                evidence = repository.get(field)
                if (
                    isinstance(evidence, list)
                    and isinstance(selected, list)
                    and len(selected) == 1
                ):
                    matches = [
                        row
                        for row in evidence
                        if isinstance(row, dict) and row.get("path") == selected[0]
                    ]
                    if len(matches) != 1 or matches[0].get("status") != "DETECTED":
                        unresolved.append(unresolved_code)
            if runtime_argv is None:
                unresolved.append("python_runtime_argv")
            if not unresolved:
                command = [
                    *runtime_argv,
                    "-m",
                    "agent_economics",
                    "test-focus",
                    "--repository-root",
                    ".",
                    "--source-root",
                    str(packages[0]),
                    "--tests-root",
                    str(tests[0]),
                    "--changed-path",
                    candidate_target,
                    "--artifact-path",
                    ".agent-artifacts/test-focus.json",
                ]
    else:
        unresolved.append(f"unsupported_next_evidence:{kind}")

    return {
        "schema": {"name": "agent-economics-next-evidence", "version": 1},
        "target": candidate_target,
        "current_probe": payload.get("tool", {}).get("name")
        if isinstance(payload.get("tool"), dict)
        else None,
        "next_evidence": {"kind": kind, "reason": item.get("reason")},
        "command": command,
        "command_display": shlex.join(command) if command is not None else None,
        "unresolved": unresolved,
        "interpretation": {
            "translates_existing_evidence_requirement_only": True,
            "does_not_authorize_edit": True,
            "does_not_execute_command": True,
            "profile_is_configuration_input_not_policy_authority": True,
            "profile_root_provenance_is_preserved": True,
            "runtime_authority_must_be_explicit": True,
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Translate an artifact's existing next-evidence requirement into a bounded command."
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--target", default=None)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument(
        "--python-command",
        action="append",
        default=None,
        help="Explicit Python launcher argv token; repeat for multi-token launchers, for example: --python-command uv --python-command run --python-command python",
    )
    args = parser.parse_args(argv)
    try:
        payload = _load(args.artifact)
        profile = _load(args.profile) if args.profile is not None else None
        print(
            json.dumps(
                next_evidence(
                    payload,
                    target=args.target,
                    profile=profile,
                    python_argv=args.python_command,
                ),
                indent=2,
                sort_keys=True,
            )
        )
    except EvidenceNextError as exc:
        raise SystemExit(f"next: {exc}") from exc


if __name__ == "__main__":
    main()
