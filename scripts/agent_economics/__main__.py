from __future__ import annotations

import importlib
import sys

from .command_catalog import COMMANDS, COMMAND_BY_NAME


def _help() -> str:
    lines = [
        "Agent Economics — bounded repository evidence for coding agents",
        "",
        "usage: python -m agent_economics <command> [options]",
        "",
    ]
    groups: list[str] = []
    for command in COMMANDS:
        if command.group not in groups:
            groups.append(command.group)
    for group in groups:
        lines.append(group.upper())
        for command in COMMANDS:
            if command.group == group:
                lines.append(f"  {command.name:20} {command.summary}")
        lines.append("")
    lines.extend(
        (
            "Run 'python -m agent_economics <command> --help' for command options.",
            "Start in an unfamiliar repository with: python -m agent_economics doctor",
        )
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        if len(args) > 1 and args[0] == "help":
            args = [args[1], "--help", *args[2:]]
        else:
            print(_help())
            return
    command = COMMAND_BY_NAME.get(args[0])
    if command is None:
        names = ", ".join(item.name for item in COMMANDS)
        raise SystemExit(
            f"agent-economics: unknown command {args[0]!r}; available: {names}"
        )
    module = importlib.import_module(f".{command.module}", __package__)
    module.main(args[1:])


if __name__ == "__main__":
    main()
