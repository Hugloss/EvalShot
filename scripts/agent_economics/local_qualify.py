from __future__ import annotations

import json
from pathlib import Path

from .command_manifest import load_command_manifest
from .command_runner import run_named_command
from .loop_state import LoopBudget, LoopSession, semantic_identity
from .repair_packet import build_repair_packet
from .workspace_state import tracked_workspace_state

_STAGE_ORDER = {"focused": 0, "affected": 1, "component": 2, "repository": 3}


def qualify_local(
    *,
    repository_root: Path,
    manifest_path: Path,
    command_names: list[str],
    changed_paths: list[str] | None = None,
    selected_paths: list[str] | None = None,
    probe_artifacts: list[Path] | None = None,
    state_path: Path = Path(".agent-artifacts/local-loop.json"),
    budget: LoopBudget = LoopBudget(),
) -> dict[str, object]:
    root = repository_root.resolve()
    manifest_target = (
        manifest_path if manifest_path.is_absolute() else root / manifest_path
    )
    manifest = load_command_manifest(manifest_target)
    if not command_names:
        raise ValueError("at least one command is required")
    unknown = [name for name in command_names if name not in manifest.commands]
    if unknown:
        raise ValueError(f"unknown commands: {unknown}")
    selected_stages = [manifest.commands[name].stage for name in command_names]
    if [_STAGE_ORDER[s] for s in selected_stages] != sorted(
        _STAGE_ORDER[s] for s in selected_stages
    ):
        raise ValueError("qualification stages must be monotonic")
    source_identity = str(tracked_workspace_state(root)["identity"])
    configured = [
        {"command": name, "stage": spec.stage, "selected": name in command_names}
        for name, spec in manifest.commands.items()
    ]
    stages: list[dict[str, object]] = []
    state_target = state_path if state_path.is_absolute() else root / state_path
    with LoopSession(state_target, budget=budget, repository_root=root) as session:
        for name in command_names:
            result = run_named_command(
                repository_root=root,
                manifest_path=manifest_target,
                name=name,
                selected_paths=selected_paths or (),
            )
            packet = build_repair_packet(
                repository_root=root,
                command_result=result,
                changed_paths=changed_paths or [],
                probe_artifacts=probe_artifacts or (),
            )
            economics = packet.get("economics", {})
            execution = result.get("execution", {})
            state = session.record(
                source_identity=source_identity,
                command_identity=str(result["command"]["identity"]),
                failure_identity=str(result["failure_identity"]),
                evidence_identity=str(packet["evidence_identity"]),
                stdout_bytes=int(execution.get("stdout_bytes", 0)),
                stderr_bytes=int(execution.get("stderr_bytes", 0)),
                elapsed_ms=float(execution.get("elapsed_ms", 0.0)),
                stage=str(result["command"]["stage"]),
                evidence_files=int(economics.get("probe_files", 0)),
                evidence_lines=len(
                    str(packet.get("diagnostic_excerpt", "")).splitlines()
                ),
                context_tokens=int(economics.get("estimated_context_tokens", 0)),
            )
            stages.append(
                {
                    "command": name,
                    "stage": result["command"]["stage"],
                    "status": result["status"],
                    "classification": result["classification"],
                    "failure_identity": result["failure_identity"],
                    "execution_economics": {
                        "elapsed_ms": float(execution.get("elapsed_ms", 0.0)),
                        "stdout_bytes": int(execution.get("stdout_bytes", 0)),
                        "stderr_bytes": int(execution.get("stderr_bytes", 0)),
                        "probe_files": int(economics.get("probe_files", 0)),
                        "probe_bytes": int(economics.get("probe_bytes", 0)),
                        "diagnostic_lines": len(
                            str(packet.get("diagnostic_excerpt", "")).splitlines()
                        ),
                        "estimated_context_tokens": int(
                            economics.get("estimated_context_tokens", 0)
                        ),
                    },
                    "repair_packet": packet if result["status"] != "PASS" else None,
                }
            )
            if result["status"] != "PASS" or state.get("stop_reason"):
                status = (
                    "LOCAL_FAILED" if result["status"] != "PASS" else "LOCAL_INCOMPLETE"
                )
                return _result(
                    status,
                    manifest.identity,
                    source_identity,
                    configured,
                    stages,
                    state.get("stop_reason"),
                )
    highest = max((_STAGE_ORDER[str(x["stage"])] for x in stages), default=-1)
    status = (
        "LOCAL_QUALIFIED"
        if highest == _STAGE_ORDER["repository"]
        else (
            "AFFECTED_PASS" if highest >= _STAGE_ORDER["affected"] else "FOCUSED_PASS"
        )
    )
    return _result(status, manifest.identity, source_identity, configured, stages, None)


def _result(
    status: str,
    manifest_identity: str,
    source_identity: str,
    configured: list[dict[str, object]],
    stages: list[dict[str, object]],
    stop_reason: object,
) -> dict[str, object]:
    executed = {str(s["command"]) for s in stages}
    stage_inventory = [
        {
            **item,
            "state": "executed"
            if item["command"] in executed
            else ("skipped" if item["selected"] else "not_selected"),
        }
        for item in configured
    ]
    totals = {
        "commands": len(stages),
        "elapsed_ms": round(
            sum(
                float(s.get("execution_economics", {}).get("elapsed_ms", 0.0))
                for s in stages
            ),
            3,
        ),
        "stdout_bytes": sum(
            int(s.get("execution_economics", {}).get("stdout_bytes", 0)) for s in stages
        ),
        "stderr_bytes": sum(
            int(s.get("execution_economics", {}).get("stderr_bytes", 0)) for s in stages
        ),
        "probe_files": sum(
            int(s.get("execution_economics", {}).get("probe_files", 0)) for s in stages
        ),
        "probe_bytes": sum(
            int(s.get("execution_economics", {}).get("probe_bytes", 0)) for s in stages
        ),
        "diagnostic_lines": sum(
            int(s.get("execution_economics", {}).get("diagnostic_lines", 0))
            for s in stages
        ),
        "estimated_context_tokens": sum(
            int(s.get("execution_economics", {}).get("estimated_context_tokens", 0))
            for s in stages
        ),
    }
    by_stage = {}
    for stage_name in _STAGE_ORDER:
        selected = [s for s in stages if s["stage"] == stage_name]
        by_stage[stage_name] = {
            "commands": len(selected),
            "elapsed_ms": round(
                sum(
                    float(s.get("execution_economics", {}).get("elapsed_ms", 0.0))
                    for s in selected
                ),
                3,
            ),
            "stdout_bytes": sum(
                int(s.get("execution_economics", {}).get("stdout_bytes", 0))
                for s in selected
            ),
            "stderr_bytes": sum(
                int(s.get("execution_economics", {}).get("stderr_bytes", 0))
                for s in selected
            ),
        }
    semantic = {
        "manifest_identity": manifest_identity,
        "source_identity": source_identity,
        "status": status,
        "stages": [
            {
                k: s[k]
                for k in (
                    "command",
                    "stage",
                    "status",
                    "classification",
                    "failure_identity",
                )
            }
            for s in stages
        ],
        "stop_reason": stop_reason,
    }
    return {
        "schema": {"name": "agent-economics-local-qualification", "version": 2},
        "status": status,
        "ci_status": "NOT_RUN",
        "source_identity": source_identity,
        "manifest_identity": manifest_identity,
        "stages": stages,
        "stage_inventory": stage_inventory,
        "loop": {"stop_reason": stop_reason},
        "economics": {**totals, "by_stage": by_stage},
        "local_verification_identity": semantic_identity(semantic),
    }


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run staged repository-local qualification."
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--command", action="append", required=True)
    parser.add_argument("--changed-path", action="append", default=[])
    parser.add_argument("--selected-path", action="append", default=[])
    parser.add_argument("--probe-artifact", action="append", type=Path, default=[])
    parser.add_argument(
        "--state-path", type=Path, default=Path(".agent-artifacts/local-loop.json")
    )
    args = parser.parse_args(argv)
    payload = qualify_local(
        repository_root=args.repository_root,
        manifest_path=args.manifest,
        command_names=args.command,
        changed_paths=args.changed_path,
        selected_paths=args.selected_path,
        probe_artifacts=args.probe_artifact,
        state_path=args.state_path,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
