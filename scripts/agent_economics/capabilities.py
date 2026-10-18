from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path

from .bounded_process import ProcessLimits, process_tree_capability, run_bounded
from .command_catalog import PROBE_COMMANDS
from .command_manifest import CommandManifestError, load_command_manifest


def _git_details(root: Path) -> dict[str, object]:
    git = shutil.which("git")
    if git is None:
        return {"available": False, "version": None, "worktree": False}
    version = run_bounded(
        repository_root=root,
        argv=(git, "--version"),
        limits=ProcessLimits(3, 4096, 4096),
    )
    worktree = run_bounded(
        repository_root=root,
        argv=(git, "rev-parse", "--is-inside-work-tree"),
        limits=ProcessLimits(3, 4096, 4096),
    )
    return {
        "available": True,
        "version": version.stdout.decode("utf-8", errors="replace").strip()
        if version.return_code == 0
        else None,
        "worktree": worktree.return_code == 0 and worktree.stdout.strip() == b"true",
    }


def capabilities(
    *, repository_root: Path, manifest_path: Path | None = None
) -> dict[str, object]:
    root = repository_root.resolve()
    workspace = root.is_dir()
    result: dict[str, object] = {
        "schema": {"name": "agent-economics-capabilities", "version": 2},
        "repository": {
            "workspace_available": workspace,
            "root": "." if workspace else None,
        },
        "platform": {"system": platform.system(), "python": platform.python_version()},
        "capabilities": {
            "git": {"available": False, "version": None, "worktree": False},
            "process_tree_termination": process_tree_capability(),
            "mutation_guard": {"available": False, "reason": "requires Git worktree"},
            "network_isolation": {
                "available": False,
                "reason": "not enforced by AgentCookbook",
            },
            "sandbox_isolation": {
                "available": False,
                "reason": "not enforced by AgentCookbook",
            },
            "probes": {"available": list(PROBE_COMMANDS)},
        },
        "commands": {},
        "warnings": [],
    }
    if not workspace:
        result["warnings"].append({"code": "repository_bytes_unavailable"})
        return result
    git_details = _git_details(root)
    result["capabilities"]["git"] = git_details
    result["capabilities"]["mutation_guard"] = {
        "available": bool(git_details["worktree"]),
        "reason": None
        if git_details["worktree"]
        else "tracked-byte mutation guard requires Git worktree",
    }
    if manifest_path is not None:
        target = manifest_path if manifest_path.is_absolute() else root / manifest_path
        try:
            manifest = load_command_manifest(target)
        except (OSError, CommandManifestError) as exc:
            result["warnings"].append(
                {"code": "command_manifest_unavailable", "detail": type(exc).__name__}
            )
        else:
            result["command_manifest_identity"] = manifest.identity
            result["commands"] = {
                name: {
                    "configured": True,
                    "available": shutil.which(spec.argv[0]) is not None,
                    "authorized": True,
                    "authorization_basis": "explicit manifest selection",
                    "stage": spec.stage,
                }
                for name, spec in manifest.commands.items()
            }
    return result


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Report portable AgentCookbook environment capabilities."
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = capabilities(
        repository_root=args.repository_root, manifest_path=args.manifest
    )
    print(
        json.dumps(payload, indent=2, sort_keys=True)
        if args.json
        else json.dumps(payload, sort_keys=True)
    )
